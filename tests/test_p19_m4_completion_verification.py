"""P19-R2 M4 CompletionGate PLAN_BOUND integration tests."""
from __future__ import annotations

import unittest
from unittest import mock

from pydantic import ValidationError

from contracts import (
    canonical_sha256,
    derive_request_identity,
    derive_run_identity,
)
from contracts.verification import (
    AcceptancePredicate,
    EntryAssessment,
    VerificationPlan,
    VerificationPlanEntryV2,
    VerificationReadiness,
)
from total_gateway.completion_gate import (
    CompletionGate,
    CompletionGateError,
    CompletionRequirements,
)


def _identity():
    request = derive_request_identity("4" * 64)
    run = derive_run_identity(request.request_id, 1)
    return request, run


def _req(request_id, run_id, **kw):
    d = dict(request_id=request_id, run_id=run_id, generation=1, text_required=True)
    d.update(kw)
    return CompletionRequirements(**d)


def _entry():
    pred = AcceptancePredicate.create(
        predicate_type="artifact.nonempty", subject_kind="artifact"
    )
    return VerificationPlanEntryV2(
        plan_entry_id="vpe_" + "0" * 64,
        verifier_id="verifier.artifact_content",
        verifier_version="3",
        predicate=pred,
        subject_identity="arv_" + "1" * 64,
        evaluation_phase="POST_EXECUTION",
        required=True,
        entry_sha256="0" * 64,
    ).with_computed_sha256()


def _plan(rid, rnid):
    return VerificationPlan(
        verification_plan_id="vpl_" + "0" * 64,
        request_id=rid, run_id=rnid, generation=1,
        registry_snapshot_sha256="a" * 64,
        entries=(_entry(),),
        plan_sha256="0" * 64,
    ).with_computed_sha256()


def _ready(plan, *, ready=True, **kw):
    d = dict(
        verification_readiness_id="vrd_" + "0" * 64,
        verification_plan_id=plan.verification_plan_id,
        verification_plan_sha256=plan.plan_sha256,
        request_id=plan.request_id, run_id=plan.run_id,
        generation=plan.generation,
        registry_snapshot_sha256=plan.registry_snapshot_sha256,
        required_entry_count=1,
        satisfied_entry_count=1 if ready else 0,
        entry_assessments=(EntryAssessment(
            plan_entry_id=plan.entries[0].plan_entry_id,
            status="PASS" if ready else "MISSING",
        ),),
        supporting_verification_record_ids=("vrs_" + "1" * 64,) if ready else (),
        verification_ready=ready,
        failure_class="NONE" if ready else "MISSING_EVIDENCE",
        evaluated_at_ms=2000, readiness_sha256="0" * 64,
    )
    d.update(kw)
    return VerificationReadiness(**d).with_computed_sha256()


def _gate():
    """Minimal real CompletionGate (temp object store + fact ledger)."""
    import tempfile
    from pathlib import Path
    from total_gateway.object_store import ContentAddressedObjectStore
    from total_gateway.fact_ledger import FactLedger

    tmp = tempfile.mkdtemp()
    os = ContentAddressedObjectStore.open(Path(tmp) / "objects", now_ms=900)
    fl = FactLedger.open(Path(tmp) / "facts.sqlite3", os, now_ms=900)
    return CompletionGate(os, fl)


class TestNoneMode(unittest.TestCase):
    def setUp(self):
        self.gate = _gate()
        r, w = _identity()
        self.rid, self.rnid = r.request_id, w.run_id

    def test_completes(self):
        d = self.gate.evaluate(_req(self.rid, self.rnid), candidate_text="hello")
        self.assertEqual(d.outcome, "COMPLETED")
        self.assertEqual(d.verification_mode, "NONE")
        self.assertTrue(d.verification_ready)

    def test_rejects_readiness(self):
        plan = _plan(self.rid, self.rnid)
        with self.assertRaises(CompletionGateError):
            self.gate.evaluate(
                _req(self.rid, self.rnid), candidate_text="x",
                verification_readiness=_ready(plan))

    def test_default_is_none(self):
        self.assertEqual(_req(self.rid, self.rnid).verification_mode, "NONE")


class TestPlanBound(unittest.TestCase):
    def setUp(self):
        self.gate = _gate()
        r, w = _identity()
        self.rid, self.rnid = r.request_id, w.run_id
        self.plan = _plan(self.rid, self.rnid)

    def _pb(self):
        return _req(self.rid, self.rnid, verification_mode="PLAN_BOUND")

    def test_ready_completes(self):
        d = self.gate.evaluate(
            self._pb(), candidate_text="x",
            verification_readiness=_ready(self.plan))
        self.assertEqual(d.outcome, "COMPLETED")
        self.assertTrue(d.verification_ready)
        self.assertTrue(d.can_transition_request_completed)
        self.assertEqual(d.verification_mode, "PLAN_BOUND")

    def test_not_ready_blocks(self):
        d = self.gate.evaluate(
            self._pb(), candidate_text="x",
            verification_readiness=_ready(self.plan, ready=False))
        self.assertNotEqual(d.outcome, "COMPLETED")
        self.assertFalse(d.verification_ready)
        self.assertFalse(d.can_transition_request_completed)

    def test_missing_readiness_raises(self):
        with self.assertRaises(CompletionGateError):
            self.gate.evaluate(self._pb(), candidate_text="x")

    def test_invalid_readiness_raises(self):
        r = _ready(self.plan)
        bad = r.model_copy(update={"satisfied_entry_count": 999})
        with self.assertRaises(CompletionGateError):
            self.gate.evaluate(self._pb(), candidate_text="x",
                               verification_readiness=bad)

    def test_lineage_mismatch_raises(self):
        other = derive_request_identity("5" * 64)
        other_plan = _plan(other.request_id, self.rnid)
        with self.assertRaises(CompletionGateError):
            self.gate.evaluate(self._pb(), candidate_text="x",
                               verification_readiness=_ready(other_plan))

    def test_not_ready_and_not_text_blocks(self):
        d = self.gate.evaluate(
            self._pb(), candidate_text=None,
            verification_readiness=_ready(self.plan, ready=False))
        self.assertNotEqual(d.outcome, "COMPLETED")


class TestAntiDoubleAuthority(unittest.TestCase):
    FILES = [
        "src/total_gateway/outcome_oracles/artifact_content.py",
        "src/total_gateway/outcome_oracles/effect_state.py",
        "src/total_gateway/outcome_oracles/repository_state.py",
        "src/total_gateway/outcome_oracles/_common.py",
        "src/total_gateway/verification_recording.py",
        "src/total_gateway/verification_registry.py",
    ]

    def test_no_oracle_has_completion_authority(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for f in self.FILES:
            src = (root / f).read_text(encoding="utf-8")
            self.assertNotIn("can_transition_request_completed", src, f)
            self.assertNotIn("persist_terminal_completion", src, f)

    def test_contracts_carry_no_completion_field(self):
        for model in (VerificationPlan, VerificationReadiness):
            fields = model.model_fields
            self.assertNotIn("can_transition", fields)
            self.assertNotIn("outcome", fields)


class TestContractIdentity(unittest.TestCase):
    def setUp(self):
        r, w = _identity()
        self.rid, self.rnid = r.request_id, w.run_id

    def test_entry_identity(self):
        e = _entry()
        self.assertTrue(e.has_valid_identity())
        bad = e.model_copy(update={"subject_identity": "arv_" + "9" * 64})
        self.assertFalse(bad.has_valid_identity())

    def test_plan_identity(self):
        p = _plan(self.rid, self.rnid)
        self.assertTrue(p.has_valid_identity())
        bad = p.model_copy(update={"generation": 99})
        self.assertFalse(bad.has_valid_identity())

    def test_readiness_identity(self):
        p = _plan(self.rid, self.rnid)
        r = _ready(p)
        self.assertTrue(r.has_valid_identity())
        bad = r.model_copy(
            update={"verification_ready": not r.verification_ready})
        self.assertFalse(bad.has_valid_identity())

    def test_readiness_ready_with_failure_rejected(self):
        p = _plan(self.rid, self.rnid)
        with self.assertRaises(ValidationError):
            _ready(p, ready=True, failure_class="MISSING_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
