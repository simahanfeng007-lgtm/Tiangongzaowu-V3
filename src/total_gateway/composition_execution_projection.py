"""Pure P7D.2 projection for a sealed composition DAG.

This module deliberately owns no scheduler and performs no writes.  It derives
the next executable step only from the immutable executable plan plus exact
Effect/Fact observations supplied by the existing Gateway authorities.  In
particular it never reads or writes the regenerative execution frontier.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any, Callable, Literal, Mapping

from contracts import canonical_json_bytes, canonical_sha256

from .composition_activation_adapter import MaterializedCompositionStepV1
from .composition_backend_transport import COMPOSITION_RESULT_PAYLOAD_SCHEMA
from .composition_executable_plan import (
    ExecutableCompositionPlanV1,
    LiteralValueBindingV1,
    OutputDeclarationV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
)
from .composition_execution_binding import (
    COMPOSITION_STEP_PIPELINE_VERSION,
    CompositionExecutionBindingError,
    derive_run_sequence,
)
from .fact_ledger import FactBatchRecord
from .object_store import ObjectReference


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_EFFECT_ID = re.compile(r"^eff_[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_ATTEMPTS = 2
_MAX_MATERIALIZED_ARGUMENT_BYTES = 1_048_576
_MISSING = object()


CompositionStepProjectionState = Literal[
    "WAITING_DEPENDENCIES",
    "READY_UNAUTHORIZED",
    "READY_AUTHORIZED",
    "CLAIMED_PRESTART",
    "STARTED_RECOVERABLE",
    "STARTED_RECONCILE",
    "SUCCEEDED",
    "FAILED_FINAL",
    "RECONCILE_REQUIRED",
]


class CompositionExecutionProjectionError(ValueError):
    """Fail-closed projection/materialization error with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(code: str) -> None:
    raise CompositionExecutionProjectionError(code)


@dataclass(frozen=True, slots=True)
class CompositionAttemptObservationV1:
    """Detached read-model for one persisted authorization attempt.

    ``effect`` is an ``EffectLedgerRecord``-shaped value.  Keeping that type
    structural avoids importing the Store (and therefore any write surface)
    into the projection module.
    """

    authorization_id: str
    step_id: str
    attempt: int
    prebound_effect_id: str
    supersedes_authorization_id: str | None = None
    supersedes_effect_id: str | None = None
    supersedes_claim_sha256: str | None = None
    effect: Any | None = None
    fact_batch: FactBatchRecord | None = None
    result_payload: Any = _MISSING
    output_object_references: tuple[ObjectReference, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.authorization_id, self.step_id):
            if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
                _error("composition.projection.observation_identity_invalid")
        if (
            type(self.attempt) is not int
            or not 1 <= self.attempt <= _MAX_ATTEMPTS
            or _EFFECT_ID.fullmatch(self.prebound_effect_id) is None
        ):
            _error("composition.projection.observation_attempt_invalid")
        predecessor = (
            self.supersedes_authorization_id,
            self.supersedes_effect_id,
            self.supersedes_claim_sha256,
        )
        if self.attempt == 1:
            if any(value is not None for value in predecessor):
                _error("composition.projection.first_attempt_has_predecessor")
        elif (
            any(value is None for value in predecessor)
            or _OPAQUE_ID.fullmatch(str(self.supersedes_authorization_id)) is None
            or _EFFECT_ID.fullmatch(str(self.supersedes_effect_id)) is None
            or _SHA256.fullmatch(str(self.supersedes_claim_sha256)) is None
        ):
            _error("composition.projection.successor_predecessor_invalid")
        if self.fact_batch is not None and self.effect is None:
            _error("composition.projection.fact_without_effect")


@dataclass(frozen=True, slots=True)
class CompositionDependencyEvidenceV1:
    """Canonical evidence persisted with a downstream authorization receipt."""

    producer_step_id: str
    step_binding_sha256: str
    authorization_id: str
    attempt: int
    effect_id: str
    claim_sha256: str
    effect_result_sha256: str
    execution_result_sha256: str
    fact_batch_sha256: str
    fact_ids: tuple[str, ...]
    fact_sha256s: tuple[str, ...]
    result_payload_object_id: str
    result_payload_sha256: str
    action_result_sha256: str
    output_object_refs: tuple[str, ...]
    output_object_reference_sha256s: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "tiangong.composition-dependency-evidence.v1",
            "producer_step_id": self.producer_step_id,
            "step_binding_sha256": self.step_binding_sha256,
            "authorization_id": self.authorization_id,
            "attempt": self.attempt,
            "effect_id": self.effect_id,
            "claim_sha256": self.claim_sha256,
            "effect_result_sha256": self.effect_result_sha256,
            "execution_result_sha256": self.execution_result_sha256,
            "fact_batch_sha256": self.fact_batch_sha256,
            "fact_ids": self.fact_ids,
            "fact_sha256s": self.fact_sha256s,
            "result_payload_object_id": self.result_payload_object_id,
            "result_payload_sha256": self.result_payload_sha256,
            "action_result_sha256": self.action_result_sha256,
            "output_object_refs": self.output_object_refs,
            "output_object_reference_sha256s": (
                self.output_object_reference_sha256s
            ),
        }

    @property
    def evidence_sha256(self) -> str:
        return canonical_sha256(self.payload())


@dataclass(frozen=True, slots=True)
class CompositionStepProjectionV1:
    step_id: str
    state: CompositionStepProjectionState
    current_attempt: int | None
    authorization_id: str | None
    effect_id: str | None
    fact_ids: tuple[str, ...]
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class CompositionExecutionProjectionV1:
    executable_plan_id: str
    steps: tuple[CompositionStepProjectionV1, ...]
    next_step_id: str | None
    recoverable_step_ids: tuple[str, ...]
    leaf_step_ids: tuple[str, ...]
    leaf_effect_ids: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    reconcile_step_ids: tuple[str, ...]
    all_steps_succeeded: bool

    def by_step_id(self) -> dict[str, CompositionStepProjectionV1]:
        return {item.step_id: item for item in self.steps}


@dataclass(frozen=True, slots=True)
class MaterializedCompositionDispatchV1:
    step: MaterializedCompositionStepV1
    dependency_evidence: tuple[CompositionDependencyEvidenceV1, ...]
    dependency_evidence_sha256: str


def _json_pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _error("composition.projection.json_pointer_invalid")
    result: list[str] = []
    for raw in pointer[1:].split("/"):
        token = ""
        index = 0
        while index < len(raw):
            character = raw[index]
            if character != "~":
                token += character
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                _error("composition.projection.json_pointer_invalid")
            token += "~" if raw[index + 1] == "0" else "/"
            index += 2
        result.append(token)
    return tuple(result)


def _json_pointer_value(value: Any, pointer: str) -> Any:
    current = value
    for token in _json_pointer_tokens(pointer):
        if isinstance(current, Mapping):
            if token not in current:
                _error("composition.projection.output_pointer_missing")
            current = current[token]
            continue
        if isinstance(current, list):
            if (
                token == "-"
                or not token.isascii()
                or not token.isdecimal()
                or (len(token) > 1 and token.startswith("0"))
            ):
                _error("composition.projection.output_pointer_invalid")
            ordinal = int(token)
            if ordinal >= len(current):
                _error("composition.projection.output_pointer_missing")
            current = current[ordinal]
            continue
        _error("composition.projection.output_pointer_missing")
    return deepcopy(current)


def _replace_json_pointer(root: dict[str, Any], pointer: str, value: Any) -> None:
    tokens = _json_pointer_tokens(pointer)
    if not tokens:
        _error("composition.projection.argument_pointer_invalid")
    current: Any = root
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                _error("composition.projection.argument_pointer_missing")
            current = current[token]
            continue
        if isinstance(current, list):
            if (
                token == "-"
                or not token.isascii()
                or not token.isdecimal()
                or (len(token) > 1 and token.startswith("0"))
            ):
                _error("composition.projection.argument_pointer_invalid")
            ordinal = int(token)
            if ordinal >= len(current):
                _error("composition.projection.argument_pointer_missing")
            current = current[ordinal]
            continue
        _error("composition.projection.argument_pointer_missing")
    final = tokens[-1]
    if isinstance(current, dict):
        if final not in current or current[final] is not None:
            _error("composition.projection.argument_slot_not_null")
        current[final] = deepcopy(value)
        return
    if isinstance(current, list):
        if (
            final == "-"
            or not final.isascii()
            or not final.isdecimal()
            or (len(final) > 1 and final.startswith("0"))
        ):
            _error("composition.projection.argument_pointer_invalid")
        ordinal = int(final)
        if ordinal >= len(current):
            _error("composition.projection.argument_pointer_missing")
        if current[ordinal] is not None:
            _error("composition.projection.argument_slot_not_null")
        current[ordinal] = deepcopy(value)
        return
    _error("composition.projection.argument_pointer_missing")


def _effect_matches_step(
    plan: ExecutableCompositionPlanV1,
    step: StepExecutionBindingV1,
    observation: CompositionAttemptObservationV1,
) -> bool:
    effect = observation.effect
    if effect is None:
        return observation.fact_batch is None
    claim = getattr(effect, "claim", None)
    try:
        run_sequence = derive_run_sequence(plan.request_id, plan.run_id)
        ordinal = next(
            index + 1
            for index, item in enumerate(plan.step_bindings)
            if item.step_id == step.step_id
        )
    except (CompositionExecutionBindingError, StopIteration):
        return False
    expected_supersedes = (
        None
        if observation.attempt == 1
        else observation.supersedes_claim_sha256
    )
    if not bool(
        claim is not None
        and callable(getattr(claim, "has_valid_sha256", None))
        and claim.has_valid_sha256()
        and claim.effect_id == observation.prebound_effect_id
        and claim.request_id == plan.request_id
        and claim.run_id == plan.run_id
        and claim.run_sequence == run_sequence
        and claim.generation == plan.generation
        and claim.effect_kind == "execution"
        and claim.ordinal == ordinal
        and claim.pipeline_version == COMPOSITION_STEP_PIPELINE_VERSION
        and claim.attempt == observation.attempt
        and claim.claim_revision == observation.attempt
        and claim.supersedes_claim_sha256 == expected_supersedes
        and claim.owner_component_id == "tiangong-backend"
    ):
        return False
    state = getattr(effect, "state", None)
    started = getattr(effect, "side_effect_started_at_ms", None)
    completed = getattr(effect, "completed_at_ms", None)
    terminal = getattr(effect, "result", None)
    if state == "CLAIMED":
        return started is None and completed is None and terminal is None
    if state == "SIDE_EFFECT_STARTED":
        return (
            isinstance(started, int)
            and started >= claim.claimed_at_ms
            and completed is None
            and terminal is None
        )
    if state in {"SUCCEEDED", "FAILED_FINAL", "AMBIGUOUS", "RECONCILED"}:
        return bool(
            isinstance(completed, int)
            and completed >= (started or claim.claimed_at_ms)
            and terminal is not None
            and terminal.effect_id == claim.effect_id
            and terminal.status == state
        )
    return False


def _fact_batch_digest(batch: FactBatchRecord) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.gateway.execution-fact-batch.v1",
            "result_sha256": canonical_sha256(
                batch.result.model_dump(mode="json")
            ),
            "fact_sha256s": tuple(item.fact_sha256 for item in batch.facts),
            "source_component_id": batch.source_component_id,
            "observed_at_ms": batch.observed_at_ms,
            "tenant_id": batch.tenant_id,
            "link_account_id": batch.link_account_id,
            "conversation_scope_hash": batch.conversation_scope_hash,
            "workspace_id": batch.workspace_id,
            "max_output_bytes": batch.max_output_bytes,
            "result_payload_object_id": batch.result_payload_object_id,
            "result_payload_sha256": batch.result_payload_sha256,
            "response_sha256": batch.response_sha256,
        }
    )


def _fact_batch_reason(
    plan: ExecutableCompositionPlanV1,
    step: StepExecutionBindingV1,
    observation: CompositionAttemptObservationV1,
    *,
    validate_result: Callable[[str, str, str, Any], None],
) -> str | None:
    batch = observation.fact_batch
    if batch is None:
        return "composition.projection.success_fact_missing"
    result = batch.result
    facts = batch.facts
    if (
        batch.batch_sha256 != _fact_batch_digest(batch)
        or batch.source_component_id != "tiangong-backend"
        or batch.workspace_id != plan.workspace.workspace_id
        or batch.observed_at_ms < result.finished_at_ms
        or batch.result_payload_sha256 != result.result_payload_sha256
        or not facts
        or tuple(item.fact_id for item in facts) != result.fact_ids
    ):
        return "composition.projection.fact_batch_mismatch"
    if (
        result.effect_id != observation.prebound_effect_id
        or result.request_id != plan.request_id
        or result.run_id != plan.run_id
        or result.generation != plan.generation
        or result.action_id != step.action_id
        or result.action_version != step.action_version
        or result.attempt != observation.attempt
        or result.status
        not in {
            "SUCCEEDED",
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
            "AMBIGUOUS",
            "CANCELLED",
            "FENCED",
        }
    ):
        return "composition.projection.fact_batch_mismatch"
    expected_fact_type = {
        "SUCCEEDED": "execution.succeeded",
        "FAILED_RETRYABLE": "execution.failed",
        "FAILED_FINAL": "execution.failed",
        "AMBIGUOUS": "execution.ambiguous",
        "CANCELLED": "execution.cancelled",
        "FENCED": "execution.fenced",
    }[result.status]
    if any(
        not fact.has_valid_sha256()
        or fact.fact_type != expected_fact_type
        or fact.source_component_id != batch.source_component_id
        or fact.request_id != result.request_id
        or fact.run_id != result.run_id
        or fact.generation != result.generation
        or fact.ticket_id != result.ticket_id
        or fact.effect_id != result.effect_id
        or fact.action_id != result.action_id
        or fact.action_version != result.action_version
        or fact.observed_at_ms != batch.observed_at_ms
        or fact.payload_sha256 != result.result_payload_sha256
        or fact.evidence_sha256 != batch.response_sha256
        for fact in facts
    ):
        return "composition.projection.fact_lineage_mismatch"
    if observation.result_payload is _MISSING:
        return "composition.projection.result_payload_unavailable"
    try:
        payload_bytes = canonical_json_bytes(observation.result_payload)
    except (TypeError, ValueError, RecursionError, OverflowError):
        return "composition.projection.result_payload_invalid"
    if (
        canonical_sha256(observation.result_payload)
        != result.result_payload_sha256
        or len(payload_bytes) > batch.max_output_bytes
    ):
        return "composition.projection.result_payload_mismatch"
    references = observation.output_object_references
    if tuple(item.object_id for item in references) != result.output_object_refs:
        return "composition.projection.output_object_set_mismatch"
    if any(
        not item.has_valid_sha256()
        or item.tenant_id != batch.tenant_id
        or item.link_account_id != batch.link_account_id
        or item.conversation_scope_hash != batch.conversation_scope_hash
        for item in references
    ):
        return "composition.projection.output_object_scope_mismatch"
    if result.status == "SUCCEEDED":
        try:
            _validated_action_result(
                observation,
                step=step,
                validate_result=validate_result,
            )
        except CompositionExecutionProjectionError as exc:
            return exc.code
    return None


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _error("composition.projection.action_result_json_invalid")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _error("composition.projection.action_result_json_invalid")


def _validated_action_result(
    observation: CompositionAttemptObservationV1,
    *,
    step: StepExecutionBindingV1,
    validate_result: Callable[[str, str, str, Any], None],
) -> Any:
    payload = observation.result_payload
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "backend_http_status",
        "backend_response_sha256",
        "execution_boundary",
        "omni_ok",
        "omni_result_json",
        "omni_result_sha256",
        "omni_result_size_bytes",
    }:
        _error("composition.projection.action_result_envelope_invalid")
    raw_json = payload.get("omni_result_json")
    if not isinstance(raw_json, str):
        _error("composition.projection.action_result_envelope_invalid")
    try:
        raw_bytes = raw_json.encode("utf-8", errors="strict")
        raw = json.loads(
            raw_json,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
        canonical = json.dumps(
            raw,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except CompositionExecutionProjectionError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise CompositionExecutionProjectionError(
            "composition.projection.action_result_json_invalid"
        ) from exc
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if (
        canonical != raw_bytes
        or not isinstance(raw, dict)
        or payload.get("schema") != COMPOSITION_RESULT_PAYLOAD_SCHEMA
        or type(payload.get("backend_http_status")) is not int
        or not 200 <= payload["backend_http_status"] < 400
        or payload.get("execution_boundary")
        != "embedded-omni-body-composition-v1"
        or payload.get("omni_ok") is not True
        or payload.get("omni_result_size_bytes") != len(raw_bytes)
        or payload.get("omni_result_sha256") != digest
        or payload.get("backend_response_sha256") != digest
    ):
        _error("composition.projection.action_result_envelope_invalid")
    try:
        validate_result(
            step.action_id,
            step.action_version,
            step.result_schema_sha256,
            raw,
        )
    except CompositionExecutionProjectionError:
        raise
    except Exception as exc:
        raise CompositionExecutionProjectionError(
            "composition.projection.action_result_schema_rejected"
        ) from exc
    return raw


def _success_evidence_reason(
    plan: ExecutableCompositionPlanV1,
    step: StepExecutionBindingV1,
    observation: CompositionAttemptObservationV1,
    *,
    validate_result: Callable[[str, str, str, Any], None],
) -> str | None:
    effect = observation.effect
    batch = observation.fact_batch
    if effect is None or batch is None:
        return "composition.projection.success_fact_missing"
    claim = effect.claim
    terminal = getattr(effect, "result", None)
    result = batch.result
    reason = _fact_batch_reason(
        plan, step, observation, validate_result=validate_result
    )
    if reason is not None:
        return reason
    if (
        getattr(effect, "state", None) != "SUCCEEDED"
        or terminal is None
        or not terminal.has_valid_sha256()
        or terminal.status != "SUCCEEDED"
        or terminal.effect_id != claim.effect_id
        or terminal.result_id != "effect-result-" + result.result_id[:120]
        or terminal.fact_id != result.fact_ids[0]
        or terminal.result_object_id != batch.result_payload_object_id
        or terminal.result_object_sha256 != batch.result_payload_sha256
        or terminal.evidence_sha256 != batch.batch_sha256
        or terminal.observed_at_ms < batch.observed_at_ms
    ):
        return "composition.projection.effect_result_mismatch"
    if result.effect_id != claim.effect_id or result.status != "SUCCEEDED":
        return "composition.projection.fact_batch_mismatch"
    return None


def _dependency_evidence(
    step: StepExecutionBindingV1,
    observation: CompositionAttemptObservationV1,
) -> CompositionDependencyEvidenceV1:
    if observation.effect is None or observation.fact_batch is None:
        _error("composition.projection.dependency_not_committed")
    effect = observation.effect
    batch = observation.fact_batch
    if effect.result is None:
        _error("composition.projection.dependency_not_committed")
    payload = observation.result_payload
    if (
        not isinstance(payload, dict)
        or _SHA256.fullmatch(str(payload.get("omni_result_sha256"))) is None
    ):
        _error("composition.projection.action_result_envelope_invalid")
    return CompositionDependencyEvidenceV1(
        producer_step_id=step.step_id,
        step_binding_sha256=step.sha256,
        authorization_id=observation.authorization_id,
        attempt=observation.attempt,
        effect_id=effect.claim.effect_id,
        claim_sha256=effect.claim.claim_sha256,
        effect_result_sha256=effect.result.result_sha256,
        execution_result_sha256=canonical_sha256(
            batch.result.model_dump(mode="json")
        ),
        fact_batch_sha256=batch.batch_sha256,
        fact_ids=tuple(item.fact_id for item in batch.facts),
        fact_sha256s=tuple(item.fact_sha256 for item in batch.facts),
        result_payload_object_id=batch.result_payload_object_id,
        result_payload_sha256=batch.result_payload_sha256,
        action_result_sha256=str(payload["omni_result_sha256"]),
        output_object_refs=batch.result.output_object_refs,
        output_object_reference_sha256s=tuple(
            item.reference_sha256
            for item in observation.output_object_references
        ),
    )


def _current_attempts(
    plan: ExecutableCompositionPlanV1,
    observations: tuple[CompositionAttemptObservationV1, ...],
) -> dict[str, CompositionAttemptObservationV1]:
    step_ids = {item.step_id for item in plan.step_bindings}
    grouped: dict[str, list[CompositionAttemptObservationV1]] = {
        step_id: [] for step_id in step_ids
    }
    seen: set[tuple[str, int]] = set()
    for observation in observations:
        if observation.step_id not in grouped:
            _error("composition.projection.unknown_step_observation")
        key = (observation.step_id, observation.attempt)
        if key in seen:
            _error("composition.projection.duplicate_attempt")
        seen.add(key)
        grouped[observation.step_id].append(observation)

    current: dict[str, CompositionAttemptObservationV1] = {}
    for step in plan.step_bindings:
        chain = sorted(grouped[step.step_id], key=lambda item: item.attempt)
        if not chain:
            continue
        if tuple(item.attempt for item in chain) != tuple(
            range(1, len(chain) + 1)
        ):
            _error("composition.projection.attempt_chain_gap")
        for previous, successor in zip(chain, chain[1:], strict=False):
            predecessor_result = (
                None if previous.effect is None else previous.effect.result
            )
            supersession_evidence = canonical_sha256(
                {
                    "domain": "tiangong.composition-prestart-supersession.v1",
                    "predecessor_authorization_id": previous.authorization_id,
                    "predecessor_effect_id": previous.prebound_effect_id,
                    "predecessor_claim_sha256": (
                        None
                        if previous.effect is None
                        else previous.effect.claim.claim_sha256
                    ),
                    "successor_authorization_id": successor.authorization_id,
                    "successor_effect_id": successor.prebound_effect_id,
                    "superseded_at_ms": (
                        None
                        if predecessor_result is None
                        else predecessor_result.observed_at_ms
                    ),
                    "handler_count": 0,
                    "fact_ledger_atomicity_claimed": False,
                }
            )
            if (
                successor.supersedes_authorization_id
                != previous.authorization_id
                or successor.supersedes_effect_id
                != previous.prebound_effect_id
                or previous.effect is None
                or not _effect_matches_step(plan, step, previous)
                or successor.supersedes_claim_sha256
                != previous.effect.claim.claim_sha256
                or previous.effect.state != "FAILED_FINAL"
                or previous.effect.side_effect_started_at_ms is not None
                or previous.fact_batch is not None
                or predecessor_result is None
                or not predecessor_result.has_valid_sha256()
                or predecessor_result.status != "FAILED_FINAL"
                or predecessor_result.error_code
                != "composition.authorization.prestart_superseded"
                or predecessor_result.result_id
                != "composition-prestart-superseded-"
                + previous.authorization_id
                or predecessor_result.fact_id
                != "composition-prestart-disposition-"
                + previous.authorization_id
                or predecessor_result.result_object_id is not None
                or predecessor_result.result_object_sha256 is not None
                or predecessor_result.evidence_sha256 != supersession_evidence
            ):
                _error("composition.projection.attempt_chain_invalid")
        current[step.step_id] = chain[-1]
    return current


def derive_composition_execution_projection(
    plan: ExecutableCompositionPlanV1,
    observations: tuple[CompositionAttemptObservationV1, ...],
    *,
    validate_result: Callable[[str, str, str, Any], None],
) -> CompositionExecutionProjectionV1:
    """Derive the deterministic DAG frontier without persisting a checkpoint."""

    if (
        not isinstance(plan, ExecutableCompositionPlanV1)
        or not plan.has_valid_identity()
        or not callable(validate_result)
    ):
        _error("composition.projection.plan_invalid")
    current = _current_attempts(plan, observations)
    intrinsic: list[CompositionStepProjectionV1] = []

    for step in plan.step_bindings:
        observation = current.get(step.step_id)
        if observation is None:
            intrinsic.append(
                CompositionStepProjectionV1(
                    step.step_id,
                    "WAITING_DEPENDENCIES",
                    None,
                    None,
                    None,
                    (),
                )
            )
            continue

        if not _effect_matches_step(plan, step, observation):
            state = "RECONCILE_REQUIRED"
            reason = "composition.projection.effect_claim_mismatch"
        elif observation.effect is None:
            state = "READY_AUTHORIZED"
            reason = None
        else:
            effect = observation.effect
            fact = observation.fact_batch
            if effect.state == "CLAIMED":
                state = (
                    "CLAIMED_PRESTART"
                    if fact is None and effect.result is None
                    else "RECONCILE_REQUIRED"
                )
                reason = (
                    None
                    if state == "CLAIMED_PRESTART"
                    else "composition.projection.fact_before_start"
                )
            elif effect.state == "SIDE_EFFECT_STARTED":
                if effect.result is not None:
                    state = "RECONCILE_REQUIRED"
                    reason = "composition.projection.started_has_terminal_result"
                elif fact is None:
                    state = "STARTED_RECONCILE"
                    reason = "composition.projection.started_fact_missing"
                else:
                    reason = _fact_batch_reason(
                        plan,
                        step,
                        observation,
                        validate_result=validate_result,
                    )
                    state = (
                        "STARTED_RECOVERABLE"
                        if reason is None
                        else "STARTED_RECONCILE"
                    )
            elif effect.state == "SUCCEEDED":
                reason = _success_evidence_reason(
                    plan,
                    step,
                    observation,
                    validate_result=validate_result,
                )
                state = "SUCCEEDED" if reason is None else "RECONCILE_REQUIRED"
            elif effect.state == "FAILED_FINAL":
                if effect.result is None or not effect.result.has_valid_sha256():
                    state = "RECONCILE_REQUIRED"
                    reason = "composition.projection.failed_effect_invalid"
                elif fact is not None:
                    batch_reason = _fact_batch_reason(
                        plan,
                        step,
                        observation,
                        validate_result=validate_result,
                    )
                    expected = _effect_result_projection(fact)
                    expected = replace(
                        expected,
                        observed_at_ms=effect.result.observed_at_ms,
                        result_sha256="0" * 64,
                    ).with_computed_sha256()
                    if (
                        batch_reason is not None
                        or expected.status != "FAILED_FINAL"
                        or expected != effect.result
                    ):
                        state = "RECONCILE_REQUIRED"
                        reason = (
                            batch_reason
                            or "composition.projection.failed_effect_fact_mismatch"
                        )
                    else:
                        state = "FAILED_FINAL"
                        reason = effect.result.error_code
                elif (
                    effect.result.result_object_id is not None
                    or effect.result.result_object_sha256 is not None
                    or effect.result.error_code is None
                    or effect.result.result_id
                    != "effect-result-" + observation.prebound_effect_id[4:20]
                    or effect.result.fact_id
                    != "fact-effect-" + observation.prebound_effect_id[4:20]
                    or effect.result.evidence_sha256
                    != canonical_sha256(
                        {
                            "authorization_id": observation.authorization_id,
                            "code": effect.result.error_code,
                            "status": "FAILED_FINAL",
                        }
                    )
                ):
                    state = "RECONCILE_REQUIRED"
                    reason = "composition.projection.failed_effect_fact_missing"
                else:
                    state = "FAILED_FINAL"
                    reason = effect.result.error_code
            else:
                state = "RECONCILE_REQUIRED"
                reason = "composition.projection.terminal_state_ambiguous"

        facts = (
            ()
            if observation.fact_batch is None
            else tuple(item.fact_id for item in observation.fact_batch.facts)
        )
        intrinsic.append(
            CompositionStepProjectionV1(
                step.step_id,
                state,
                observation.attempt,
                observation.authorization_id,
                observation.prebound_effect_id,
                facts,
                reason,
            )
        )

    # A sealed plan is an acyclic graph, but its canonical tuple is not required
    # to be a topological ordering for dependency-only edges. Establish the set
    # of valid successes as a graph fixed point before deriving any readiness.
    # This also prevents a succeeded descendant from laundering a missing or
    # invalid predecessor merely because it appeared earlier in the tuple.
    intrinsic_by_step = {item.step_id: item for item in intrinsic}
    valid_succeeded: set[str] = set()
    while True:
        newly_succeeded = {
            step.step_id
            for step in plan.step_bindings
            if intrinsic_by_step[step.step_id].state == "SUCCEEDED"
            and all(item in valid_succeeded for item in step.depends_on)
        }
        expanded = valid_succeeded | newly_succeeded
        if expanded == valid_succeeded:
            break
        valid_succeeded = expanded

    projected: list[CompositionStepProjectionV1] = []
    for step in plan.step_bindings:
        item = intrinsic_by_step[step.step_id]
        dependencies_ready = all(
            dependency in valid_succeeded for dependency in step.depends_on
        )
        observation = current.get(step.step_id)
        if observation is None:
            item = replace(
                item,
                state=(
                    "READY_UNAUTHORIZED"
                    if dependencies_ready
                    else "WAITING_DEPENDENCIES"
                ),
            )
        elif not dependencies_ready and item.state not in {
            "FAILED_FINAL",
            "STARTED_RECONCILE",
            "RECONCILE_REQUIRED",
        }:
            item = replace(
                item,
                state="RECONCILE_REQUIRED",
                reason_code="composition.projection.authorized_before_dependencies",
            )
        projected.append(item)

    outgoing = {
        dependency
        for step in plan.step_bindings
        for dependency in step.depends_on
    }
    leaf_step_ids = tuple(
        step.step_id for step in plan.step_bindings if step.step_id not in outgoing
    )
    by_step = {item.step_id: item for item in projected}
    leaf_effect_ids = tuple(
        by_step[step_id].effect_id
        for step_id in leaf_step_ids
        if by_step[step_id].state == "SUCCEEDED"
        and by_step[step_id].effect_id is not None
    )
    actionable = {
        "READY_UNAUTHORIZED", "READY_AUTHORIZED", "CLAIMED_PRESTART"
    }
    recoverable = tuple(
        item.step_id for item in projected if item.state == "STARTED_RECOVERABLE"
    )
    failed = tuple(
        item.step_id for item in projected if item.state == "FAILED_FINAL"
    )
    reconcile = tuple(
        item.step_id
        for item in projected
        if item.state in {"STARTED_RECONCILE", "RECONCILE_REQUIRED"}
    )
    next_step_id = (
        None
        if failed or reconcile or recoverable
        else next(
            (item.step_id for item in projected if item.state in actionable),
            None,
        )
    )
    all_succeeded = len(valid_succeeded) == len(plan.step_bindings)
    return CompositionExecutionProjectionV1(
        executable_plan_id=plan.executable_plan_id,
        steps=tuple(projected),
        next_step_id=next_step_id,
        recoverable_step_ids=recoverable,
        leaf_step_ids=leaf_step_ids,
        leaf_effect_ids=leaf_effect_ids,
        failed_step_ids=failed,
        reconcile_step_ids=reconcile,
        all_steps_succeeded=all_succeeded,
    )


def _effect_result_projection(batch: FactBatchRecord) -> Any:
    """Build the exact Gateway terminal projection without importing Store."""

    from .effects import EffectResult

    result = batch.result
    status = (
        "SUCCEEDED"
        if result.status == "SUCCEEDED"
        else "AMBIGUOUS"
        if result.status == "AMBIGUOUS"
        else "FAILED_FINAL"
    )
    return EffectResult(
        result_id="effect-result-" + result.result_id[:120],
        effect_id=result.effect_id,
        status=status,
        fact_id=result.fact_ids[0],
        result_object_id=batch.result_payload_object_id,
        result_object_sha256=batch.result_payload_sha256,
        evidence_sha256=batch.batch_sha256,
        error_code=None if status == "SUCCEEDED" else result.error_code,
        observed_at_ms=batch.observed_at_ms,
        result_sha256="0" * 64,
    ).with_computed_sha256()


def _output_declaration(
    plan: ExecutableCompositionPlanV1,
    binding: StepOutputValueBindingV1,
) -> tuple[StepExecutionBindingV1, OutputDeclarationV1]:
    producers = tuple(
        item for item in plan.step_bindings if item.step_id == binding.producer_step_id
    )
    if len(producers) != 1:
        _error("composition.projection.output_producer_missing")
    declarations = tuple(
        item
        for item in producers[0].output_declarations
        if item.output_binding_id == binding.output_binding_id
    )
    if (
        len(declarations) != 1
        or declarations[0].sha256 != binding.output_declaration_sha256
        or not declarations[0].has_valid_sha256()
    ):
        _error("composition.projection.output_declaration_mismatch")
    return producers[0], declarations[0]


def _extract_step_output(
    plan: ExecutableCompositionPlanV1,
    binding: StepOutputValueBindingV1,
    committed: Mapping[str, CompositionAttemptObservationV1],
    *,
    validate_value: Callable[[str, Any], None],
    validate_result: Callable[[str, str, str, Any], None],
    resolve_value_schema: Callable[[str, str, str], Any],
) -> tuple[Any, CompositionDependencyEvidenceV1]:
    producer, declaration = _output_declaration(plan, binding)
    observation = committed.get(producer.step_id)
    if observation is None:
        _error("composition.projection.output_not_committed")
    reason = _success_evidence_reason(
        plan,
        producer,
        observation,
        validate_result=validate_result,
    )
    if reason is not None:
        _error(reason)
    batch = observation.fact_batch
    if batch is None:  # guarded above; keeps static type checkers honest
        _error("composition.projection.output_not_committed")
    try:
        value_schema = resolve_value_schema(
            producer.action_id,
            producer.action_version,
            declaration.value_schema_sha256,
        )
    except CompositionExecutionProjectionError:
        raise
    except Exception as exc:
        raise CompositionExecutionProjectionError(
            "composition.projection.output_value_authority_missing"
        ) from exc
    if (
        getattr(value_schema, "source_kind", None) != declaration.source_kind
        or (
            declaration.source_kind == "RESULT_PAYLOAD"
            and getattr(value_schema, "json_pointer", None)
            != declaration.json_pointer
        )
        or (
            declaration.source_kind != "RESULT_PAYLOAD"
            and getattr(value_schema, "json_pointer", None) is not None
        )
    ):
        _error("composition.projection.output_selector_authority_mismatch")
    if declaration.source_kind == "RESULT_PAYLOAD":
        action_result = _validated_action_result(
            observation,
            step=producer,
            validate_result=validate_result,
        )
        value = _json_pointer_value(
            action_result, declaration.json_pointer or ""
        )
    elif declaration.source_kind == "FACT_ID":
        ordinal = declaration.ordinal
        if ordinal is None or ordinal >= len(batch.facts):
            _error("composition.projection.fact_ordinal_missing")
        value = batch.facts[ordinal].fact_id
    else:
        ordinal = declaration.ordinal
        if ordinal is None or ordinal >= len(observation.output_object_references):
            _error("composition.projection.object_ordinal_missing")
        value = observation.output_object_references[ordinal].object_id
    try:
        validate_value(declaration.value_schema_sha256, value)
    except CompositionExecutionProjectionError:
        raise
    except Exception as exc:
        raise CompositionExecutionProjectionError(
            "composition.projection.output_value_schema_rejected"
        ) from exc
    return value, _dependency_evidence(producer, observation)


def materialize_ready_composition_step(
    plan: ExecutableCompositionPlanV1,
    *,
    step_id: str,
    committed: Mapping[str, CompositionAttemptObservationV1],
    validate_value: Callable[[str, Any], None],
    validate_result: Callable[[str, str, str, Any], None],
    resolve_value_schema: Callable[[str, str, str], Any],
) -> MaterializedCompositionDispatchV1:
    """Resolve one dependency-ready step from committed, schema-bound outputs."""

    if (
        not plan.has_valid_identity()
        or not callable(validate_value)
        or not callable(validate_result)
        or not callable(resolve_value_schema)
    ):
        _error("composition.projection.materialization_authority_invalid")
    matches = tuple(item for item in plan.step_bindings if item.step_id == step_id)
    if len(matches) != 1:
        _error("composition.projection.step_missing")
    step = matches[0]
    if step.target_skeleton is None or step.target_slot is not None:
        _error("composition.projection.dynamic_target_unsupported")
    if set(committed) != set(step.depends_on):
        _error("composition.projection.dependency_set_mismatch")

    arguments = deepcopy(step.args_skeleton)
    inputs = {item.input_id: item for item in plan.plan_inputs}
    evidence_by_step: dict[str, CompositionDependencyEvidenceV1] = {}
    for slot in step.argument_slots:
        binding = slot.value_binding
        if isinstance(binding, LiteralValueBindingV1):
            resolved = deepcopy(binding.value)
        elif isinstance(binding, PlanInputValueBindingV1):
            plan_input = inputs.get(binding.input_id)
            if (
                plan_input is None
                or plan_input.sha256 != binding.input_sha256
                or not plan_input.has_valid_sha256()
                or plan_input.input_kind != "INLINE_JSON"
            ):
                _error("composition.projection.plan_input_mismatch")
            resolved = _json_pointer_value(
                plan_input.inline_value, binding.json_pointer
            )
            try:
                validate_value(plan_input.value_schema_sha256, resolved)
            except Exception as exc:
                raise CompositionExecutionProjectionError(
                    "composition.projection.plan_input_schema_rejected"
                ) from exc
        elif isinstance(binding, StepOutputValueBindingV1):
            resolved, dependency = _extract_step_output(
                plan,
                binding,
                committed,
                validate_value=validate_value,
                validate_result=validate_result,
                resolve_value_schema=resolve_value_schema,
            )
            evidence_by_step[dependency.producer_step_id] = dependency
        else:  # pragma: no cover - discriminated contract already rejects it
            _error("composition.projection.value_binding_unsupported")
        _replace_json_pointer(arguments, slot.destination_json_pointer, resolved)

    for dependency_id in step.depends_on:
        if dependency_id not in evidence_by_step:
            producer = next(
                item for item in plan.step_bindings if item.step_id == dependency_id
            )
            observation = committed[dependency_id]
            reason = _success_evidence_reason(
                plan,
                producer,
                observation,
                validate_result=validate_result,
            )
            if reason is not None:
                _error(reason)
            evidence_by_step[dependency_id] = _dependency_evidence(
                producer, observation
            )
    dependency_evidence = tuple(
        evidence_by_step[item] for item in step.depends_on
    )
    try:
        encoded = canonical_json_bytes(arguments)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise CompositionExecutionProjectionError(
            "composition.projection.arguments_invalid"
        ) from exc
    if len(encoded) > _MAX_MATERIALIZED_ARGUMENT_BYTES:
        _error("composition.projection.arguments_too_large")
    materialized = MaterializedCompositionStepV1(
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        registration_id=plan.registration_id,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        step=step,
        target=step.target_skeleton,
        arguments=arguments,
        target_sha256=canonical_sha256(step.target_skeleton),
        arguments_sha256=canonical_sha256(arguments),
    )
    evidence_payload = tuple(item.payload() for item in dependency_evidence)
    return MaterializedCompositionDispatchV1(
        step=materialized,
        dependency_evidence=dependency_evidence,
        dependency_evidence_sha256=canonical_sha256(evidence_payload),
    )


def resolve_final_output_aliases(
    plan: ExecutableCompositionPlanV1,
    *,
    committed: Mapping[str, CompositionAttemptObservationV1],
    validate_value: Callable[[str, Any], None],
    validate_result: Callable[[str, str, str, Any], None],
    resolve_value_schema: Callable[[str, str, str], Any],
) -> dict[str, Any]:
    """Resolve every declared final alias; implicit reply fallbacks are forbidden."""

    expected_step_ids = {item.step_id for item in plan.step_bindings}
    if set(committed) != expected_step_ids:
        _error("composition.projection.final_outputs_before_completion")
    for step in plan.step_bindings:
        observation = committed[step.step_id]
        if (
            observation.step_id != step.step_id
            or _success_evidence_reason(
                plan,
                step,
                observation,
                validate_result=validate_result,
            )
            is not None
        ):
            _error("composition.projection.final_outputs_before_completion")

    resolved: dict[str, Any] = {}
    for alias in plan.final_output_aliases:
        value, _evidence = _extract_step_output(
            plan,
            alias.value_binding,
            committed,
            validate_value=validate_value,
            validate_result=validate_result,
            resolve_value_schema=resolve_value_schema,
        )
        resolved[alias.alias] = value
    return resolved


__all__ = [
    "CompositionAttemptObservationV1",
    "CompositionDependencyEvidenceV1",
    "CompositionExecutionProjectionError",
    "CompositionExecutionProjectionV1",
    "CompositionStepProjectionV1",
    "MaterializedCompositionDispatchV1",
    "derive_composition_execution_projection",
    "materialize_ready_composition_step",
    "resolve_final_output_aliases",
]
