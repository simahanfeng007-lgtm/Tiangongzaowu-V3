"""Bounded, trusted conversation projection for cross-request context.

The backend may keep detailed tool state while one request is still running.
That state is audit/runtime data, not conversation memory.  Once a request is
terminal, this projector exposes only the user's request and either a compact
final-result capsule or one interruption checkpoint to the next request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from contracts import canonical_json_bytes, canonical_sha256

from .object_store import ContentAddressedObjectStore, ObjectStoreError
from .store import GatewayStateStore


@dataclass(frozen=True)
class ConversationProjectionPolicy:
    # Contract maxima: durable checkpoints keep long work resumable while
    # provider-specific compaction owns the physical model-window boundary.
    max_turns: int = 64
    max_characters: int = 128_000
    max_message_characters: int = 128_000
    max_key_facts: int = 32
    max_key_fact_characters: int = 2_000

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_turns <= 64
            or not 2_000 <= self.max_characters <= 128_000
            or not 256 <= self.max_message_characters <= self.max_characters
            or not 0 <= self.max_key_facts <= 32
            or not 64 <= self.max_key_fact_characters <= 2_000
        ):
            raise ValueError("conversation projection policy is invalid")


@dataclass(frozen=True)
class ConversationProjection:
    messages: tuple[dict[str, str], ...]
    terminal_capsules: int
    checkpoint_capsules: int
    omitted_turns: int
    source_turns: int

    def metadata(self) -> dict[str, object]:
        return {
            "schema": "tiangong.gateway.conversation-projection.v1",
            "retention_mode": "terminal_result_or_checkpoint",
            "source_turns": self.source_turns,
            "terminal_capsules": self.terminal_capsules,
            "checkpoint_capsules": self.checkpoint_capsules,
            "omitted_turns": self.omitted_turns,
            "message_count": len(self.messages),
            "raw_tool_calls_included": False,
            "raw_tool_results_included": False,
            "projection_sha256": canonical_sha256(list(self.messages)),
        }


@dataclass(frozen=True)
class CapsuleProjectionComparison:
    request_id: str
    legacy_projection_sha256: str
    capsule_projection_sha256: str | None
    equivalent: bool
    capsule_kind: str | None
    model_input_switched: bool = False


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate result payload key")
        result[key] = value
    return result


def _bounded_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    marker = "\n…[已按上下文权重截断]…\n"
    tail = min(800, max(160, limit // 4))
    head = max(1, limit - tail - len(marker))
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def estimate_projected_context_tokens(
    messages: tuple[dict[str, str], ...] | list[dict[str, str]],
    current_request: str = "",
) -> int:
    """Estimate the actual retained conversation projection, not recall fill.

    Non-ASCII text is conservatively counted at one token per character and
    ASCII at four characters per token, plus small per-message framing.  This
    value is observability only; provider-side compaction remains authoritative.
    """

    def count(value: object) -> int:
        text = str(value or "")
        if not text:
            return 0
        ascii_count = sum(1 for character in text if ord(character) < 128)
        return (len(text) - ascii_count) + (ascii_count + 3) // 4

    total = count(current_request) + (4 if str(current_request or "") else 0)
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        total += count(message.get("content")) + 4
    return min(10_000_000, max(0, total))


class SessionContextProjector:
    """Project prior requests from the authoritative gateway journal."""

    def __init__(
        self,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        *,
        policy: ConversationProjectionPolicy | None = None,
    ) -> None:
        self._store = store
        self._objects = objects
        self._policy = policy or ConversationProjectionPolicy()

    def _result_payload(self, object_id: str | None) -> Mapping[str, Any] | None:
        if not object_id:
            return None
        try:
            data = self._objects.read_bytes(object_id)
            value = json.loads(
                data.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite result payload")),
            )
            if not isinstance(value, dict) or canonical_json_bytes(value) != data:
                return None
            return value
        except (OSError, UnicodeDecodeError, ValueError, TypeError, ObjectStoreError):
            return None

    def _terminal_effect(self, request_id: str):
        generation = self._store.get_generation(request_id)
        if generation is None:
            return None
        effects = self._store.list_effects_for_request(
            request_id,
            run_id=generation.run_id,
            generation=generation.generation,
        )
        terminal = [
            item
            for item in effects
            if item.claim.effect_kind == "execution" and item.result is not None
        ]
        return terminal[-1] if terminal else None

    def _key_facts(self, payload: Mapping[str, Any]) -> tuple[str, ...]:
        facts: list[str] = []
        summary = payload.get("process_summary")
        if isinstance(summary, str) and summary.strip():
            facts.append(_bounded_text(summary, self._policy.max_key_fact_characters))
        raw = payload.get("key_facts")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    facts.append(_bounded_text(item, self._policy.max_key_fact_characters))
                if len(facts) >= self._policy.max_key_facts:
                    break
        return tuple(facts[: self._policy.max_key_facts])

    def _assistant_capsule(self, request_id: str) -> tuple[str, bool]:
        effect = self._terminal_effect(request_id)
        result = None if effect is None else effect.result
        payload = self._result_payload(None if result is None else result.result_object_id)
        reply = "" if payload is None else _bounded_text(
            payload.get("reply_text"), self._policy.max_message_characters
        )
        facts = () if payload is None else self._key_facts(payload)
        if result is not None and result.status == "SUCCEEDED" and reply:
            if facts:
                fact_text = "\n".join(f"- {item}" for item in facts)
                reply = _bounded_text(
                    f"{reply}\n\n[过程关键信息]\n{fact_text}",
                    self._policy.max_message_characters,
                )
            return reply, False

        status = "interrupted" if result is None else str(result.status).lower()
        error_code = "history.terminal_result_missing" if result is None else str(
            result.error_code or "history.final_result_unavailable"
        )
        if error_code == "compat.backend.waiting_for_user":
            lines = [
                "[A5授权断点]",
                "状态：等待用户明确授权",
                f"授权码：{error_code}",
                "下一条短消息若明确表示确认、同意或授权，只会绑定本会话唯一一项待确认动作。",
            ]
        else:
            lines = [
                "[断点快照]",
                f"状态：{status}",
                f"错误码：{error_code}",
            ]
        if reply:
            lines.extend(("已确认结果：", reply))
        if facts:
            lines.append("已确认过程事实：")
            lines.extend(f"- {item}" for item in facts)
        lines.append("仅保留此断点；原始工具调用、工具输出和中间推演不进入后续上下文。")
        return _bounded_text("\n".join(lines), self._policy.max_message_characters), True

    def compare_persistent_capsule(
        self,
        request_id: str,
    ) -> CapsuleProjectionComparison:
        """Shadow-compare P3 capsules without changing current model input."""
        legacy_text, legacy_checkpoint = self._assistant_capsule(request_id)
        generation = self._store.get_generation(request_id)
        record = None
        if generation is not None:
            record = self._store.get_terminal_request_capsule(
                request_id,
                run_id=generation.run_id,
                generation=generation.generation,
            ) or self._store.get_active_request_capsule(
                request_id,
                run_id=generation.run_id,
                generation=generation.generation,
            )
        capsule_text: str | None = None
        capsule_kind: str | None = None
        capsule_checkpoint = False
        if record is not None:
            capsule = record.capsule
            capsule_kind = capsule.capsule_kind
            capsule_checkpoint = capsule.capsule_kind != "TERMINAL_RESULT"
            if capsule_checkpoint:
                capsule_text = _bounded_text(
                    "\n".join(
                        (
                            "[断点快照]",
                            f"最近安全步骤：{capsule.latest_safe_step}",
                            f"下一步：{capsule.next_step}",
                            "仅保留恢复所需事实；原始工具调用和中间推演不进入后续上下文。",
                        )
                    ),
                    self._policy.max_message_characters,
                )
            else:
                capsule_text = _bounded_text(
                    capsule.final_result,
                    self._policy.max_message_characters,
                )
        return CapsuleProjectionComparison(
            request_id=request_id,
            legacy_projection_sha256=canonical_sha256(
                {"checkpoint": legacy_checkpoint, "text": legacy_text}
            ),
            capsule_projection_sha256=(
                None
                if capsule_text is None
                else canonical_sha256(
                    {"checkpoint": capsule_checkpoint, "text": capsule_text}
                )
            ),
            equivalent=(
                capsule_text is not None
                and capsule_checkpoint == legacy_checkpoint
                and capsule_text == legacy_text
            ),
            capsule_kind=capsule_kind,
        )

    def project(
        self,
        *,
        session_scope_hash: str,
        before_sequence: int,
        current_request_id: str,
    ) -> ConversationProjection:
        if not session_scope_hash or before_sequence < 1 or not current_request_id:
            raise ValueError("conversation projection scope is invalid")
        candidates = [
            item
            for item in self._store.get_session_queue(session_scope_hash)
            if item.sequence < before_sequence
            and item.request_id != current_request_id
            and item.state == "COMPLETED"
        ]
        turns: list[tuple[dict[str, str], dict[str, str], bool]] = []
        for item in candidates:
            envelope = self._store.get_request_envelope(item.request_id)
            if envelope is None:
                continue
            user_text = _bounded_text(envelope.text, self._policy.max_message_characters)
            if not user_text:
                continue
            assistant_text, checkpoint = self._assistant_capsule(item.request_id)
            turns.append(
                (
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                    checkpoint,
                )
            )

        selected: list[tuple[dict[str, str], dict[str, str], bool]] = []
        used = 0
        for turn in reversed(turns):
            size = len(turn[0]["content"]) + len(turn[1]["content"])
            if len(selected) >= self._policy.max_turns:
                break
            if not selected and size > self._policy.max_characters:
                # The newest turn is the continuity anchor. Under an unusually
                # tight policy, compact that pair instead of silently skipping
                # it and allowing older, smaller turns to win.
                user_budget = max(256, min(
                    self._policy.max_message_characters,
                    self._policy.max_characters // 3,
                ))
                assistant_budget = max(256, self._policy.max_characters - user_budget)
                turn = (
                    {"role": "user", "content": _bounded_text(turn[0]["content"], user_budget)},
                    {"role": "assistant", "content": _bounded_text(turn[1]["content"], assistant_budget)},
                    turn[2],
                )
                size = len(turn[0]["content"]) + len(turn[1]["content"])
            if used + size > self._policy.max_characters:
                # Conversation history is a recency-ordered suffix.  Once a
                # newer turn cannot fit, backfilling with older shorter turns
                # would create a false continuity gap and distort context
                # weight, so stop at this boundary.
                break
            selected.append(turn)
            used += size
        selected.reverse()
        messages = tuple(message for user, assistant, _ in selected for message in (user, assistant))
        checkpoints = sum(1 for _, _, checkpoint in selected if checkpoint)
        return ConversationProjection(
            messages=messages,
            terminal_capsules=len(selected) - checkpoints,
            checkpoint_capsules=checkpoints,
            omitted_turns=max(0, len(turns) - len(selected)),
            source_turns=len(turns),
        )


__all__ = [
    "CapsuleProjectionComparison",
    "ConversationProjection",
    "ConversationProjectionPolicy",
    "SessionContextProjector",
    "estimate_projected_context_tokens",
]
