"""P11 deterministic KnowledgeGap -> WorldCuriosity -> WorldInquiry construction."""
from __future__ import annotations

import re

from contracts.canonical import canonical_sha256
from contracts.world_understanding.curiosity import KnowledgeGap, WorldCuriosity, derive_curiosity_id
from contracts.world_understanding.inquiry import WorldInquiry, derive_inquiry_id
from contracts.world_understanding._base import WorldRecordRef

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_FORBIDDEN_MODALITY = re.compile(r"(?:\s|/|\\|;|\||&&|\$\(|`|powershell|cmd(?:\.exe)?|bash|sh\b)", re.IGNORECASE)

_MODALITY_BY_EVIDENCE = {
    "revalidation_observation": "source_reobservation",
    "conflict_discriminating_observation": "independent_discriminating_observation",
    "uncertainty_reducing_observation": "bounded_reality_observation",
    "filesystem_observation": "filesystem_observation",
    "git_observation": "git_observation",
    "runtime_observation": "runtime_observation",
    "tool_result_observation": "tool_result_observation",
    "execution_observation": "execution_fact_observation",
}


def validate_observation_modalities(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(str(value).strip() for value in values if str(value).strip())))
    if len(normalized) > 256:
        raise ValueError("WORLD_INQUIRY_MODALITY_LIMIT")
    for value in normalized:
        if not _SAFE_ID.fullmatch(value) or _FORBIDDEN_MODALITY.search(value):
            raise ValueError("WORLD_INQUIRY_EXECUTABLE_MODALITY_FORBIDDEN")
    return normalized


def _question(gap: KnowledgeGap) -> str:
    ref = gap.subject_refs[0] if gap.subject_refs else None
    subject = "the scoped world state" if ref is None else f"{ref.record_type}:{ref.record_id}"
    missing = ", ".join(gap.missing_evidence_types) or "independent observation"
    return f"What independent reality observation would reduce the unresolved gap for {subject} ({missing})?"


class CuriosityGenerator:
    def build_curiosity(
        self,
        gap: KnowledgeGap,
        *,
        frame_ref: WorldRecordRef | None,
        created_at_ms: int,
        expires_at_ms: int | None = None,
    ) -> WorldCuriosity:
        if not gap.has_valid_hash():
            raise ValueError("WORLD_GAP_HASH_INVALID")
        question = _question(gap)
        provenance = gap.basis_refs
        curiosity_id = derive_curiosity_id(
            world_scope_hash=gap.scope.world_scope_hash,
            frame_ref=frame_ref,
            question=question,
            subject_refs=gap.subject_refs,
            provenance_refs=provenance,
            created_at_ms=created_at_ms,
        )
        return WorldCuriosity(
            curiosity_id=curiosity_id,
            scope=gap.scope,
            frame_ref=frame_ref,
            subject_refs=gap.subject_refs,
            question=question,
            curiosity_kind="knowledge_gap_observation",
            trigger_reasons=tuple(sorted(set(gap.missing_evidence_types))),
            uncertainty_milli=gap.uncertainty_milli,
            novelty_milli=min(1000, 500 + gap.observability_gap_milli // 2),
            prediction_error_milli=gap.prediction_error_milli,
            conflict_milli=gap.conflict_milli,
            impact_milli=gap.impact_milli,
            task_relevance_milli=gap.relevance_milli,
            expected_information_gain_milli=min(1000, max(gap.gap_score_milli, gap.uncertainty_milli)),
            expected_cost_milli=max(1, 100 + gap.observability_gap_milli),
            missing_evidence_types=gap.missing_evidence_types,
            provenance_refs=provenance,
            created_at_ms=created_at_ms,
            expires_at_ms=expires_at_ms,
            curiosity_sha256="0" * 64,
        ).with_computed_hash()

    def build_inquiry(
        self,
        gap: KnowledgeGap,
        curiosity: WorldCuriosity,
        *,
        correlation_id: str,
        source_world_state_ref: WorldRecordRef | None,
        inquiry_budget_remaining: int,
        created_at_ms: int | None = None,
    ) -> WorldInquiry:
        if not gap.has_valid_hash() or not curiosity.has_valid_hash():
            raise ValueError("WORLD_INQUIRY_BASIS_HASH_INVALID")
        if gap.scope != curiosity.scope:
            raise ValueError("WORLD_INQUIRY_SCOPE_MISMATCH")
        modalities = validate_observation_modalities(
            tuple(_MODALITY_BY_EVIDENCE.get(value, "bounded_reality_observation") for value in gap.missing_evidence_types)
        )
        inquiry_id = derive_inquiry_id(
            world_scope_hash=gap.scope.world_scope_hash,
            question=curiosity.question,
            knowledge_gap_id=gap.gap_id,
            subject_refs=gap.subject_refs,
        )
        dedup_key = canonical_sha256({
            "domain": "tiangong.world.inquiry-dedup.v1",
            "world_scope_hash": gap.scope.world_scope_hash,
            "gap_id": gap.gap_id,
            "question": curiosity.question,
            "modalities": modalities,
        })
        now_ms = curiosity.created_at_ms if created_at_ms is None else int(created_at_ms)
        return WorldInquiry(
            inquiry_id=inquiry_id,
            correlation_id=correlation_id,
            curiosity_id=curiosity.curiosity_id,
            knowledge_gap_id=gap.gap_id,
            scope=gap.scope,
            frame_ref=curiosity.frame_ref,
            subject_refs=gap.subject_refs,
            question=curiosity.question,
            inquiry_kind="world_observation_request",
            reason_codes=tuple(sorted(set(curiosity.trigger_reasons))),
            missing_evidence_types=gap.missing_evidence_types,
            supporting_refs=gap.basis_refs,
            conflict_refs=gap.subject_refs if gap.conflict_milli else (),
            expected_information_gain_milli=curiosity.expected_information_gain_milli,
            impact_milli=gap.impact_milli,
            urgency_milli=max(gap.staleness_milli, gap.conflict_milli),
            estimated_cost_class="LOW" if curiosity.expected_cost_milli <= 250 else "MEDIUM" if curiosity.expected_cost_milli <= 700 else "HIGH",
            risk_hint="UNKNOWN",
            suggested_observation_modalities=modalities,
            source_world_state_ref=source_world_state_ref,
            source_cognition_refs=(),
            dedup_key=dedup_key,
            inquiry_budget_remaining=max(0, int(inquiry_budget_remaining)),
            created_at_ms=now_ms,
            expires_at_ms=curiosity.expires_at_ms,
            inquiry_sha256="0" * 64,
        ).with_computed_hash()


__all__ = ["CuriosityGenerator", "validate_observation_modalities"]
