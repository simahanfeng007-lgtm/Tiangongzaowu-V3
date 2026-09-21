"""P12-R1D controlled composition planning turn.

Explicit opt-in only: ``TIANGONG_COMPOSITION_PLANNER_MODE=controlled`` plus a
complete operator tool-source pin enables exactly one turn shape — prepare the
request-bound Source composition through the installed R1C2 bridge, call the
existing model adapter outside every Store lock, compile the reply through the
original P4 chain (SMALL Proposal ABI, at most one repair, original trivalent
semantics), and hand an admissible result to the original P7 registration seam.

There is NO silent fallback: a controlled turn that fails surfaces its
original error to the user and the legacy planner is NOT replayed for the same
message. The default planner is unchanged; the global default switch belongs
to P14, not to this module.
"""
from __future__ import annotations

import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Callable

COMPOSITION_PLANNER_MODE_ENV = "TIANGONG_COMPOSITION_PLANNER_MODE"
COMPOSITION_TOOL_SOURCE_PIN_ENV = "TIANGONG_COMPOSITION_TOOL_SOURCE_PIN"

_PIN_FIELDS = (
    "repository", "bundle_path", "bundle_sha256", "base_commit",
    "candidate_commit", "requested_action_ids", "action_entry_path",
    "repository_id", "worktree_id",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class CompositionTurnError(RuntimeError):
    """A controlled turn failed; the caller must NOT fall back silently."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def composition_planner_mode() -> str:
    """Read the explicit opt-in; the default is the unchanged legacy planner."""

    value = os.environ.get(COMPOSITION_PLANNER_MODE_ENV, "off").strip().lower()
    return value if value in {"off", "controlled"} else "off"


def load_operator_tool_source_pin(path: Path):
    """Load the operator-pinned P8 coordinates; never accept model fields."""

    from total_gateway.composition_source_preparation import PlanningToolSource

    if not path.is_file():
        raise CompositionTurnError("pin.missing", str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompositionTurnError("pin.invalid", str(exc)) from exc
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(field), (str, list))
        or (isinstance(payload.get(field), list) and not all(
            isinstance(item, str) for item in payload[field]))
        for field in _PIN_FIELDS
    ):
        raise CompositionTurnError("pin.incomplete")
    try:
        tool_source = PlanningToolSource(
            repository=Path(payload["repository"]),
            bundle_path=Path(payload["bundle_path"]),
            bundle_sha256=payload["bundle_sha256"],
            base_commit=payload["base_commit"],
            candidate_commit=payload["candidate_commit"],
            requested_action_ids=tuple(payload["requested_action_ids"]),
            action_entry_path=payload["action_entry_path"],
            repository_id=payload["repository_id"],
            worktree_id=payload["worktree_id"],
        )
    except (TypeError, ValueError) as exc:
        raise CompositionTurnError("pin.invalid", str(exc)) from exc
    return tool_source


def pin_available_verifiers(path: Path) -> frozenset:
    """The operator-declared verification intents for this pinned bundle.

    The P4 validator needs the intent set BEFORE the plan compiles, so the
    operator declares it beside the pin; an absent declaration keeps the
    honest refused-by-default behaviour (empty set -> UNKNOWN/REJECT).
    """

    if not path.is_file():
        raise CompositionTurnError("pin.missing", str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompositionTurnError("pin.invalid", str(exc)) from exc
    declared = payload.get("available_verifiers")
    if declared is None:
        return frozenset()
    if (not isinstance(declared, list) or not declared
            or not all(isinstance(item, str) and item for item in declared)):
        raise CompositionTurnError("pin.verifiers_invalid")
    return frozenset(declared)


def _registry_from_pin(tool_source, generated_at_ms: int):
    from total_gateway.action_registry import compile_action_authority

    try:
        with zipfile.ZipFile(tool_source.bundle_path) as archive:
            manifest = json.loads(
                archive.read("build-report.json"),
            )["build_artifact"]["gateway_manifest"]
    except (OSError, KeyError, ValueError) as exc:
        raise CompositionTurnError("pin.registry_unavailable", str(exc)) from exc
    return compile_action_authority(manifest, generated_at_ms=generated_at_ms).registry


def _require_run_identity(run_context) -> None:
    for field in ("request_id", "run_id", "generation", "workspace_id",
                  "life_id", "principal_scope_hash"):
        value = getattr(run_context, field, None)
        if value in (None, ""):
            raise CompositionTurnError("identity.missing", field)
    if str(getattr(run_context, "principal_scope_hash", "")).strip().lower() \
            in {"", "unknown"} or not _SHA256.fullmatch(
            str(run_context.principal_scope_hash)):
        raise CompositionTurnError("identity.principal_invalid")


def run_controlled_composition_turn(
    *,
    user_text: str,
    model_call: Callable[[str], str],
    run_context=None,
    bridge=None,
    admission_provider: Callable[[object], dict] | None = None,
    available_verifiers: frozenset = frozenset(),
    now_ms: int | None = None,
) -> dict:
    """One controlled composition roundtrip; never falls back silently.

    Returns a status dict with ``outcome`` one of ``registered`` (admission
    committed through the original P7 seam), ``refused`` (the original P4
    trivalent verdict rejected this plan — findings preserved) or
    ``registration_not_configured`` (an admissible plan exists but the
    operator has not installed the system admission evidence provider yet).
    Every failure raises ``CompositionTurnError`` with the original code.
    """

    from .run_context import current_run_context

    if not str(user_text or "").strip():
        raise CompositionTurnError("turn.empty_user_text")
    context = run_context or current_run_context()
    _require_run_identity(context)
    pin_path = os.environ.get(COMPOSITION_TOOL_SOURCE_PIN_ENV, "").strip()
    if not pin_path:
        raise CompositionTurnError("pin.env_missing")
    tool_source = load_operator_tool_source_pin(Path(pin_path))
    if not available_verifiers:
        available_verifiers = pin_available_verifiers(Path(pin_path))
    if bridge is None:
        from .world_context_integration import _runtime_instance
        bridge = _runtime_instance()
    prepared_at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    registry = _registry_from_pin(tool_source, generated_at_ms=0)

    # The model prompt is the R1C2 SMALL Proposal ABI prompt; the model call
    # runs outside every Gateway/World Store lock by construction.
    try:
        prepared, prompt = bridge.prepare_composition_for_turn(
            run_context=context, user_text=user_text, tool_source=tool_source,
            registry=registry, now_ms=prepared_at_ms)
    except ValueError as exc:
        raise CompositionTurnError("prepare.rejected", str(exc)[:400]) from exc
    primary_text = model_call(prompt)
    if not isinstance(primary_text, str) or not primary_text.strip():
        raise CompositionTurnError("model.empty_reply")

    validated_at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        result = bridge.compile_composition_for_turn(
            prepared, primary_text, run_context=context,
            tool_source=tool_source, validated_at_ms=validated_at_ms,
            available_verifiers=available_verifiers)
    except ValueError as exc:
        # One repair is attempted inside the original parser; a hard parse
        # failure carries the original error, never an empty fallback.
        raise CompositionTurnError("compile.rejected", str(exc)[:400]) from exc

    validation = result.validation
    if validation.result == "PROVED_INVALID":
        return {"outcome": "refused", "reason": "PROVED_INVALID",
                "findings": sorted({f.code for f in validation.findings}),
                "plan_id": result.plan.plan_id}
    if validation.result == "UNKNOWN" and not (
        validation.unknown_disposition == "PROVISIONAL_ALLOW"
        and validation.mandatory_verification
    ):
        return {"outcome": "refused", "reason": "UNKNOWN",
                "findings": sorted({f.code for f in validation.findings}),
                "plan_id": result.plan.plan_id}
    if admission_provider is None:
        return {"outcome": "registration_not_configured",
                "plan_id": result.plan.plan_id,
                "validation": validation.result}
    admission = admission_provider(result)
    registration_id = getattr(admission, "registration_id", None) \
        if not isinstance(admission, dict) else admission.get("registration_id")
    executable_plan_id = getattr(admission, "executable_plan_id", None) \
        if not isinstance(admission, dict) else admission.get("executable_plan_id")
    return {"outcome": "registered", "plan_id": result.plan.plan_id,
            "validation": validation.result,
            "registration_id": registration_id,
            "executable_plan_id": executable_plan_id}


def run_governed_composition_turn(
    mode_config,
    *,
    user_text: str,
    model_call: Callable[[str], str],
    run_context=None,
    bridge=None,
    admission_provider: Callable[[object], dict] | None = None,
    now_ms: int | None = None,
) -> dict:
    """P14-B/C pre-wiring: one turn governed by a validated mode config.

    The turn policy comes from ``resolve_turn_policy(mode_config)`` — the
    mode authority, never a model or an envelope field. OFF refuses before
    any work; SHADOW runs prepare+compile and returns plan-only output
    (nothing registers, nothing executes — the shadow sidecar semantics);
    LIMITED/DEFAULT add admission through the same explicit provider
    contract as the controlled turn. The legacy ``off/controlled`` env
    channel and this governed entry are separate callers of the same
    core; neither changes the other's behaviour, and neither flips any
    production default.
    """
    from total_gateway.composition_planner_mode_authority import (
        PlannerModeAuthorityError, resolve_turn_policy,
    )
    try:
        policy = resolve_turn_policy(mode_config)
    except PlannerModeAuthorityError as exc:
        raise CompositionTurnError("mode.config_invalid", exc.code) from exc
    if not policy.may_prepare:
        return {"outcome": "mode_disabled", "mode": policy.mode}

    context = run_context
    pin_path = os.environ.get(COMPOSITION_TOOL_SOURCE_PIN_ENV, "").strip()
    if not pin_path:
        raise CompositionTurnError("pin.env_missing")
    tool_source = load_operator_tool_source_pin(Path(pin_path))
    verifiers = pin_available_verifiers(Path(pin_path))
    if bridge is None:
        from .world_context_integration import _runtime_instance
        bridge = _runtime_instance()
    if context is None:
        from .run_context import current_run_context
        context = current_run_context()
    _require_run_identity(context)
    prepared_at = now_ms if now_ms is not None else int(time.time() * 1000)
    registry = _registry_from_pin(tool_source, generated_at_ms=0)
    try:
        prepared, prompt = bridge.prepare_composition_for_turn(
            run_context=context, user_text=user_text,
            tool_source=tool_source, registry=registry,
            now_ms=prepared_at)
    except ValueError as exc:
        raise CompositionTurnError("prepare.rejected", str(exc)[:400]) from exc
    primary_text = model_call(prompt)
    if not isinstance(primary_text, str) or not primary_text.strip():
        raise CompositionTurnError("model.empty_reply")
    try:
        result = bridge.compile_composition_for_turn(
            prepared, primary_text, run_context=context,
            tool_source=tool_source,
            validated_at_ms=now_ms if now_ms is not None
            else int(time.time() * 1000),
            available_verifiers=verifiers)
    except ValueError as exc:
        raise CompositionTurnError("compile.rejected", str(exc)[:400]) from exc
    validation = result.validation
    refused = validation.result == "PROVED_INVALID" or (
        validation.result == "UNKNOWN" and not (
            validation.unknown_disposition == "PROVISIONAL_ALLOW"
            and validation.mandatory_verification))
    if refused:
        return {"outcome": "refused", "mode": policy.mode,
                "reason": validation.result,
                "findings": sorted({f.code for f in validation.findings}),
                "plan_id": result.plan.plan_id}
    if not policy.may_register or admission_provider is None:
        return {"outcome": "plan_only" if policy.may_register
                else "plan_only_shadow",
                "mode": policy.mode, "plan_id": result.plan.plan_id,
                "validation": validation.result}
    admission = admission_provider(result)
    registration_id = getattr(admission, "registration_id", None) \
        if not isinstance(admission, dict) else admission.get("registration_id")
    executable_plan_id = getattr(admission, "executable_plan_id", None) \
        if not isinstance(admission, dict) else admission.get("executable_plan_id")
    return {"outcome": "registered", "mode": policy.mode,
            "plan_id": result.plan.plan_id,
            "validation": validation.result,
            "registration_id": registration_id,
            "executable_plan_id": executable_plan_id}


__all__ = [
    "COMPOSITION_PLANNER_MODE_ENV",
    "COMPOSITION_TOOL_SOURCE_PIN_ENV",
    "CompositionTurnError",
    "composition_planner_mode",
    "load_operator_tool_source_pin",
    "pin_available_verifiers",
    "run_controlled_composition_turn",
    "run_governed_composition_turn",
]
