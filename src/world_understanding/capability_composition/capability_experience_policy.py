"""P5 capability-specific experience admission, statistics, and stale policy.

The generic P15 memory promotion rules are intentionally untouched.  This
module is pure: it accepts machine evidence, emits immutable DATA records and a
MemoryCoordinator materialization intent, and never opens or writes a Store.
"""

from __future__ import annotations

from math import isqrt
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from contracts import canonical_json_bytes, canonical_sha256
from contracts.capability_composition import (
    AttributionIntegrityV1,
    CapabilityCombinationExperienceV1,
    CapabilityCompositionPlanV1,
    SourceRevisionRefV1,
)
from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256

from .capability_experience_attribution import (
    AttributionTraceV1,
    attribution_has_valid_sha256,
)
from .compiler import plan_has_valid_sha256


CAPABILITY_EXPERIENCE_POLICY_VERSION = "capability-experience-p5-v1"
CAPABILITY_EXPERIENCE_SCHEMA = "tiangong.capability-experience-policy.v1"
NEGATIVE_CAPABILITY_EVIDENCE_SCHEMA = (
    "tiangong.negative-capability-evidence.v1"
)
CAPABILITY_MEMORY_INTENT_SCHEMA = (
    "tiangong.capability-experience-memory-intent.v1"
)

FailureCategory = Literal[
    "VALIDATOR_INVALID",
    "RUNTIME_FAILURE",
    "PERMISSION_DENIED",
    "TOOL_UNAVAILABLE",
    "VERIFICATION_FAILURE",
    "SOURCE_STALE_MISMATCH",
    "AMBIGUOUS_EFFECT",
    "CONTEXT_IDENTITY_TRUNCATED",
    "ATTRIBUTION_FAILURE",
    "COMPLETION_INCOMPLETE",
    "PRIVACY_SCOPE_MISMATCH",
    "PRINCIPAL_SCOPE_MISMATCH",
    "PROMPT_INJECTION",
    "SECRET_PRESENT",
    "RECONCILE_REQUIRED",
]
AdmissionDecision = Literal[
    "POSITIVE_EXPERIENCE",
    "NEGATIVE_EVIDENCE",
    "NO_MEMORY",
    "MARK_STALE",
    "REQUIRE_REVALIDATION",
]
SourceFreshness = Literal["CURRENT", "REVALIDATION_REQUIRED", "STALE"]


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError("values must be sorted and unique")
    return values


def _source_sort_key(
    source: SourceRevisionRefV1,
) -> tuple[str, str, str, str, str, str]:
    return (
        source.source_kind,
        source.semantic_id,
        source.version,
        source.source_sha256,
        source.descriptor_sha256,
        source.manifest_sha256 or "",
    )


def _source_refs(
    plan: CapabilityCompositionPlanV1,
) -> tuple[SourceRevisionRefV1, ...]:
    return tuple(
        sorted(
            (*plan.method_source_refs, *plan.action_source_refs),
            key=_source_sort_key,
        )
    )


def source_revision_family(
    sources: tuple[SourceRevisionRefV1, ...],
) -> str:
    """Hash stable source identity while excluding revision-specific hashes."""

    if not sources:
        raise ValueError("source revision family cannot be empty")
    return "srcfam_" + canonical_sha256(
        {
            "domain": "tiangong.capability-source-family.v1",
            "sources": [
                {
                    "source_kind": source.source_kind,
                    "semantic_id": source.semantic_id,
                    "version": source.version,
                }
                for source in sorted(sources, key=_source_sort_key)
            ],
        }
    )


def exact_source_hashes(
    sources: tuple[SourceRevisionRefV1, ...],
) -> tuple[str, ...]:
    if not sources:
        raise ValueError("exact source revisions cannot be empty")
    return tuple(
        sorted(
            canonical_sha256(source.model_dump(mode="json"))
            for source in sources
        )
    )


def capability_experience_key(
    *,
    goal_class: str,
    composition_topology_sha256: str,
    source_revision_family_id: str,
    environment_class: str,
) -> str:
    return "capexpkey_" + canonical_sha256(
        {
            "domain": "tiangong.capability-experience-key.v1",
            "goal_class": goal_class,
            "composition_topology_sha256": composition_topology_sha256,
            "source_revision_family": source_revision_family_id,
            "environment_class": environment_class,
        }
    )


class CapabilityExperiencePolicyConfigV1(ContractModel):
    schema_version: Literal[
        "tiangong.capability-experience-policy-config.v1"
    ] = "tiangong.capability-experience-policy-config.v1"
    policy_version: Literal[CAPABILITY_EXPERIENCE_POLICY_VERSION] = (
        CAPABILITY_EXPERIENCE_POLICY_VERSION
    )
    minimum_positive_quality_milli: int = Field(default=700, ge=0, le=1000)
    stable_min_success_count: int = Field(default=5, ge=1, le=10_000)
    stable_min_independent_context_count: int = Field(
        default=4, ge=1, le=10_000
    )
    stable_max_failure_count: int = Field(default=1, ge=0, le=10_000)
    stable_min_lower_confidence_milli: int = Field(
        default=700, ge=0, le=1000
    )
    stable_min_average_quality_milli: int = Field(
        default=800, ge=0, le=1000
    )
    evidence_decay_window_ms: int = Field(
        default=30 * 24 * 60 * 60 * 1000,
        ge=60_000,
        le=3650 * 24 * 60 * 60 * 1000,
    )
    wilson_z_million: int = Field(default=1_282_000, ge=1, le=10_000_000)
    config_sha256: Sha256

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"config_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.config_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CapabilityExperiencePolicyConfigV1":
        return self.model_copy(update={"config_sha256": self.computed_sha256()})


DEFAULT_CAPABILITY_EXPERIENCE_POLICY = (
    CapabilityExperiencePolicyConfigV1(
        config_sha256="0" * 64
    ).with_computed_sha256()
)


class CapabilityExperienceObservationV1(ContractModel):
    """One post-execution observation presented to the P5 policy."""

    schema_version: Literal[
        "tiangong.capability-experience-observation.v1"
    ] = "tiangong.capability-experience-observation.v1"
    observation_id: OpaqueId
    life_id: OpaqueId
    principal_ref: OpaqueId
    principal_scope_hash: Sha256
    privacy_scope: OpaqueId
    privacy_scope_hash: Sha256
    goal_class: OpaqueId
    environment_class: OpaqueId
    scene_fingerprint: Sha256
    context_fingerprint_sha256: Sha256
    composition_topology_sha256: Sha256
    plan: CapabilityCompositionPlanV1
    trace: AttributionTraceV1
    attribution: AttributionIntegrityV1
    outcome: Literal["SUCCESS", "FAILURE", "PARTIAL", "RECONCILE"]
    quality_milli: int = Field(ge=0, le=1000)
    failure_category: FailureCategory | None = None
    failure_reason_codes: tuple[OpaqueId, ...] = ()
    observed_at_ms: int = Field(ge=0)
    observation_sha256: Sha256
    model_generated: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    _failure_reasons = field_validator("failure_reason_codes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if (
            self.plan.request_id != self.trace.request_id
            or self.plan.run_id != self.trace.run_id
            or self.plan.generation != self.trace.generation
            or self.plan.plan_sha256 != self.trace.composition_plan_sha256
            or self.plan.principal_scope_hash != self.principal_scope_hash
            or self.plan.context_fingerprint_sha256
            != self.context_fingerprint_sha256
            or self.plan.dependency_graph_sha256
            != self.composition_topology_sha256
        ):
            raise ValueError("experience observation crosses plan/trace scope")
        if (
            self.attribution.request_id != self.plan.request_id
            or self.attribution.run_id != self.plan.run_id
            or self.attribution.generation != self.plan.generation
            or self.attribution.composition_plan_sha256
            != self.plan.plan_sha256
        ):
            raise ValueError("experience attribution crosses plan scope")
        if self.trace.principal_scope_hash != self.principal_scope_hash:
            raise ValueError("trace principal scope disagrees with observation")
        if self.trace.privacy_scope_hash != self.privacy_scope_hash:
            raise ValueError("trace privacy scope disagrees with observation")
        if self.outcome == "SUCCESS" and self.failure_category is not None:
            raise ValueError("successful observation cannot carry failure category")
        if self.outcome != "SUCCESS" and self.failure_category is None:
            raise ValueError("non-success observation requires failure category")
        if self.observed_at_ms < self.trace.collected_at_ms:
            raise ValueError("observation cannot predate attribution trace")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"observation_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.observation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CapabilityExperienceObservationV1":
        return self.model_copy(
            update={"observation_sha256": self.computed_sha256()}
        )


class CapabilityExperienceAdmissionDecisionV1(ContractModel):
    schema_version: Literal[
        "tiangong.capability-experience-admission.v1"
    ] = "tiangong.capability-experience-admission.v1"
    observation_id: OpaqueId
    decision: AdmissionDecision
    positive_allowed: bool
    negative_allowed: bool
    experience_key: OpaqueId
    source_revision_family: OpaqueId
    exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    reason_codes: tuple[OpaqueId, ...]
    failure_category: FailureCategory | None = None
    attribution_sha256: Sha256
    decided_at_ms: int = Field(ge=0)
    decision_sha256: Sha256
    model_generated: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    _sources = field_validator("exact_source_hashes")(_sorted_unique)
    _reasons = field_validator("reason_codes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_decision_flags(self) -> Self:
        expected_positive = self.decision == "POSITIVE_EXPERIENCE"
        expected_negative = self.decision in {
            "NEGATIVE_EVIDENCE",
            "MARK_STALE",
            "REQUIRE_REVALIDATION",
        }
        if self.positive_allowed != expected_positive:
            raise ValueError("positive admission flag disagrees with decision")
        if self.negative_allowed != expected_negative:
            raise ValueError("negative admission flag disagrees with decision")
        if self.positive_allowed and self.failure_category is not None:
            raise ValueError("positive admission cannot carry failure category")
        if self.negative_allowed and self.failure_category is None:
            raise ValueError("negative admission requires a failure category")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"decision_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_sha256()

    def with_computed_sha256(
        self,
    ) -> "CapabilityExperienceAdmissionDecisionV1":
        return self.model_copy(update={"decision_sha256": self.computed_sha256()})


class NegativeCapabilityEvidenceV1(ContractModel):
    """Bounded negative evidence; never promoted as positive muscle memory."""

    schema_version: Literal[NEGATIVE_CAPABILITY_EVIDENCE_SCHEMA] = (
        NEGATIVE_CAPABILITY_EVIDENCE_SCHEMA
    )
    evidence_id: OpaqueId
    observation_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    life_id: OpaqueId
    principal_ref: OpaqueId
    principal_scope_hash: Sha256
    privacy_scope: OpaqueId
    privacy_scope_hash: Sha256
    goal_class: OpaqueId
    environment_class: OpaqueId
    scene_fingerprint: Sha256
    context_fingerprint_sha256: Sha256
    composition_topology_sha256: Sha256
    composition_plan_sha256: Sha256
    source_revision_family: OpaqueId
    exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    failure_category: FailureCategory
    reason_codes: tuple[OpaqueId, ...] = Field(min_length=1)
    attribution_sha256: Sha256
    completion_decision_sha256: Sha256
    verification_readiness_sha256: Sha256
    verification_record_refs: tuple[OpaqueId, ...] = ()
    terminal_fact_hashes: tuple[Sha256, ...] = ()
    observed_at_ms: int = Field(ge=0)
    context_section: Literal["DATA"] = "DATA"
    instruction_authority: Literal[False] = False
    world_authority: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    evidence_sha256: Sha256

    _sources = field_validator("exact_source_hashes")(_sorted_unique)
    _reasons = field_validator("reason_codes")(_sorted_unique)
    _verification_refs = field_validator("verification_record_refs")(
        _sorted_unique
    )
    _fact_hashes = field_validator("terminal_fact_hashes")(_sorted_unique)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"evidence_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "NegativeCapabilityEvidenceV1":
        return self.model_copy(update={"evidence_sha256": self.computed_sha256()})


class CapabilityExperienceAggregateStateV1(ContractModel):
    """One content-addressed aggregate stored as L3 CAPABILITY_KNOWLEDGE DATA."""

    schema_version: Literal[
        "tiangong.capability-experience-aggregate.v1"
    ] = "tiangong.capability-experience-aggregate.v1"
    experience_key: OpaqueId
    life_id: OpaqueId
    principal_ref: OpaqueId
    principal_scope_hash: Sha256
    privacy_scope: OpaqueId
    privacy_scope_hash: Sha256
    experience: CapabilityCombinationExperienceV1
    context_fingerprints: tuple[Sha256, ...]
    observation_ids: tuple[OpaqueId, ...]
    negative_evidence_ids: tuple[OpaqueId, ...] = ()
    quality_sum_milli: int = Field(ge=0)
    quality_observation_count: int = Field(ge=0)
    last_observed_at_ms: int = Field(ge=0)
    context_section: Literal["DATA"] = "DATA"
    instruction_authority: Literal[False] = False
    world_authority: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    state_sha256: Sha256

    _contexts = field_validator("context_fingerprints")(_sorted_unique)
    _observations = field_validator("observation_ids")(_sorted_unique)
    _negative_ids = field_validator("negative_evidence_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_statistics(self) -> Self:
        if not experience_has_valid_sha256(self.experience):
            raise ValueError("aggregate contains an invalid experience")
        if self.experience.independent_context_count != len(
            self.context_fingerprints
        ):
            raise ValueError("independent context count is stale")
        if self.experience.success_count + self.experience.failure_count != len(
            self.observation_ids
        ):
            raise ValueError("observation count disagrees with experience statistics")
        if self.quality_observation_count != self.experience.success_count:
            raise ValueError("quality statistics must count positive observations only")
        if self.experience.source_revision_family != source_revision_family(
            (*self.experience.method_source_refs, *self.experience.action_source_refs)
        ):
            raise ValueError("experience source family is invalid")
        if self.experience.exact_source_hashes != exact_source_hashes(
            (*self.experience.method_source_refs, *self.experience.action_source_refs)
        ):
            raise ValueError("experience exact source hashes are invalid")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"state_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.state_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CapabilityExperienceAggregateStateV1":
        return self.model_copy(update={"state_sha256": self.computed_sha256()})


class CapabilityExperienceInvalidationIntentV1(ContractModel):
    """Non-writing request for the existing Memory invalidation DAG authority."""

    schema_version: Literal[
        "tiangong.capability-experience-invalidation-intent.v1"
    ] = "tiangong.capability-experience-invalidation-intent.v1"
    experience_id: OpaqueId
    experience_sha256: Sha256
    prior_source_revision_family: OpaqueId
    current_source_revision_family: OpaqueId
    prior_exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    current_exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    freshness: Literal["REVALIDATION_REQUIRED", "STALE"]
    reason_code: Literal[
        "capability_experience.source_revision_changed",
        "capability_experience.source_family_changed",
    ]
    requested_at_ms: int = Field(ge=0)
    may_write_store: Literal[False] = False
    may_authorize: Literal[False] = False
    intent_sha256: Sha256

    _prior = field_validator("prior_exact_source_hashes")(_sorted_unique)
    _current = field_validator("current_exact_source_hashes")(_sorted_unique)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"intent_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.intent_sha256 == self.computed_sha256()

    def with_computed_sha256(
        self,
    ) -> "CapabilityExperienceInvalidationIntentV1":
        return self.model_copy(update={"intent_sha256": self.computed_sha256()})


class CapabilityExperienceMemoryIntentV1(ContractModel):
    """Payload intent consumed only by the existing MemoryCoordinator authority."""

    schema_version: Literal[CAPABILITY_MEMORY_INTENT_SCHEMA] = (
        CAPABILITY_MEMORY_INTENT_SCHEMA
    )
    life_id: OpaqueId
    principal_ref: OpaqueId
    principal_scope_hash: Sha256
    privacy_scope: OpaqueId
    privacy_scope_hash: Sha256
    layer: Literal["L3_EXPERIENCE"] = "L3_EXPERIENCE"
    semantic_domain: Literal["CAPABILITY_KNOWLEDGE"] = "CAPABILITY_KNOWLEDGE"
    claim_key: OpaqueId
    policy_version: Literal[CAPABILITY_EXPERIENCE_POLICY_VERSION] = (
        CAPABILITY_EXPERIENCE_POLICY_VERSION
    )
    parent_derivation_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    experience_state_sha256: Sha256
    negative_evidence_sha256: Sha256 | None = None
    plaintext_sha256: Sha256
    created_at_ms: int = Field(ge=0)
    context_section: Literal["DATA"] = "DATA"
    instruction_authority: Literal[False] = False
    world_candidate_eligible: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    may_write_store: Literal[False] = False
    coordinator_required: Literal[True] = True
    intent_sha256: Sha256

    _parents = field_validator("parent_derivation_ids")(_sorted_unique)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"intent_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.intent_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CapabilityExperienceMemoryIntentV1":
        return self.model_copy(update={"intent_sha256": self.computed_sha256()})


class CapabilityExperienceRecallQueryV1(ContractModel):
    schema_version: Literal[
        "tiangong.capability-experience-recall-query.v1"
    ] = "tiangong.capability-experience-recall-query.v1"
    principal_scope_hash: Sha256
    privacy_scope_hash: Sha256
    goal_class: OpaqueId
    environment_class: OpaqueId
    current_source_revision_family: OpaqueId
    current_exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    now_ms: int = Field(ge=0)
    include_probation: bool = True
    limit: int = Field(default=8, ge=1, le=64)

    _sources = field_validator("current_exact_source_hashes")(_sorted_unique)


class CapabilityExperienceRecallItemV1(ContractModel):
    experience_id: OpaqueId
    experience_sha256: Sha256
    lifecycle: Literal["PROBATION", "STABLE"]
    posterior_success_milli: int = Field(ge=0, le=1000)
    lower_confidence_milli: int = Field(ge=0, le=1000)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    independent_context_count: int = Field(ge=0)
    last_success_ms: int | None = Field(default=None, ge=0)
    context_section: Literal["DATA"] = "DATA"
    instruction_authority: Literal[False] = False
    world_authority: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False


def computed_experience_sha256(
    experience: CapabilityCombinationExperienceV1,
) -> str:
    return canonical_sha256(
        experience.model_dump(mode="json", exclude={"experience_sha256"})
    )


def experience_has_valid_sha256(
    experience: CapabilityCombinationExperienceV1,
) -> bool:
    return experience.experience_sha256 == computed_experience_sha256(
        experience
    )


def posterior_success_milli(success_count: int, failure_count: int) -> int:
    """Beta(1,1) posterior mean using integer fixed point only."""

    if min(success_count, failure_count) < 0:
        raise ValueError("experience counts cannot be negative")
    return ((success_count + 1) * 1000) // (
        success_count + failure_count + 2
    )


def wilson_lower_confidence_milli(
    success_count: int,
    failure_count: int,
    *,
    z_million: int = 1_282_000,
) -> int:
    """Deterministic Wilson lower bound; all arithmetic is integer/isqrt."""

    if min(success_count, failure_count) < 0:
        raise ValueError("experience counts cannot be negative")
    if z_million <= 0:
        raise ValueError("Wilson z must be positive")
    n = success_count + failure_count
    if n == 0:
        return 0
    scale = 1_000_000
    p = (success_count * scale) // n
    z_squared = (z_million * z_million) // scale
    center = p + z_squared // (2 * n)
    radicand = (
        (p * (scale - p)) // n
        + (z_million * z_million) // (4 * n * n)
    )
    margin = (z_million * isqrt(radicand)) // scale
    numerator = max(0, center - margin)
    denominator = scale + z_squared // n
    lower_million = (numerator * scale) // denominator
    return min(1000, (lower_million * 1000) // scale)


def _failure_category_for_gate(
    observation: CapabilityExperienceObservationV1,
    reasons: set[str],
) -> FailureCategory:
    if observation.failure_category is not None:
        return observation.failure_category
    trace = observation.trace
    if trace.context_identity_truncated:
        return "CONTEXT_IDENTITY_TRUNCATED"
    if trace.secret_or_credential_present:
        return "SECRET_PRESENT"
    if trace.prompt_injection_present:
        return "PROMPT_INJECTION"
    if trace.unresolved_reconciliation or observation.outcome == "RECONCILE":
        return "RECONCILE_REQUIRED"
    if "capability_experience.principal_scope_mismatch" in reasons:
        return "PRINCIPAL_SCOPE_MISMATCH"
    if "capability_experience.privacy_scope_mismatch" in reasons:
        return "PRIVACY_SCOPE_MISMATCH"
    if any("source" in reason for reason in reasons):
        return "SOURCE_STALE_MISMATCH"
    if any("verification" in reason for reason in reasons):
        return "VERIFICATION_FAILURE"
    if any("completion" in reason for reason in reasons):
        return "COMPLETION_INCOMPLETE"
    return "ATTRIBUTION_FAILURE"


def evaluate_capability_experience_admission(
    observation: CapabilityExperienceObservationV1,
    *,
    expected_principal_scope_hash: str,
    expected_privacy_scope_hash: str,
    config: CapabilityExperiencePolicyConfigV1 = (
        DEFAULT_CAPABILITY_EXPERIENCE_POLICY
    ),
    decided_at_ms: int,
) -> CapabilityExperienceAdmissionDecisionV1:
    """Apply the capability-specific positive/negative memory gate."""

    if decided_at_ms < observation.observed_at_ms:
        raise ValueError("admission decision cannot predate observation")
    if not config.has_valid_sha256():
        raise ValueError("capability experience policy config hash is invalid")
    sources = _source_refs(observation.plan)
    family = source_revision_family(sources)
    source_hashes = exact_source_hashes(sources)
    key = capability_experience_key(
        goal_class=observation.goal_class,
        composition_topology_sha256=(
            observation.composition_topology_sha256
        ),
        source_revision_family_id=family,
        environment_class=observation.environment_class,
    )
    reasons: set[str] = set()
    if not observation.has_valid_sha256():
        reasons.add("capability_experience.observation_hash_invalid")
    if not plan_has_valid_sha256(observation.plan):
        reasons.add("capability_experience.plan_hash_invalid")
    if not observation.trace.has_valid_sha256():
        reasons.add("capability_experience.trace_hash_invalid")
    if not attribution_has_valid_sha256(observation.attribution):
        reasons.add("capability_experience.attribution_hash_invalid")
    if observation.attribution.state != "PASS":
        reasons.add("capability_experience.attribution_failed")
    if observation.principal_scope_hash != expected_principal_scope_hash:
        reasons.add("capability_experience.principal_scope_mismatch")
    if observation.privacy_scope_hash != expected_privacy_scope_hash:
        reasons.add("capability_experience.privacy_scope_mismatch")
    if not observation.plan.action_source_refs:
        reasons.add("capability_experience.action_source_missing")
    if not observation.trace.source_refs_complete:
        reasons.add("capability_experience.source_refs_incomplete")
    if not observation.trace.source_revisions_continuous:
        reasons.add("capability_experience.source_revision_discontinuous")

    completion = observation.trace.completion
    if completion.outcome != "COMPLETED" or not (
        completion.can_transition_request_completed
    ):
        reasons.add("capability_experience.completion_incomplete")
    if completion.needs_reconciliation or observation.trace.unresolved_reconciliation:
        reasons.add("capability_experience.reconciliation_unresolved")
    if observation.trace.has_acceptance_obligations:
        if completion.verification_mode != "PLAN_BOUND":
            reasons.add("capability_experience.verification_not_plan_bound")
        if not completion.verification_ready:
            reasons.add("capability_experience.verification_not_ready")
        if not observation.trace.active_verification_plan_complete:
            reasons.add("capability_experience.verification_plan_incomplete")
        if not observation.trace.verification_record_refs:
            reasons.add("capability_experience.verification_records_missing")
    if not observation.trace.effect_fact_lineage_complete:
        reasons.add("capability_experience.effect_fact_lineage_incomplete")
    if not observation.trace.terminal_fact_hashes:
        reasons.add("capability_experience.terminal_fact_hashes_missing")
    if observation.quality_milli < config.minimum_positive_quality_milli:
        reasons.add("capability_experience.quality_below_gate")

    hazard_flags = (
        (
            observation.trace.human_takeover,
            "capability_experience.human_takeover",
        ),
        (
            observation.trace.alternate_execution_chain,
            "capability_experience.alternate_execution_chain",
        ),
        (
            observation.trace.unknown_external_overwrite,
            "capability_experience.unknown_external_overwrite",
        ),
        (
            observation.trace.unknown_side_effects,
            "capability_experience.unknown_side_effects",
        ),
        (
            observation.trace.secret_or_credential_present,
            "capability_experience.secret_or_credential_present",
        ),
        (
            observation.trace.prompt_injection_present,
            "capability_experience.prompt_injection_present",
        ),
        (
            observation.trace.context_identity_truncated,
            "capability_experience.context_identity_truncated",
        ),
    )
    for active, reason in hazard_flags:
        if active:
            reasons.add(reason)

    source_reason = {
        "capability_experience.source_refs_incomplete",
        "capability_experience.source_revision_discontinuous",
        "capability_experience.action_source_missing",
    }.intersection(reasons)
    if source_reason:
        decision: AdmissionDecision = "REQUIRE_REVALIDATION"
    elif observation.outcome == "SUCCESS" and not reasons:
        decision = "POSITIVE_EXPERIENCE"
    elif observation.outcome != "SUCCESS" or reasons:
        decision = "NEGATIVE_EVIDENCE"
    else:
        decision = "NO_MEMORY"

    failure_category = (
        None
        if decision in {"POSITIVE_EXPERIENCE", "NO_MEMORY"}
        else _failure_category_for_gate(observation, reasons)
    )
    if decision == "POSITIVE_EXPERIENCE":
        decision_reasons = ("capability_experience.positive_gate_passed",)
    elif decision == "NO_MEMORY":
        decision_reasons = ("capability_experience.no_material_evidence",)
    else:
        decision_reasons = tuple(sorted(reasons or set(observation.failure_reason_codes)))
        if not decision_reasons:
            decision_reasons = ("capability_experience.failure_observed",)

    value = CapabilityExperienceAdmissionDecisionV1(
        observation_id=observation.observation_id,
        decision=decision,
        positive_allowed=decision == "POSITIVE_EXPERIENCE",
        negative_allowed=decision in {
            "NEGATIVE_EVIDENCE",
            "MARK_STALE",
            "REQUIRE_REVALIDATION",
        },
        experience_key=key,
        source_revision_family=family,
        exact_source_hashes=source_hashes,
        reason_codes=decision_reasons,
        failure_category=failure_category,
        attribution_sha256=observation.attribution.attribution_sha256,
        decided_at_ms=decided_at_ms,
        decision_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def build_negative_capability_evidence(
    observation: CapabilityExperienceObservationV1,
    admission: CapabilityExperienceAdmissionDecisionV1,
) -> NegativeCapabilityEvidenceV1:
    if not observation.has_valid_sha256():
        raise ValueError("experience observation hash is invalid")
    if not admission.has_valid_sha256():
        raise ValueError("experience admission hash is invalid")
    if admission.observation_id != observation.observation_id:
        raise ValueError("negative evidence crosses observation identity")
    if not admission.negative_allowed or admission.failure_category is None:
        raise ValueError("admission does not permit negative evidence")
    completion = observation.trace.completion
    readiness_hash = completion.verification_readiness_sha256 or canonical_sha256(
        {
            "domain": "tiangong.no-verification-readiness.v1",
            "completion_decision_sha256": completion.decision_sha256,
            "verification_mode": completion.verification_mode,
        }
    )
    evidence_id = "negcap_" + canonical_sha256(
        {
            "domain": "tiangong.negative-capability-evidence-id.v1",
            "observation_sha256": observation.observation_sha256,
            "admission_sha256": admission.decision_sha256,
        }
    )
    value = NegativeCapabilityEvidenceV1(
        evidence_id=evidence_id,
        observation_id=observation.observation_id,
        request_id=observation.plan.request_id,
        run_id=observation.plan.run_id,
        generation=observation.plan.generation,
        life_id=observation.life_id,
        principal_ref=observation.principal_ref,
        principal_scope_hash=observation.principal_scope_hash,
        privacy_scope=observation.privacy_scope,
        privacy_scope_hash=observation.privacy_scope_hash,
        goal_class=observation.goal_class,
        environment_class=observation.environment_class,
        scene_fingerprint=observation.scene_fingerprint,
        context_fingerprint_sha256=observation.context_fingerprint_sha256,
        composition_topology_sha256=(
            observation.composition_topology_sha256
        ),
        composition_plan_sha256=observation.plan.plan_sha256,
        source_revision_family=admission.source_revision_family,
        exact_source_hashes=admission.exact_source_hashes,
        failure_category=admission.failure_category,
        reason_codes=admission.reason_codes,
        attribution_sha256=observation.attribution.attribution_sha256,
        completion_decision_sha256=completion.decision_sha256,
        verification_readiness_sha256=readiness_hash,
        verification_record_refs=observation.trace.verification_record_refs,
        terminal_fact_hashes=observation.trace.terminal_fact_hashes,
        observed_at_ms=observation.observed_at_ms,
        evidence_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _experience_outcome(
    observation: CapabilityExperienceObservationV1,
) -> Literal["SUCCESS", "FAILURE", "PARTIAL", "RECONCILE"]:
    return observation.outcome


def _experience_lifecycle(
    *,
    prior_lifecycle: str | None,
    success_count: int,
    failure_count: int,
    independent_context_count: int,
    lower_confidence_milli: int,
    average_quality_milli: int,
    last_success_ms: int | None,
    now_ms: int,
    config: CapabilityExperiencePolicyConfigV1,
) -> Literal[
    "PROBATION", "STABLE", "STALE", "REVALIDATION_REQUIRED", "RETIRED"
]:
    if prior_lifecycle in {"STALE", "REVALIDATION_REQUIRED", "RETIRED"}:
        return prior_lifecycle  # caller must start a new current-source aggregate
    recent = (
        last_success_ms is not None
        and now_ms - last_success_ms <= config.evidence_decay_window_ms
    )
    stable = (
        success_count >= config.stable_min_success_count
        and independent_context_count
        >= config.stable_min_independent_context_count
        and failure_count <= config.stable_max_failure_count
        and lower_confidence_milli
        >= config.stable_min_lower_confidence_milli
        and average_quality_milli
        >= config.stable_min_average_quality_milli
        and recent
    )
    return "STABLE" if stable else "PROBATION"


def _new_experience(
    observation: CapabilityExperienceObservationV1,
    admission: CapabilityExperienceAdmissionDecisionV1,
    *,
    negative: NegativeCapabilityEvidenceV1 | None,
    config: CapabilityExperiencePolicyConfigV1,
) -> CapabilityExperienceAggregateStateV1:
    positive = admission.positive_allowed
    negative_allowed = admission.negative_allowed
    if positive == negative_allowed:
        raise ValueError("observation must enter exactly one statistics pool")
    success_count = 1 if positive else 0
    failure_count = 1 if negative_allowed else 0
    posterior = posterior_success_milli(success_count, failure_count)
    lower = wilson_lower_confidence_milli(
        success_count,
        failure_count,
        z_million=config.wilson_z_million,
    )
    sources = _source_refs(observation.plan)
    completion = observation.trace.completion
    readiness_hash = completion.verification_readiness_sha256 or canonical_sha256(
        {
            "domain": "tiangong.no-verification-readiness.v1",
            "completion_decision_sha256": completion.decision_sha256,
            "verification_mode": completion.verification_mode,
        }
    )
    experience_id = "capexp_" + canonical_sha256(
        {
            "domain": "tiangong.capability-experience-id.v1",
            "life_id": observation.life_id,
            "principal_scope_hash": observation.principal_scope_hash,
            "privacy_scope_hash": observation.privacy_scope_hash,
            "experience_key": admission.experience_key,
        }
    )
    experience = CapabilityCombinationExperienceV1(
        experience_id=experience_id,
        goal_class=observation.goal_class,
        environment_class=observation.environment_class,
        scene_fingerprint=observation.scene_fingerprint,
        context_fingerprint_sha256=observation.context_fingerprint_sha256,
        method_source_refs=observation.plan.method_source_refs,
        action_source_refs=observation.plan.action_source_refs,
        topology_sha256=observation.composition_topology_sha256,
        composition_plan_sha256=observation.plan.plan_sha256,
        request_id=observation.plan.request_id,
        run_id=observation.plan.run_id,
        generation=observation.plan.generation,
        completion_decision_sha256=completion.decision_sha256,
        verification_readiness_sha256=readiness_hash,
        verification_record_refs=observation.trace.verification_record_refs,
        terminal_fact_hashes=observation.trace.terminal_fact_hashes,
        outcome=_experience_outcome(observation),
        success_count=success_count,
        failure_count=failure_count,
        independent_context_count=1,
        last_success_ms=observation.observed_at_ms if positive else None,
        last_failure_ms=(
            observation.observed_at_ms if negative_allowed else None
        ),
        posterior_success_milli=posterior,
        lower_confidence_milli=lower,
        lifecycle="PROBATION",
        source_revision_family=source_revision_family(sources),
        exact_source_hashes=exact_source_hashes(sources),
        experience_sha256="0" * 64,
    )
    experience = experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(experience)}
    )
    state = CapabilityExperienceAggregateStateV1(
        experience_key=admission.experience_key,
        life_id=observation.life_id,
        principal_ref=observation.principal_ref,
        principal_scope_hash=observation.principal_scope_hash,
        privacy_scope=observation.privacy_scope,
        privacy_scope_hash=observation.privacy_scope_hash,
        experience=experience,
        context_fingerprints=(observation.context_fingerprint_sha256,),
        observation_ids=(observation.observation_id,),
        negative_evidence_ids=(
            (negative.evidence_id,) if negative is not None else ()
        ),
        quality_sum_milli=observation.quality_milli if positive else 0,
        quality_observation_count=1 if positive else 0,
        last_observed_at_ms=observation.observed_at_ms,
        state_sha256="0" * 64,
    )
    return state.with_computed_sha256()


def apply_capability_experience_observation(
    prior: CapabilityExperienceAggregateStateV1 | None,
    observation: CapabilityExperienceObservationV1,
    admission: CapabilityExperienceAdmissionDecisionV1,
    *,
    config: CapabilityExperiencePolicyConfigV1 = (
        DEFAULT_CAPABILITY_EXPERIENCE_POLICY
    ),
) -> tuple[
    CapabilityExperienceAggregateStateV1,
    NegativeCapabilityEvidenceV1 | None,
]:
    """Idempotently update one aggregate; positive and negative pools are disjoint."""

    if not observation.has_valid_sha256():
        raise ValueError("experience observation hash is invalid")
    if not admission.has_valid_sha256():
        raise ValueError("experience admission hash is invalid")
    if admission.observation_id != observation.observation_id:
        raise ValueError("admission crosses observation identity")
    if admission.decision == "NO_MEMORY":
        raise ValueError("NO_MEMORY observation cannot update experience state")
    negative = (
        build_negative_capability_evidence(observation, admission)
        if admission.negative_allowed
        else None
    )
    if prior is None:
        return (
            _new_experience(
                observation,
                admission,
                negative=negative,
                config=config,
            ),
            negative,
        )
    if not prior.has_valid_sha256():
        raise ValueError("prior experience state hash is invalid")
    if (
        prior.experience_key != admission.experience_key
        or prior.life_id != observation.life_id
        or prior.principal_ref != observation.principal_ref
        or prior.principal_scope_hash != observation.principal_scope_hash
        or prior.privacy_scope != observation.privacy_scope
        or prior.privacy_scope_hash != observation.privacy_scope_hash
    ):
        raise ValueError("experience aggregate crosses identity or scope")
    if observation.observation_id in prior.observation_ids:
        return prior, None
    if prior.experience.lifecycle in {
        "STALE",
        "REVALIDATION_REQUIRED",
        "RETIRED",
    }:
        raise ValueError("inactive experience requires a new current-source aggregate")
    if (
        prior.experience.source_revision_family
        != admission.source_revision_family
        or prior.experience.exact_source_hashes
        != admission.exact_source_hashes
    ):
        raise ValueError("source drift must be handled before statistics update")

    positive = admission.positive_allowed
    success_count = prior.experience.success_count + (1 if positive else 0)
    failure_count = prior.experience.failure_count + (
        1 if admission.negative_allowed else 0
    )
    contexts = tuple(
        sorted(
            set(prior.context_fingerprints)
            | {observation.context_fingerprint_sha256}
        )
    )
    observations = tuple(
        sorted(set(prior.observation_ids) | {observation.observation_id})
    )
    negatives = tuple(
        sorted(
            set(prior.negative_evidence_ids)
            | ({negative.evidence_id} if negative is not None else set())
        )
    )
    quality_sum = prior.quality_sum_milli + (
        observation.quality_milli if positive else 0
    )
    quality_count = prior.quality_observation_count + (1 if positive else 0)
    average_quality = quality_sum // max(1, quality_count)
    posterior = posterior_success_milli(success_count, failure_count)
    lower = wilson_lower_confidence_milli(
        success_count,
        failure_count,
        z_million=config.wilson_z_million,
    )
    last_success = (
        observation.observed_at_ms
        if positive
        else prior.experience.last_success_ms
    )
    last_failure = (
        observation.observed_at_ms
        if admission.negative_allowed
        else prior.experience.last_failure_ms
    )
    lifecycle = _experience_lifecycle(
        prior_lifecycle=prior.experience.lifecycle,
        success_count=success_count,
        failure_count=failure_count,
        independent_context_count=len(contexts),
        lower_confidence_milli=lower,
        average_quality_milli=average_quality,
        last_success_ms=last_success,
        now_ms=observation.observed_at_ms,
        config=config,
    )
    completion = observation.trace.completion
    readiness_hash = completion.verification_readiness_sha256 or canonical_sha256(
        {
            "domain": "tiangong.no-verification-readiness.v1",
            "completion_decision_sha256": completion.decision_sha256,
            "verification_mode": completion.verification_mode,
        }
    )
    experience = prior.experience.model_copy(
        update={
            "scene_fingerprint": observation.scene_fingerprint,
            "context_fingerprint_sha256": (
                observation.context_fingerprint_sha256
            ),
            "composition_plan_sha256": observation.plan.plan_sha256,
            "request_id": observation.plan.request_id,
            "run_id": observation.plan.run_id,
            "generation": observation.plan.generation,
            "completion_decision_sha256": completion.decision_sha256,
            "verification_readiness_sha256": readiness_hash,
            "verification_record_refs": (
                observation.trace.verification_record_refs
            ),
            "terminal_fact_hashes": observation.trace.terminal_fact_hashes,
            "outcome": _experience_outcome(observation),
            "success_count": success_count,
            "failure_count": failure_count,
            "independent_context_count": len(contexts),
            "last_success_ms": last_success,
            "last_failure_ms": last_failure,
            "posterior_success_milli": posterior,
            "lower_confidence_milli": lower,
            "lifecycle": lifecycle,
            "experience_sha256": "0" * 64,
        }
    )
    experience = experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(experience)}
    )
    state = CapabilityExperienceAggregateStateV1(
        experience_key=prior.experience_key,
        life_id=prior.life_id,
        principal_ref=prior.principal_ref,
        principal_scope_hash=prior.principal_scope_hash,
        privacy_scope=prior.privacy_scope,
        privacy_scope_hash=prior.privacy_scope_hash,
        experience=experience,
        context_fingerprints=contexts,
        observation_ids=observations,
        negative_evidence_ids=negatives,
        quality_sum_milli=quality_sum,
        quality_observation_count=quality_count,
        last_observed_at_ms=observation.observed_at_ms,
        state_sha256="0" * 64,
    )
    return state.with_computed_sha256(), negative


def assess_capability_experience_source_freshness(
    state: CapabilityExperienceAggregateStateV1,
    current_sources: tuple[SourceRevisionRefV1, ...],
) -> SourceFreshness:
    if not state.has_valid_sha256():
        raise ValueError("experience state hash is invalid")
    current_family = source_revision_family(current_sources)
    current_exact = exact_source_hashes(current_sources)
    if current_family != state.experience.source_revision_family:
        return "STALE"
    if current_exact != state.experience.exact_source_hashes:
        return "REVALIDATION_REQUIRED"
    return "CURRENT"


def mark_capability_experience_source_change(
    state: CapabilityExperienceAggregateStateV1,
    current_sources: tuple[SourceRevisionRefV1, ...],
    *,
    requested_at_ms: int,
) -> tuple[
    CapabilityExperienceAggregateStateV1,
    CapabilityExperienceInvalidationIntentV1 | None,
]:
    freshness = assess_capability_experience_source_freshness(
        state, current_sources
    )
    if freshness == "CURRENT":
        return state, None
    current_family = source_revision_family(current_sources)
    current_exact = exact_source_hashes(current_sources)
    lifecycle = (
        "STALE" if freshness == "STALE" else "REVALIDATION_REQUIRED"
    )
    experience = state.experience.model_copy(
        update={"lifecycle": lifecycle, "experience_sha256": "0" * 64}
    )
    experience = experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(experience)}
    )
    updated = state.model_copy(
        update={
            "experience": experience,
            "last_observed_at_ms": max(
                state.last_observed_at_ms, requested_at_ms
            ),
            "state_sha256": "0" * 64,
        }
    )
    updated = updated.with_computed_sha256()
    intent = CapabilityExperienceInvalidationIntentV1(
        experience_id=state.experience.experience_id,
        experience_sha256=state.experience.experience_sha256,
        prior_source_revision_family=(
            state.experience.source_revision_family
        ),
        current_source_revision_family=current_family,
        prior_exact_source_hashes=state.experience.exact_source_hashes,
        current_exact_source_hashes=current_exact,
        freshness=lifecycle,
        reason_code=(
            "capability_experience.source_family_changed"
            if freshness == "STALE"
            else "capability_experience.source_revision_changed"
        ),
        requested_at_ms=requested_at_ms,
        intent_sha256="0" * 64,
    ).with_computed_sha256()
    return updated, intent


def build_capability_experience_memory_intent(
    state: CapabilityExperienceAggregateStateV1,
    *,
    parent_derivation_ids: tuple[str, ...],
    negative_evidence: NegativeCapabilityEvidenceV1 | None = None,
    created_at_ms: int,
) -> tuple[CapabilityExperienceMemoryIntentV1, bytes]:
    """Build DATA bytes and a non-writing intent for MemoryCoordinator."""

    if not state.has_valid_sha256():
        raise ValueError("experience state hash is invalid")
    if negative_evidence is not None:
        if not negative_evidence.has_valid_sha256():
            raise ValueError("negative evidence hash is invalid")
        if (
            negative_evidence.principal_scope_hash
            != state.principal_scope_hash
            or negative_evidence.privacy_scope_hash
            != state.privacy_scope_hash
        ):
            raise ValueError("negative evidence crosses memory scope")
    payload = {
        "schema": "tiangong.capability-experience-memory-payload.v1",
        "layer": "L3_EXPERIENCE",
        "semantic_domain": "CAPABILITY_KNOWLEDGE",
        "context_section": "DATA",
        "instruction_authority": False,
        "world_authority": False,
        "may_authorize": False,
        "may_execute": False,
        "experience_state": state.model_dump(mode="json"),
        "negative_evidence": (
            None
            if negative_evidence is None
            else negative_evidence.model_dump(mode="json")
        ),
    }
    plaintext = canonical_json_bytes(payload)
    claim_key = "capability-experience:" + canonical_sha256(
        {
            "life_id": state.life_id,
            "principal_scope_hash": state.principal_scope_hash,
            "privacy_scope_hash": state.privacy_scope_hash,
            "experience_key": state.experience_key,
        }
    )
    intent = CapabilityExperienceMemoryIntentV1(
        life_id=state.life_id,
        principal_ref=state.principal_ref,
        principal_scope_hash=state.principal_scope_hash,
        privacy_scope=state.privacy_scope,
        privacy_scope_hash=state.privacy_scope_hash,
        claim_key=claim_key,
        parent_derivation_ids=parent_derivation_ids,
        experience_state_sha256=state.state_sha256,
        negative_evidence_sha256=(
            None
            if negative_evidence is None
            else negative_evidence.evidence_sha256
        ),
        plaintext_sha256=canonical_sha256(payload),
        created_at_ms=created_at_ms,
        intent_sha256="0" * 64,
    ).with_computed_sha256()
    return intent, plaintext


def recall_capability_experiences(
    states: tuple[CapabilityExperienceAggregateStateV1, ...],
    query: CapabilityExperienceRecallQueryV1,
) -> tuple[CapabilityExperienceRecallItemV1, ...]:
    """Exact-scope recall; stale/revalidation/retired records are excluded."""

    selected: list[CapabilityExperienceRecallItemV1] = []
    for state in states:
        if not state.has_valid_sha256():
            continue
        experience = state.experience
        if (
            state.principal_scope_hash != query.principal_scope_hash
            or state.privacy_scope_hash != query.privacy_scope_hash
            or experience.goal_class != query.goal_class
            or experience.environment_class != query.environment_class
            or experience.source_revision_family
            != query.current_source_revision_family
            or experience.exact_source_hashes
            != query.current_exact_source_hashes
            or experience.lifecycle
            in {"STALE", "REVALIDATION_REQUIRED", "RETIRED"}
            or (
                experience.lifecycle == "PROBATION"
                and not query.include_probation
            )
        ):
            continue
        selected.append(
            CapabilityExperienceRecallItemV1(
                experience_id=experience.experience_id,
                experience_sha256=experience.experience_sha256,
                lifecycle=experience.lifecycle,
                posterior_success_milli=experience.posterior_success_milli,
                lower_confidence_milli=experience.lower_confidence_milli,
                success_count=experience.success_count,
                failure_count=experience.failure_count,
                independent_context_count=(
                    experience.independent_context_count
                ),
                last_success_ms=experience.last_success_ms,
            )
        )
    ordered = sorted(
        selected,
        key=lambda item: (
            0 if item.lifecycle == "STABLE" else 1,
            -item.lower_confidence_milli,
            -item.posterior_success_milli,
            -(item.last_success_ms or 0),
            item.experience_id,
        ),
    )
    return tuple(ordered[: query.limit])


__all__ = [
    "CAPABILITY_EXPERIENCE_POLICY_VERSION",
    "CapabilityExperienceAdmissionDecisionV1",
    "CapabilityExperienceAggregateStateV1",
    "CapabilityExperienceInvalidationIntentV1",
    "CapabilityExperienceMemoryIntentV1",
    "CapabilityExperienceObservationV1",
    "CapabilityExperiencePolicyConfigV1",
    "CapabilityExperienceRecallItemV1",
    "CapabilityExperienceRecallQueryV1",
    "DEFAULT_CAPABILITY_EXPERIENCE_POLICY",
    "NegativeCapabilityEvidenceV1",
    "apply_capability_experience_observation",
    "assess_capability_experience_source_freshness",
    "build_capability_experience_memory_intent",
    "build_negative_capability_evidence",
    "capability_experience_key",
    "computed_experience_sha256",
    "evaluate_capability_experience_admission",
    "exact_source_hashes",
    "experience_has_valid_sha256",
    "mark_capability_experience_source_change",
    "posterior_success_milli",
    "recall_capability_experiences",
    "source_revision_family",
    "wilson_lower_confidence_milli",
]
