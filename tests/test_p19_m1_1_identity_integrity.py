"""P19-R2 M1.1 identity-integrity regression tests (review matrix A/C/E/F/H/I).

model_copy(update=...) bypasses pydantic validation, so every trust
boundary must re-verify derived identity and nested descriptor
integrity instead of trusting the constructor path.
"""

from __future__ import annotations

import unittest

import pytest

from contracts.verification import (
    RegistrySnapshot,
    VerificationRecord,
    derive_registry_snapshot_id,
    derive_verification_record_id,
)
from total_gateway.verification_recording import (
    VerificationRecordRejected,
    VerificationRecorder,
)
from total_gateway.verification_registry import VerifierRegistry

from tests.test_p19_m1_registry import _FakeStore


WRONG_BUT_VALID_ID = "vrs_" + "e" * 64
PLACEHOLDER_ID = "vrs_" + "0" * 64
WRONG_BUT_VALID_SNAPSHOT_ID = "vrg_" + "e" * 64


def _good_record(snapshot_sha: str) -> VerificationRecord:
    payload = dict(
        verification_record_id=PLACEHOLDER_ID,
        request_id="req_" + "a" * 64,
        run_id="run_" + "b" * 64,
        generation=1,
        verifier_id="verifier.effect_state",
        verifier_version="2",
        registry_snapshot_sha256=snapshot_sha,
        predicate_id="vpd_m11",
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
    record = VerificationRecord(**payload).with_computed_sha256()
    return record.model_copy(
        update={
            "verification_record_id": derive_verification_record_id(
                result_sha256=record.result_sha256
            )
        }
    )


def _snapshot_with_descriptors(descriptors) -> RegistrySnapshot:
    """Build a snapshot whose OUTER hashes and derived id are fully valid."""
    partial = RegistrySnapshot(
        registry_snapshot_id="vrg_" + "0" * 64,
        verifiers=tuple(sorted(descriptors, key=lambda d: d.verifier_id)),
        captured_at_ms=1,
        snapshot_sha256="0" * 64,
    ).with_computed_sha256()
    return partial.model_copy(
        update={
            "registry_snapshot_id": derive_registry_snapshot_id(
                snapshot_sha256=partial.snapshot_sha256
            )
        }
    )


class RecordIdentityTests(unittest.TestCase):
    """A / C: wrong-but-valid-format or placeholder ids must fail closed."""

    def setUp(self) -> None:
        self.snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        self.store = _FakeStore()
        self.recorder = VerificationRecorder(
            snapshot=self.snapshot, store=self.store
        )

    def test_a_wrong_but_valid_format_id_rejected_by_recorder(self) -> None:
        good = _good_record(self.snapshot.snapshot_sha256)
        bad_id = good.model_copy(update={"verification_record_id": WRONG_BUT_VALID_ID})
        # hash stays valid; only the derived identity is wrong
        self.assertTrue(bad_id.has_valid_result_sha256())
        self.assertFalse(bad_id.has_valid_identity())
        with pytest.raises(VerificationRecordRejected):
            self.recorder.record(bad_id, recorded_at_ms=2_000)
        self.assertEqual(self.store.records, [])

    def test_c_placeholder_id_rejected_by_recorder(self) -> None:
        good = _good_record(self.snapshot.snapshot_sha256)
        placeholder = good.model_copy(update={"verification_record_id": PLACEHOLDER_ID})
        self.assertTrue(placeholder.has_valid_result_sha256())
        with pytest.raises(VerificationRecordRejected):
            self.recorder.record(placeholder, recorded_at_ms=2_000)
        self.assertEqual(self.store.records, [])

    def test_valid_record_still_passes(self) -> None:
        good = _good_record(self.snapshot.snapshot_sha256)
        self.assertTrue(good.has_valid_identity())
        outcome = self.recorder.record(good, recorded_at_ms=2_000)
        self.assertTrue(outcome.created_by_this_call)


class SnapshotIdentityTests(unittest.TestCase):
    """D / E / F: snapshot identity + nested descriptor integrity."""

    def setUp(self) -> None:
        self.store = _FakeStore()

    def test_d_wrong_snapshot_id_rejected_by_recorder(self) -> None:
        snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        bad = snapshot.model_copy(
            update={"registry_snapshot_id": WRONG_BUT_VALID_SNAPSHOT_ID}
        )
        self.assertTrue(bad.has_valid_snapshot_sha256())
        self.assertFalse(bad.has_valid_identity())
        with pytest.raises(VerificationRecordRejected):
            VerificationRecorder(snapshot=bad, store=self.store)

    def test_e_tampered_descriptor_with_valid_outer_hashes_rejected(self) -> None:
        descriptors = list(VerifierRegistry.with_defaults().descriptors)
        # Tamper WITHOUT recomputing descriptor_sha256 -> stale inner hash,
        # then rebuild the outer snapshot hash + derived id so the outer
        # layer is fully self-consistent.
        tampered = descriptors[0].model_copy(update={"timeout_ms": 999_999})
        descriptors[0] = tampered
        snapshot = _snapshot_with_descriptors(descriptors)
        self.assertTrue(snapshot.has_valid_snapshot_sha256())
        self.assertTrue(snapshot.has_valid_identity())
        with pytest.raises(VerificationRecordRejected):
            VerificationRecorder(snapshot=snapshot, store=self.store)

    def test_f_allowlist_escape_with_all_hashes_valid_rejected(self) -> None:
        descriptors = list(VerifierRegistry.with_defaults().descriptors)
        malicious = descriptors[0].model_copy(
            update={
                "supported_predicate_types": (
                    "artifact.nonempty",
                    "factuality.correct",
                )
            }
        ).with_computed_sha256()  # descriptor hash is valid for the bad content
        descriptors[0] = malicious
        snapshot = _snapshot_with_descriptors(descriptors)
        self.assertTrue(snapshot.has_valid_identity())
        # descriptor itself passes model-level rules; only the registry
        # allowlist catches it — through the recorder trust boundary.
        with pytest.raises(VerificationRecordRejected):
            VerificationRecorder(snapshot=snapshot, store=self.store)

    def test_legitimate_snapshot_accepted(self) -> None:
        snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        recorder = VerificationRecorder(snapshot=snapshot, store=self.store)
        self.assertTrue(recorder.snapshot.has_valid_identity())


class DeterministicIdentityTests(unittest.TestCase):
    """H / I: identity stability."""

    def test_h_same_descriptors_same_identity(self) -> None:
        first = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        second = VerifierRegistry.with_defaults().snapshot(captured_at_ms=2)
        self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(
            first.registry_snapshot_id, second.registry_snapshot_id
        )

    def test_i_captured_at_change_does_not_change_identity(self) -> None:
        base = VerifierRegistry.with_defaults().snapshot(captured_at_ms=100)
        later = base.model_copy(update={"captured_at_ms": 999_999})
        self.assertEqual(later.snapshot_sha256, base.snapshot_sha256)
        self.assertEqual(
            later.registry_snapshot_id, base.registry_snapshot_id
        )
        # ...and the derived id still matches after the metadata change
        self.assertTrue(later.has_valid_identity())


if __name__ == "__main__":
    unittest.main()
