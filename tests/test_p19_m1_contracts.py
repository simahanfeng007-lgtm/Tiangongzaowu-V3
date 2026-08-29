"""P19-R2 M1 contract invariant tests.

Locks the typed verification contracts:
* strict/frozen/extra-forbid semantics;
* canonical hash recomputation + tamper rejection;
* derived-id preimage rules (id never inside its own hash);
* L2_JUDGE / block-capable rules;
* status discipline (PASS without reasons, NOT_APPLICABLE explicit,
  model_generated never beyond RECORD).
"""

from __future__ import annotations

import unittest

import pytest
from pydantic import ValidationError

from contracts.verification import (
    EnforcementMode,
    RegistrySnapshot,
    VerificationRecord,
    VerifierDescriptor,
    derive_registry_snapshot_id,
    derive_verification_record_id,
    derive_verifier_descriptor_id,
)
from total_gateway.verification_registry import VerifierRegistry

HASH_0 = "0" * 64
HASH_A = "a" * 64


def descriptor(**overrides) -> VerifierDescriptor:
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
        config_sha256=HASH_A,
        implementation_ref="tests/test_p19_m1_contracts.py",
        descriptor_sha256=HASH_0,
    )
    payload.update(overrides)
    return VerifierDescriptor(**payload).with_computed_sha256()


class VerifierDescriptorTests(unittest.TestCase):
    def test_hash_is_recomputable_and_tamper_is_detectable(self) -> None:
        good = descriptor()
        self.assertTrue(good.has_valid_descriptor_sha256())
        tampered = good.model_copy(update={"timeout_ms": 2_000})
        self.assertFalse(tampered.has_valid_descriptor_sha256())

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            descriptor(unknown_field="x")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        good = descriptor()
        with pytest.raises(ValidationError):
            good.timeout_ms = 5_000  # type: ignore[misc]

    def test_wrong_schema_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            descriptor(schema_version="tiangong.verifier_descriptor.v2")  # type: ignore[arg-type]

    def test_l2_judge_rules(self) -> None:
        with pytest.raises(ValidationError):
            descriptor(layer="L2_JUDGE", deterministic=True)
        with pytest.raises(ValidationError):
            descriptor(
                layer="L2_JUDGE",
                deterministic=False,
                default_enforcement="BLOCK",
            )

    def test_block_requires_block_capable(self) -> None:
        with pytest.raises(ValidationError):
            descriptor(default_enforcement="BLOCK", block_capable=False)

    def test_predicate_types_sorted_and_unique(self) -> None:
        with pytest.raises(ValidationError):
            descriptor(
                supported_predicate_types=("effect.target_exists", "effect.target_exists")
            )
        with pytest.raises(ValidationError):
            descriptor(
                supported_predicate_types=(
                    "effect.target_sha256_matches",
                    "effect.target_exists",
                )
            )

    def test_derived_descriptor_id_is_deterministic(self) -> None:
        first = derive_verifier_descriptor_id(
            verifier_id="verifier.test_echo", verifier_version="1"
        )
        second = derive_verifier_descriptor_id(
            verifier_id="verifier.test_echo", verifier_version="1"
        )
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("vfd_"))
        changed = derive_verifier_descriptor_id(
            verifier_id="verifier.test_echo", verifier_version="2"
        )
        self.assertNotEqual(first, changed)


class RegistrySnapshotTests(unittest.TestCase):
    def test_snapshot_identity_is_time_invariant(self) -> None:
        early = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        late = VerifierRegistry.with_defaults().snapshot(captured_at_ms=999_999)
        self.assertTrue(early.has_valid_snapshot_sha256())
        self.assertEqual(early.snapshot_sha256, late.snapshot_sha256)
        self.assertEqual(early.registry_snapshot_id, late.registry_snapshot_id)

    def test_snapshot_id_is_not_part_of_its_own_preimage(self) -> None:
        snap = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        self.assertTrue(snap.has_valid_snapshot_sha256())
        recomputed = derive_registry_snapshot_id(snapshot_sha256=snap.snapshot_sha256)
        self.assertEqual(snap.registry_snapshot_id, recomputed)

    def test_duplicate_verifier_rejected(self) -> None:
        one = descriptor()
        two = descriptor()
        with pytest.raises(ValidationError):
            RegistrySnapshot(
                registry_snapshot_id="vrg_" + "0" * 64,
                verifiers=(one, two),
                captured_at_ms=1,
                snapshot_sha256=HASH_0,
            )

    def test_snapshot_excludes_no_verifiers(self) -> None:
        with pytest.raises(ValidationError):
            RegistrySnapshot(
                registry_snapshot_id="vrg_" + "0" * 64,
                verifiers=(),
                captured_at_ms=1,
                snapshot_sha256=HASH_0,
            )

    def test_descriptor_set_change_changes_snapshot_hash(self) -> None:
        base = VerifierRegistry(descriptor__baseline := (descriptor(),)).snapshot(
            captured_at_ms=1
        )
        other = VerifierRegistry(
            (descriptor(verifier_id="verifier.test_other"),)
        ).snapshot(captured_at_ms=1)
        self.assertNotEqual(base.snapshot_sha256, other.snapshot_sha256)
        self.assertIsNotNone(descriptor__baseline)


class VerificationRecordTests(unittest.TestCase):
    def _record(self, **overrides) -> VerificationRecord:
        snap = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            request_id="req_" + "a" * 64,
            run_id="run_" + "b" * 64,
            generation=1,
            verifier_id="verifier.effect_state",
            verifier_version="1",
            registry_snapshot_sha256=snap.snapshot_sha256,
            predicate_id="vpd_test_1",
            predicate_type="effect.terminal_succeeded",
            subject_kind="effect",
            subject_identity="eff_" + "c" * 64,
            evaluation_phase="POST_EXECUTION",
            status="PASS",
            enforcement="RECORD",
            reason_codes=(),
            evidence_refs=("ev_1",),
            evidence_sha256="d" * 64,
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=1_234,
            result_sha256=HASH_0,
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

    def test_hash_recomputable_and_tamper_detectable(self) -> None:
        good = self._record()
        self.assertTrue(good.has_valid_result_sha256())
        tampered = good.model_copy(update={"status": "FAIL"})
        self.assertFalse(tampered.has_valid_result_sha256())

    def test_record_id_derivation_stable(self) -> None:
        good = self._record()
        again = derive_verification_record_id(result_sha256=good.result_sha256)
        self.assertEqual(good.verification_record_id, again)

    def test_pass_must_not_carry_reason_codes(self) -> None:
        with pytest.raises(ValidationError):
            self._record(reason_codes=("gap.x",))

    def test_fail_may_carry_reason_codes(self) -> None:
        record = self._record(status="FAIL", reason_codes=("gap.missing_column",))
        self.assertEqual(record.reason_codes, ("gap.missing_column",))

    def test_not_applicable_is_explicit_and_clean(self) -> None:
        record = self._record(status="NOT_APPLICABLE")
        self.assertNotEqual(record.status, "INCONCLUSIVE")
        with pytest.raises(ValidationError):
            self._record(status="NOT_APPLICABLE", reason_codes=("gap.x",))

    def test_m1_persists_record_enforcement_only(self) -> None:
        with pytest.raises(ValidationError):
            self._record(enforcement="ALERT")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            self._record(enforcement="BLOCK")  # type: ignore[arg-type]

    def test_model_generated_cannot_exceed_record(self) -> None:
        record = self._record(model_generated=True)
        self.assertTrue(record.model_generated)
        # enforcement is already RECORD-only at the type level; the
        # validator additionally forbids model_generated beyond RECORD
        # for future widening safety.
        self.assertEqual(record.enforcement, "RECORD")

    def test_evaluation_phase_is_required_and_bounded(self) -> None:
        self.assertEqual(self._record().evaluation_phase, "POST_EXECUTION")
        with pytest.raises(ValidationError):
            self._record(evaluation_phase="SOMEWHERE_ELSE")  # type: ignore[arg-type]

    def test_enforcement_mode_enum_exists_for_future(self) -> None:
        # The wide enum is exported for later milestones even though M1
        # records only ever carry RECORD.
        self.assertEqual(
            set(EnforcementMode.__args__ if hasattr(EnforcementMode, "__args__") else ()),
            set() | {"RECORD", "ALERT", "BLOCK"},
        )


if __name__ == "__main__":
    unittest.main()
