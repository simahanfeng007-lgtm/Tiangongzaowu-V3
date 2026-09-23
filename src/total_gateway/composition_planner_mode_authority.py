"""P14-A draft: the composition-planner mode authority contract.

A PRE-STUDY, not a cutover: this module defines and validates the mode
configuration and transition rules the P14 switch will need — the frozen
SHADOW/LIMITED/DEFAULT progression with cooldowns, observation windows,
machine-checkable prerequisites (the P11 formal exit among them), an A0
risk ceiling that cannot be silently lifted, and a decider that is
operator/system ONLY — a model may never select the mode. Nothing here
wires the production orchestration (that is P14-B, gated on the P11
formal exit); the shipped production default stays exactly as it is.
"""
from __future__ import annotations

from contracts.composition_profile import composition_profile_valid, composition_profile_risk_ceiling

from typing import Literal

from pydantic import Field, field_validator, model_validator
from contracts import canonical_sha256
from contracts.models import ContractModel

MODE_AUTHORITY_SCHEMA = "tiangong.composition-planner-mode.v1"
TRANSITION_SCHEMA = "tiangong.composition-planner-mode-transition.v1"

PlannerMode = Literal["OFF", "SHADOW", "LIMITED", "DEFAULT"]

# Frozen progression: forward exactly one step; rollback may jump home.
_FORWARD = {
    "OFF": "SHADOW",
    "SHADOW": "LIMITED",
    "LIMITED": "DEFAULT",
}
_ROLLBACK = {
    "SHADOW": "OFF",
    "LIMITED": ("OFF", "SHADOW"),
    "DEFAULT": ("OFF", "LIMITED"),
}

# The machine-checkable prerequisites a forward transition demands. The
# P11 formal exit is FIRST: no limited/default without it, by contract.
_PREREQUISITES = {
    "SHADOW": ("p11_shadow_observations",),
    "LIMITED": ("p11_formal_exit", "source_credentials", "verifier_coverage"),
    "DEFAULT": ("p11_formal_exit", "limited_observation_window",
                "source_credentials", "verifier_coverage"),
}


class PlannerModeAuthorityError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class PlannerModeConfigV1(ContractModel):
    """One versioned mode configuration; the A0 ceiling is structural."""

    schema_version: Literal[MODE_AUTHORITY_SCHEMA] = MODE_AUTHORITY_SCHEMA
    config_version: int = Field(ge=1)
    mode: PlannerMode
    # Structural A0: no configuration value can widen the risk ceiling.
    risk_ceiling: Literal["A0", "A3", "A4"] = "A0"
    execution_profile_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    execution_profile_sha256: str | None = Field(default=None, exclude_if=lambda value: value is None)
    workspace_scope: tuple[str, ...] = Field(min_length=1, max_length=64)
    cooldown_ms: int = Field(ge=0)
    observation_window_ms: int = Field(ge=0)
    created_at_ms: int = Field(ge=0)
    config_sha256: str

    @model_validator(mode="after")
    def _fixed_execution_profile(self):
        if (not composition_profile_valid(self.execution_profile_id, self.execution_profile_sha256)
                or self.risk_ceiling != composition_profile_risk_ceiling(self.execution_profile_id, self.execution_profile_sha256)):
            raise ValueError("mode risk ceiling requires a known fixed system execution profile")
        return self

    @field_validator("workspace_scope")
    @classmethod
    def _scope_unique(cls, value):
        if value != tuple(sorted(set(value))):
            raise ValueError("mode workspace scope must be sorted and unique")
        return value

    def payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"config_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.config_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "PlannerModeConfigV1":
        return self.model_copy(
            update={"config_sha256": self.computed_sha256()})


class PlannerModeTransitionV1(ContractModel):
    """One audited transition decision; the decider is never a model."""

    schema_version: Literal[TRANSITION_SCHEMA] = TRANSITION_SCHEMA
    from_mode: PlannerMode
    to_mode: PlannerMode
    decided_by: Literal["operator", "system"]
    reason: str = Field(min_length=1, max_length=400)
    decided_at_ms: int = Field(ge=0)
    resulting_config_version: int = Field(ge=1)
    transition_sha256: str

    def payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"transition_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.transition_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "PlannerModeTransitionV1":
        return self.model_copy(
            update={"transition_sha256": self.computed_sha256()})


def validate_mode_transition(
    current: PlannerModeConfigV1,
    transition: PlannerModeTransitionV1,
    *,
    prerequisites: dict[str, bool],
    last_transition_at_ms: int,
    now_ms: int,
) -> PlannerModeConfigV1:
    """Validate one transition and derive the next configuration.

    Refuses: forward skips (OFF must pass SHADOW, SHADOW must pass
    LIMITED), cooldown violations, missing prerequisites (each named),
    model deciders, hash tampering on either object, and scope changes
    smuggled inside a transition (a scope change is a new configuration,
    not a mode move).
    """
    if not current.has_valid_sha256():
        raise PlannerModeAuthorityError("mode.config.hash_invalid")
    if not transition.has_valid_sha256():
        raise PlannerModeAuthorityError("mode.transition.hash_invalid")
    if transition.from_mode != current.mode:
        raise PlannerModeAuthorityError(
            "mode.transition.source_mismatch",
            f"{transition.from_mode}!={current.mode}")
    target = transition.to_mode
    if target == current.mode:
        raise PlannerModeAuthorityError("mode.transition.noop_forbidden")
    forward = _FORWARD.get(current.mode) == target
    rollback = target in _ROLLBACK.get(current.mode, ())
    if not forward and not rollback:
        raise PlannerModeAuthorityError(
            "mode.transition.forward_skip_forbidden",
            f"{current.mode}->{target}")
    if now_ms < transition.decided_at_ms:
        raise PlannerModeAuthorityError("mode.transition.time_inverted")
    if forward and now_ms - last_transition_at_ms < current.cooldown_ms:
        raise PlannerModeAuthorityError(
            "mode.transition.cooldown_active",
            f"{now_ms - last_transition_at_ms}ms < {current.cooldown_ms}ms")
    if forward:
        missing = tuple(sorted(
            name for name in _PREREQUISITES.get(target, ())
            if not prerequisites.get(name)))
        if missing:
            raise PlannerModeAuthorityError(
                "mode.transition.prerequisite_missing", ",".join(missing))
    next_config = PlannerModeConfigV1(
        config_version=current.config_version + 1,
        mode=target,
        workspace_scope=current.workspace_scope,
        cooldown_ms=current.cooldown_ms,
        observation_window_ms=current.observation_window_ms,
        created_at_ms=transition.decided_at_ms,
        config_sha256="0" * 64,
    ).with_computed_sha256()
    if transition.resulting_config_version != next_config.config_version:
        raise PlannerModeAuthorityError(
            "mode.transition.version_discontinuous")
    return next_config


def check_restart_consistency(
    stored: PlannerModeConfigV1, effective: PlannerModeConfigV1,
) -> None:
    """After a restart the effective mode must be the stored one, exactly."""
    if not stored.has_valid_sha256() or not effective.has_valid_sha256():
        raise PlannerModeAuthorityError("mode.restart.hash_invalid")
    if stored.config_sha256 != effective.config_sha256:
        raise PlannerModeAuthorityError("mode.restart.drift",
                                        "stored != effective")


class TurnPolicyV1(ContractModel):
    """What ONE governed turn may do under the current mode.

    P14-B/C pre-wiring: the policy is derived from a validated mode
    configuration only — never from a model, an envelope field or an
    implicit fallback. ``may_dispatch`` stays structurally False: the
    original Policy/Ticket/Grant chain alone owns execution.
    """

    schema_version: Literal[
        "tiangong.composition-planner-turn-policy.v1"
    ] = "tiangong.composition-planner-turn-policy.v1"
    mode: PlannerMode
    may_prepare: bool
    may_register: bool
    may_dispatch: Literal[False] = False

    @model_validator(mode="after")
    def _policy_is_monotone(self):
        if self.may_register and not self.may_prepare:
            raise ValueError("mode turn policy cannot register unprepared")
        return self


_TURN_POLICIES: dict[str, tuple[bool, bool]] = {
    # mode: (may_prepare, may_register)
    "OFF": (False, False),       # disabled outright
    "SHADOW": (True, False),      # plan-only sidecar: compile, never register
    "LIMITED": (True, True),      # controlled registration
    "DEFAULT": (True, True),      # governed registration at the default mode
}


def resolve_turn_policy(config: PlannerModeConfigV1) -> TurnPolicyV1:
    """Derive one turn's permissions from a validated mode configuration."""
    if not isinstance(config, PlannerModeConfigV1) \
            or not config.has_valid_sha256():
        raise PlannerModeAuthorityError("mode.turn_policy.config_invalid")
    may_prepare, may_register = _TURN_POLICIES[config.mode]
    return TurnPolicyV1(
        mode=config.mode, may_prepare=may_prepare,
        may_register=may_register)


__all__ = [
    "MODE_AUTHORITY_SCHEMA", "TRANSITION_SCHEMA",
    "PlannerModeAuthorityError", "PlannerModeConfigV1",
    "PlannerModeTransitionV1", "TurnPolicyV1",
    "validate_mode_transition", "check_restart_consistency",
    "resolve_turn_policy",
]
