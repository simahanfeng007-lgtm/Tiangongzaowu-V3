from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contracts.cognition_prior import CognitionPrior, derive_cognition_prior_id
from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.cognition_statement import CognitionStatement, CognitionValue, derive_cognition_id
from contracts.cognition_revision import CognitionRevision, derive_cognition_revision_id

Z = "0" * 64
A = "a" * 64
B = "b" * 64
ONE, TWO, THREE, FOUR = ("1" * 64, "2" * 64, "3" * 64, "4" * 64)
LIFE = "life.main"


def evid(fill: str) -> str:
    return "cev_" + fill * 64


def source_ref(*, kind: str = "code_perception") -> CognitionSourceRef:
    return CognitionSourceRef(source_kind=kind, object_id="repo.main", object_revision=1, sha256=Z)


def evidence(**changes) -> CognitionEvidence:
    material = dict(
        life_id=LIFE, domain="software", world_scope_hash=A, principal_scope_hash=B,
        privacy_scope="system", source_ref=source_ref(), evidence_class="observed",
        source_credibility_milli=900, authority_ceiling_milli=950,
        provenance_integrity_milli=1000, observation_mode="positive",
        observation="Observed software structure.", coverage_milli=1000,
        search_scope_hash=None, independence_group_hash=ONE,
        lineage_root_hashes=(TWO,), derived_from_evidence_ids=(), ancestor_cognition_ids=(),
        content_object_id="obj.code", content_sha256=Z, extractor_kind="deterministic",
        observed_at_ms=10, valid_from_ms=0, valid_until_ms=None, volatility_class="long",
    )
    material.update(changes)
    return CognitionEvidence(
        evidence_id=derive_cognition_evidence_id(**material),
        **material,
        evidence_sha256=Z,
    )


def statement(*, value="authoritative_orchestrator", status="CANDIDATE", level="C0", supports=(), counters=(), revision=1, supersedes=None, last_verified=None, valid_until=None, auto_verify=True) -> CognitionStatement:
    cid = derive_cognition_id(
        life_id=LIFE, domain="software", world_scope_hash=A, principal_scope_hash=B,
        claim_kind="component_role", subject_ref="tiangong.zongdiaodu",
        predicate="role", condition_sha256=None,
    )
    if auto_verify and last_verified is None and status in {"STABLE", "CORE"}:
        last_verified = 10
    return CognitionStatement(
        cognition_id=cid, life_id=LIFE, domain="software", world_scope_hash=A,
        principal_scope_hash=B, privacy_scope="system", claim_kind="component_role",
        subject_ref="tiangong.zongdiaodu", predicate="role",
        value=CognitionValue(kind="entity_ref", entity_ref=value),
        proposal_origin="deterministic_extraction", status=status, stability_level=level,
        confidence_milli=0 if status == "CANDIDATE" else 800,
        supporting_evidence_ids=supports, counterevidence_ids=counters,
        valid_from_ms=0, valid_until_ms=valid_until, last_verified_at_ms=last_verified,
        revision=revision, supersedes_statement_sha256=supersedes, statement_sha256=Z,
    ).with_computed_statement_sha256()


def revision_payload(*, old: CognitionStatement | None, new: CognitionStatement, sequence: int, transition: str, authority: str = "deterministic_policy", groups=(), counter_groups=(), triggers=()) -> dict:
    from_hash = old.statement_sha256 if old else None
    rid = derive_cognition_revision_id(cognition_id=new.cognition_id, sequence=sequence, from_statement_sha256=from_hash, to_statement_sha256=new.statement_sha256)
    return dict(
        cognition_revision_id=rid, life_id=LIFE, cognition_id=new.cognition_id,
        sequence=sequence, previous_revision_sha256=None if sequence == 1 else FOUR,
        from_statement_sha256=from_hash, to_statement_sha256=new.statement_sha256,
        from_status=old.status if old else None, to_status=new.status,
        from_stability_level=old.stability_level if old else None,
        to_stability_level=new.stability_level, transition=transition,
        trigger_evidence_ids=triggers, support_independence_groups=groups,
        counter_independence_groups=counter_groups, support_milli=850 if groups else 0,
        counter_milli=700 if counter_groups else 0, correlation_discount_milli=0,
        staleness_penalty_milli=0, decision_authority=authority,
        policy_ref="policy.cognition.v1", policy_sha256=Z,
        reason_codes=("evidence.quorum",), created_at_ms=20, revision_sha256=Z,
    )


class WorldCognitionContractTests(unittest.TestCase):
    def test_prior_cannot_be_empirical_evidence(self):
        pid = derive_cognition_prior_id(life_id=LIFE, domain="software", prior_key="evidence_first")
        base = dict(prior_id=pid, life_id=LIFE, domain="software", prior_key="evidence_first", prior_kind="epistemic", principle="Observed evidence outranks inference.", interpretive_weight_milli=900, source_policy_ref="policy.cognition.v1", source_policy_sha256=Z, revision=1, status="active", created_at_ms=1, prior_sha256=Z)
        self.assertTrue(CognitionPrior(**base).with_computed_prior_sha256().has_valid_prior_sha256())
        with self.assertRaises(ValidationError):
            CognitionPrior(**base, empirical_evidence_weight_milli=1)

    def test_evidence_cannot_launder_authority(self):
        with self.assertRaises(ValidationError):
            evidence(source_credibility_milli=951, authority_ceiling_milli=950)

    def test_negative_evidence_requires_scope_and_positive_coverage(self):
        with self.assertRaises(ValidationError):
            evidence(observation_mode="negative")
        with self.assertRaises(ValidationError):
            evidence(observation_mode="negative", search_scope_hash=THREE, coverage_milli=0)
        item = evidence(observation_mode="negative", search_scope_hash=THREE, coverage_milli=700).with_computed_evidence_sha256()
        self.assertTrue(item.has_valid_evidence_sha256())

    def test_positive_evidence_cannot_claim_negative_search_scope(self):
        with self.assertRaises(ValidationError):
            evidence(search_scope_hash=THREE)

    def test_llm_evidence_cannot_masquerade_as_observation(self):
        with self.assertRaises(ValidationError):
            evidence(extractor_kind="llm_synthesis", evidence_class="observed")
        item = evidence(extractor_kind="llm_synthesis", evidence_class="model_inference").with_computed_evidence_sha256()
        self.assertTrue(item.has_valid_evidence_sha256())

    def test_model_source_cannot_be_relabeled_as_direct_evidence(self):
        with self.assertRaises(ValidationError):
            evidence(source_ref=source_ref(kind="model_synthesis"), evidence_class="observed")

    def test_evidence_identity_changes_with_provenance(self):
        first = evidence()
        self.assertNotEqual(first.evidence_id, evidence(source_credibility_milli=850).evidence_id)
        self.assertNotEqual(first.evidence_id, evidence(lineage_root_hashes=(THREE,)).evidence_id)

    def test_cognition_slot_identity_excludes_value(self):
        first = statement()
        second = statement(value="replacement_orchestrator")
        self.assertEqual(first.cognition_id, second.cognition_id)
        self.assertNotEqual(first.statement_sha256, second.statement_sha256)

    def test_typed_value_requires_exactly_one_branch(self):
        with self.assertRaises(ValidationError):
            CognitionValue(kind="string", string_value="x", boolean_value=True)

    def test_statement_lifecycle_requires_evidence_and_verification(self):
        with self.assertRaises(ValidationError):
            statement(status="STABLE", level="C2", supports=(evid("1"),))
        with self.assertRaises(ValidationError):
            statement(status="STABLE", level="C2", supports=(evid("1"), evid("2")), last_verified=None, auto_verify=False)
        stable = statement(status="STABLE", level="C2", supports=(evid("1"), evid("2")), last_verified=10)
        self.assertTrue(stable.has_valid_statement_sha256())

    def test_challenged_requires_counterevidence(self):
        with self.assertRaises(ValidationError):
            statement(status="CHALLENGED", level="C2", supports=(evid("1"),))

    def test_retired_requires_closed_validity(self):
        with self.assertRaises(ValidationError):
            statement(status="RETIRED", level="C2")
        self.assertTrue(statement(status="RETIRED", level="C2", valid_until=20).has_valid_statement_sha256())

    def test_stable_promotion_requires_independent_groups(self):
        old = statement(status="PROVISIONAL", level="C1", supports=(evid("1"),))
        new = statement(status="STABLE", level="C2", supports=(evid("1"), evid("2")), revision=2, supersedes=old.statement_sha256)
        base = revision_payload(old=old, new=new, sequence=2, transition="PROMOTE")
        with self.assertRaises(ValidationError):
            CognitionRevision(**{**base, "support_independence_groups": (ONE,)})
        accepted = CognitionRevision(**{**base, "support_independence_groups": (ONE, TWO)}).with_computed_revision_sha256()
        self.assertTrue(accepted.has_valid_revision_sha256())

    def test_deterministic_genesis_must_start_candidate_c0(self):
        stable = statement(status="STABLE", level="C2", supports=(evid("1"), evid("2")))
        payload = revision_payload(old=None, new=stable, sequence=1, transition="GENESIS", groups=(ONE, TWO))
        with self.assertRaises(ValidationError):
            CognitionRevision(**payload)

    def test_c4_protection_requires_explicit_authority(self):
        core = statement(status="CORE", level="C3", supports=(evid("1"), evid("2"), evid("3")))
        protected = statement(status="CORE", level="C4", supports=(evid("1"), evid("2"), evid("3")), revision=2, supersedes=core.statement_sha256)
        payload = revision_payload(old=core, new=protected, sequence=2, transition="PROTECT", groups=(ONE, TWO, THREE))
        with self.assertRaises(ValidationError):
            CognitionRevision(**payload)
        accepted = CognitionRevision(**{**payload, "decision_authority": "explicit_system_authority"}).with_computed_revision_sha256()
        self.assertTrue(accepted.has_valid_revision_sha256())

    def test_protected_c4_cannot_be_superseded_by_ordinary_policy(self):
        current = statement(status="CORE", level="C4", supports=(evid("1"), evid("2"), evid("3")))
        replacement = statement(value="replacement_orchestrator", status="CORE", level="C4", supports=(evid("1"), evid("2"), evid("3")), revision=2, supersedes=current.statement_sha256)
        payload = revision_payload(old=current, new=replacement, sequence=2, transition="SUPERSEDE", groups=(ONE, TWO, THREE))
        with self.assertRaises(ValidationError):
            CognitionRevision(**payload)
        accepted = CognitionRevision(**{**payload, "decision_authority": "explicit_system_authority"}).with_computed_revision_sha256()
        self.assertTrue(accepted.has_valid_revision_sha256())

    def test_support_and_counter_groups_cannot_overlap(self):
        old = statement(status="PROVISIONAL", level="C1", supports=(evid("1"),))
        challenged = statement(status="CHALLENGED", level="C1", supports=(evid("1"),), counters=(evid("2"),), revision=2, supersedes=old.statement_sha256)
        payload = revision_payload(old=old, new=challenged, sequence=2, transition="CHALLENGE", groups=(ONE,), counter_groups=(ONE,), triggers=(evid("2"),))
        with self.assertRaises(ValidationError):
            CognitionRevision(**payload)


if __name__ == "__main__":
    unittest.main()
