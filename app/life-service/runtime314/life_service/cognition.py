"""G2 unified cognition shadow: persistent stimulus inbox + bounded lanes.

This module is shadow-only.  The legacy four sovereign schedulers remain the
production authority until the canary cutover; the vNext cognition path records
its durable stimulus inbox, lane leases, root/child projection and structured
model attempt shadow in ``LifeShadowStore`` without changing legacy writes.

Contract: the cognition decider is model IO and MUST run outside any Life
writer lock (invariant I14).  This coordinator never acquires the runtime
writer lock; callers must not invoke ``run_pass`` while holding it.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .store import LifeShadowStore, LifeShadowStoreError


MAX_FOREGROUND_STREAK = 8
"""Anti-starvation bound frozen for G2: after this many consecutive foreground
selections the next selection is forced to the oldest background stimulus."""

LANE_DURATION_MS = 120_000
MAX_INFLIGHT_MODEL_CALLS_PER_LIFE = 2


@dataclass(frozen=True)
class CognitionTrigger:
    """One durable stimulus inbox row."""

    event_id: str
    lane: str
    base_priority: int
    payload_sha256: str
    coalesce: bool = False


class UnifiedCognitionShadow:
    """One cognition entry point for the vNext sidecar."""

    def __init__(
        self,
        store: LifeShadowStore,
        *,
        cognition_decider: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        binding_factory: Callable[[str, str, int], str] | None = None,
        max_foreground_streak: int = MAX_FOREGROUND_STREAK,
        lane_duration_ms: int = LANE_DURATION_MS,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._decider = cognition_decider
        self._binding_factory = binding_factory
        self._max_streak = max_foreground_streak
        self._lane_duration_ms = lane_duration_ms
        self._now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)

    def enqueue(self, life_id: str, trigger: CognitionTrigger) -> bool:
        return self._store.enqueue_stimulus(
            life_id,
            trigger.event_id,
            lane=trigger.lane,
            base_priority=trigger.base_priority,
            payload_sha256=trigger.payload_sha256,
            enqueued_at_ms=self._now_fn(),
            coalesce=trigger.coalesce,
        )

    def set_decider(
        self, decider: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None
    ) -> None:
        if decider is not None and not callable(decider):
            raise TypeError("cognition decider must be callable")
        self._decider = decider

    def enqueue_many(
        self, life_id: str, triggers: list[CognitionTrigger]
    ) -> dict[str, int]:
        created = 0
        deduped = 0
        coalesced = 0
        for trigger in triggers:
            if self.enqueue(life_id, trigger):
                created += 1
            elif trigger.coalesce and trigger.lane == "background":
                coalesced += 1
            else:
                deduped += 1
        return {"created": created, "deduped": deduped, "coalesced": coalesced}

    def run_pass(self, life_id: str, *, owner_instance_id: str) -> dict[str, Any]:
        """Select, claim and process exactly one stimulus per pass."""
        now_ms = self._now_fn()
        claim = f"{owner_instance_id}:{now_ms}"
        item = self._store.select_next_stimulus(
            life_id,
            claim_token=claim,
            now_ms=now_ms,
            max_foreground_streak=self._max_streak,
        )
        if item is None:
            return {"processed": False, "reason": "empty"}
        lease = self._store.acquire_lane(
            life_id,
            str(item["lane"]),
            owner_instance_id=owner_instance_id,
            now_ms=now_ms,
            duration_ms=self._lane_duration_ms,
        )
        if lease is None:
            self._store.release_stimulus(
                enqueue_seq=int(item["enqueue_seq"]), claim_token=claim
            )
            return {"processed": False, "reason": "lane_busy"}
        try:
            root_experience_id = ""
            if self._binding_factory is not None:
                root_experience_id = self._binding_factory(
                    life_id, str(item["event_id"]), now_ms
                )
            if self._decider is None:
                self._record_shadow(
                    life_id,
                    item,
                    root_experience_id=root_experience_id,
                    status="preflight_unavailable",
                    slot_no=1,
                    now_ms=now_ms,
                )
                self._store.commit_stimulus(
                    life_id,
                    enqueue_seq=int(item["enqueue_seq"]),
                    claim_token=claim,
                    now_ms=now_ms,
                )
                return {
                    "processed": True,
                    "lane": item["lane"],
                    "event_id": item["event_id"],
                    "decider": None,
                }
            decision = self._decider(
                {
                    "life_id": life_id,
                    "event_id": item["event_id"],
                    "lane": item["lane"],
                    "payload_sha256": item["payload_sha256"],
                    "root_experience_id": root_experience_id,
                    "now_ms": now_ms,
                }
            )
            if not isinstance(decision, Mapping):
                raise LifeShadowStoreError("cognition decider result is invalid")
            self._record_shadow(
                life_id,
                item,
                root_experience_id=root_experience_id,
                status="succeeded",
                slot_no=1,
                now_ms=now_ms,
                decision=decision,
            )
            self._store.commit_stimulus(
                life_id,
                enqueue_seq=int(item["enqueue_seq"]),
                claim_token=claim,
                now_ms=now_ms,
            )
            return {
                "processed": True,
                "lane": item["lane"],
                "event_id": item["event_id"],
                "decision": dict(decision),
            }
        except Exception:
            # A crashing decider pass must not strand the stimulus in
            # `selected` forever. Record a `failed` shadow and consume the
            # stimulus so the queue keeps flowing; releasing it back to
            # pending would re-select the same failing item head-of-line.
            self._record_shadow(
                life_id,
                item,
                root_experience_id=root_experience_id,
                status="failed",
                slot_no=1,
                now_ms=now_ms,
            )
            self._store.commit_stimulus(
                life_id,
                enqueue_seq=int(item["enqueue_seq"]),
                claim_token=claim,
                now_ms=now_ms,
            )
            raise
        finally:
            self._store.release_lane(life_id, str(item["lane"]), lease_id=lease)

    def run_drain(
        self,
        life_id: str,
        *,
        owner_instance_id: str,
        max_items: int = 1000,
    ) -> dict[str, Any]:
        processed = 0
        while processed < max_items:
            result = self.run_pass(life_id, owner_instance_id=owner_instance_id)
            if not result.get("processed"):
                break
            processed += 1
        return {
            "processed": processed,
            "health": self._store.cognition_health(life_id),
        }

    def _record_shadow(
        self,
        life_id: str,
        item: Mapping[str, object],
        *,
        root_experience_id: str,
        status: str,
        slot_no: int,
        now_ms: int,
        decision: Mapping[str, object] | None = None,
    ) -> None:
        event_id = str(item["event_id"])
        payload = str(item["payload_sha256"])
        lane = str(item["lane"])
        request_sha256 = hashlib.sha256(
            f"tiangong.v21.model-attempt-request.v1\0{event_id}\0{payload}".encode("utf-8")
        ).hexdigest()
        provider = str((decision or {}).get("provider") or "shadow")
        model = str((decision or {}).get("model") or "none")
        finish_reason = (
            str((decision or {}).get("finish_reason") or "")
            if decision is not None
            else None
        )
        output = (decision or {}).get("output_text")
        output_sha256 = (
            hashlib.sha256(str(output).encode("utf-8")).hexdigest()
            if output is not None
            else None
        )
        attempt_shadow_id = "mas_" + hashlib.sha256(
            (
                f"tiangong.v21.model-attempt-shadow.v1\0{life_id}\0"
                f"{event_id}\0{slot_no}"
            ).encode("utf-8")
        ).hexdigest()
        payload_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "attempt_shadow_id": attempt_shadow_id,
                    "life_id": life_id,
                    "root_experience_id": root_experience_id,
                    "episode_id": event_id,
                    "lane": lane,
                    "slot_no": slot_no,
                    "provider": provider,
                    "model": model,
                    "request_sha256": request_sha256,
                    "status": status,
                    "finish_reason": finish_reason,
                    "output_text_sha256": output_sha256,
                    "started_at_ms": now_ms,
                    "completed_at_ms": now_ms,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._store.put_model_attempt_shadow(
            attempt_shadow_id=attempt_shadow_id,
            life_id=life_id,
            root_experience_id=root_experience_id,
            episode_id=event_id,
            lane=lane,
            slot_no=slot_no,
            provider=provider,
            model=model,
            request_sha256=request_sha256,
            status=status,
            finish_reason=finish_reason,
            output_text_sha256=output_sha256,
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
            payload_sha256=payload_sha256,
        )
