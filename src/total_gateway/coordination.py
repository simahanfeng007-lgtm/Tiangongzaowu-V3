"""Persistent generation-lease views and fenced-result decisions."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import FenceDecision, GenerationFence


@dataclass(frozen=True)
class GenerationLeaseView:
    request_id: str
    run_id: str
    run_sequence: int
    generation: int
    gateway_epoch: int
    lease_id: str | None
    owner_instance_id: str
    status: str
    fence: GenerationFence
    revision: int
    updated_at_ms: int
    cancel_reason_code: str | None


@dataclass(frozen=True)
class FencedResultDecision:
    result_id: str
    request_id: str
    run_id: str
    generation: int
    fence_id: str
    disposition: str
    reason_code: str
    result_sha256: str
    observed_at_ms: int
    persisted_by_this_call: bool
    duplicate: bool
    fence_decision: FenceDecision | None


__all__ = ["FencedResultDecision", "GenerationLeaseView"]
