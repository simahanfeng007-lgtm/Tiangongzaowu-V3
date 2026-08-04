"""Dynamic timeout, account budget and durable progress for WeChat file transfer."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass

from contracts import DynamicTimeoutPolicy, TimeoutDecision, canonical_sha256, compute_dynamic_timeout

from .delivery_ledger import DeliveryLedger, DeliveryTransferProgressFact
from .wechat_text_outbound import WechatOutboundPolicy


class WechatTransferControlError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WechatTransferReservation:
    account_key: str
    reserved_bytes: int


class WechatTransferBudget:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._in_flight: dict[str, int] = {}
        self._reserved_bytes: dict[str, int] = {}
        self._observed_bps: dict[str, int] = {}

    @contextmanager
    def reserve(
        self,
        account_key: str,
        *,
        size_bytes: int,
        max_concurrent: int,
        max_reserved_bytes: int,
    ):
        if not account_key or size_bytes < 1:
            raise ValueError("WeChat transfer reservation is invalid")
        with self._lock:
            in_flight = self._in_flight.get(account_key, 0)
            reserved = self._reserved_bytes.get(account_key, 0)
            if in_flight >= max_concurrent:
                raise WechatTransferControlError("wechat.file.account_concurrency.exceeded")
            if reserved + size_bytes > max_reserved_bytes:
                raise WechatTransferControlError("wechat.file.account_byte_budget.exceeded")
            self._in_flight[account_key] = in_flight + 1
            self._reserved_bytes[account_key] = reserved + size_bytes
        try:
            yield WechatTransferReservation(account_key, size_bytes)
        finally:
            with self._lock:
                current_count = self._in_flight.get(account_key, 0)
                current_bytes = self._reserved_bytes.get(account_key, 0)
                if current_count <= 1:
                    self._in_flight.pop(account_key, None)
                else:
                    self._in_flight[account_key] = current_count - 1
                if current_bytes <= size_bytes:
                    self._reserved_bytes.pop(account_key, None)
                else:
                    self._reserved_bytes[account_key] = current_bytes - size_bytes

    def observed_throughput(self, account_key: str) -> int | None:
        with self._lock:
            return self._observed_bps.get(account_key)

    def observe(self, account_key: str, *, bytes_transferred: int, elapsed_ms: int) -> int:
        if bytes_transferred < 1 or elapsed_ms < 1:
            raise ValueError("WeChat throughput observation is invalid")
        measured = max(1, bytes_transferred * 1_000 // elapsed_ms)
        with self._lock:
            previous = self._observed_bps.get(account_key)
            smoothed = measured if previous is None else (previous * 3 + measured) // 4
            self._observed_bps[account_key] = smoothed
            return smoothed


def compute_wechat_upload_timeout(
    policy: WechatOutboundPolicy,
    *,
    payload_bytes: int,
    ticket_timeout_ms: int,
    observed_throughput_bps: int | None,
) -> TimeoutDecision:
    timeout_policy = DynamicTimeoutPolicy(
        policy_id="wechat.file.upload.dynamic.v1",
        revision=1,
        phase="channel_upload",
        base_timeout_ms=policy.upload_base_timeout_ms,
        min_timeout_ms=policy.upload_min_timeout_ms,
        max_timeout_ms=policy.upload_max_timeout_ms,
        nominal_throughput_bps=policy.upload_nominal_throughput_bps,
        minimum_throughput_bps=policy.upload_minimum_throughput_bps,
        safety_factor_milli=policy.upload_safety_factor_milli,
        idle_timeout_ms=policy.upload_idle_timeout_ms,
        policy_sha256="0" * 64,
    ).with_computed_sha256()
    return compute_dynamic_timeout(
        timeout_policy,
        payload_bytes=payload_bytes,
        observed_throughput_bps=observed_throughput_bps,
        remaining_deadline_ms=ticket_timeout_ms,
    )


class WechatProgressRecorder:
    def __init__(
        self,
        ledger: DeliveryLedger,
        *,
        effect_id: str,
        part_id: str,
        part_index: int,
        phase: str,
        total_bytes: int,
        interval_bytes: int,
        clock_ms,
    ) -> None:
        if total_bytes < 1 or interval_bytes < 1:
            raise ValueError("WeChat progress range is invalid")
        self._ledger = ledger
        self._effect_id = effect_id
        self._part_id = part_id
        self._part_index = part_index
        self._phase = phase
        self._total = total_bytes
        self._interval = interval_bytes
        self._clock_ms = clock_ms
        self._recorded = 0

    def update(self, completed_bytes: int, *, force: bool = False) -> None:
        if completed_bytes < self._recorded or completed_bytes > self._total:
            raise WechatTransferControlError("wechat.file.progress.invalid")
        if completed_bytes == 0:
            return
        if not force and completed_bytes < self._total and (
            completed_bytes - self._recorded < self._interval
        ):
            return
        if completed_bytes == self._recorded:
            return
        fact = DeliveryTransferProgressFact(
            effect_id=self._effect_id,
            part_id=self._part_id,
            part_index=self._part_index,
            phase=self._phase,
            bytes_completed=completed_bytes,
            total_bytes=self._total,
            observed_at_ms=self._clock_ms(),
            evidence_sha256=canonical_sha256(
                {
                    "domain": "tiangong.communication.wechat-transfer-progress.v1",
                    "effect_id": self._effect_id,
                    "part_id": self._part_id,
                    "phase": self._phase,
                    "bytes_completed": completed_bytes,
                    "total_bytes": self._total,
                }
            ),
            progress_sha256="0" * 64,
        ).with_computed_sha256()
        self._ledger.record_transfer_progress(fact)
        self._recorded = completed_bytes


__all__ = [
    "WechatProgressRecorder",
    "WechatTransferBudget",
    "WechatTransferControlError",
    "WechatTransferReservation",
    "compute_wechat_upload_timeout",
]
