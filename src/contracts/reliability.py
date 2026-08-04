"""Failure, retry, dynamic-timeout, and circuit-breaker contracts."""

from __future__ import annotations

import contextvars
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .models import (
    ContractModel,
    OpaqueId,
    ReasonCode,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
)

# CC-loop structure: the gateway binds an absolute wall-clock deadline to every
# effect execution, and every nested layer (LLM call, simple-chain loop) reads
# it through this context so the chain terminates BEFORE the gateway watchdog
# marks the effect AMBIGUOUS.  A wedged worker can therefore never occupy a
# pool slot indefinitely or block the session queue.
_EXECUTION_DEADLINE_MS: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tiangong_execution_deadline_ms",
    default=0,
)


def set_execution_deadline_ms(value: int) -> contextvars.Token[int]:
    """Bind an absolute wall-clock deadline (epoch ms) to the current context."""
    return _EXECUTION_DEADLINE_MS.set(max(0, int(value or 0)))


def reset_execution_deadline(token: contextvars.Token[int]) -> None:
    _EXECUTION_DEADLINE_MS.reset(token)


def current_execution_deadline_ms() -> int:
    """Return the active effect deadline, or 0 when no deadline is bound."""
    return _EXECUTION_DEADLINE_MS.get()


def _schema_config(name: str) -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:{name}",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )


FailureDisposition = Literal[
    "TERMINAL",
    "RETRYABLE",
    "AMBIGUOUS",
    "CANCELLED",
    "FENCED",
]
OperationPhase = Literal[
    "inbox_receive",
    "attachment_download",
    "planning",
    "life_snapshot",
    "skill_resolution",
    "backend_execution",
    "artifact_qc",
    "artifact_fetch",
    "channel_upload",
    "channel_send",
    "reconciliation",
    "storage",
    "startup",
]


class ErrorDescriptor(ContractModel):
    """Trusted component classification; model prose cannot create this fact."""

    model_config = _schema_config("ErrorDescriptor")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    error_id: OpaqueId
    error_code: ReasonCode
    source_component_id: OpaqueId
    phase: OperationPhase
    disposition: FailureDisposition
    observed_at_ms: int = Field(ge=0)
    attempt: int = Field(ge=1, le=10_000)
    side_effect_started: bool
    retry_after_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    retry_after_source: Literal["header", "platform", "policy"] | None = None
    safe_message: str | None = Field(default=None, max_length=512)
    evidence_sha256: Sha256
    model_generated: Literal[False] = False

    @model_validator(mode="after")
    def validate_failure_semantics(self) -> Self:
        if self.disposition == "RETRYABLE" and self.side_effect_started:
            raise ValueError("a started external side effect cannot be blindly retryable")
        if self.disposition == "AMBIGUOUS" and not self.side_effect_started:
            raise ValueError("ambiguous outcome requires a started side effect")
        if self.disposition in {"CANCELLED", "FENCED"} and self.side_effect_started:
            raise ValueError("cancelled or fenced work cannot have started a side effect")
        if (self.retry_after_ms is None) != (self.retry_after_source is None):
            raise ValueError("Retry-After duration and source must be present together")
        if self.retry_after_ms is not None and self.disposition != "RETRYABLE":
            raise ValueError("Retry-After is valid only for a retryable failure")
        return self


JitterMode = Literal["none", "full", "equal"]


class RetryPolicy(ContractModel):
    model_config = _schema_config("RetryPolicy")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    policy_id: OpaqueId
    revision: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=100)
    base_delay_ms: int = Field(ge=0, le=3_600_000)
    max_delay_ms: int = Field(ge=0, le=86_400_000)
    multiplier_milli: int = Field(ge=1_000, le=10_000)
    jitter_mode: JitterMode = "none"
    jitter_seed_sha256: Sha256 | None = None
    respect_retry_after: bool = True
    max_retry_after_ms: int = Field(ge=0, le=86_400_000)
    retry_budget_ms: int = Field(ge=0, le=604_800_000)
    minimum_attempt_runtime_ms: int = Field(ge=0, le=3_600_000)
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.base_delay_ms > self.max_delay_ms:
            raise ValueError("retry base delay exceeds maximum delay")
        if (self.jitter_mode == "none") != (self.jitter_seed_sha256 is None):
            raise ValueError("deterministic jitter requires a seed and none mode forbids one")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


RetryAction = Literal[
    "RETRY",
    "STOP_TERMINAL",
    "RECONCILE_REQUIRED",
    "STOP_CANCELLED",
    "STOP_FENCED",
    "STOP_EXHAUSTED",
    "STOP_DEADLINE",
]


class RetryDecision(ContractModel):
    model_config = _schema_config("RetryDecision")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    error_id: OpaqueId
    action: RetryAction
    should_retry: bool
    should_reconcile: bool
    current_attempt: int = Field(ge=1, le=10_000)
    next_attempt: int | None = Field(default=None, ge=2, le=10_000)
    delay_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    scheduled_at_ms: int | None = Field(default=None, ge=0)
    reason_code: ReasonCode

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.should_retry != (self.action == "RETRY"):
            raise ValueError("only RETRY may set should_retry")
        if self.should_reconcile != (self.action == "RECONCILE_REQUIRED"):
            raise ValueError("only RECONCILE_REQUIRED may set should_reconcile")
        retry_fields = (self.next_attempt, self.delay_ms, self.scheduled_at_ms)
        if self.should_retry and any(value is None for value in retry_fields):
            raise ValueError("retry decision requires next attempt, delay, and schedule")
        if not self.should_retry and any(value is not None for value in retry_fields):
            raise ValueError("non-retry decision cannot carry a retry schedule")
        if self.next_attempt is not None and self.next_attempt != self.current_attempt + 1:
            raise ValueError("next retry attempt must increment exactly once")
        return self


def _stop(error: ErrorDescriptor, action: RetryAction, reason_code: str) -> RetryDecision:
    return RetryDecision(
        error_id=error.error_id,
        action=action,
        should_retry=False,
        should_reconcile=action == "RECONCILE_REQUIRED",
        current_attempt=error.attempt,
        reason_code=reason_code,
    )


def _base_backoff_ms(policy: RetryPolicy, failed_attempt: int) -> int:
    delay = policy.base_delay_ms
    for _ in range(failed_attempt - 1):
        delay = min(
            policy.max_delay_ms,
            (delay * policy.multiplier_milli + 999) // 1_000,
        )
    return min(delay, policy.max_delay_ms)


def _jittered_delay_ms(policy: RetryPolicy, failed_attempt: int, delay_ms: int) -> int:
    if policy.jitter_mode == "none" or delay_ms == 0:
        return delay_ms
    assert policy.jitter_seed_sha256 is not None
    sample = int(
        canonical_sha256(
            {
                "domain": "tiangong.retry.jitter.v1",
                "policy_id": policy.policy_id,
                "policy_revision": policy.revision,
                "seed_sha256": policy.jitter_seed_sha256,
                "failed_attempt": failed_attempt,
            }
        )[:16],
        16,
    )
    if policy.jitter_mode == "full":
        return sample % (delay_ms + 1)
    floor = delay_ms // 2
    return floor + sample % (delay_ms - floor + 1)


def decide_retry(
    error: ErrorDescriptor,
    policy: RetryPolicy,
    *,
    now_ms: int,
    elapsed_ms: int,
    deadline_at_ms: int | None = None,
) -> RetryDecision:
    if now_ms < 0 or elapsed_ms < 0:
        raise ValueError("retry comparison time cannot be negative")
    if not policy.has_valid_sha256():
        raise ValueError("retry policy digest is invalid")
    if error.disposition == "TERMINAL":
        return _stop(error, "STOP_TERMINAL", "retry.terminal")
    if error.disposition == "AMBIGUOUS":
        return _stop(error, "RECONCILE_REQUIRED", "retry.ambiguous_reconcile")
    if error.disposition == "CANCELLED":
        return _stop(error, "STOP_CANCELLED", "retry.cancelled")
    if error.disposition == "FENCED":
        return _stop(error, "STOP_FENCED", "retry.fenced")
    if error.attempt >= policy.max_attempts:
        return _stop(error, "STOP_EXHAUSTED", "retry.attempts_exhausted")

    backoff = _jittered_delay_ms(policy, error.attempt, _base_backoff_ms(policy, error.attempt))
    delay = backoff
    if error.retry_after_ms is not None and policy.respect_retry_after:
        if error.retry_after_ms > policy.max_retry_after_ms:
            return _stop(error, "STOP_EXHAUSTED", "retry.retry_after_exceeds_policy")
        delay = max(delay, error.retry_after_ms)
    required_budget = delay + policy.minimum_attempt_runtime_ms
    if elapsed_ms + required_budget > policy.retry_budget_ms:
        return _stop(error, "STOP_EXHAUSTED", "retry.budget_exhausted")
    if deadline_at_ms is not None and now_ms + required_budget > deadline_at_ms:
        return _stop(error, "STOP_DEADLINE", "retry.deadline_exceeded")
    return RetryDecision(
        error_id=error.error_id,
        action="RETRY",
        should_retry=True,
        should_reconcile=False,
        current_attempt=error.attempt,
        next_attempt=error.attempt + 1,
        delay_ms=delay,
        scheduled_at_ms=now_ms + delay,
        reason_code="retry.scheduled",
    )


class DynamicTimeoutPolicy(ContractModel):
    model_config = _schema_config("DynamicTimeoutPolicy")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    policy_id: OpaqueId
    revision: int = Field(ge=1)
    phase: OperationPhase
    base_timeout_ms: int = Field(ge=0, le=3_600_000)
    min_timeout_ms: int = Field(ge=1, le=3_600_000)
    max_timeout_ms: int = Field(ge=1, le=3_600_000)
    nominal_throughput_bps: int = Field(ge=1, le=10_000_000_000)
    minimum_throughput_bps: int = Field(ge=1, le=10_000_000_000)
    safety_factor_milli: int = Field(ge=1_000, le=10_000)
    idle_timeout_ms: int = Field(ge=1, le=3_600_000)
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.min_timeout_ms > self.max_timeout_ms:
            raise ValueError("minimum timeout exceeds maximum timeout")
        if self.minimum_throughput_bps > self.nominal_throughput_bps:
            raise ValueError("minimum throughput exceeds nominal throughput")
        if self.idle_timeout_ms > self.max_timeout_ms:
            raise ValueError("idle timeout exceeds operation maximum timeout")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


class TimeoutDecision(ContractModel):
    model_config = _schema_config("TimeoutDecision")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    allowed: bool
    disposition: Literal["COMPUTED", "DEADLINE_TOO_SHORT"]
    phase: OperationPhase
    payload_bytes: int = Field(ge=0, le=2_147_483_648)
    effective_throughput_bps: int = Field(ge=1, le=10_000_000_000)
    timeout_ms: int = Field(ge=0, le=3_600_000)
    idle_timeout_ms: int = Field(ge=0, le=3_600_000)
    deadline_limited: bool
    reason_code: ReasonCode

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.allowed != (self.disposition == "COMPUTED"):
            raise ValueError("only a computed timeout is allowed")
        if not self.allowed and (self.timeout_ms or self.idle_timeout_ms or self.deadline_limited):
            raise ValueError("rejected timeout cannot carry an execution window")
        if self.allowed and (self.timeout_ms == 0 or self.idle_timeout_ms == 0):
            raise ValueError("allowed timeout requires positive operation and idle windows")
        if self.idle_timeout_ms > self.timeout_ms:
            raise ValueError("idle timeout cannot exceed operation timeout")
        return self


def compute_dynamic_timeout(
    policy: DynamicTimeoutPolicy,
    *,
    payload_bytes: int,
    observed_throughput_bps: int | None = None,
    remaining_deadline_ms: int | None = None,
) -> TimeoutDecision:
    if not policy.has_valid_sha256():
        raise ValueError("timeout policy digest is invalid")
    if payload_bytes < 0 or payload_bytes > 2_147_483_648:
        raise ValueError("payload size is outside the contract limit")
    if observed_throughput_bps is not None and observed_throughput_bps < 1:
        raise ValueError("observed throughput must be positive")
    if remaining_deadline_ms is not None and remaining_deadline_ms < 0:
        raise ValueError("remaining deadline cannot be negative")

    observed = observed_throughput_bps or policy.nominal_throughput_bps
    effective_bps = max(
        policy.minimum_throughput_bps,
        min(policy.nominal_throughput_bps, observed),
    )
    transfer_ms = (payload_bytes * 1_000 + effective_bps - 1) // effective_bps
    raw_timeout = (
        (policy.base_timeout_ms + transfer_ms) * policy.safety_factor_milli + 999
    ) // 1_000
    timeout_ms = max(policy.min_timeout_ms, min(policy.max_timeout_ms, raw_timeout))
    if remaining_deadline_ms is not None and remaining_deadline_ms < policy.min_timeout_ms:
        return TimeoutDecision(
            allowed=False,
            disposition="DEADLINE_TOO_SHORT",
            phase=policy.phase,
            payload_bytes=payload_bytes,
            effective_throughput_bps=effective_bps,
            timeout_ms=0,
            idle_timeout_ms=0,
            deadline_limited=False,
            reason_code="timeout.deadline_too_short",
        )
    deadline_limited = remaining_deadline_ms is not None and timeout_ms > remaining_deadline_ms
    if deadline_limited:
        assert remaining_deadline_ms is not None
        timeout_ms = remaining_deadline_ms
    return TimeoutDecision(
        allowed=True,
        disposition="COMPUTED",
        phase=policy.phase,
        payload_bytes=payload_bytes,
        effective_throughput_bps=effective_bps,
        timeout_ms=timeout_ms,
        idle_timeout_ms=min(policy.idle_timeout_ms, timeout_ms),
        deadline_limited=deadline_limited,
        reason_code="timeout.deadline_limited" if deadline_limited else "timeout.computed",
    )


class CircuitBreakerPolicy(ContractModel):
    model_config = _schema_config("CircuitBreakerPolicy")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    policy_id: OpaqueId
    revision: int = Field(ge=1)
    failure_threshold: int = Field(ge=1, le=10_000)
    rolling_window_ms: int = Field(ge=1_000, le=86_400_000)
    open_duration_ms: int = Field(ge=1_000, le=86_400_000)
    half_open_max_in_flight: int = Field(ge=1, le=1_000)
    half_open_success_threshold: int = Field(ge=1, le=1_000)
    policy_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


CircuitState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class CircuitBreakerSnapshot(ContractModel):
    model_config = _schema_config("CircuitBreakerSnapshot")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    breaker_id: OpaqueId
    target_component_id: OpaqueId
    policy_id: OpaqueId
    policy_revision: int = Field(ge=1)
    policy_sha256: Sha256
    state: CircuitState
    revision: int = Field(ge=0)
    window_started_at_ms: int = Field(ge=0)
    counted_failures: int = Field(ge=0, le=10_000)
    opened_at_ms: int | None = Field(default=None, ge=0)
    next_probe_at_ms: int | None = Field(default=None, ge=0)
    half_open_in_flight: int = Field(ge=0, le=1_000)
    half_open_successes: int = Field(ge=0, le=1_000)
    updated_at_ms: int = Field(ge=0)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.updated_at_ms < self.window_started_at_ms:
            raise ValueError("circuit update predates its failure window")
        if self.state == "CLOSED":
            if self.opened_at_ms is not None or self.next_probe_at_ms is not None:
                raise ValueError("closed circuit cannot retain an open deadline")
            if self.half_open_in_flight or self.half_open_successes:
                raise ValueError("closed circuit cannot retain half-open counters")
        else:
            if self.opened_at_ms is None or self.next_probe_at_ms is None:
                raise ValueError("open or half-open circuit requires an open/probe deadline")
        if self.state == "OPEN" and (self.half_open_in_flight or self.half_open_successes):
            raise ValueError("open circuit cannot retain half-open counters")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"snapshot_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"snapshot_sha256": self.computed_sha256()})


class CircuitPermission(ContractModel):
    model_config = _schema_config("CircuitPermission")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    accepted: bool
    probe: bool
    disposition: Literal[
        "ALLOW_CLOSED",
        "ALLOW_PROBE",
        "DENY_OPEN",
        "DENY_PROBE_LIMIT",
        "TIME_INVALID",
        "DIGEST_INVALID",
        "POLICY_MISMATCH",
    ]
    retry_after_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    reason_code: ReasonCode
    previous: CircuitBreakerSnapshot
    current: CircuitBreakerSnapshot

    @model_validator(mode="after")
    def validate_permission(self) -> Self:
        if self.accepted != self.disposition.startswith("ALLOW_"):
            raise ValueError("only ALLOW dispositions may grant circuit permission")
        if self.probe != (self.disposition == "ALLOW_PROBE"):
            raise ValueError("only ALLOW_PROBE may mark a request as a probe")
        if self.disposition == "DENY_OPEN":
            if self.retry_after_ms is None:
                raise ValueError("open circuit denial requires Retry-After")
        elif self.retry_after_ms is not None:
            raise ValueError("only open circuit denial may carry Retry-After")
        if self.disposition == "ALLOW_PROBE":
            if self.current.revision != self.previous.revision + 1:
                raise ValueError("probe acquisition must advance circuit revision")
        elif self.current != self.previous:
            raise ValueError("non-probe permission decision cannot mutate circuit state")
        return self


class CircuitUpdate(ContractModel):
    model_config = _schema_config("CircuitUpdate")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    outcome: Literal["SUCCESS", "COUNTED_FAILURE", "IGNORED_FAILURE"]
    probe: bool
    disposition: Literal[
        "CLOSED_RESET",
        "CLOSED_RECORDED",
        "OPENED",
        "HALF_OPEN_PROGRESS",
        "REOPENED",
        "IGNORED",
        "DIGEST_INVALID",
        "POLICY_MISMATCH",
    ]
    observed_at_ms: int = Field(ge=0)
    evidence_sha256: Sha256
    reason_code: ReasonCode
    previous: CircuitBreakerSnapshot
    current: CircuitBreakerSnapshot

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        unchanged = self.disposition in {"IGNORED", "DIGEST_INVALID", "POLICY_MISMATCH"}
        if unchanged and self.current != self.previous:
            raise ValueError("ignored circuit outcome cannot mutate state")
        if not unchanged and self.current.revision != self.previous.revision + 1:
            raise ValueError("applied circuit outcome must advance revision")
        return self


def new_circuit_breaker(
    policy: CircuitBreakerPolicy,
    *,
    breaker_id: str,
    target_component_id: str,
    now_ms: int,
) -> CircuitBreakerSnapshot:
    if not policy.has_valid_sha256():
        raise ValueError("circuit policy digest is invalid")
    return CircuitBreakerSnapshot(
        breaker_id=breaker_id,
        target_component_id=target_component_id,
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_sha256=policy.policy_sha256,
        state="CLOSED",
        revision=0,
        window_started_at_ms=now_ms,
        counted_failures=0,
        half_open_in_flight=0,
        half_open_successes=0,
        updated_at_ms=now_ms,
        snapshot_sha256="0" * 64,
    ).with_computed_sha256()


def _policy_matches(snapshot: CircuitBreakerSnapshot, policy: CircuitBreakerPolicy) -> bool:
    return (
        snapshot.policy_id == policy.policy_id
        and snapshot.policy_revision == policy.revision
        and snapshot.policy_sha256 == policy.policy_sha256
        and policy.has_valid_sha256()
    )


def acquire_circuit_permission(
    snapshot: CircuitBreakerSnapshot,
    policy: CircuitBreakerPolicy,
    *,
    now_ms: int,
) -> CircuitPermission:
    def decision(
        accepted: bool,
        probe: bool,
        disposition: str,
        reason_code: str,
        current: CircuitBreakerSnapshot,
        retry_after_ms: int | None = None,
    ) -> CircuitPermission:
        return CircuitPermission(
            accepted=accepted,
            probe=probe,
            disposition=disposition,
            retry_after_ms=retry_after_ms,
            reason_code=reason_code,
            previous=snapshot,
            current=current,
        )

    if not snapshot.has_valid_sha256():
        return decision(False, False, "DIGEST_INVALID", "circuit.digest_invalid", snapshot)
    if not _policy_matches(snapshot, policy):
        return decision(False, False, "POLICY_MISMATCH", "circuit.policy_mismatch", snapshot)
    if now_ms < snapshot.updated_at_ms:
        return decision(False, False, "TIME_INVALID", "circuit.request_backdated", snapshot)
    if snapshot.state == "CLOSED":
        return decision(True, False, "ALLOW_CLOSED", "circuit.closed", snapshot)
    if snapshot.state == "OPEN":
        assert snapshot.next_probe_at_ms is not None
        if now_ms < snapshot.next_probe_at_ms:
            return decision(
                False,
                False,
                "DENY_OPEN",
                "circuit.open",
                snapshot,
                retry_after_ms=snapshot.next_probe_at_ms - now_ms,
            )
        current = snapshot.model_copy(
            update={
                "state": "HALF_OPEN",
                "revision": snapshot.revision + 1,
                "half_open_in_flight": 1,
                "half_open_successes": 0,
                "updated_at_ms": now_ms,
            }
        ).with_computed_sha256()
        return decision(True, True, "ALLOW_PROBE", "circuit.half_open_probe", current)
    if snapshot.half_open_in_flight >= policy.half_open_max_in_flight:
        return decision(
            False,
            False,
            "DENY_PROBE_LIMIT",
            "circuit.half_open_probe_limit",
            snapshot,
        )
    current = snapshot.model_copy(
        update={
            "revision": snapshot.revision + 1,
            "half_open_in_flight": snapshot.half_open_in_flight + 1,
            "updated_at_ms": now_ms,
        }
    ).with_computed_sha256()
    return decision(True, True, "ALLOW_PROBE", "circuit.half_open_probe", current)


def record_circuit_outcome(
    snapshot: CircuitBreakerSnapshot,
    policy: CircuitBreakerPolicy,
    *,
    outcome: Literal["SUCCESS", "COUNTED_FAILURE", "IGNORED_FAILURE"],
    probe: bool,
    observed_at_ms: int,
    evidence_sha256: str,
) -> CircuitUpdate:
    def update(
        disposition: str,
        reason_code: str,
        current: CircuitBreakerSnapshot,
    ) -> CircuitUpdate:
        return CircuitUpdate(
            outcome=outcome,
            probe=probe,
            disposition=disposition,
            observed_at_ms=observed_at_ms,
            evidence_sha256=evidence_sha256,
            reason_code=reason_code,
            previous=snapshot,
            current=current,
        )

    if not snapshot.has_valid_sha256():
        return update("DIGEST_INVALID", "circuit.digest_invalid", snapshot)
    if not _policy_matches(snapshot, policy):
        return update("POLICY_MISMATCH", "circuit.policy_mismatch", snapshot)
    if observed_at_ms < snapshot.updated_at_ms:
        return update("IGNORED", "circuit.outcome_backdated", snapshot)
    if (
        snapshot.state == "OPEN"
        or (snapshot.state == "HALF_OPEN" and not probe)
        or (snapshot.state == "HALF_OPEN" and probe and snapshot.half_open_in_flight == 0)
        or (snapshot.state == "CLOSED" and probe)
    ):
        return update("IGNORED", "circuit.outcome_late", snapshot)

    if snapshot.state == "CLOSED":
        if outcome == "IGNORED_FAILURE":
            return update("IGNORED", "circuit.failure_ignored", snapshot)
        if outcome == "SUCCESS":
            current = snapshot.model_copy(
                update={
                    "revision": snapshot.revision + 1,
                    "window_started_at_ms": observed_at_ms,
                    "counted_failures": 0,
                    "updated_at_ms": observed_at_ms,
                }
            ).with_computed_sha256()
            return update("CLOSED_RESET", "circuit.success_reset", current)
        window_expired = observed_at_ms - snapshot.window_started_at_ms > policy.rolling_window_ms
        failure_count = 1 if window_expired else snapshot.counted_failures + 1
        window_started = observed_at_ms if window_expired else snapshot.window_started_at_ms
        if failure_count >= policy.failure_threshold:
            current = snapshot.model_copy(
                update={
                    "state": "OPEN",
                    "revision": snapshot.revision + 1,
                    "window_started_at_ms": window_started,
                    "counted_failures": failure_count,
                    "opened_at_ms": observed_at_ms,
                    "next_probe_at_ms": observed_at_ms + policy.open_duration_ms,
                    "updated_at_ms": observed_at_ms,
                }
            ).with_computed_sha256()
            return update("OPENED", "circuit.failure_threshold_reached", current)
        current = snapshot.model_copy(
            update={
                "revision": snapshot.revision + 1,
                "window_started_at_ms": window_started,
                "counted_failures": failure_count,
                "updated_at_ms": observed_at_ms,
            }
        ).with_computed_sha256()
        return update("CLOSED_RECORDED", "circuit.failure_recorded", current)

    remaining_in_flight = max(0, snapshot.half_open_in_flight - 1)
    if outcome == "COUNTED_FAILURE":
        current = snapshot.model_copy(
            update={
                "state": "OPEN",
                "revision": snapshot.revision + 1,
                "counted_failures": min(10_000, snapshot.counted_failures + 1),
                "opened_at_ms": observed_at_ms,
                "next_probe_at_ms": observed_at_ms + policy.open_duration_ms,
                "half_open_in_flight": 0,
                "half_open_successes": 0,
                "updated_at_ms": observed_at_ms,
            }
        ).with_computed_sha256()
        return update("REOPENED", "circuit.probe_failed", current)
    successes = snapshot.half_open_successes + (outcome == "SUCCESS")
    if outcome == "SUCCESS" and successes >= policy.half_open_success_threshold:
        current = snapshot.model_copy(
            update={
                "state": "CLOSED",
                "revision": snapshot.revision + 1,
                "window_started_at_ms": observed_at_ms,
                "counted_failures": 0,
                "opened_at_ms": None,
                "next_probe_at_ms": None,
                "half_open_in_flight": 0,
                "half_open_successes": 0,
                "updated_at_ms": observed_at_ms,
            }
        ).with_computed_sha256()
        return update("CLOSED_RESET", "circuit.probe_recovered", current)
    current = snapshot.model_copy(
        update={
            "revision": snapshot.revision + 1,
            "half_open_in_flight": remaining_in_flight,
            "half_open_successes": successes,
            "updated_at_ms": observed_at_ms,
        }
    ).with_computed_sha256()
    return update("HALF_OPEN_PROGRESS", "circuit.probe_progress", current)


__all__ = [
    "CircuitBreakerPolicy",
    "CircuitBreakerSnapshot",
    "CircuitPermission",
    "CircuitUpdate",
    "DynamicTimeoutPolicy",
    "ErrorDescriptor",
    "FailureDisposition",
    "OperationPhase",
    "RetryDecision",
    "RetryPolicy",
    "TimeoutDecision",
    "acquire_circuit_permission",
    "compute_dynamic_timeout",
    "decide_retry",
    "new_circuit_breaker",
    "record_circuit_outcome",
]
