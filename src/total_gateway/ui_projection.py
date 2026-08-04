"""Evidence-aware UI projection for execution, artifact QC, and delivery lanes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from contracts import (
    StateSnapshot,
    aggregate_request_status,
    canonical_sha256,
)
from contracts.models import ContractModel
from contracts.state_machine import STATE_VALUES


LaneName = Literal["execution", "artifact", "delivery"]
LaneSource = Literal["VERIFIED_LEDGER", "LEGACY_OBSERVATION", "ABSENT"]
LaneTone = Literal["pending", "running", "done", "failed", "blocked"]


_LABELS: dict[str, str] = {
    "NOT_STARTED": "尚未开始",
    "PLANNED": "已规划",
    "TICKET_ISSUED": "已授权",
    "CLAIMED": "已领取",
    "RUNNING": "处理中",
    "SUCCEEDED": "执行成功",
    "FAILED_RETRYABLE": "失败，可安全重试",
    "FAILED_FINAL": "失败",
    "AMBIGUOUS": "结果不明，等待对账",
    "RECONCILE_REQUIRED": "必须对账，禁止盲目重发",
    "CANCELLED": "已取消",
    "FENCED": "已被新代任务隔离",
    "NOT_REQUIRED": "无需产物",
    "PENDING": "等待产物事实",
    "CREATED": "产物已生成，等待质检",
    "QC_PENDING": "产物质检中",
    "QC_PASSED": "产物质检通过",
    "QC_FAILED": "产物质检失败",
    "REJECTED": "产物已拒绝",
    "SUPERSEDED": "产物已被新版本替代",
    "NOT_PLANNED": "尚未计划投递",
    "FETCHING": "正在取件",
    "UPLOADING": "正在上传",
    "SENDING": "正在发送",
    "CHANNEL_ACCEPTED": "渠道已接受",
    "DELIVERED": "对端已送达",
}

_MACHINE_LABEL_OVERRIDES: dict[tuple[LaneName, str], str] = {
    ("execution", "FAILED_RETRYABLE"): "执行失败，可安全重试",
    ("execution", "FAILED_FINAL"): "执行失败",
    ("delivery", "FAILED_RETRYABLE"): "投递失败，可安全重试",
    ("delivery", "FAILED_FINAL"): "投递失败",
    ("delivery", "TICKET_ISSUED"): "投递已授权",
}


def _tone(machine: LaneName, state: str) -> LaneTone:
    if state in {"AMBIGUOUS", "RECONCILE_REQUIRED", "FENCED"}:
        return "blocked"
    if state in {"FAILED_RETRYABLE", "FAILED_FINAL", "QC_FAILED", "REJECTED"}:
        return "failed"
    if state in {"SUCCEEDED", "QC_PASSED", "NOT_REQUIRED", "CHANNEL_ACCEPTED", "DELIVERED"}:
        return "done"
    if state in {"NOT_STARTED", "PENDING", "NOT_PLANNED", "PLANNED"}:
        return "pending"
    if state == "CANCELLED":
        return "blocked"
    return "running"


class GatewayUiLane(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    machine: LaneName
    state: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    tone: LaneTone
    source: LaneSource
    evidence_verified: bool
    entity_count: int = Field(ge=0)
    reason_code: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_lane(self) -> Self:
        if self.state not in STATE_VALUES[self.machine]:
            raise ValueError("UI lane state does not belong to its machine")
        if self.evidence_verified != (self.source == "VERIFIED_LEDGER"):
            raise ValueError("only verified ledger lanes may claim verified evidence")
        return self


class GatewayUiProjection(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    projection_schema: Literal["tiangong.gateway.ui-projection.v1"] = (
        "tiangong.gateway.ui-projection.v1"
    )
    gateway_request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    presentation_request_id: str = Field(min_length=1, max_length=160)
    request_state: str = Field(min_length=1, max_length=64)
    overall_phase: Literal[
        "queued",
        "executing",
        "qc",
        "delivering",
        "channel_accepted",
        "delivered",
        "completed",
        "partial",
        "failed",
        "cancelled",
        "reconcile_required",
    ]
    needs_reconciliation: bool
    execution: GatewayUiLane
    artifact: GatewayUiLane
    delivery: GatewayUiLane
    observed_at_ms: int = Field(ge=0)
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if (self.overall_phase == "reconcile_required") != self.needs_reconciliation:
            raise ValueError("UI reconciliation flag disagrees with overall phase")
        if (self.execution.machine, self.artifact.machine, self.delivery.machine) != (
            "execution",
            "artifact",
            "delivery",
        ):
            raise ValueError("UI projection lanes are out of order")
        if self.request_state not in STATE_VALUES["request"]:
            raise ValueError("UI request state is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"projection_sha256"}))

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"projection_sha256": self.computed_sha256()})


def _choose_state(machine: LaneName, snapshots: tuple[StateSnapshot, ...]) -> str:
    states = {item.state for item in snapshots}
    if machine == "execution":
        precedence = (
            "RECONCILE_REQUIRED",
            "AMBIGUOUS",
            "FAILED_FINAL",
            "FENCED",
            "FAILED_RETRYABLE",
            "CANCELLED",
            "RUNNING",
            "CLAIMED",
            "TICKET_ISSUED",
            "PLANNED",
            "NOT_STARTED",
            "SUCCEEDED",
        )
    elif machine == "artifact":
        precedence = (
            "QC_FAILED",
            "REJECTED",
            "QC_PENDING",
            "CREATED",
            "PENDING",
            "QC_PASSED",
            "SUPERSEDED",
            "NOT_REQUIRED",
        )
    else:
        precedence = (
            "RECONCILE_REQUIRED",
            "AMBIGUOUS",
            "FAILED_FINAL",
            "FENCED",
            "FAILED_RETRYABLE",
            "CANCELLED",
            "SENDING",
            "UPLOADING",
            "FETCHING",
            "TICKET_ISSUED",
            "PLANNED",
            "NOT_PLANNED",
            "CHANNEL_ACCEPTED",
            "DELIVERED",
        )
    return next((state for state in precedence if state in states), precedence[-1])


def _legacy_execution_state(payload: Mapping[str, object], journal_state: str) -> str:
    run = payload.get("run")
    run_payload = run if isinstance(run, Mapping) else {}
    raw = str(
        run_payload.get("status")
        or run_payload.get("phase")
        or run_payload.get("stage")
        or ""
    ).strip().upper()
    if raw in {"AMBIGUOUS", "RECONCILE_REQUIRED", "UNKNOWN_OUTCOME"}:
        return "RECONCILE_REQUIRED"
    if raw in {"FAILED", "FAILURE", "FAILED_SAFE", "ERROR", "BLOCKED"}:
        return "FAILED_FINAL"
    if raw in {"CANCELLED", "CANCELED", "ABORTED", "INTERRUPTED"}:
        return "CANCELLED"
    if raw in {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE"}:
        return "SUCCEEDED"
    if raw:
        return "RUNNING"
    return "NOT_STARTED" if journal_state == "QUEUED" else "RUNNING"


def _lane(
    machine: LaneName,
    state: str,
    *,
    source: LaneSource,
    count: int,
) -> GatewayUiLane:
    return GatewayUiLane(
        machine=machine,
        state=state,
        label=_MACHINE_LABEL_OVERRIDES.get((machine, state), _LABELS[state]),
        tone=_tone(machine, state),
        source=source,
        evidence_verified=source == "VERIFIED_LEDGER",
        entity_count=count,
        reason_code=f"ui.{machine}.{state.lower()}",
    )


def build_gateway_ui_projection(
    *,
    gateway_request_id: str,
    presentation_request_id: str,
    journal_state: str,
    snapshots: Iterable[StateSnapshot],
    legacy_status: Mapping[str, object],
    observed_at_ms: int,
) -> GatewayUiProjection:
    grouped: dict[str, tuple[StateSnapshot, ...]] = {}
    items = tuple(snapshots)
    for item in items:
        if item.request_id != gateway_request_id:
            raise ValueError("UI snapshot belongs to another request")
    for machine in ("request", "execution", "artifact", "delivery"):
        grouped[machine] = tuple(item for item in items if item.machine == machine)

    request_items = grouped["request"]
    if len(request_items) > 1:
        raise ValueError("UI projection has more than one request authority")
    if request_items:
        request_snapshot = request_items[0]
        request_state = request_snapshot.state
    elif journal_state == "QUEUED":
        request_snapshot = None
        request_state = "QUEUED"
    elif journal_state == "COMPLETED":
        request_snapshot = None
        request_state = "COMPLETED"
    else:
        request_snapshot = None
        request_state = "EXECUTING"

    if request_snapshot is not None:
        def current_scope(item: StateSnapshot) -> bool:
            return (
                item.run_id == request_snapshot.run_id
                and item.generation == request_snapshot.generation
            )
    else:
        current_generation = max((item.generation for item in items), default=0)

        def current_scope(item: StateSnapshot) -> bool:
            return item.generation == current_generation

    execution_items = tuple(item for item in grouped["execution"] if current_scope(item))
    artifact_items = tuple(item for item in grouped["artifact"] if current_scope(item))
    delivery_items = tuple(item for item in grouped["delivery"] if current_scope(item))
    execution_state = (
        _choose_state("execution", execution_items)
        if execution_items
        else _legacy_execution_state(legacy_status, journal_state)
    )
    artifact_state = _choose_state("artifact", artifact_items) if artifact_items else "PENDING"
    delivery_state = _choose_state("delivery", delivery_items) if delivery_items else "NOT_PLANNED"
    execution = _lane(
        "execution",
        execution_state,
        source="VERIFIED_LEDGER" if execution_items else "LEGACY_OBSERVATION",
        count=len(execution_items),
    )
    artifact = _lane(
        "artifact",
        artifact_state,
        source="VERIFIED_LEDGER" if artifact_items else "ABSENT",
        count=len(artifact_items),
    )
    delivery = _lane(
        "delivery",
        delivery_state,
        source="VERIFIED_LEDGER" if delivery_items else "ABSENT",
        count=len(delivery_items),
    )

    if request_snapshot is not None:
        aggregate = aggregate_request_status(
            request_snapshot,
            executions=execution_items,
            artifacts=artifact_items,
            deliveries=delivery_items,
        )
        overall = {
            "received": "queued",
            "planning": "queued",
            "waiting_confirmation": "queued",
            "executing": "executing",
            "validating": "qc",
            "delivering": "delivering",
            "channel_accepted": "channel_accepted",
            "delivered": "delivered",
            "partial": "partial",
            "failed": "failed",
            "cancelled": "cancelled",
            "superseded": "cancelled",
            "reconcile_required": "reconcile_required",
        }[aggregate.display_phase]
    else:
        if execution.state in {"AMBIGUOUS", "RECONCILE_REQUIRED"} or delivery.state in {
            "AMBIGUOUS",
            "RECONCILE_REQUIRED",
        }:
            overall = "reconcile_required"
        elif execution.tone == "failed" or artifact.tone == "failed" or delivery.tone == "failed":
            overall = "failed"
        elif execution.state == "CANCELLED" or delivery.state == "CANCELLED":
            overall = "cancelled"
        elif delivery.state == "DELIVERED":
            overall = "delivered"
        elif delivery.state == "CHANNEL_ACCEPTED":
            overall = "channel_accepted"
        elif delivery.state not in {"NOT_PLANNED", "PLANNED"}:
            overall = "delivering"
        elif artifact.state in {"CREATED", "QC_PENDING", "PENDING"} and execution.state == "SUCCEEDED":
            overall = "qc"
        elif execution.state == "SUCCEEDED":
            overall = "completed"
        elif journal_state == "QUEUED":
            overall = "queued"
        else:
            overall = "executing"

    return GatewayUiProjection(
        gateway_request_id=gateway_request_id,
        presentation_request_id=presentation_request_id,
        request_state=request_state,
        overall_phase=overall,
        needs_reconciliation=overall == "reconcile_required",
        execution=execution,
        artifact=artifact,
        delivery=delivery,
        observed_at_ms=observed_at_ms,
        projection_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "GatewayUiLane",
    "GatewayUiProjection",
    "build_gateway_ui_projection",
]
