"""P7C.1 adapter for an already-registered executable composition plan.

The adapter deliberately owns no Policy, Ticket, Grant, Runtime, Effect, Fact,
P19, or Completion authority.  It exposes the narrow compatibility seam used
by later orchestration and delegates issuance to the existing
``OmniGrantAuthority``.  Static materialization lives here as a deterministic,
side-effect-free operation so the authority can rebuild caller-independent
arguments from the sealed P7C.0 companion.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from contracts import canonical_json_bytes, canonical_sha256

from .composition_executable_plan import (
    ExecutableCompositionPlanV1,
    LiteralValueBindingV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_MAX_MATERIALIZED_ARGUMENT_BYTES = 1_048_576


class CompositionActivationAdapterError(ValueError):
    """Fail-closed P7C.1 adapter/materialization error with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MaterializedCompositionStepV1:
    """Pure projection of one sealed root step; never an authorization."""

    executable_plan_id: str
    executable_plan_sha256: str
    registration_id: str
    request_id: str
    run_id: str
    generation: int
    principal_scope_hash: str
    step: StepExecutionBindingV1
    target: str
    arguments: dict[str, Any]
    target_sha256: str
    arguments_sha256: str


class _CompositionGrantIssuer(Protocol):
    def issue_composition_step(
        self,
        *,
        parent_ticket_id: str,
        registration_id: str,
        step_id: str,
        now_ms: int | None = None,
    ) -> dict[str, Any]: ...


def _error(code: str) -> None:
    raise CompositionActivationAdapterError(code)


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _error("composition.authorization.json_pointer_invalid")
    result: list[str] = []
    for raw in pointer[1:].split("/"):
        token = ""
        index = 0
        while index < len(raw):
            char = raw[index]
            if char != "~":
                token += char
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                _error("composition.authorization.json_pointer_invalid")
            token += "~" if raw[index + 1] == "0" else "/"
            index += 2
        result.append(token)
    return tuple(result)


def _pointer_value(value: Any, pointer: str) -> Any:
    current = value
    for token in _pointer_tokens(pointer):
        if isinstance(current, Mapping):
            if token not in current:
                _error("composition.authorization.plan_input_pointer_missing")
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-" or not token.isascii() or not token.isdecimal():
                _error("composition.authorization.plan_input_pointer_invalid")
            if len(token) > 1 and token.startswith("0"):
                _error("composition.authorization.plan_input_pointer_invalid")
            ordinal = int(token)
            if ordinal >= len(current):
                _error("composition.authorization.plan_input_pointer_missing")
            current = current[ordinal]
            continue
        _error("composition.authorization.plan_input_pointer_missing")
    return deepcopy(current)


def _replace_pointer(root: dict[str, Any], pointer: str, replacement: Any) -> None:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        _error("composition.authorization.argument_pointer_invalid")
    current: Any = root
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                _error("composition.authorization.argument_pointer_missing")
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-" or not token.isascii() or not token.isdecimal():
                _error("composition.authorization.argument_pointer_invalid")
            if len(token) > 1 and token.startswith("0"):
                _error("composition.authorization.argument_pointer_invalid")
            ordinal = int(token)
            if ordinal >= len(current):
                _error("composition.authorization.argument_pointer_missing")
            current = current[ordinal]
            continue
        _error("composition.authorization.argument_pointer_missing")

    final = tokens[-1]
    if isinstance(current, dict):
        if final not in current or current[final] is not None:
            _error("composition.authorization.argument_slot_not_null")
        current[final] = deepcopy(replacement)
        return
    if isinstance(current, list):
        if final == "-" or not final.isascii() or not final.isdecimal():
            _error("composition.authorization.argument_pointer_invalid")
        if len(final) > 1 and final.startswith("0"):
            _error("composition.authorization.argument_pointer_invalid")
        ordinal = int(final)
        if ordinal >= len(current):
            _error("composition.authorization.argument_pointer_missing")
        if current[ordinal] is not None:
            _error("composition.authorization.argument_slot_not_null")
        current[ordinal] = deepcopy(replacement)
        return
    _error("composition.authorization.argument_pointer_missing")


def materialize_static_root_step(
    plan: ExecutableCompositionPlanV1,
    *,
    step_id: str,
) -> MaterializedCompositionStepV1:
    """Rebuild one P7C.1 first-slice invocation only from sealed plan data.

    P7D owns dependency scheduling and ``STEP_OUTPUT`` resolution.  P7C.1 thus
    accepts only dependency-free steps with a static target and inline/literal
    argument values.  Object grants may remain plan inputs and are verified by
    the authority, but using opaque bytes as an argument value has no frozen
    representation yet and is rejected.
    """

    if not isinstance(plan, ExecutableCompositionPlanV1) or not plan.has_valid_identity():
        _error("composition.authorization.plan_invalid")
    matches = tuple(item for item in plan.step_bindings if item.step_id == step_id)
    if len(matches) != 1:
        _error("composition.authorization.step_missing")
    step = matches[0]
    if not step.has_valid_sha256():
        _error("composition.authorization.step_invalid")
    if step.depends_on:
        _error("composition.authorization.dependencies_not_ready")
    if step.target_skeleton is None or step.target_slot is not None:
        _error("composition.authorization.dynamic_target_unsupported")

    try:
        arguments = deepcopy(step.args_skeleton)
        canonical_json_bytes(arguments)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise CompositionActivationAdapterError(
            "composition.authorization.arguments_invalid"
        ) from exc

    inputs = {item.input_id: item for item in plan.plan_inputs}
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
            ):
                _error("composition.authorization.plan_input_mismatch")
            if plan_input.input_kind != "INLINE_JSON":
                _error("composition.authorization.object_argument_unsupported")
            resolved = _pointer_value(plan_input.inline_value, binding.json_pointer)
        elif isinstance(binding, StepOutputValueBindingV1):
            _error("composition.authorization.step_output_not_ready")
        else:  # pragma: no cover - the discriminated contract is already strict
            _error("composition.authorization.value_binding_unsupported")
        _replace_pointer(arguments, slot.destination_json_pointer, resolved)

    try:
        encoded = canonical_json_bytes(arguments)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise CompositionActivationAdapterError(
            "composition.authorization.arguments_invalid"
        ) from exc
    if len(encoded) > _MAX_MATERIALIZED_ARGUMENT_BYTES:
        _error("composition.authorization.arguments_too_large")

    target = step.target_skeleton
    return MaterializedCompositionStepV1(
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        registration_id=plan.registration_id,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        step=step,
        target=target,
        arguments=arguments,
        target_sha256=canonical_sha256(target),
        arguments_sha256=canonical_sha256(arguments),
    )


class CompositionActivationAdapter:
    """Narrow P7C.1 entry point backed by the one existing grant authority."""

    def __init__(
        self,
        issuer: _CompositionGrantIssuer,
        *,
        execution_available: bool = True,
    ) -> None:
        if not callable(getattr(issuer, "issue_composition_step", None)):
            raise ValueError("composition adapter requires the Omni grant authority")
        if type(execution_available) is not bool:
            raise ValueError("composition execution availability is invalid")
        self._issuer = issuer
        self._execution_available = execution_available

    def authorize_step(
        self,
        *,
        parent_ticket_id: str,
        registration_id: str,
        step_id: str,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if not self._execution_available:
            _error("composition.authorization.execution_unavailable")
        for value in (parent_ticket_id, registration_id, step_id):
            if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
                _error("composition.authorization.identity_invalid")
        if now_ms is not None and (type(now_ms) is not int or now_ms < 0):
            _error("composition.authorization.time_invalid")
        return self._issuer.issue_composition_step(
            parent_ticket_id=parent_ticket_id,
            registration_id=registration_id,
            step_id=step_id,
            now_ms=now_ms,
        )


__all__ = [
    "CompositionActivationAdapter",
    "CompositionActivationAdapterError",
    "MaterializedCompositionStepV1",
    "materialize_static_root_step",
]
