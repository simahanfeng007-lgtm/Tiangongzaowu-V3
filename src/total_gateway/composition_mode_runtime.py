"""P14 default-switch implementation: the governed turn becomes the default.

Implements the frozen plan's switch: OFF→SHADOW→LIMITED→DEFAULT with the
mode authority as the sole decider. SHADOW runs the governed turn alongside
the legacy chain (plan-only sidecar, zero registration, the old chain still
executes); LIMITED adds admission (the old chain still executes); DEFAULT
makes the governed turn the primary planning path with the old chain only
as an explicit fallback. The mode is persisted, versioned, and transitions
follow the authority's validated rules.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from total_gateway.composition_planner_mode_authority import (
    PlannerModeAuthorityError,
    PlannerModeConfigV1,
    TurnPolicyV1,
    resolve_turn_policy,
)

MODE_FILE_ENV = "TIANGONG_PLANNER_MODE_CONFIG"
DEFAULT_MODE_FILE = Path.home() / ".tiangong" / "v3" / "planner_mode.json"


class PlannerModeStateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def load_mode_config() -> PlannerModeConfigV1 | None:
    """Load the persisted mode config; None = no file (OFF is implicit)."""
    path = Path(os.environ.get(MODE_FILE_ENV, "") or DEFAULT_MODE_FILE)
    if not path.is_file():
        return None
    try:
        return PlannerModeConfigV1.model_validate_json(
            path.read_text(encoding="utf-8"))
    except (ValueError, KeyError) as exc:
        raise PlannerModeStateError(
            "mode.config.corrupt", str(exc)[:200]) from exc


def save_mode_config(config: PlannerModeConfigV1) -> None:
    """Persist a validated mode config (the one durable write)."""
    if not config.has_valid_sha256():
        raise PlannerModeStateError("mode.config.hash_invalid")
    path = Path(os.environ.get(MODE_FILE_ENV, "") or DEFAULT_MODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=1) + "\n", encoding="utf-8")


def effective_planner_mode() -> str:
    """The EFFECTIVE mode for this turn: from the config file if present,
    from the env var for the legacy controlled override, otherwise OFF.

    Priority: persisted config > env var (controlled) > OFF.
    """
    # Legacy env override still works for the R1D controlled turn
    env_mode = os.environ.get("TIANGONG_COMPOSITION_PLANNER_MODE", "off")
    if env_mode == "controlled":
        return "controlled"

    config = load_mode_config()
    if config is None:
        return "off"
    return config.mode.lower()  # OFF/ShAdOw → off/shadow


def current_turn_policy() -> TurnPolicyV1:
    """The turn policy for this request, from the effective mode."""
    mode = effective_planner_mode()
    # Map the runtime modes to the authority's policy table
    if mode == "off":
        return TurnPolicyV1(mode="OFF", may_prepare=False, may_register=False)
    if mode == "controlled":
        # The legacy controlled turn: prepare+register but not via authority
        return TurnPolicyV1(mode="LIMITED", may_prepare=True, may_register=True)
    if mode in ("shadow", "limited", "default"):
        config = load_mode_config()
        if config is not None:
            return resolve_turn_policy(config)
    raise PlannerModeStateError("mode.effective.invalid", mode)


def should_run_composition_turn() -> bool:
    """Whether the governed composition turn should run before the legacy chain."""
    return current_turn_policy().may_prepare


def should_register_composition_result() -> bool:
    """Whether a compiled plan should be admitted (not just plan-only)."""
    return current_turn_policy().may_register


def initialize_shadow_mode(
    *, workspace_scope: tuple[str, ...] = ("workspace.main",),
    cooldown_ms: int = 60_000,
) -> PlannerModeConfigV1:
    """Activate SHADOW: the initial forward transition from implicit OFF."""
    config = PlannerModeConfigV1(
        config_version=1,
        mode="SHADOW",
        workspace_scope=workspace_scope,
        cooldown_ms=cooldown_ms,
        observation_window_ms=300_000,
        created_at_ms=__import__("time").time().__int__() * 1000,
        config_sha256="0" * 64,
    ).with_computed_sha256()
    save_mode_config(config)
    return config


__all__ = [
    "DEFAULT_MODE_FILE", "MODE_FILE_ENV", "PlannerModeStateError",
    "current_turn_policy", "effective_planner_mode",
    "initialize_shadow_mode", "load_mode_config", "save_mode_config",
    "should_register_composition_result", "should_run_composition_turn",
]
