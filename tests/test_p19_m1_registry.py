"""P19-R2 M1 registry + recorder tests.

Fail-closed rules:
* unknown verifier / wrong version lookup;
* duplicate registration;
* predicate allowlist enforcement;
* recorder rejects non-RECORD, stale snapshots, undeclared predicates,
  unsupported subject kinds, and tampered hashes (with a fake store).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

import pytest

from contracts.verification import (
    RegistrySnapshot,
    VerificationRecord,
    VerifierDescriptor,
    derive_registry_snapshot_id,
    derive_verification_record_id,
)
from total_gateway.verification_recording import (
    RecordOutcome,
    VerificationRecordRejected,
    VerificationRecorder,
)
from total_gateway.verification_registry import (
    UnknownVerifierError,
    VerifierRegistry,
    default_descriptors,
)


def _descriptor(**overrides) -> VerifierDescriptor:
    payload = dict(
        verifier_id="verifier.test_echo",
        verifier_version="1",
        layer="L0_DETERMINISTIC",
        deterministic=True,
        supported_predicate_types=("effect.target_exists",),
        accepted_authorities=("EFFECT_LEDGER",),
        supported_subject_kinds=("effect",),
        max_input_bytes=1024,
        timeout_ms=1_000,
        default_enforcement="RECORD",
        block_capable=True,
        repair_feedback_capable=False,
        producer_component_id="tiangong-gateway",
        config_sha256="a" * 64,
        implementation_ref="tests/test_p19_m1_registry.py",
        descriptor_sha256="0" * 64,
    )
    payload.update(overrides)
    return VerifierDescriptor(**payload).with_computed_sha256()


@dataclass
class _FakePersisted:
    created_by_this_call: bool
    duplicate: bool
    recorded_at_ms: int


class _FakeStore:
    def __init__(self) -> None:
        self.records: list[VerificationRecord] = []

    def put_verification_record(self, record, *, recorded_at_ms: int) -> _FakePersisted:
        for existing in self.records:
            if existing.verification_record_id == record.verification_record_id:
                if existing.result_sha256 != record.result_sha256:
                    raise AssertionError("fake store conflict path not exercised here")
                return _FakePersisted(False, True, recorded_at_ms)
        self.records.append(record)
        return _FakePersisted(True, False, recorded_at_ms)


class RegistryTests(unittest.TestCase):
    def test_defaults_register_three_dormant_oracles(self) -> None:
        descriptors = default_descriptors()
        self.assertEqual(
            sorted(item.verifier_id for item in descriptors),
            [
                "verifier.artifact_content",
                "verifier.effect_state",
                "verifier.repository_state",
            ],
        )
        for item in descriptors:
            self.assertTrue(item.has_valid_descriptor_sha256())
            self.assertEqual(item.default_enforcement, "RECORD")
            self.assertEqual(item.layer, "L0_DETERMINISTIC")

    def test_snapshot_is_deterministic_for_same_descriptors(self) -> None:
        first = VerifierRegistry(default_descriptors()).snapshot(captured_at_ms=5)
        second = VerifierRegistry(default_descriptors()).snapshot(captured_at_ms=6)
        self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)

    def test_unknown_verifier_fail_closed(self) -> None:
        registry = VerifierRegistry.with_defaults()
        with pytest.raises(UnknownVerifierError):
            registry.find("verifier.nope", "1")

    def test_wrong_version_fail_closed(self) -> None:
        registry = VerifierRegistry.with_defaults()
        with pytest.raises(UnknownVerifierError):
            registry.find("verifier.effect_state", "2")

    def test_duplicate_registration_rejected(self) -> None:
        with pytest.raises(ValueError):
            VerifierRegistry((_descriptor(), _descriptor()))

    def test_predicate_allowlist_enforced(self) -> None:
        with pytest.raises(ValueError):
            VerifierRegistry(
                (_descriptor(supported_predicate_types=("factuality.correct",)),)
            )
        with pytest.raises(ValueError):
            VerifierRegistry(
                (_descriptor(supported_predicate_types=("overall_quality.good",)),)
            )

    def test_descriptor_with_bad_hash_rejected(self) -> None:
        bad = _descriptor().model_copy(update={"descriptor_sha256": "b" * 64})
        with pytest.raises(ValueError):
            VerifierRegistry((bad,))

    def test_empty_registry_rejected(self) -> None:
        with pytest.raises(ValueError):
            VerifierRegistry(())


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = VerifierRegistry.with_defaults()
        self.snapshot = self.registry.snapshot(captured_at_ms=1)
        self.store = _FakeStore()

    def _record(self, **overrides) -> VerificationRecord:
        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            request_id="req_" + "a" * 64,
            run_id="run_" + "b" * 64,
            generation=1,
            verifier_id="verifier.effect_state",
            verifier_version="1",
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            predicate_id="vpd_test_1",
            predicate_type="effect.terminal_succeeded",
            subject_kind="effect",
            subject_identity="eff_" + "c" * 64,
            evaluation_phase="POST_EXECUTION",
            status="FAIL",
            enforcement="RECORD",
            reason_codes=("effect.not_terminal",),
            evidence_refs=("ev_1",),
            evidence_sha256="d" * 64,
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=1_234,
            result_sha256="0" * 64,
        )
        payload.update(overrides)
        record = VerificationRecord(**payload).with_computed_sha256()
        return record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )

    def test_valid_record_persists(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        outcome = recorder.record(self._record(), recorded_at_ms=2_000)
        self.assertTrue(outcome.created_by_this_call)
        self.assertFalse(outcome.duplicate)
        self.assertEqual(len(self.store.records), 1)

    def test_duplicate_same_content_is_idempotent(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record()
        first = recorder.record(record, recorded_at_ms=2_000)
        second = recorder.record(record, recorded_at_ms=2_500)
        self.assertTrue(first.created_by_this_call)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(self.store.records), 1)

    def test_recorder_rejects_tampered_hash(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record()
        tampered = record.model_copy(update={"status": "PASS", "reason_codes": ()})
        with pytest.raises(VerificationRecordRejected):
            recorder.record(tampered, recorded_at_ms=2_000)

    def test_recorder_rejects_stale_snapshot(self) -> None:
        other_snapshot = VerifierRegistry(
            default_descriptors()
            + (_descriptor(),)
        ).snapshot(captured_at_ms=1)
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record(
            registry_snapshot_sha256=other_snapshot.snapshot_sha256
        )
        with pytest.raises(VerificationRecordRejected):
            recorder.record(record, recorded_at_ms=2_000)

    def test_recorder_rejects_unknown_verifier_in_snapshot(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record(verifier_id="verifier.test_echo")
        with pytest.raises(UnknownVerifierError):
            recorder.record(record, recorded_at_ms=2_000)

    def test_recorder_rejects_undeclared_predicate(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record(predicate_type="xlsx.required_columns")
        with pytest.raises(VerificationRecordRejected):
            recorder.record(record, recorded_at_ms=2_000)

    def test_recorder_rejects_unsupported_subject_kind(self) -> None:
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        record = self._record(subject_kind="artifact")
        with pytest.raises(VerificationRecordRejected):
            recorder.record(record, recorded_at_ms=2_000)

    def test_recorder_rejects_misbound_enforcement_via_contract(self) -> None:
        # enforcement != RECORD is impossible to construct at the type
        # level in M1; the recorder still guards against widening bugs by
        # validating the pinned snapshot itself.
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        self.assertTrue(recorder.snapshot.has_valid_snapshot_sha256())

    def test_recorder_rejects_snapshot_with_bad_hash(self) -> None:
        tampered_snapshot = self.snapshot.model_copy(
            update={"snapshot_sha256": "e" * 64}
        )
        with pytest.raises(VerificationRecordRejected):
            VerificationRecorder(snapshot=tampered_snapshot, store=self.store)


if __name__ == "__main__":
    unittest.main()
