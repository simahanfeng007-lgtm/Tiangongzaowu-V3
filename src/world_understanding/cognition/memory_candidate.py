"""P15 M7: memory world candidate bridge into World Cognition.

The bridge is World Understanding internal: it converts a memory candidate
into immutable CognitionEvidence, rejects WU/GIT echo-only candidates, and
only after the existing stability policy is satisfied may it materialize a
WorldPatch record.  Memory itself never creates a WorldPatch.
"""

from __future__ import annotations

from typing import Iterable

from contracts.canonical import canonical_sha256
from contracts.cognition_evidence import (
    CognitionEvidence,
    CognitionSourceRef,
    derive_cognition_evidence_id,
)
from contracts.world_understanding.memory_candidate import MemoryWorldCandidate

from . import stability
from .evidence import CognitionEvidenceLedger
from .store import WorldCognitionStore
from ..world_state.store import WorldStateStore


WU_DERIVED_SOURCE_KINDS = frozenset(
    {
        "code_perception",
        "model_synthesis",
    }
)
MEMORY_EPISTEMIC_CLASS = {
    "observed": "observed",
    "user_asserted": "user_asserted",
    "verified": "execution_verified",
}
MEMORY_EPISTEMIC_AUTHORITY = {
    "user_asserted": 750,
    "observed": 1000,
    "verified": 1000,
}


class MemoryWorldCandidateBridge:
    """Deterministic candidate intake into the existing cognition pipeline."""

    def __init__(
        self,
        cognition_store: WorldCognitionStore,
        *,
        policy: stability.StabilityPolicy | None = None,
    ) -> None:
        self.store = cognition_store
        self.ledger = CognitionEvidenceLedger(cognition_store)
        self.policy = policy or stability.StabilityPolicy()

    def to_cognition_evidence(
        self, candidate: MemoryWorldCandidate, *, now_ms: int
    ) -> CognitionEvidence:
        """Map one candidate into immutable memory-projection evidence."""

        authority = min(
            1000,
            candidate.confidence_milli,
            MEMORY_EPISTEMIC_AUTHORITY[candidate.epistemic_status],
        )
        source_ref = CognitionSourceRef(
            source_kind="memory",
            object_id=candidate.source_derivation_id,
            object_revision=candidate.source_memory_revision,
            sha256=candidate.source_assertion_sha256,
        )
        independence_group_hash = canonical_sha256(
            {
                "domain": "tiangong.world.memory-evidence-group.v1",
                "lineage_root_hashes": candidate.lineage_root_hashes,
            }
        )
        evidence_id = derive_cognition_evidence_id(
            life_id=candidate.life_id,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            privacy_scope=candidate.privacy_scope,
            source_ref=source_ref,
            evidence_class=MEMORY_EPISTEMIC_CLASS[
                candidate.epistemic_status
            ],
            source_credibility_milli=authority,
            authority_ceiling_milli=authority,
            provenance_integrity_milli=authority,
            observation_mode="positive",
            observation=candidate.semantic_payload,
            coverage_milli=1000,
            search_scope_hash=None,
            independence_group_hash=independence_group_hash,
            lineage_root_hashes=candidate.lineage_root_hashes,
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id=candidate.source_memory_id,
            content_sha256=candidate.source_assertion_sha256,
            extractor_kind="memory_projection",
            observed_at_ms=now_ms,
            valid_from_ms=candidate.valid_from_ms,
            valid_until_ms=candidate.valid_until_ms,
            volatility_class=candidate.volatility_class,
        )
        return CognitionEvidence(
            schema_version="tiangong.cognition.contracts.v1",
            evidence_id=evidence_id,
            life_id=candidate.life_id,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            privacy_scope=candidate.privacy_scope,
            source_ref=source_ref,
            evidence_class=MEMORY_EPISTEMIC_CLASS[
                candidate.epistemic_status
            ],
            source_credibility_milli=authority,
            authority_ceiling_milli=authority,
            provenance_integrity_milli=authority,
            observation_mode="positive",
            observation=candidate.semantic_payload,
            coverage_milli=1000,
            search_scope_hash=None,
            independence_group_hash=independence_group_hash,
            lineage_root_hashes=candidate.lineage_root_hashes,
            derived_from_evidence_ids=(),
            ancestor_cognition_ids=(),
            content_object_id=candidate.source_memory_id,
            content_sha256=candidate.source_assertion_sha256,
            extractor_kind="memory_projection",
            observed_at_ms=now_ms,
            valid_from_ms=candidate.valid_from_ms,
            valid_until_ms=candidate.valid_until_ms,
            volatility_class=candidate.volatility_class,
            evidence_sha256="0" * 64,
        ).with_computed_evidence_sha256()

    def has_independent_reality_root(self, candidate: MemoryWorldCandidate) -> bool:
        """False when every candidate root is already a WU/GIT echo."""

        for root in candidate.lineage_root_hashes:
            covering = self.store.find_evidence_by_lineage_root(root)
            if not covering:
                return True
            if not all(
                item.source_ref.source_kind in WU_DERIVED_SOURCE_KINDS
                for item in covering
            ):
                return True
        return False

    def ingest(
        self, candidate: MemoryWorldCandidate, *, now_ms: int
    ) -> dict[str, object]:
        """Intake one candidate; echo-only candidates add no new group."""

        if not self.has_independent_reality_root(candidate):
            return {
                "outcome": "echo_only",
                "reason": "all_roots_are_wu_echoes",
                "evidence": None,
            }
        evidence = self.to_cognition_evidence(candidate, now_ms=now_ms)
        created = self.ledger.ingest(evidence)
        return {
            "outcome": "accepted" if created else "duplicate",
            "reason": "",
            "evidence": evidence,
        }

    def stability_report(
        self,
        candidate: MemoryWorldCandidate,
        *,
        now_ms: int,
        extra_support: Iterable[CognitionEvidence] = (),
        extra_counter: Iterable[CognitionEvidence] = (),
    ) -> stability.StabilityReport:
        evidence = self.to_cognition_evidence(candidate, now_ms=now_ms)
        return stability.evaluate_evidence(
            cognition_id="cog_"
            + canonical_sha256(
                {
                    "domain": "tiangong.world.memory-cognition.v1",
                    "claim_key": candidate.claim_key,
                    "world_scope_hash": candidate.world_scope_hash,
                }
            ),
            life_id=candidate.life_id,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            support=(evidence, *tuple(extra_support)),
            counter=tuple(extra_counter),
            now_ms=now_ms,
            policy=self.policy,
        )

    def materialize_world_patch(
        self,
        *,
        candidate: MemoryWorldCandidate,
        now_ms: int,
        world_state_store: WorldStateStore,
        extra_support: Iterable[CognitionEvidence] = (),
        extra_counter: Iterable[CognitionEvidence] = (),
        evidence_ids: tuple[str, ...] = (),
    ) -> dict[str, object] | None:
        """Create a WorldPatch only after stability allows it."""

        report = self.stability_report(
            candidate,
            now_ms=now_ms,
            extra_support=extra_support,
            extra_counter=extra_counter,
        )
        level = stability.highest_eligible_level(report, self.policy)
        if level in {"C0", "C1"}:
            return None
        patch = {
            "schema": "tiangong.world.memory-world-patch.v1",
            "record_id": "wpat_"
            + canonical_sha256(
                {
                    "domain": "tiangong.world.memory-world-patch-id.v1",
                    "candidate_id": candidate.candidate_id,
                    "stability_level": level,
                }
            ),
            "patch_id": "wpat_"
            + canonical_sha256(
                {
                    "domain": "tiangong.world.memory-world-patch-id.v1",
                    "candidate_id": candidate.candidate_id,
                    "stability_level": level,
                }
            ),
            "candidate_id": candidate.candidate_id,
            "claim_key": candidate.claim_key,
            "stability_level": level,
            "support_milli": report.support_milli,
            "counter_milli": report.counter_milli,
            "support_group_count": report.support_group_count,
            "evidence_ids": tuple(dict.fromkeys(evidence_ids)),
            "created_at_ms": now_ms,
            "revision": 1,
            "status": "OPEN",
            "patch_sha256": canonical_sha256(
                {
                    "domain": "tiangong.world.memory-world-patch.v1",
                    "candidate_id": candidate.candidate_id,
                    "stability_level": level,
                    "created_at_ms": now_ms,
                }
            ),
        }
        world_state_store.put_active_cognition_record(patch)
        return patch


__all__ = [
    "MEMORY_EPISTEMIC_AUTHORITY",
    "MEMORY_EPISTEMIC_CLASS",
    "MemoryWorldCandidateBridge",
    "WU_DERIVED_SOURCE_KINDS",
]
