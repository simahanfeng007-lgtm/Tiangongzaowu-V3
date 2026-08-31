"""P19-R2 M5 §12: Deterministic Verification Repair Policy V1.

Single authoritative policy configuration. All budgets enter
policy_config_sha256; dispositions must bind this hash.

M5 §11: NO LLM involvement in the REPAIR/WAIT/RECONCILE/REVIEW/BLOCK
decision. LLM can participate in repair EXECUTION, never in deciding
WHETHER to repair.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from contracts.canonical import canonical_sha256

POLICY_VERSION = "v1"

#: §12: all numeric budgets in one frozen config.
MAX_ATTEMPTS_PER_PLAN_ENTRY = 2
MAX_TOTAL_AUTO_REPAIRS_PER_GENERATION = 4
MAX_SAME_FAILURE_SIGNATURE_REPEATS = 2
MAX_SUBJECT_SUCCESSOR_DEPTH = 4
MAX_SIDE_EFFECTING_REPAIRS_PER_ENTRY = 1

#: Store write-boundary limits for RepairDirective fields. These are
#: code-enforced at put time — a hash-valid directive cannot widen its
#: execution budget beyond what the authoritative policy allows.
MIN_REPAIR_EXECUTION_BUDGET_MS = 60_000
MAX_REPAIR_EXECUTION_BUDGET_MS = 600_000
MAX_REPAIR_EXPIRY_DELTA_MS = 3_600_000

#: §13 Repairability Matrix: predicate types eligible for auto REPAIR.
AUTO_REPAIRABLE_CONTENT_PREDICATES = frozenset({
    "artifact.nonempty",
    "artifact.min_visible_text_chars",
    "xlsx.required_columns",
    "xlsx.min_data_rows",
    "text.required_markers",
    "pptx.min_nonempty_slides",
    "csv.required_columns",
})

#: §13 B: effect postcondition predicates — REPAIR only if effect
#: is non-AMBIGUOUS and Runtime authority allows re-execution.
EFFECT_POSTCONDITION_PREDICATES = frozenset({
    "effect.target_exists",
    "effect.target_sha256_matches",
    "effect.required_change_observed",
    "effect.terminal_succeeded",
})

#: §13 C: repository predicates that CAN be auto-repaired.
REPOSITORY_REPAIRABLE_PREDICATES = frozenset({
    "repository.required_paths_changed",
})

#: §13 C: repository predicates that MUST NOT be auto-repaired.
REPOSITORY_NON_REPAIRABLE = frozenset({
    "repository.forbidden_paths_unchanged",
    "repository.source_authority_valid",
    "repository.no_generated_mirror_direct_edit",
})


@dataclasses.dataclass(frozen=True)
class RepairPolicyConfig:
    """Immutable policy config; hash covers every numeric budget."""
    max_attempts_per_plan_entry: int = MAX_ATTEMPTS_PER_PLAN_ENTRY
    max_total_auto_repairs_per_generation: int = MAX_TOTAL_AUTO_REPAIRS_PER_GENERATION
    max_same_failure_signature_repeats: int = MAX_SAME_FAILURE_SIGNATURE_REPEATS
    max_subject_successor_depth: int = MAX_SUBJECT_SUCCESSOR_DEPTH
    max_side_effecting_repairs_per_entry: int = MAX_SIDE_EFFECTING_REPAIRS_PER_ENTRY
    auto_repairable_content_predicates: frozenset = dataclasses.field(
        default_factory=lambda: AUTO_REPAIRABLE_CONTENT_PREDICATES
    )
    effect_postcondition_predicates: frozenset = dataclasses.field(
        default_factory=lambda: EFFECT_POSTCONDITION_PREDICATES
    )
    repository_repairable_predicates: frozenset = dataclasses.field(
        default_factory=lambda: REPOSITORY_REPAIRABLE_PREDICATES
    )
    repository_non_repairable: frozenset = dataclasses.field(
        default_factory=lambda: REPOSITORY_NON_REPAIRABLE
    )

    def config_payload(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "max_attempts_per_plan_entry": self.max_attempts_per_plan_entry,
            "max_total_auto_repairs_per_generation": (
                self.max_total_auto_repairs_per_generation
            ),
            "max_same_failure_signature_repeats": (
                self.max_same_failure_signature_repeats
            ),
            "max_subject_successor_depth": self.max_subject_successor_depth,
            "max_side_effecting_repairs_per_entry": (
                self.max_side_effecting_repairs_per_entry
            ),
            "auto_repairable_content_predicates": sorted(
                self.auto_repairable_content_predicates
            ),
            "effect_postcondition_predicates": sorted(
                self.effect_postcondition_predicates
            ),
            "repository_repairable_predicates": sorted(
                self.repository_repairable_predicates
            ),
            "repository_non_repairable": sorted(self.repository_non_repairable),
        }

    def config_sha256(self) -> str:
        return canonical_sha256(self.config_payload())


DEFAULT_POLICY = RepairPolicyConfig()


def evaluate_disposition(
    *,
    predicate_type: str,
    verification_status: str,
    failure_kind: str,
    attempt_no: int,
    max_attempts: int,
    same_signature_count: int,
    effect_is_ambiguous: bool = False,
    policy: RepairPolicyConfig | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Deterministic disposition: returns (action, reason_codes).

    §13 Repairability Matrix:
    - FAIL + repairable predicate + budget OK → REPAIR
    - MISSING_EVIDENCE → WAIT (not REPAIR)
    - INCONCLUSIVE → WAIT (or RECONCILE if authority issue)
    - AUTHORITY_ERROR → RECONCILE
    - PLAN_CONFIG_ERROR / RECORD_MISMATCH → REVIEW
    - AMBIGUOUS effect → RECONCILE (never auto-replay)
    - Budget exhausted → REVIEW
    - Same failure signature repeated → REVIEW
    """
    if policy is None:
        policy = DEFAULT_POLICY

    # §13 D: AMBIGUOUS → always RECONCILE
    if effect_is_ambiguous:
        return "RECONCILE", ("repair_policy.ambiguous_effect_no_replay",)

    # §13 E: MISSING → WAIT
    if failure_kind == "MISSING_EVIDENCE":
        return "WAIT", ("repair_policy.missing_evidence_wait",)

    # §13 F: INCONCLUSIVE → WAIT
    if failure_kind == "INCONCLUSIVE":
        return "WAIT", ("repair_policy.inconclusive_wait",)

    # §13 G: AUTHORITY_ERROR → RECONCILE
    if failure_kind == "AUTHORITY_ERROR":
        return "RECONCILE", ("repair_policy.authority_error_reconcile",)

    # §13 H: PLAN_CONFIG_ERROR / RECORD_MISMATCH → REVIEW
    if failure_kind in ("PLAN_CONFIG_ERROR", "RECORD_MISMATCH"):
        return "REVIEW", (f"repair_policy.{failure_kind.lower()}_review",)

    # §13 A-C: FAIL cases — check repairability
    if failure_kind == "VERIFICATION_FAILED":
        # Budget check (§27.1)
        if attempt_no >= max_attempts:
            return "REVIEW", ("repair_policy.entry_budget_exhausted",)

        # Same failure signature check (§27.3)
        if same_signature_count >= policy.max_same_failure_signature_repeats:
            return "REVIEW", ("repair_policy.same_failure_signature_repeat",)

        # §13 C: repository non-repairable
        if predicate_type in policy.repository_non_repairable:
            return "REVIEW", ("repair_policy.repository_non_repairable",)

        # §13 A: auto-repairable content predicates
        if predicate_type in policy.auto_repairable_content_predicates:
            return "REPAIR", ("repair_policy.deterministic_content_repairable",)

        # §13 B: effect postconditions (caller must also check non-AMBIGUOUS)
        if predicate_type in policy.effect_postcondition_predicates:
            return "REPAIR", ("repair_policy.effect_postcondition_repairable",)

        # §13 C: repository repairable
        if predicate_type in policy.repository_repairable_predicates:
            return "REPAIR", ("repair_policy.repository_path_repairable",)

        # Unknown predicate → conservative REVIEW
        return "REVIEW", ("repair_policy.predicate_not_in_repairability_matrix",)

    # Should not reach here, but fail-closed
    return "REVIEW", ("repair_policy.unhandled_failure_kind",)


def compute_disposition_action(
    *,
    predicate_type: str,
    verification_status: str,
    failure_kind: str,
    attempt_no: int,
    same_signature_count: int,
    successor_depth: int,
    generation_repair_count: int,
    side_effect_repair_count: int,
    subject_kind: str,
    effect_is_ambiguous: bool,
    policy: RepairPolicyConfig | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Full budget-aware decision — M5 Final #4/#5 single source of truth.

    Shared by the RepairCoordinator (decision time) and the Store v28
    disposition revalidation (write time), so a disposition can never be
    persisted unless the Store independently recomputes the same action
    from Store-derived budget counts.
    """
    if policy is None:
        policy = DEFAULT_POLICY

    base_action, base_reasons = evaluate_disposition(
        predicate_type=predicate_type,
        verification_status=verification_status,
        failure_kind=failure_kind,
        attempt_no=attempt_no,
        max_attempts=policy.max_attempts_per_plan_entry,
        same_signature_count=same_signature_count,
        effect_is_ambiguous=effect_is_ambiguous,
        policy=policy,
    )

    extra_reasons: list[str] = []
    if (
        generation_repair_count
        >= policy.max_total_auto_repairs_per_generation
    ):
        extra_reasons.append("repair_policy.generation_budget_exhausted")
    if successor_depth >= policy.max_subject_successor_depth:
        extra_reasons.append("repair_policy.successor_depth_exhausted")
    if (
        subject_kind in ("effect", "repository")
        and side_effect_repair_count
        >= policy.max_side_effecting_repairs_per_entry
    ):
        extra_reasons.append("repair_policy.side_effect_budget_exhausted")

    # M5 Final #5: any exhausted budget overrides a base REPAIR.
    if extra_reasons and base_action == "REPAIR":
        return "REVIEW", tuple(extra_reasons)
    return base_action, base_reasons


__all__ = [
    "DEFAULT_POLICY",
    "RepairPolicyConfig",
    "compute_disposition_action",
    "evaluate_disposition",
    "MAX_REPAIR_EXECUTION_BUDGET_MS",
    "MAX_REPAIR_EXPIRY_DELTA_MS",
    "MIN_REPAIR_EXECUTION_BUDGET_MS",
    "POLICY_VERSION",
]
