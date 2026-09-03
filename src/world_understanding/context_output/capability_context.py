"""P6 typed capability sections inside the existing WORLD_CONTEXT_SLOT.

Identity-bearing lines are mandatory and never truncated. The output remains
context-only DATA: it cannot authorize, confirm, change risk, or execute.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable, Literal

from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.context_packet import WorldContextPacket
from world_understanding.capability_composition import (
    CapabilityExperienceRecallItemV1,
    CompositionCandidateSnapshotV1,
    NegativeCapabilityEvidenceV1,
)

from .slot import (
    WorldContextSlot,
    build_world_context_slot,
    conservative_token_estimate,
)


CapabilityContextMode = Literal["SHADOW", "LIMITED", "DEFAULT"]
CapabilityContextStatus = Literal["AVAILABLE", "UNAVAILABLE"]

_NEVER_COMPRESS_KEYS = (
    "activation_ref",
    "action_ref",
    "candidate_id",
    "method_ref",
    "plan_ref",
    "source_revision",
    "verification_plan_ref",
    "world_state_ref",
)


@dataclass(frozen=True, slots=True)
class CapabilityContextBudgetsV1:
    method_limit: int = 15
    action_limit: int = 30
    experience_limit: int = 8
    negative_evidence_limit: int = 5

    def __post_init__(self) -> None:
        if not 8 <= self.method_limit <= 15:
            raise ValueError("CAPABILITY_CONTEXT_METHOD_BUDGET_INVALID")
        if not 12 <= self.action_limit <= 30:
            raise ValueError("CAPABILITY_CONTEXT_ACTION_BUDGET_INVALID")
        if not 3 <= self.experience_limit <= 8:
            raise ValueError("CAPABILITY_CONTEXT_EXPERIENCE_BUDGET_INVALID")
        if not 0 <= self.negative_evidence_limit <= 5:
            raise ValueError("CAPABILITY_CONTEXT_NEGATIVE_BUDGET_INVALID")


@dataclass(frozen=True, slots=True)
class ProtectedContextIdentityV1:
    key: str
    value: str

    def __post_init__(self) -> None:
        if self.key not in _NEVER_COMPRESS_KEYS:
            raise ValueError("CAPABILITY_CONTEXT_IDENTITY_KEY_INVALID")
        if not self.value or "\n" in self.value or "\r" in self.value:
            raise ValueError("CAPABILITY_CONTEXT_IDENTITY_VALUE_INVALID")


@dataclass(frozen=True, slots=True)
class MethodContextEntryV1:
    candidate_id: str
    method_ref: str
    version: str
    source_revision: str
    descriptor_sha256: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class ActionContextEntryV1:
    candidate_id: str
    action_ref: str
    version: str
    source_revision: str
    descriptor_sha256: str
    effect_class: str
    risk_floor: str
    availability: str


@dataclass(frozen=True, slots=True)
class ExperienceContextEntryV1:
    experience_id: str
    experience_sha256: str
    lifecycle: str
    posterior_success_milli: int
    lower_confidence_milli: int
    success_count: int
    failure_count: int
    independent_context_count: int


@dataclass(frozen=True, slots=True)
class NegativeEvidenceContextEntryV1:
    evidence_id: str
    evidence_sha256: str
    failure_category: str
    reason_codes: tuple[str, ...]
    source_revision_family: str
    exact_source_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityContextPacketV1:
    schema: str
    world_state_ref: WorldRecordRef
    frame_binding_sha256: str
    candidate_snapshot_sha256: str
    method_candidates: tuple[MethodContextEntryV1, ...]
    action_candidates: tuple[ActionContextEntryV1, ...]
    procedural_experience: tuple[ExperienceContextEntryV1, ...]
    negative_evidence: tuple[NegativeEvidenceContextEntryV1, ...]
    protected_identities: tuple[ProtectedContextIdentityV1, ...]
    composition_abi: str
    packet_sha256: str
    context_only: bool = True
    authorization_source: bool = False
    authorizes: bool = False
    confirms: bool = False
    changes_risk: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.schema != "tiangong.capability-context-packet.v1":
            raise ValueError("CAPABILITY_CONTEXT_SCHEMA_INVALID")
        if (
            not self.context_only
            or self.authorization_source
            or self.authorizes
            or self.confirms
            or self.changes_risk
            or self.may_execute
        ):
            raise ValueError("CAPABILITY_CONTEXT_AUTHORITY_INVALID")
        method_ids = tuple(item.candidate_id for item in self.method_candidates)
        action_ids = tuple(item.candidate_id for item in self.action_candidates)
        if method_ids != tuple(sorted(set(method_ids))):
            raise ValueError("CAPABILITY_CONTEXT_METHOD_ORDER_INVALID")
        if action_ids != tuple(sorted(set(action_ids))):
            raise ValueError("CAPABILITY_CONTEXT_ACTION_ORDER_INVALID")
        identities = tuple(
            (item.key, item.value) for item in self.protected_identities
        )
        if identities != tuple(sorted(set(identities))):
            raise ValueError("CAPABILITY_CONTEXT_IDENTITY_ORDER_INVALID")
        world_identity = (
            "world_state_ref",
            f"{self.world_state_ref.record_id}@{self.world_state_ref.sha256}",
        )
        if world_identity not in identities:
            raise ValueError("CAPABILITY_CONTEXT_WORLD_STATE_IDENTITY_MISSING")

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "world_state_ref": self.world_state_ref.model_dump(mode="json"),
            "frame_binding_sha256": self.frame_binding_sha256,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "method_candidates": [asdict(item) for item in self.method_candidates],
            "action_candidates": [asdict(item) for item in self.action_candidates],
            "procedural_experience": [
                asdict(item) for item in self.procedural_experience
            ],
            "negative_evidence": [
                asdict(item) for item in self.negative_evidence
            ],
            "protected_identities": [
                asdict(item) for item in self.protected_identities
            ],
            "composition_abi": self.composition_abi,
            "context_only": self.context_only,
            "authorization_source": self.authorization_source,
            "authorizes": self.authorizes,
            "confirms": self.confirms,
            "changes_risk": self.changes_risk,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.packet_sha256 == self.computed_sha256()


@dataclass(frozen=True, slots=True)
class CapabilityContextBuildResultV1:
    mode: CapabilityContextMode
    status: CapabilityContextStatus
    slot: WorldContextSlot
    reason_code: str
    fallback_used: bool
    audited_migration_fallback: bool
    result_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "status": self.status,
            "slot_sha256": self.slot.slot_sha256,
            "reason_code": self.reason_code,
            "fallback_used": self.fallback_used,
            "audited_migration_fallback": self.audited_migration_fallback,
        }

    def has_valid_sha256(self) -> bool:
        return self.result_sha256 == canonical_sha256(self.payload())


def _source_revision_sha256(value: object) -> str:
    return canonical_sha256(value.model_dump(mode="json"))


def build_capability_context_packet(
    *,
    world_state_ref: WorldRecordRef,
    frame_binding_sha256: str,
    candidates: CompositionCandidateSnapshotV1,
    experiences: tuple[CapabilityExperienceRecallItemV1, ...] = (),
    negative_evidence: tuple[NegativeCapabilityEvidenceV1, ...] = (),
    protected_identities: tuple[ProtectedContextIdentityV1, ...] = (),
    budgets: CapabilityContextBudgetsV1 = CapabilityContextBudgetsV1(),
) -> CapabilityContextPacketV1:
    if world_state_ref.record_type != "world_state":
        raise ValueError("CAPABILITY_CONTEXT_WORLD_STATE_REF_INVALID")
    if not candidates.has_valid_sha256():
        raise ValueError("CAPABILITY_CONTEXT_CANDIDATE_HASH_INVALID")
    if candidates.may_authorize or candidates.may_execute:
        raise ValueError("CAPABILITY_CONTEXT_CANDIDATE_AUTHORITY_INVALID")
    if len(candidates.method_candidates) > budgets.method_limit:
        raise ValueError("CAPABILITY_CONTEXT_METHOD_BUDGET_EXCEEDED")
    if len(candidates.action_candidates) > budgets.action_limit:
        raise ValueError("CAPABILITY_CONTEXT_ACTION_BUDGET_EXCEEDED")
    if len(experiences) > budgets.experience_limit:
        raise ValueError("CAPABILITY_CONTEXT_EXPERIENCE_BUDGET_EXCEEDED")
    if len(negative_evidence) > budgets.negative_evidence_limit:
        raise ValueError("CAPABILITY_CONTEXT_NEGATIVE_BUDGET_EXCEEDED")
    if any(
        item.context_section != "DATA" or item.instruction_authority
        for item in experiences
    ):
        raise ValueError("CAPABILITY_CONTEXT_EXPERIENCE_AUTHORITY_INVALID")
    if any(not item.has_valid_sha256() for item in negative_evidence):
        raise ValueError("CAPABILITY_CONTEXT_NEGATIVE_EVIDENCE_HASH_INVALID")
    if any(
        item.context_section != "DATA"
        or item.instruction_authority
        or item.world_authority
        or item.may_authorize
        or item.may_execute
        for item in negative_evidence
    ):
        raise ValueError("CAPABILITY_CONTEXT_NEGATIVE_EVIDENCE_AUTHORITY_INVALID")

    methods = tuple(
        MethodContextEntryV1(
            candidate_id=item.candidate_id,
            method_ref="method:" + item.primitive.method_id,
            version=item.primitive.version,
            source_revision=_source_revision_sha256(item.primitive.source_ref),
            descriptor_sha256=item.primitive.descriptor_sha256,
            title=item.primitive.title,
            summary=item.primitive.semantic_summary,
        )
        for item in candidates.method_candidates
    )
    actions = tuple(
        ActionContextEntryV1(
            candidate_id=item.candidate_id,
            action_ref="action:" + item.primitive.action_id,
            version=item.primitive.action_version,
            source_revision=_source_revision_sha256(item.source_revision),
            descriptor_sha256=item.primitive.descriptor_sha256,
            effect_class=item.primitive.effect_class,
            risk_floor=item.primitive.risk_floor,
            availability=item.primitive.availability,
        )
        for item in candidates.action_candidates
    )
    experience_entries = tuple(
        ExperienceContextEntryV1(
            experience_id=item.experience_id,
            experience_sha256=item.experience_sha256,
            lifecycle=item.lifecycle,
            posterior_success_milli=item.posterior_success_milli,
            lower_confidence_milli=item.lower_confidence_milli,
            success_count=item.success_count,
            failure_count=item.failure_count,
            independent_context_count=item.independent_context_count,
        )
        for item in experiences
    )
    negative_entries = tuple(
        NegativeEvidenceContextEntryV1(
            evidence_id=item.evidence_id,
            evidence_sha256=item.evidence_sha256,
            failure_category=item.failure_category,
            reason_codes=item.reason_codes,
            source_revision_family=item.source_revision_family,
            exact_source_hashes=item.exact_source_hashes,
        )
        for item in negative_evidence
    )
    world_identity = ProtectedContextIdentityV1(
        key="world_state_ref",
        value=f"{world_state_ref.record_id}@{world_state_ref.sha256}",
    )
    identities = tuple(
        sorted(
            set((*protected_identities, world_identity)),
            key=lambda item: (item.key, item.value),
        )
    )
    packet = CapabilityContextPacketV1(
        schema="tiangong.capability-context-packet.v1",
        world_state_ref=world_state_ref,
        frame_binding_sha256=frame_binding_sha256,
        candidate_snapshot_sha256=candidates.candidate_snapshot_sha256,
        method_candidates=methods,
        action_candidates=actions,
        procedural_experience=experience_entries,
        negative_evidence=negative_entries,
        protected_identities=identities,
        composition_abi=(
            "proposal=tiangong.composition-proposal.v1;"
            "plan=tiangong.capability-composition.contracts.v1;"
            "model_authority=false;system_compiler_required=true;"
            "tri_state_validation=PROVED_VALID|PROVED_INVALID|UNKNOWN"
        ),
        packet_sha256="0" * 64,
    )
    return replace(packet, packet_sha256=packet.computed_sha256())


def _identity_lines(packet: CapabilityContextPacketV1) -> list[str]:
    lines = [
        "[CURRENT_WORLD]",
        f"world_state_ref={packet.world_state_ref.record_id}@{packet.world_state_ref.sha256}",
        f"frame_binding={packet.frame_binding_sha256}",
        f"candidate_snapshot={packet.candidate_snapshot_sha256}",
        "context_only=true",
        "authorization_source=false",
        "authorizes=false",
        "confirms=false",
        "changes_risk=false",
        "may_execute=false",
    ]
    for identity in packet.protected_identities:
        if identity.key != "world_state_ref":
            lines.append(f"{identity.key}={identity.value}")
    lines.extend(("", "[METHOD_CANDIDATES]"))
    for item in packet.method_candidates:
        lines.append(
            " ".join(
                (
                    f"candidate_id={item.candidate_id}",
                    f"method_ref={item.method_ref}",
                    f"version={item.version}",
                    f"source_revision={item.source_revision}",
                    f"descriptor={item.descriptor_sha256}",
                )
            )
        )
    lines.extend(("", "[ACTION_CANDIDATES]"))
    for item in packet.action_candidates:
        lines.append(
            " ".join(
                (
                    f"candidate_id={item.candidate_id}",
                    f"action_ref={item.action_ref}",
                    f"version={item.version}",
                    f"source_revision={item.source_revision}",
                    f"descriptor={item.descriptor_sha256}",
                    f"effect={item.effect_class}",
                    f"risk_floor={item.risk_floor}",
                    f"availability={item.availability}",
                )
            )
        )
    lines.extend(("", "[PROCEDURAL_EXPERIENCE]"))
    for item in packet.procedural_experience:
        lines.append(
            " ".join(
                (
                    f"experience_ref={item.experience_id}@{item.experience_sha256}",
                    f"lifecycle={item.lifecycle}",
                    f"posterior_milli={item.posterior_success_milli}",
                    f"lower_confidence_milli={item.lower_confidence_milli}",
                    f"success={item.success_count}",
                    f"failure={item.failure_count}",
                    f"independent_contexts={item.independent_context_count}",
                )
            )
        )
    lines.extend(("", "[NEGATIVE_EVIDENCE]"))
    for item in packet.negative_evidence:
        lines.append(
            " ".join(
                (
                    f"negative_ref={item.evidence_id}@{item.evidence_sha256}",
                    f"category={item.failure_category}",
                    f"source_family={item.source_revision_family}",
                    "source_revisions=" + ",".join(item.exact_source_hashes),
                    "reasons=" + ",".join(item.reason_codes),
                )
            )
        )
    lines.extend(
        (
            "",
            "[COMPOSITION_ABI]",
            packet.composition_abi,
            "never_compress=" + ",".join(_NEVER_COMPRESS_KEYS),
        )
    )
    return lines


def _summary_lines(packet: CapabilityContextPacketV1) -> list[str]:
    return [
        f"method_summary candidate_id={item.candidate_id} title={item.title} summary={item.summary}"
        for item in packet.method_candidates
    ]


def _build_result(
    *,
    mode: CapabilityContextMode,
    status: CapabilityContextStatus,
    slot: WorldContextSlot,
    reason_code: str,
    fallback_used: bool,
    audited_migration_fallback: bool,
) -> CapabilityContextBuildResultV1:
    value = CapabilityContextBuildResultV1(
        mode=mode,
        status=status,
        slot=slot,
        reason_code=reason_code,
        fallback_used=fallback_used,
        audited_migration_fallback=audited_migration_fallback,
        result_sha256="0" * 64,
    )
    return replace(value, result_sha256=canonical_sha256(value.payload()))


def _fallback_allowed(
    mode: CapabilityContextMode, audited_migration_fallback: bool
) -> bool:
    return mode == "SHADOW" or (
        mode == "LIMITED" and audited_migration_fallback
    )


def build_capability_world_context_slot(
    world_packet: WorldContextPacket,
    capability_packet: CapabilityContextPacketV1,
    *,
    mode: CapabilityContextMode,
    audited_migration_fallback: bool = False,
    token_estimator: Callable[[str], int] = conservative_token_estimate,
) -> CapabilityContextBuildResultV1:
    """Append capability DATA to the one existing WORLD_CONTEXT_SLOT."""

    base = build_world_context_slot(
        world_packet, token_estimator=token_estimator
    )
    if mode not in {"SHADOW", "LIMITED", "DEFAULT"}:
        raise ValueError("CAPABILITY_CONTEXT_MODE_INVALID")
    reason = ""
    if not capability_packet.has_valid_sha256():
        reason = "CAPABILITY_CONTEXT_PACKET_HASH_INVALID"
    elif world_packet.basis_world_state_ref is None:
        reason = "CAPABILITY_CONTEXT_WORLD_STATE_REQUIRED"
    elif world_packet.basis_world_state_ref != capability_packet.world_state_ref:
        reason = "CAPABILITY_CONTEXT_WORLD_STATE_MISMATCH"
    if reason:
        return _build_result(
            mode=mode,
            status="UNAVAILABLE",
            slot=base,
            reason_code=reason,
            fallback_used=_fallback_allowed(
                mode, audited_migration_fallback
            ),
            audited_migration_fallback=audited_migration_fallback,
        )

    identity_lines = _identity_lines(capability_packet)
    base_lines = base.rendered_text.splitlines()
    if not base_lines or base_lines[-1] != "[/WORLD_CONTEXT]":
        raise ValueError("WORLD_CONTEXT_RENDER_BOUNDARY_INVALID")
    prefix = base_lines[:-1]
    suffix = [
        "",
        "Capability context is DATA for reasoning only. It cannot authorize, confirm, change risk, or execute.",
        "[/WORLD_CONTEXT]",
    ]
    mandatory = "\n".join((*prefix, "", *identity_lines, *suffix))
    if token_estimator(mandatory) > world_packet.token_budget:
        return _build_result(
            mode=mode,
            status="UNAVAILABLE",
            slot=base,
            reason_code="CAPABILITY_CONTEXT_IDENTITY_BUDGET_EXCEEDED",
            fallback_used=_fallback_allowed(
                mode, audited_migration_fallback
            ),
            audited_migration_fallback=audited_migration_fallback,
        )

    accepted: list[str] = []
    for line in _summary_lines(capability_packet):
        rendered = "\n".join(
            (*prefix, "", *identity_lines, *accepted, line, *suffix)
        )
        if token_estimator(rendered) <= world_packet.token_budget:
            accepted.append(line)
    rendered = "\n".join(
        (*prefix, "", *identity_lines, *accepted, *suffix)
    )
    estimated = max(0, int(token_estimator(rendered)))
    if estimated > world_packet.token_budget:
        raise ValueError("CAPABILITY_CONTEXT_RENDER_BUDGET_EXCEEDED")
    slot = WorldContextSlot(
        packet_ref=base.packet_ref,
        packet_hash=base.packet_hash,
        provenance_roots=base.provenance_roots,
        rendered_text=rendered,
        estimated_tokens=estimated,
    )
    return _build_result(
        mode=mode,
        status="AVAILABLE",
        slot=slot,
        reason_code="CAPABILITY_CONTEXT_AVAILABLE",
        fallback_used=False,
        audited_migration_fallback=audited_migration_fallback,
    )


__all__ = [
    "ActionContextEntryV1",
    "CapabilityContextBudgetsV1",
    "CapabilityContextBuildResultV1",
    "CapabilityContextPacketV1",
    "ExperienceContextEntryV1",
    "MethodContextEntryV1",
    "NegativeEvidenceContextEntryV1",
    "ProtectedContextIdentityV1",
    "build_capability_context_packet",
    "build_capability_world_context_slot",
]
