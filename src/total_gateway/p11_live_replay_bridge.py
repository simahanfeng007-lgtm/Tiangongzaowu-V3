"""Read-only P11 bridge from append-only RunObservation to Formal Shadow.

The bridge owns no client, store, policy, ticket, grant, Runtime, verifier, or
Completion authority.  It only validates already-recorded paired model output,
parses the small proposal ABI, proves the compiled Plan binds that proposal,
and returns content-addressed evidence for the P11 evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal

from contracts import canonical_json_bytes, canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionValidationResultV1,
)
from world_understanding.capability_composition import (
    CompositionCandidateSnapshotV1,
    CompositionProposalParseError,
    P11DynamicPathObservationV1,
    P11ModelProfileV1,
    P11StaticPathObservationV1,
    observe_dynamic_composition_path,
    parse_with_single_repair,
    plan_binds_proposal,
)

from .run_observation import RunObservation


P11_LIVE_REPLAY_BINDING_SCHEMA = "tiangong.p11-live-replay-binding.v1"

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class P11LiveReplayError(ValueError):
    """Fail-closed live-replay intake error with a stable code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _error(code: str, detail: str | None = None) -> None:
    raise P11LiveReplayError(code, detail)


def _require_id(value: str, field: str) -> None:
    if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
        _error("p11.live.identity_invalid", field)


def _require_sha(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _error("p11.live.sha256_invalid", field)


def _output_sha256(text: str) -> str:
    return canonical_sha256(
        {"domain": "tiangong.p11-live-model-output.v1", "text": text}
    )


def _static_output(static: P11StaticPathObservationV1) -> str:
    return canonical_json_bytes(
        {**static.payload(), "observation_sha256": static.observation_sha256}
    ).decode("utf-8")


@dataclass(frozen=True, slots=True)
class P11LiveReplayBindingV1:
    schema: str
    task_id: str
    task_input_sha256: str
    profile_sha256: str
    model_snapshot_sha256: str
    candidate_snapshot_sha256: str
    primary_observation_id: str
    primary_observation_sha256: str
    repair_observation_id: str | None
    repair_observation_sha256: str | None
    selected_attempt: Literal["PRIMARY", "REPAIR", "NONE"]
    selected_output_sha256: str | None
    legacy_output_sha256: str
    proposal_sha256: str | None
    plan_sha256: str | None
    binding_sha256: str

    def __post_init__(self) -> None:
        if self.schema != P11_LIVE_REPLAY_BINDING_SCHEMA:
            _error("p11.live.binding_schema_invalid")
        for value, field in (
            (self.task_id, "task_id"),
            (self.primary_observation_id, "primary_observation_id"),
        ):
            _require_id(value, field)
        repair = (
            self.repair_observation_id,
            self.repair_observation_sha256,
        )
        if sum(value is not None for value in repair) not in {0, 2}:
            _error("p11.live.repair_binding_incomplete")
        if self.repair_observation_id is not None:
            _require_id(self.repair_observation_id, "repair_observation_id")
        for value, field in (
            (self.task_input_sha256, "task_input"),
            (self.profile_sha256, "profile"),
            (self.model_snapshot_sha256, "model_snapshot"),
            (self.candidate_snapshot_sha256, "candidate_snapshot"),
            (self.primary_observation_sha256, "primary_observation"),
            (self.legacy_output_sha256, "legacy_output"),
            (self.binding_sha256, "binding"),
        ):
            _require_sha(value, field)
        for value, field in (
            (self.repair_observation_sha256, "repair_observation"),
            (self.selected_output_sha256, "selected_output"),
            (self.proposal_sha256, "proposal"),
            (self.plan_sha256, "plan"),
        ):
            if value is not None:
                _require_sha(value, field)
        if self.selected_attempt not in {"PRIMARY", "REPAIR", "NONE"}:
            _error("p11.live.selected_attempt_invalid")
        if (self.selected_attempt == "NONE") != (
            self.selected_output_sha256 is None
        ):
            _error("p11.live.selected_output_binding_invalid")
        if self.selected_attempt == "REPAIR" and self.repair_observation_id is None:
            _error("p11.live.selected_repair_missing")
        if self.plan_sha256 is not None and self.proposal_sha256 is None:
            _error("p11.live.plan_without_proposal")

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "task_input_sha256": self.task_input_sha256,
            "profile_sha256": self.profile_sha256,
            "model_snapshot_sha256": self.model_snapshot_sha256,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "primary_observation_id": self.primary_observation_id,
            "primary_observation_sha256": self.primary_observation_sha256,
            "repair_observation_id": self.repair_observation_id,
            "repair_observation_sha256": self.repair_observation_sha256,
            "selected_attempt": self.selected_attempt,
            "selected_output_sha256": self.selected_output_sha256,
            "legacy_output_sha256": self.legacy_output_sha256,
            "proposal_sha256": self.proposal_sha256,
            "plan_sha256": self.plan_sha256,
        }

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == canonical_sha256(self.payload())

    def with_computed_sha256(self) -> "P11LiveReplayBindingV1":
        return replace(self, binding_sha256=canonical_sha256(self.payload()))


@dataclass(frozen=True, slots=True)
class P11LiveReplayResultV1:
    binding: P11LiveReplayBindingV1
    dynamic_path: P11DynamicPathObservationV1
    result_sha256: str

    def __post_init__(self) -> None:
        if not self.binding.has_valid_sha256():
            _error("p11.live.binding_hash_invalid")
        if not self.dynamic_path.has_valid_sha256():
            _error("p11.live.dynamic_hash_invalid")
        if (
            self.dynamic_path.task_id != self.binding.task_id
            or self.dynamic_path.model.profile_sha256
            != self.binding.profile_sha256
            or self.dynamic_path.live_replay_binding_sha256
            != self.binding.binding_sha256
            or self.dynamic_path.composition_plan_sha256
            != self.binding.plan_sha256
        ):
            _error("p11.live.result_binding_invalid")
        _require_sha(self.result_sha256, "result")

    def payload(self) -> dict[str, object]:
        return {
            "binding": {
                **self.binding.payload(),
                "binding_sha256": self.binding.binding_sha256,
            },
            "dynamic_path": {
                **self.dynamic_path.payload(),
                "observation_sha256": self.dynamic_path.observation_sha256,
            },
        }

    def has_valid_sha256(self) -> bool:
        return self.result_sha256 == canonical_sha256(self.payload())

    def with_computed_sha256(self) -> "P11LiveReplayResultV1":
        return replace(self, result_sha256=canonical_sha256(self.payload()))


def _validate_observation(
    observation: RunObservation,
    *,
    observation_kind: Literal["primary", "repair"],
    task_id: str,
    task_input_sha256: str,
    model: P11ModelProfileV1,
    expected_legacy_output: str,
) -> None:
    if not observation.has_valid_sha256():
        _error("p11.live.run_observation_hash_invalid")
    if observation.gold_id != task_id or observation.input_sha256 != task_input_sha256:
        _error("p11.live.run_observation_task_drift")
    expected_model = f"{model.model_id}@{model.model_revision}"
    if (
        observation.model_snapshot.provider != model.provider_id
        or observation.model_snapshot.model != expected_model
    ):
        _error("p11.live.run_observation_model_drift")
    if observation.legacy_output != expected_legacy_output:
        _error("p11.live.run_observation_static_drift")
    if observation.candidate_output is None:
        _error(f"p11.live.{observation_kind}_output_missing")
    if observation.terminal_state != "completed":
        _error("p11.live.run_observation_not_completed", observation_kind)


def observe_p11_live_dynamic_path(
    *,
    task_id: str,
    task_input_sha256: str,
    goal_fingerprint_sha256: str,
    model: P11ModelProfileV1,
    static_path: P11StaticPathObservationV1,
    candidates: CompositionCandidateSnapshotV1,
    primary_observation: RunObservation,
    context_tokens: int,
    repair_observation: RunObservation | None = None,
    plan: CapabilityCompositionPlanV1 | None = None,
    validation: CompositionValidationResultV1 | None = None,
    shadow: object | None = None,
    plan_error_code: str | None = None,
) -> P11LiveReplayResultV1:
    """Bind a live paired output to P11 without executing either path."""

    _require_id(task_id, "task_id")
    _require_sha(task_input_sha256, "task_input")
    _require_sha(goal_fingerprint_sha256, "goal_fingerprint")
    if model.provider_id.startswith("recorded.") or model.model_id.startswith(
        "recorded."
    ):
        _error("p11.live.recorded_profile_forbidden")
    if not model.has_valid_sha256():
        _error("p11.live.model_profile_hash_invalid")
    if not candidates.has_valid_sha256():
        _error("p11.live.candidate_snapshot_hash_invalid")
    if (
        not static_path.has_valid_sha256()
        or static_path.task_id != task_id
        or static_path.query_sha256 != task_input_sha256
    ):
        _error("p11.live.static_path_binding_invalid")

    legacy_output = _static_output(static_path)
    _validate_observation(
        primary_observation,
        observation_kind="primary",
        task_id=task_id,
        task_input_sha256=task_input_sha256,
        model=model,
        expected_legacy_output=legacy_output,
    )
    if repair_observation is not None:
        _validate_observation(
            repair_observation,
            observation_kind="repair",
            task_id=task_id,
            task_input_sha256=task_input_sha256,
            model=model,
            expected_legacy_output=legacy_output,
        )
        if (
            repair_observation.cohort_id != primary_observation.cohort_id
            or repair_observation.pair_key != primary_observation.pair_key
            or repair_observation.created_at_ms
            < primary_observation.created_at_ms
        ):
            _error("p11.live.repair_observation_drift")

    primary_text = primary_observation.candidate_output
    repair_text = (
        None if repair_observation is None else repair_observation.candidate_output
    )
    proposal = None
    parse_error_code: str | None = None
    repaired = False
    if primary_text is None:
        if repair_observation is not None:
            _error("p11.live.repair_without_primary_output")
        parse_error_code = "p11.live.primary_output_missing"
    else:
        try:
            outcome = parse_with_single_repair(
                primary_text,
                candidates,
                repair_text=repair_text,
            )
            proposal = outcome.proposal
            repaired = outcome.repaired
            if repair_observation is not None and not repaired:
                _error("p11.live.unnecessary_repair_observation")
        except CompositionProposalParseError as exc:
            parse_error_code = exc.code

    if proposal is None and plan is not None:
        _error("p11.live.plan_without_parsed_proposal")
    if plan is not None:
        if (
            plan.goal_fingerprint != goal_fingerprint_sha256
            or not plan_binds_proposal(plan, proposal)  # type: ignore[arg-type]
        ):
            _error("p11.live.plan_proposal_binding_invalid")
    final_error = parse_error_code
    if proposal is not None and plan is None:
        final_error = plan_error_code
        if final_error is None:
            _error("p11.live.plan_error_code_missing")

    selected_attempt: Literal["PRIMARY", "REPAIR", "NONE"]
    selected_text: str | None
    if repair_observation is not None and repair_text is not None:
        selected_attempt, selected_text = "REPAIR", repair_text
    elif primary_text is not None:
        selected_attempt, selected_text = "PRIMARY", primary_text
    else:
        selected_attempt, selected_text = "NONE", None

    model_snapshot_sha256 = canonical_sha256(
        primary_observation.model_snapshot.model_dump(mode="json")
    )
    binding = P11LiveReplayBindingV1(
        schema=P11_LIVE_REPLAY_BINDING_SCHEMA,
        task_id=task_id,
        task_input_sha256=task_input_sha256,
        profile_sha256=model.profile_sha256,
        model_snapshot_sha256=model_snapshot_sha256,
        candidate_snapshot_sha256=candidates.candidate_snapshot_sha256,
        primary_observation_id=primary_observation.observation_id,
        primary_observation_sha256=primary_observation.record_sha256,
        repair_observation_id=(
            None if repair_observation is None else repair_observation.observation_id
        ),
        repair_observation_sha256=(
            None if repair_observation is None else repair_observation.record_sha256
        ),
        selected_attempt=selected_attempt,
        selected_output_sha256=(
            None if selected_text is None else _output_sha256(selected_text)
        ),
        legacy_output_sha256=_output_sha256(legacy_output),
        proposal_sha256=(
            None if proposal is None else proposal.proposal_sha256
        ),
        plan_sha256=None if plan is None else plan.plan_sha256,
        binding_sha256="0" * 64,
    ).with_computed_sha256()

    dynamic = observe_dynamic_composition_path(
        task_id=task_id,
        model=model,
        context_tokens=context_tokens,
        parse_succeeded=proposal is not None,
        repair_attempted=repair_observation is not None,
        repaired=repaired,
        error_code=final_error,
        live_replay_binding_sha256=binding.binding_sha256,
        goal_fingerprint_sha256=goal_fingerprint_sha256,
        plan=plan,
        validation=validation,
        shadow=shadow,  # type: ignore[arg-type]
    )
    return P11LiveReplayResultV1(
        binding=binding,
        dynamic_path=dynamic,
        result_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "P11_LIVE_REPLAY_BINDING_SCHEMA",
    "P11LiveReplayBindingV1",
    "P11LiveReplayError",
    "P11LiveReplayResultV1",
    "observe_p11_live_dynamic_path",
]
