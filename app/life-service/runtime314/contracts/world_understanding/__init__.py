"""World Understanding contract surface (P1 only; no runtime behavior)."""
from ._base import WORLD_SCHEMA_VERSION, EpistemicState, PrivacyScope, TruthState, WorldClaim, WorldRecordRef, WorldValue
from .authority import AuthorityBinding, AuthorityDomain, AuthorizationClass
from .source import SourceKind, WorldSourceRef
from .scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from .time import WorldTime
from .observability import ObservabilityMode, ObservabilityState, compute_observability_quality_milli
from .ingress import IngressKind, WorldIngressEnvelope, derive_ingress_dedup_key, derive_ingress_envelope_id
from .known import DirectKnownRecord, DerivedKnownRecord, DerivationType, derive_direct_known_id, derive_derived_known_id
from .world_cut import SourceWatermark, WorldCut, derive_world_cut_id
from .event import WorldEvent, derive_world_event_id
from .entity import EntityResolutionCandidate, EntityResolutionState, WorldAttribute, WorldEntity, derive_entity_id, derive_entity_candidate_id
from .relation import RelationExtractionMode, RelationMaterializationClass, WorldRelation, derive_relation_id
from .hypothesis import HypothesisOrigin, WorldHypothesis
from .state import WorldState
from .prediction import PredictionOutcome, PredictionOutcomeKind, PredictionStatus, WorldPrediction, derive_prediction_id, derive_prediction_outcome_id
from .query import WorldQuery, derive_world_query_id
from .repository_query import (
    RepositoryGraphQuery,
    RepositoryGraphQueryResult,
    RepositoryQueryDirection,
    RepositoryQueryMode,
    RepositoryQueryTruncationReason,
    RepositoryTraversalDirection,
    RepositoryTraversalStep,
    derive_repository_graph_query_id,
    derive_repository_graph_result_id,
)
from .life_learning import LifeArtifactKind, LifeLearningEpistemicStatus, LifeLearningObservation, LifeLearningStatus
from .context_packet import ExpansionHandle, WorldContextItem, WorldContextPacket, derive_expansion_handle_id, derive_world_packet_id
from .curiosity import KnowledgeGap, WorldCuriosity, derive_knowledge_gap_id, derive_curiosity_id
from .inquiry import InquiryOutcome, InquiryStatus, SelfWillDecision, WorldInquiry, derive_inquiry_id, derive_inquiry_outcome_id
from .derivation import DerivationEdge, DerivationRef, derive_derivation_id, derive_derivation_edge_id
from .transform_metrics import TransformCostObservation, TransformQualityProfile
from .outputs import WorldContextOutputPort, WorldInquiryOutputPort
from .cognition_compat import CognitionStatementRef

__all__ = [
"WORLD_SCHEMA_VERSION","TruthState","EpistemicState","PrivacyScope","WorldRecordRef","WorldValue","WorldClaim",
"AuthorityBinding","AuthorityDomain","AuthorizationClass","SourceKind","WorldSourceRef","ScopeBinding","WorldScope","derive_world_id","derive_world_scope_hash","WorldTime",
"ObservabilityMode","ObservabilityState","compute_observability_quality_milli","IngressKind","WorldIngressEnvelope","derive_ingress_dedup_key","derive_ingress_envelope_id",
"DirectKnownRecord","DerivedKnownRecord","DerivationType","derive_direct_known_id","derive_derived_known_id","SourceWatermark","WorldCut","derive_world_cut_id","WorldEvent",
"WorldAttribute","WorldEntity","EntityResolutionCandidate","EntityResolutionState","derive_entity_id","WorldRelation","RelationMaterializationClass","RelationExtractionMode","derive_relation_id",
"WorldHypothesis","HypothesisOrigin","WorldState","WorldPrediction","PredictionOutcome","PredictionStatus","PredictionOutcomeKind","derive_prediction_id","WorldQuery","WorldContextPacket","WorldContextItem","ExpansionHandle",
"RepositoryGraphQuery","RepositoryGraphQueryResult","RepositoryQueryDirection","RepositoryQueryMode","RepositoryQueryTruncationReason","RepositoryTraversalDirection","RepositoryTraversalStep",
"LifeArtifactKind","LifeLearningEpistemicStatus","LifeLearningObservation","LifeLearningStatus",
"WorldCuriosity","KnowledgeGap","WorldInquiry","InquiryOutcome","InquiryStatus","SelfWillDecision","derive_inquiry_id","DerivationRef","DerivationEdge","TransformCostObservation","TransformQualityProfile",
"WorldContextOutputPort","WorldInquiryOutputPort","CognitionStatementRef","derive_world_event_id","derive_entity_candidate_id","derive_prediction_outcome_id","derive_world_query_id","derive_repository_graph_query_id","derive_repository_graph_result_id","derive_expansion_handle_id","derive_world_packet_id",
"derive_knowledge_gap_id","derive_curiosity_id","derive_inquiry_outcome_id","derive_derivation_id","derive_derivation_edge_id"]
