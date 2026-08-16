"""P18.1 canonical provider-turn contract.

This is a wire/turn contract only.  It is not task state and cannot become a
second Runtime or Continuity Authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
import uuid
from typing import Any, Mapping, Sequence


PROVIDER_TURN_SCHEMA = "tiangong.v3.provider_turn_envelope.v1"
TOOL_CALL_BINDING_SCHEMA = "tiangong.v3.tool_call_binding.v1"
CONTINUATION_SCHEMA = "tiangong.v3.provider_continuation_state.v1"
_MAX_CONTINUATION_BYTES = 64 * 1024


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolCallBinding:
    canonical_call_id: str
    provider_call_id: str
    tool_name: str
    protocol_family: str
    binding_type: str
    provider_item_id: str = ""
    sequence_index: int = 0
    binding_hash: str = ""
    schema: str = TOOL_CALL_BINDING_SCHEMA

    def __post_init__(self) -> None:
        canonical = str(self.canonical_call_id or self.provider_call_id or "").strip()
        provider = str(self.provider_call_id or canonical).strip()
        if not canonical or not provider:
            raise ValueError("tool call binding requires canonical/provider call ids")
        object.__setattr__(self, "canonical_call_id", canonical)
        object.__setattr__(self, "provider_call_id", provider)
        if not self.binding_hash:
            object.__setattr__(self, "binding_hash", stable_hash({
                "canonical_call_id": canonical,
                "provider_call_id": provider,
                "tool_name": self.tool_name,
                "protocol_family": self.protocol_family,
                "binding_type": self.binding_type,
                "provider_item_id": self.provider_item_id,
                "sequence_index": self.sequence_index,
            }))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "canonical_call_id": self.canonical_call_id,
            "provider_call_id": self.provider_call_id,
            "tool_name": self.tool_name,
            "protocol_family": self.protocol_family,
            "binding_type": self.binding_type,
            "provider_item_id": self.provider_item_id,
            "sequence_index": self.sequence_index,
            "binding_hash": self.binding_hash,
        }


@dataclass(frozen=True, slots=True)
class ProviderContinuationState:
    mode: str = "none"
    opaque_payload: Any = None
    state_hash: str = ""
    created_from_turn_hash: str = ""
    provider_identity: str = ""
    protocol_family: str = ""
    model_id: str = ""
    expires_at: float | None = None
    portable: bool = False
    schema: str = CONTINUATION_SCHEMA

    def __post_init__(self) -> None:
        allowed = {"none", "local_replay", "opaque_client_state", "remote_optional"}
        if self.mode not in allowed:
            raise ValueError(f"unsupported continuation mode: {self.mode}")
        encoded = json.dumps(self.opaque_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        if len(encoded) > _MAX_CONTINUATION_BYTES:
            raise ValueError("provider continuation state exceeds bounded limit")
        if self.mode == "none" and self.opaque_payload not in (None, {}, [], ""):
            raise ValueError("continuation mode none cannot carry opaque payload")
        if not self.state_hash:
            object.__setattr__(self, "state_hash", stable_hash({
                "mode": self.mode,
                "opaque_payload": self.opaque_payload,
                "provider_identity": self.provider_identity,
                "protocol_family": self.protocol_family,
                "model_id": self.model_id,
            }))
        # Provider-private continuation is deliberately non-portable.  A
        # provider switch must cold-rehydrate from P18 structured authority.
        if self.portable:
            raise ValueError("provider continuation state must not be portable")

    def is_stale(self, now: float | None = None) -> bool:
        return self.expires_at is not None and float(self.expires_at) <= float(now if now is not None else time.time())

    def compatible_with(self, *, provider_identity: str, protocol_family: str, model_id: str) -> bool:
        if self.mode == "none" or self.is_stale():
            return False
        return (
            self.provider_identity == provider_identity
            and self.protocol_family == protocol_family
            and self.model_id == model_id
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode,
            "opaque_payload": self.opaque_payload,
            "state_hash": self.state_hash,
            "created_from_turn_hash": self.created_from_turn_hash,
            "provider_identity": self.provider_identity,
            "protocol_family": self.protocol_family,
            "model_id": self.model_id,
            "expires_at": self.expires_at,
            "portable": False,
        }


class ProviderTurnEnvelope(str):
    """One normalized provider turn with backward-compatible text behavior.

    ``str(envelope)`` intentionally remains the legacy Gutong wire. Structured
    fields are the authority for provider call IDs and continuation state.
    Private reasoning is never serialized into that string.
    """

    def __new__(
        cls,
        value: Any,
        *,
        turn_id: str = "",
        provider_identity: str = "",
        service_preset: str = "",
        protocol_family: str = "",
        optimization_family: str = "",
        model_id: str = "",
        visible_text: str = "",
        tool_calls: Sequence[Mapping[str, Any]] | None = None,
        tool_call_bindings: Sequence[ToolCallBinding | Mapping[str, Any]] | None = None,
        private_reasoning: str = "",
        provider_continuation_state: ProviderContinuationState | Mapping[str, Any] | None = None,
        provider_continuation_mode: str = "none",
        provider_continuation_hash: str = "",
        finish_reason: str = "",
        stop_semantics: str = "",
        usage: Mapping[str, Any] | None = None,
        stream_metadata: Mapping[str, Any] | None = None,
        retry_semantics: Mapping[str, Any] | None = None,
        raw_response_hash: str = "",
        # v0 compatibility spelling; never changes provider_identity semantics.
        provider_id: str = "",
    ):
        obj = super().__new__(cls, str(value or ""))
        obj.schema = PROVIDER_TURN_SCHEMA
        obj.turn_id = str(turn_id or f"turn_{uuid.uuid4().hex}")
        obj.provider_identity = str(provider_identity or provider_id or "")
        obj.service_preset = str(service_preset or "")
        obj.protocol_family = str(protocol_family or "")
        obj.optimization_family = str(optimization_family or provider_id or "")
        obj.model_id = str(model_id or "")
        obj.visible_text = str(visible_text or "")
        obj.tool_calls = tuple(dict(item) for item in (tool_calls or []) if isinstance(item, Mapping))

        bindings: list[ToolCallBinding] = []
        for index, item in enumerate(tool_call_bindings or []):
            if isinstance(item, ToolCallBinding):
                bindings.append(item)
            elif isinstance(item, Mapping):
                bindings.append(ToolCallBinding(
                    canonical_call_id=str(item.get("canonical_call_id") or item.get("provider_call_id") or ""),
                    provider_call_id=str(item.get("provider_call_id") or item.get("canonical_call_id") or ""),
                    tool_name=str(item.get("tool_name") or ""),
                    protocol_family=str(item.get("protocol_family") or protocol_family or ""),
                    binding_type=str(item.get("binding_type") or "prompt_contract"),
                    provider_item_id=str(item.get("provider_item_id") or ""),
                    sequence_index=int(item.get("sequence_index") if item.get("sequence_index") is not None else index),
                    binding_hash=str(item.get("binding_hash") or ""),
                ))
        obj.tool_call_bindings = tuple(bindings)
        obj.private_reasoning = str(private_reasoning or "")

        continuation: ProviderContinuationState
        if isinstance(provider_continuation_state, ProviderContinuationState):
            continuation = provider_continuation_state
        elif isinstance(provider_continuation_state, Mapping):
            continuation = ProviderContinuationState(
                mode=str(provider_continuation_state.get("mode") or provider_continuation_mode or "none"),
                opaque_payload=provider_continuation_state.get("opaque_payload"),
                state_hash=str(provider_continuation_state.get("state_hash") or provider_continuation_hash or ""),
                created_from_turn_hash=str(provider_continuation_state.get("created_from_turn_hash") or ""),
                provider_identity=str(provider_continuation_state.get("provider_identity") or obj.provider_identity),
                protocol_family=str(provider_continuation_state.get("protocol_family") or obj.protocol_family),
                model_id=str(provider_continuation_state.get("model_id") or obj.model_id),
                expires_at=provider_continuation_state.get("expires_at"),
                portable=False,
            )
        else:
            continuation = ProviderContinuationState(
                mode=str(provider_continuation_mode or "none"),
                opaque_payload=None,
                state_hash=str(provider_continuation_hash or ""),
                provider_identity=obj.provider_identity,
                protocol_family=obj.protocol_family,
                model_id=obj.model_id,
            )
        obj.provider_continuation_state = continuation
        obj.provider_continuation_mode = continuation.mode
        obj.provider_continuation_hash = continuation.state_hash

        obj.finish_reason = str(finish_reason or "")
        obj.stop_semantics = str(stop_semantics or finish_reason or "")
        obj.usage = dict(usage or {})
        obj.stream_metadata = dict(stream_metadata or {})
        obj.retry_semantics = dict(retry_semantics or {})
        obj.raw_response_hash = str(raw_response_hash or "")
        # Old code/tests read provider_id as L4 family.
        obj.provider_id = str(provider_id or optimization_family or provider_identity or "")
        obj.turn_hash = stable_hash({
            "turn_id": obj.turn_id,
            "provider_identity": obj.provider_identity,
            "protocol_family": obj.protocol_family,
            "model_id": obj.model_id,
            "visible_text": obj.visible_text,
            "tool_calls": obj.tool_calls,
            "bindings": [item.as_dict() for item in obj.tool_call_bindings],
            "finish_reason": obj.finish_reason,
            "usage": obj.usage,
            "raw_response_hash": obj.raw_response_hash,
        })
        return obj

    def public_dict(self) -> dict[str, Any]:
        """Safe metadata projection; excludes private reasoning/opaque payload."""
        return {
            "schema": self.schema,
            "turn_id": self.turn_id,
            "provider_identity": self.provider_identity,
            "service_preset": self.service_preset,
            "protocol_family": self.protocol_family,
            "optimization_family": self.optimization_family,
            "model_id": self.model_id,
            "visible_text": self.visible_text,
            "tool_calls": [dict(item) for item in self.tool_calls],
            "tool_call_bindings": [item.as_dict() for item in self.tool_call_bindings],
            "provider_continuation_mode": self.provider_continuation_mode,
            "provider_continuation_hash": self.provider_continuation_hash,
            "finish_reason": self.finish_reason,
            "stop_semantics": self.stop_semantics,
            "usage": dict(self.usage),
            "stream_metadata": dict(self.stream_metadata),
            "retry_semantics": dict(self.retry_semantics),
            "raw_response_hash": self.raw_response_hash,
            "turn_hash": self.turn_hash,
        }


# Compatibility constructor/name. There is still only one implementation.
ModelTurnReply = ProviderTurnEnvelope
