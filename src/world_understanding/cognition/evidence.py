"""Governed evidence ingestion for World Cognition Core.

Persistence is intentionally dumb and immutable; this ledger is the mandatory
Facade ingestion boundary. Derived evidence may preserve or reduce authority,
never amplify it or erase lineage/ancestor provenance.
"""

from __future__ import annotations

from contracts.cognition_evidence import CognitionEvidence

from .store import CognitionIntegrityError, WorldCognitionStore


class CognitionEvidenceLedger:
    def __init__(self, store: WorldCognitionStore) -> None:
        self.store = store

    @staticmethod
    def _effective_authority(evidence: CognitionEvidence) -> int:
        return min(
            evidence.source_credibility_milli,
            evidence.authority_ceiling_milli,
            evidence.provenance_integrity_milli,
        )

    def ingest(self, evidence: CognitionEvidence) -> bool:
        parent_ids = tuple(evidence.derived_from_evidence_ids)
        if not parent_ids:
            return self.store.put_evidence(evidence)

        parents = self.store.get_evidence_many(parent_ids)
        by_id = {parent.evidence_id: parent for parent in parents}
        missing = [parent_id for parent_id in parent_ids if parent_id not in by_id]
        if missing:
            raise CognitionIntegrityError(
                f"derived cognition evidence references unknown parents: {missing}"
            )

        required_roots: set[str] = set()
        required_ancestors: set[str] = set()
        parent_authorities: list[int] = []
        parent_provenance: list[int] = []
        for parent_id in parent_ids:
            parent = by_id[parent_id]
            if (
                parent.life_id != evidence.life_id
                or parent.domain != evidence.domain
                or parent.world_scope_hash != evidence.world_scope_hash
                or parent.principal_scope_hash != evidence.principal_scope_hash
            ):
                raise CognitionIntegrityError(
                    "derived cognition evidence cannot cross life/domain/world/principal scope"
                )
            required_roots.update(parent.lineage_root_hashes)
            required_ancestors.update(parent.ancestor_cognition_ids)
            parent_authorities.append(self._effective_authority(parent))
            parent_provenance.append(parent.provenance_integrity_milli)

        child_roots = set(evidence.lineage_root_hashes)
        if not required_roots.issubset(child_roots):
            raise CognitionIntegrityError(
                "derived cognition evidence cannot drop parent lineage roots"
            )
        child_ancestors = set(evidence.ancestor_cognition_ids)
        if not required_ancestors.issubset(child_ancestors):
            raise CognitionIntegrityError(
                "derived cognition evidence cannot drop ancestor cognition provenance"
            )

        # Conservative non-amplification: a synthesized artifact cannot receive
        # a higher authority ceiling than the weakest parent it claims to derive
        # from. Independent parents should remain separate evidence items if they
        # are intended to contribute independent support.
        max_derived_authority = min(parent_authorities)
        if evidence.authority_ceiling_milli > max_derived_authority:
            raise CognitionIntegrityError(
                "derived cognition evidence authority ceiling exceeds parent provenance"
            )
        if evidence.provenance_integrity_milli > min(parent_provenance):
            raise CognitionIntegrityError(
                "derived cognition evidence provenance integrity cannot exceed parents"
            )

        return self.store.put_evidence(evidence)


__all__ = ["CognitionEvidenceLedger"]
