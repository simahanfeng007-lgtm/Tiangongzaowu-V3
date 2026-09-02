"""P4 proposal, compiler, validator, and early-evaluation core.

This package is side-effect free and non-authorizing. Execution continues to be
owned exclusively by the existing Total Gateway / Policy / Ticket / Grant /
Omni Body / Runtime / P19 chain.
"""

from .compiler import (
    analyze_composition_risk,
    compile_capability_composition_plan,
    computed_plan_sha256,
    plan_has_valid_sha256,
)
from .evaluation import (
    P4EarlyEvaluationReportV1,
    P4EvaluationCaseResultV1,
    P4EvaluationInputV1,
    P4ModelMetricsV1,
    run_p4_early_evaluation,
)
from .models import (
    ActionCandidateBindingV1,
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
    CompositionCompileContextV1,
    MAX_ACTION_CANDIDATES,
    MAX_METHOD_CANDIDATES,
    MethodCandidateBindingV1,
    build_candidate_snapshot,
    derive_action_source_revision,
    validate_registry_binding,
)
from .parser import (
    CompositionProposalParseError,
    ProposalParseOutcomeV1,
    computed_proposal_sha256,
    parse_composition_proposal,
    parse_with_single_repair,
    proposal_has_valid_sha256,
)
from .validator import (
    computed_validation_sha256,
    validate_capability_composition_plan,
    validation_has_valid_sha256,
)

__all__ = [
    "ActionCandidateBindingV1",
    "CapabilityCompositionError",
    "CompositionCandidateSnapshotV1",
    "CompositionCompileContextV1",
    "CompositionProposalParseError",
    "MAX_ACTION_CANDIDATES",
    "MAX_METHOD_CANDIDATES",
    "MethodCandidateBindingV1",
    "P4EarlyEvaluationReportV1",
    "P4EvaluationCaseResultV1",
    "P4EvaluationInputV1",
    "P4ModelMetricsV1",
    "ProposalParseOutcomeV1",
    "analyze_composition_risk",
    "build_candidate_snapshot",
    "compile_capability_composition_plan",
    "computed_plan_sha256",
    "computed_proposal_sha256",
    "computed_validation_sha256",
    "derive_action_source_revision",
    "parse_composition_proposal",
    "parse_with_single_repair",
    "plan_has_valid_sha256",
    "proposal_has_valid_sha256",
    "run_p4_early_evaluation",
    "validate_capability_composition_plan",
    "validate_registry_binding",
    "validation_has_valid_sha256",
]
