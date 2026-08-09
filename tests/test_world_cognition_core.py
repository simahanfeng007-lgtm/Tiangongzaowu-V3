from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))

from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.cognition_revision import CognitionRevision, derive_cognition_revision_id
from contracts.cognition_statement import CognitionStatement, CognitionValue
from v3.world_cognition.consolidator import CognitionConsolidator, CognitionProposal
from v3.world_cognition.facade import WorldCognitionFacade
from v3.world_cognition.retrieval import CognitionRetriever
from v3.world_cognition.stability import StabilityPolicy, evaluate_evidence, highest_eligible_level
from v3.world_cognition.store import CognitionConflictError, CognitionIntegrityError, WorldCognitionStore


LIFE = "life.main"
WORLD = "a" * 64
PRINCIPAL = "b" * 64
NOW = 10_000_000
ZERO = "0" * 64


def h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ev(
    tag: str,
    *,
    group: str | None = None,
    evidence_class: str = "observed",
    source_kind: str = "code_perception",
    extractor_kind: str = "deterministic",
    credibility: int = 950,
    authority: int = 1000,
    provenance: int = 1000,
    observation_mode: str = "positive",
    coverage: int = 1000,
    observed_at: int = NOW,
    valid_until: int | None = None,
    volatility: str = "structural",
    ancestors: tuple[str, ...] = (),
    world: str = WORLD,
    principal: str = PRINCIPAL,
) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind=source_kind,
        object_id=f"repo.e.{tag}",
        object_revision=1,
        sha256=h(f"source:{tag}"),
    )
    material = dict(
        life_id=LIFE,
        domain="software",
        world_scope_hash=world,
        principal_scope_hash=principal,
        privacy_scope="system",
        source_ref=source,
        evidence_class=evidence_class,
        source_credibility_milli=credibility,
        authority_ceiling_milli=authority,
        provenance_integrity_milli=provenance,
        observation_mode=observation_mode,
        observation=f"observation {tag}",
        coverage_milli=coverage,
        search_scope_hash=h(f"search:{tag}") if observation_mode in {"negative", "aggregate"} else None,
        independence_group_hash=h(f"group:{group or tag}"),
        lineage_root_hashes=(h(f"root:{tag}"),),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=tuple(sorted(ancestors)),
        content_object_id=f"content.{tag}",
        content_sha256=h(f"content:{tag}"),
        extractor_kind=extractor_kind,
        observed_at_ms=observed_at,
        valid_from_ms=0,
        valid_until_ms=valid_until,
        volatility_class=volatility,
    )
    item = CognitionEvidence(
        evidence_id=derive_cognition_evidence_id(**material),
        **material,
        evidence_sha256=ZERO,
    )
    return item.with_computed_evidence_sha256()


def proposal(value: str = "authoritative_orchestrator", *, privacy: str = "system", origin: str = "deterministic_extraction") -> CognitionProposal:
    return CognitionProposal(
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        privacy_scope=privacy,
        claim_kind="component_role",
        subject_ref="tiangong.zongdiaodu",
        predicate="role",
        value=CognitionValue(kind="entity_ref", entity_ref=value),
        proposal_origin=origin,
    )


def put(store: WorldCognitionStore, *items: CognitionEvidence) -> tuple[str, ...]:
    for item in items:
        store.put_evidence(item)
    return tuple(item.evidence_id for item in items)


class WorldCognitionCoreTests(unittest.TestCase):
    def test_disabled_facade_is_zero_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "world_cognition"
            facade = WorldCognitionFacade(enabled=False, root=root)
            self.assertEqual(facade.project_context(life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL), "")
            self.assertIsNone(facade.consolidate(proposal(), now_ms=NOW))
            self.assertFalse(facade.put_evidence(ev("off")))
            self.assertEqual(facade.install_software_priors(life_id=LIFE, now_ms=NOW), ())
            self.assertFalse(root.exists())

    def test_store_reads_do_not_create_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wc"
            store = WorldCognitionStore(root)
            self.assertIsNone(store.get_head(proposal().cognition_id))
            self.assertEqual(store.counts()["heads"], 0)
            self.assertFalse(root.exists())

    def test_store_rejects_invalid_contract_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            item = ev("bad").model_copy(update={"evidence_sha256": ZERO})
            with self.assertRaises(CognitionIntegrityError):
                store.put_evidence(item)

    def test_evidence_insert_is_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            item = ev("idem")
            self.assertTrue(store.put_evidence(item))
            self.assertFalse(store.put_evidence(item))
            self.assertEqual(store.get_evidence(item.evidence_id), item)

    def test_cross_scope_evidence_cannot_consolidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            item = ev("scope", world="c" * 64)
            store.put_evidence(item)
            with self.assertRaises(CognitionIntegrityError):
                CognitionConsolidator(store).consolidate(proposal(), support_evidence_ids=(item.evidence_id,), now_ms=NOW)

    def test_one_independent_direct_group_only_reaches_provisional(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            ids = put(store, ev("p1"))
            result = CognitionConsolidator(store).consolidate(proposal(), support_evidence_ids=ids, now_ms=NOW)
            self.assertEqual((result.head.status, result.head.stability_level), ("PROVISIONAL", "C1"))

    def test_two_independent_direct_groups_reach_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            ids = put(store, ev("s1"), ev("s2"))
            result = CognitionConsolidator(store).consolidate(proposal(), support_evidence_ids=ids, now_ms=NOW)
            self.assertEqual((result.head.status, result.head.stability_level), ("STABLE", "C2"))
            self.assertIn("PROMOTE", result.transitions)

    def test_three_independent_direct_groups_reach_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            ids = put(store, ev("c1"), ev("c2"), ev("c3"))
            result = CognitionConsolidator(store).consolidate(proposal(), support_evidence_ids=ids, now_ms=NOW)
            self.assertEqual((result.head.status, result.head.stability_level), ("CORE", "C3"))
            self.assertEqual(result.transitions.count("PROMOTE"), 3)

    def test_model_synthesis_cannot_self_promote(self):
        items = [
            ev(f"llm{i}", evidence_class="model_inference", source_kind="model_synthesis", extractor_kind="llm_synthesis")
            for i in range(5)
        ]
        report = evaluate_evidence(
            cognition_id=proposal().cognition_id, life_id=LIFE, domain="software",
            world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL,
            support=items, now_ms=NOW,
        )
        self.assertEqual(report.support_milli, 0)
        self.assertEqual(highest_eligible_level(report), "C0")

    def test_correlated_copies_do_not_create_independent_quorum(self):
        items = [ev(f"dup{i}", group="same", credibility=700) for i in range(10)]
        report = evaluate_evidence(
            cognition_id=proposal().cognition_id, life_id=LIFE, domain="software",
            world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL,
            support=items, now_ms=NOW,
        )
        self.assertEqual(report.support_group_count, 1)
        self.assertGreater(report.correlation_discount_milli, 0)
        self.assertEqual(highest_eligible_level(report), "C1")

    def test_self_derived_evidence_has_zero_support(self):
        p = proposal()
        item = ev("self", ancestors=(p.cognition_id,))
        report = evaluate_evidence(
            cognition_id=p.cognition_id, life_id=LIFE, domain="software",
            world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL,
            support=(item,), now_ms=NOW,
        )
        self.assertEqual(report.support_milli, 0)
        self.assertEqual(report.dropped_self_derived, (item.evidence_id,))

    def test_negative_evidence_coverage_limits_weight(self):
        low = ev("neg-low", observation_mode="negative", coverage=100)
        high = ev("neg-high", observation_mode="negative", coverage=1000)
        low_report = evaluate_evidence(cognition_id=proposal().cognition_id, life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, support=(low,), now_ms=NOW)
        high_report = evaluate_evidence(cognition_id=proposal().cognition_id, life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, support=(high,), now_ms=NOW)
        self.assertLess(low_report.support_milli, high_report.support_milli)

    def test_expired_and_future_evidence_are_not_counted(self):
        expired = ev("expired", valid_until=NOW - 1)
        future = ev("future", observed_at=NOW + 10 * 60 * 1000)
        report = evaluate_evidence(cognition_id=proposal().cognition_id, life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, support=(expired, future), now_ms=NOW)
        self.assertEqual(report.support_milli, 0)
        self.assertIn(expired.evidence_id, report.dropped_expired)
        self.assertIn(future.evidence_id, report.dropped_invalid)

    def test_same_group_on_both_sides_is_removed_from_decision(self):
        a = ev("conf-a", group="conflict")
        b = ev("conf-b", group="conflict")
        report = evaluate_evidence(cognition_id=proposal().cognition_id, life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, support=(a,), counter=(b,), now_ms=NOW)
        self.assertEqual(report.support_milli, 0)
        self.assertEqual(report.counter_milli, 0)
        self.assertEqual(len(report.conflicted_groups), 1)

    def test_candidate_value_can_be_replaced_before_stabilization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            first = core.consolidate(proposal("role.old"), now_ms=NOW)
            self.assertEqual(first.head.stability_level, "C0")
            second = core.consolidate(proposal("role.new"), now_ms=NOW + 1)
            self.assertEqual(second.head.value.entity_ref, "role.new")
            self.assertIn("REPLACE_CANDIDATE", second.transitions)

    def test_same_value_new_evidence_refreshes_or_promotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            one, two = ev("r1"), ev("r2")
            put(store, one, two)
            first = core.consolidate(proposal(), support_evidence_ids=(one.evidence_id,), now_ms=NOW)
            self.assertEqual(first.head.stability_level, "C1")
            second = core.consolidate(proposal(), support_evidence_ids=(two.evidence_id,), now_ms=NOW + 1)
            self.assertEqual(second.head.stability_level, "C2")
            self.assertIn("PROMOTE", second.transitions)

    def test_insufficient_new_value_does_not_overwrite_stable_cognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            base = (ev("base1"), ev("base2"))
            challenger = ev("weak", credibility=300)
            put(store, *base, challenger)
            stable = core.consolidate(proposal("role.old"), support_evidence_ids=tuple(x.evidence_id for x in base), now_ms=NOW)
            self.assertEqual(stable.head.stability_level, "C2")
            attempt = core.consolidate(proposal("role.new"), support_evidence_ids=(challenger.evidence_id,), now_ms=NOW + 1)
            self.assertFalse(attempt.changed)
            self.assertEqual(attempt.head.value.entity_ref, "role.old")

    def test_strong_new_value_must_challenge_reverify_before_supersede(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            old = (ev("old1"), ev("old2"))
            new = (ev("new1"), ev("new2"))
            put(store, *old, *new)
            core.consolidate(proposal("role.old"), support_evidence_ids=tuple(x.evidence_id for x in old), now_ms=NOW)
            result = core.consolidate(proposal("role.new"), support_evidence_ids=tuple(x.evidence_id for x in new), now_ms=NOW + 1)
            self.assertEqual(result.transitions, ("CHALLENGE", "BEGIN_REVERIFY", "SUPERSEDE"))
            self.assertEqual((result.head.value.entity_ref, result.head.stability_level), ("role.new", "C2"))

    def test_c4_cannot_be_superseded_by_deterministic_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            old = tuple(ev(f"p-old{i}") for i in range(3))
            new = tuple(ev(f"p-new{i}") for i in range(3))
            put(store, *old, *new)
            core_result = core.consolidate(proposal("role.old"), support_evidence_ids=tuple(x.evidence_id for x in old), now_ms=NOW)
            protected = core.protect(core_result.head.cognition_id, now_ms=NOW + 1, decision_authority="explicit_system_authority")
            self.assertEqual(protected.stability_level, "C4")
            attempt = core.consolidate(proposal("role.new"), support_evidence_ids=tuple(x.evidence_id for x in new), now_ms=NOW + 2)
            self.assertNotEqual(attempt.head.value.entity_ref, "role.new")
            self.assertEqual(attempt.reason, "protected_c4_requires_authority")
            final = core.consolidate(proposal("role.new"), support_evidence_ids=tuple(x.evidence_id for x in new), now_ms=NOW + 3, decision_authority="explicit_system_authority")
            self.assertEqual((final.head.value.entity_ref, final.head.stability_level), ("role.new", "C4"))

    def test_llm_string_cannot_be_revision_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CognitionIntegrityError):
                CognitionConsolidator(WorldCognitionStore(Path(tmp) / "wc")).consolidate(proposal(), now_ms=NOW, decision_authority="llm")

    def test_privacy_scope_of_existing_slot_cannot_be_downgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            item = ev("privacy")
            put(store, item)
            core.consolidate(proposal(privacy="private"), support_evidence_ids=(item.evidence_id,), now_ms=NOW)
            with self.assertRaises(CognitionIntegrityError):
                core.consolidate(proposal(privacy="public"), support_evidence_ids=(item.evidence_id,), now_ms=NOW + 1)

    def test_retrieval_only_projects_live_stable_cognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            items = (
                ev("live1", volatility="transient", observed_at=NOW),
                ev("live2", volatility="transient", observed_at=NOW),
            )
            put(store, *items)
            result = core.consolidate(proposal(), support_evidence_ids=tuple(x.evidence_id for x in items), now_ms=NOW)
            self.assertEqual(result.head.stability_level, "C2")
            retriever = CognitionRetriever(store)
            fresh = retriever.project_context(life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, now_ms=NOW, query="zongdiaodu")
            self.assertIn("tiangong.zongdiaodu", fresh)
            stale = retriever.project_context(life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, now_ms=NOW + 24 * 60 * 60 * 1000, query="zongdiaodu")
            self.assertEqual(stale, "")

    def test_retrieval_enforces_privacy_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            items = (ev("priv1"), ev("priv2"))
            put(store, *items)
            core.consolidate(proposal(privacy="private"), support_evidence_ids=tuple(x.evidence_id for x in items), now_ms=NOW)
            retriever = CognitionRetriever(store)
            hidden = retriever.project_context(life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, now_ms=NOW)
            visible = retriever.project_context(life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, now_ms=NOW, allowed_privacy_scopes=("private",))
            self.assertEqual(hidden, "")
            self.assertIn("tiangong.zongdiaodu", visible)

    def test_store_compare_and_swap_rejects_stale_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldCognitionStore(Path(tmp) / "wc")
            core = CognitionConsolidator(store)
            item = ev("cas")
            put(store, item)
            first = core.consolidate(proposal(), support_evidence_ids=(item.evidence_id,), now_ms=NOW)
            stale = first.head
            latest_decision = store.get_latest_revision(stale.cognition_id)
            # Construct two valid REFRESH transitions from the same head. The first
            # commits; the second must fail CAS rather than overwrite it.
            def build(tag: str, at: int):
                statement = stale.model_copy(update={
                    "revision": stale.revision + 1,
                    "confidence_milli": stale.confidence_milli + (1 if tag == "a" else 2),
                    "supersedes_statement_sha256": stale.statement_sha256,
                    "statement_sha256": ZERO,
                }).with_computed_statement_sha256()
                rid = derive_cognition_revision_id(cognition_id=stale.cognition_id, sequence=statement.revision, from_statement_sha256=stale.statement_sha256, to_statement_sha256=statement.statement_sha256)
                decision = CognitionRevision(
                    cognition_revision_id=rid, life_id=LIFE, cognition_id=stale.cognition_id,
                    sequence=statement.revision, previous_revision_sha256=latest_decision.revision_sha256,
                    from_statement_sha256=stale.statement_sha256, to_statement_sha256=statement.statement_sha256,
                    from_status=stale.status, to_status=statement.status,
                    from_stability_level=stale.stability_level, to_stability_level=statement.stability_level,
                    transition="REFRESH", trigger_evidence_ids=(item.evidence_id,),
                    support_independence_groups=(item.independence_group_hash,), counter_independence_groups=(),
                    support_milli=800, counter_milli=0, correlation_discount_milli=0, staleness_penalty_milli=0,
                    decision_authority="deterministic_policy", policy_ref="policy.world_cognition.stability.v1",
                    policy_sha256=StabilityPolicy().sha256, reason_codes=("cognition.refresh",), created_at_ms=at,
                    revision_sha256=ZERO,
                ).with_computed_revision_sha256()
                return statement, decision
            a_stmt, a_dec = build("a", NOW + 1)
            b_stmt, b_dec = build("b", NOW + 2)
            store.commit_transition(a_stmt, a_dec, expected_head_sha256=stale.statement_sha256)
            with self.assertRaises(CognitionConflictError):
                store.commit_transition(b_stmt, b_dec, expected_head_sha256=stale.statement_sha256)

    def test_structural_evidence_does_not_decay_by_wall_clock(self):
        item = ev("structural", observed_at=1, volatility="structural")
        report = evaluate_evidence(cognition_id=proposal().cognition_id, life_id=LIFE, domain="software", world_scope_hash=WORLD, principal_scope_hash=PRINCIPAL, support=(item,), now_ms=NOW + 10**12)
        self.assertGreater(report.support_milli, 800)
        self.assertEqual(report.staleness_penalty_milli, 0)


if __name__ == "__main__":
    unittest.main()
