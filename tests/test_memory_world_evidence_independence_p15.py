"""P15 M7: memory evidence folds by lineage and never self-confirms."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts.cognition_evidence import (
    CognitionEvidence,
    CognitionSourceRef,
    derive_cognition_evidence_id,
)
from contracts.canonical import canonical_sha256
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event
from world_understanding.cognition.memory_candidate import (
    MemoryWorldCandidateBridge,
)
from world_understanding.cognition.store import WorldCognitionStore


LIFE = "life_p15_world_independence"
PRINCIPAL = "principal_test"
PRIVACY = "private"


def direct_evidence(
    *,
    life_id: str,
    world_scope_hash: str,
    principal_scope_hash: str,
    root_hash: str,
    object_id: str,
) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind="fact_execution",
        object_id=object_id,
        object_revision=1,
        sha256=canonical_sha256({"object": object_id}),
    )
    evidence_id = derive_cognition_evidence_id(
        life_id=life_id,
        domain="external",
        world_scope_hash=world_scope_hash,
        principal_scope_hash=principal_scope_hash,
        privacy_scope="private",
        source_ref=source,
        evidence_class="execution_verified",
        source_credibility_milli=1000,
        authority_ceiling_milli=1000,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation="direct tool result",
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=canonical_sha256(
            {"group": "direct", "root": root_hash}
        ),
        lineage_root_hashes=(root_hash,),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id=object_id,
        content_sha256=canonical_sha256({"object": object_id}),
        extractor_kind="direct_tool",
        observed_at_ms=3_500,
        valid_from_ms=3_500,
        valid_until_ms=None,
        volatility_class="structural",
    )
    return CognitionEvidence(
        schema_version="tiangong.cognition.contracts.v1",
        evidence_id=evidence_id,
        life_id=life_id,
        domain="external",
        world_scope_hash=world_scope_hash,
        principal_scope_hash=principal_scope_hash,
        privacy_scope="private",
        source_ref=source,
        evidence_class="execution_verified",
        source_credibility_milli=1000,
        authority_ceiling_milli=1000,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation="direct tool result",
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=canonical_sha256(
            {"group": "direct", "root": root_hash}
        ),
        lineage_root_hashes=(root_hash,),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id=object_id,
        content_sha256=canonical_sha256({"object": object_id}),
        extractor_kind="direct_tool",
        observed_at_ms=3_500,
        valid_from_ms=3_500,
        valid_until_ms=None,
        volatility_class="structural",
        evidence_sha256="0" * 64,
    ).with_computed_evidence_sha256()


class MemoryWorldEvidenceIndependenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LifeShadowStore.open(
            self.root / "life.shadow.sqlite3", create=True, now_ms=500
        )
        self.coordinator = MemoryCoordinator(self.store)
        self.cognition = WorldCognitionStore(self.root / "wu")
        self.bridge = MemoryWorldCandidateBridge(self.cognition)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l3_world(self, *, suffix: str, claim_key: str, plaintext: bytes):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key=claim_key,
            semantic_domain="WORLD",
            plaintext=plaintext,
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        return value, l3[1]

    def _candidates(self):
        self._l3_world(
            suffix="11" * 32,
            claim_key="claim:root-a",
            plaintext=b"fact A",
        )
        self._l3_world(
            suffix="12" * 32,
            claim_key="claim:root-b",
            plaintext=b"fact B",
        )
        _c, _s, candidates = (
            self.coordinator.project_memory_world_candidates(
                life_id=LIFE, now_ms=4_000
            )
        )
        return candidates

    def test_distinct_lineages_form_two_independence_groups(self) -> None:
        candidates = self._candidates()
        self.assertEqual(len(candidates), 2)
        report = self.bridge.stability_report(
            candidates[0],
            now_ms=4_000,
            extra_support=(
                direct_evidence(
                    life_id=LIFE,
                    world_scope_hash=candidates[0].world_scope_hash,
                    principal_scope_hash=candidates[0].principal_scope_hash,
                    root_hash=candidates[0].lineage_root_hashes[0],
                    object_id="exec_a",
                ),
                direct_evidence(
                    life_id=LIFE,
                    world_scope_hash=candidates[1].world_scope_hash,
                    principal_scope_hash=candidates[1].principal_scope_hash,
                    root_hash=candidates[1].lineage_root_hashes[0],
                    object_id="exec_b",
                ),
            ),
        )
        self.assertGreaterEqual(report.support_group_count, 2)

    def test_memory_evidence_is_never_direct(self) -> None:
        candidates = self._candidates()
        evidence = self.bridge.to_cognition_evidence(
            candidates[0], now_ms=4_000
        )
        self.assertEqual(evidence.source_ref.source_kind, "memory")
        report = self.bridge.stability_report(
            candidates[0], now_ms=4_000
        )
        self.assertEqual(report.direct_support_group_count, 0)

    def test_ingest_is_durable_and_idempotent(self) -> None:
        candidates = self._candidates()
        first = self.bridge.ingest(candidates[0], now_ms=4_000)
        second = self.bridge.ingest(candidates[0], now_ms=4_000)
        self.assertEqual(first["outcome"], "accepted")
        self.assertEqual(second["outcome"], "duplicate")
        stored = self.cognition.get_evidence(
            first["evidence"].evidence_id
        )
        self.assertIsNotNone(stored)

    def test_echo_only_candidate_adds_no_group(self) -> None:
        candidates = self._candidates()
        root = candidates[0].lineage_root_hashes[0]
        # Simulate a WU/GIT-derived echo covering the root first.
        echo_source = CognitionSourceRef(
            source_kind="code_perception",
            object_id="git_frame_1",
            object_revision=1,
            sha256=canonical_sha256({"git": "frame"}),
        )
        echo_id = derive_cognition_evidence_id(
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidates[0].world_scope_hash,
            principal_scope_hash=candidates[0].principal_scope_hash,
            privacy_scope="private",
            source_ref=echo_source,
            evidence_class="observed",
            source_credibility_milli=1000,
            authority_ceiling_milli=1000,
            provenance_integrity_milli=1000,
            observation_mode="positive",
            observation="repository says fact A",
            coverage_milli=1000,
            search_scope_hash=None,
            independence_group_hash=canonical_sha256(
                {"group": "echo", "root": root}
            ),
            lineage_root_hashes=(root,),
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id="git_frame_1",
            content_sha256=canonical_sha256({"git": "frame"}),
            extractor_kind="direct_tool",
            observed_at_ms=3_500,
            valid_from_ms=3_500,
            valid_until_ms=None,
            volatility_class="structural",
        )
        echo = CognitionEvidence(
            schema_version="tiangong.cognition.contracts.v1",
            evidence_id=echo_id,
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidates[0].world_scope_hash,
            principal_scope_hash=candidates[0].principal_scope_hash,
            privacy_scope="private",
            source_ref=echo_source,
            evidence_class="observed",
            source_credibility_milli=1000,
            authority_ceiling_milli=1000,
            provenance_integrity_milli=1000,
            observation_mode="positive",
            observation="repository says fact A",
            coverage_milli=1000,
            search_scope_hash=None,
            independence_group_hash=canonical_sha256(
                {"group": "echo", "root": root}
            ),
            lineage_root_hashes=(root,),
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id="git_frame_1",
            content_sha256=canonical_sha256({"git": "frame"}),
            extractor_kind="direct_tool",
            observed_at_ms=3_500,
            valid_from_ms=3_500,
            valid_until_ms=None,
            volatility_class="structural",
            evidence_sha256="0" * 64,
        ).with_computed_evidence_sha256()
        self.bridge.ledger.ingest(echo)
        echo_candidate = candidates[0].model_copy(
            update={
                "source_derivation_id": "mdr_" + "99" * 32,
                "candidate_id": "wmc_" + "99" * 32,
            }
        )
        echo_candidate = echo_candidate.with_computed_candidate_sha256()
        outcome = self.bridge.ingest(echo_candidate, now_ms=4_000)
        self.assertEqual(outcome["outcome"], "echo_only")
        self.assertIn("echo", outcome["reason"])

    def test_shared_root_memory_and_direct_fold_one_group(self) -> None:
        candidates = self._candidates()
        direct = direct_evidence(
            life_id=LIFE,
            world_scope_hash=candidates[0].world_scope_hash,
            principal_scope_hash=candidates[0].principal_scope_hash,
            root_hash=candidates[0].lineage_root_hashes[0],
            object_id="exec_shared",
        )
        report = self.bridge.stability_report(
            candidates[0],
            now_ms=4_000,
            extra_support=(direct,),
        )
        self.assertEqual(report.support_group_count, 1)
        self.assertEqual(report.direct_support_group_count, 1)

    def test_two_memory_candidates_never_inflate_group_count(self) -> None:
        candidates = self._candidates()
        report = self.bridge.stability_report(
            candidates[0], now_ms=4_000
        )
        self.assertEqual(report.support_group_count, 1)
        self.assertEqual(report.direct_support_group_count, 0)


if __name__ == "__main__":
    unittest.main()
