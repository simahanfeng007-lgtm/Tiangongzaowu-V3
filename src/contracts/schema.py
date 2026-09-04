"""Deterministic JSON Schema bundle generation for gateway contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .delivery import ArtifactManifest, ComponentManifest, DeliveryReceipt, DeliveryTicket, OutboundPlan
from .cutover import ChannelCutoverSnapshot, ChannelDrainEvidence, ChannelOwnershipLease
from .life import (
    AppraisalVectorV3,
    LifeContextAuthorization,
    LifeEventEnvelope,
    LifeEventIngress,
    LifeEventIngressReceipt,
    LifeRevisionVector,
    TaskContinuityCapsule,
    ViabilityObservation,
    ViabilityState,
)
from .causal import CausalEpisode, CausalHypothesis
from .memory import (
    CausalContextPack,
    CausalNodeV3,
    ContextTokenBudget,
    MemoryAssertionV3,
    MemoryRelationV3,
    PrivacyDeletionTombstone,
)
from .affect import (
    AffectExpressionCase,
    AffectExpressionSelection,
    AffectIntakeReceipt,
    AffectSignal,
    AffectSourcePolicySnapshot,
    AffectiveStateV3,
)
from .agency import (
    ActionCandidate,
    ActionImpact,
    AgencyDecision,
    AutonomyPolicySnapshot,
    AutonomyUsageSnapshot,
    CapabilityEvidence,
    CapabilityLearningDecision,
    CapabilityProfile,
    CapabilityRollbackRecord,
    EpisodeOutcomeEvidence,
    ReflectionCard,
    ReflectionQuestionDecision,
)
from .execution import (
    CapabilityManifest,
    CompositionExecutionBindingV1,
    ExecutionResult,
    ExecutionTicket,
    FactRecord,
)
from .write_evidence import WriteEvidenceV2
from .verification import (
    EntryAssessment,
    RuntimeCloseoutEvidence,
    VerificationPlan,
    VerificationPlanEntryV2,
    VerificationReadiness,
    AcceptancePredicate,
    RegistrySnapshot,
    VerificationRecord,
    VerifierDescriptor,
)
from .policy import (
    ActionIntent,
    ActionPermission,
    ActionRegistrySnapshot,
    OmniCapabilityGrant,
    PolicyDecision,
    SkillActivationGrant,
    UserConfirmationGrant,
)
from .identities import (
    ArtifactRevisionIdentity,
    DeliveryIdentity,
    EffectIdentity,
    FenceDecision,
    GenerationFence,
    RequestIdentity,
    RunIdentity,
)
from .ingress import ChannelAckPermit, ProductionInboundAcceptance, ProductionInboundSubmission
from .models import AttachmentRef, InboundEnvelope, LifeSnapshot, SkillSelectionRecord
from .state_machine import AggregateStatus, StateSnapshot, TransitionDecision, TransitionEvent
from .scope import InboundScope, InboundScopeKeys, OutboundScope, OutboundScopeKeys
from .reliability import (
    CircuitBreakerPolicy,
    CircuitBreakerSnapshot,
    CircuitPermission,
    CircuitUpdate,
    DynamicTimeoutPolicy,
    ErrorDescriptor,
    RetryDecision,
    RetryPolicy,
    TimeoutDecision,
)
from .readiness import (
    ComponentReadinessEvidence,
    ReadinessDecision,
    ReadinessExpectation,
)
from .release import ReleaseManifest
from .shadow import (
    ShadowComparison,
    ShadowDecisionObservation,
    ShadowIngressCopy,
    ShadowObservationBatch,
)
from .security import (
    EmergencyKeyRevocationManifest,
    KeyRotationManifest,
    ProtectedPrivateKeyEnvelope,
    PublicKeyDescriptor,
    RedactedLogPayload,
    RedactionPolicy,
    ServiceAuthAssertion,
    TrustBundle,
)


CONTRACT_MODELS = (
    AcceptancePredicate,
    RuntimeCloseoutEvidence,
    VerificationPlan,
    VerificationPlanEntryV2,
    VerificationReadiness,
    WriteEvidenceV2,
    RegistrySnapshot,
    VerificationRecord,
    VerifierDescriptor,
    ActionCandidate,
    ActionImpact,
    ActionIntent,
    ActionPermission,
    ActionRegistrySnapshot,
    AffectExpressionCase,
    AffectExpressionSelection,
    AffectIntakeReceipt,
    AffectSignal,
    AffectSourcePolicySnapshot,
    AffectiveStateV3,
    AgencyDecision,
    AutonomyPolicySnapshot,
    AutonomyUsageSnapshot,
    AppraisalVectorV3,
    AttachmentRef,
    ArtifactManifest,
    ArtifactRevisionIdentity,
    AggregateStatus,
    CapabilityManifest,
    CapabilityEvidence,
    CapabilityLearningDecision,
    CircuitBreakerPolicy,
    CircuitBreakerSnapshot,
    CircuitPermission,
    CircuitUpdate,
    ChannelCutoverSnapshot,
    ChannelAckPermit,
    ChannelDrainEvidence,
    ChannelOwnershipLease,
    CapabilityProfile,
    CapabilityRollbackRecord,
    CausalEpisode,
    CausalHypothesis,
    CausalContextPack,
    CausalNodeV3,
    ComponentManifest,
    ComponentReadinessEvidence,
    CompositionExecutionBindingV1,
    ContextTokenBudget,
    DeliveryReceipt,
    DeliveryIdentity,
    DeliveryTicket,
    DynamicTimeoutPolicy,
    EmergencyKeyRevocationManifest,
    ErrorDescriptor,
    ExecutionResult,
    ExecutionTicket,
    EpisodeOutcomeEvidence,
    EffectIdentity,
    FactRecord,
    FenceDecision,
    GenerationFence,
    InboundEnvelope,
    InboundScope,
    InboundScopeKeys,
    KeyRotationManifest,
    LifeContextAuthorization,
    LifeEventEnvelope,
    LifeEventIngress,
    LifeEventIngressReceipt,
    LifeRevisionVector,
    LifeSnapshot,
    MemoryAssertionV3,
    MemoryRelationV3,
    OutboundPlan,
    OutboundScope,
    OutboundScopeKeys,
    OmniCapabilityGrant,
    ProtectedPrivateKeyEnvelope,
    ProductionInboundAcceptance,
    ProductionInboundSubmission,
    PrivacyDeletionTombstone,
    PolicyDecision,
    PublicKeyDescriptor,
    ReadinessDecision,
    ReadinessExpectation,
    ReleaseManifest,
    RedactedLogPayload,
    RedactionPolicy,
    RequestIdentity,
    RetryDecision,
    RetryPolicy,
    RunIdentity,
    ServiceAuthAssertion,
    ShadowComparison,
    ShadowDecisionObservation,
    ShadowIngressCopy,
    ShadowObservationBatch,
    SkillSelectionRecord,
    SkillActivationGrant,
    StateSnapshot,
    TaskContinuityCapsule,
    TransitionDecision,
    TransitionEvent,
    TimeoutDecision,
    TrustBundle,
    UserConfirmationGrant,
    ViabilityState,
    ViabilityObservation,
    ReflectionCard,
    ReflectionQuestionDecision,
)


def contract_schema_bundle() -> dict[str, dict[str, Any]]:
    """Return schemas ordered by stable model name."""

    return {
        model.__name__: model.model_json_schema(mode="validation")
        for model in sorted(CONTRACT_MODELS, key=lambda item: item.__name__)
    }


def contract_schema_bundle_sha256() -> str:
    """Hash the schema bundle without relying on filesystem state."""

    encoded = json.dumps(
        contract_schema_bundle(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["contract_schema_bundle", "contract_schema_bundle_sha256"]
