"""OpenAI Chat Completions transport for P18.1."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..model_endpoint import ModelEndpointConfig, ProtocolFamily
from ..model_protocol_contract import ProviderContinuationState, ProviderTurnEnvelope, ToolCallBinding, stable_hash
from .model_transport_contract import (
    StreamState,
    TransportRequest,
    content_text,
    drop_last_role_messages,
    extract_native_roundtrip_context,
    json_output,
)


class OpenAIChatTransport:
    protocol_family = ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value

    def build_url(self, endpoint: ModelEndpointConfig) -> str:
        return f"{endpoint.base_url.rstrip('/')}/chat/completions"

    def build_headers(self, endpoint: ModelEndpointConfig, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def build_request(self, endpoint: ModelEndpointConfig, api_key: str, canonical_payload: Mapping[str, Any]) -> TransportRequest:
        payload = dict(canonical_payload)
        native = extract_native_roundtrip_context(payload, endpoint)
        if native is not None:
            messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
            messages = drop_last_role_messages(messages, role="assistant", count=len(native.results))
            opaque = native.turn.provider_continuation_state.opaque_payload
            opaque = opaque if isinstance(opaque, Mapping) else {}
            replay_calls = opaque.get("assistant_tool_calls") if isinstance(opaque.get("assistant_tool_calls"), list) else []
            if replay_calls:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": native.turn.visible_text or None,
                    "tool_calls": [dict(item) for item in replay_calls if isinstance(item, Mapping)],
                }
                # DeepSeek reasoning continuation remains opaque/private and is
                # replayed only to the same provider/protocol/model.
                reasoning = opaque.get("assistant_reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    assistant_message["reasoning_content"] = reasoning
                messages.append(assistant_message)
                for binding, result in zip(native.bindings, native.results, strict=True):
                    messages.append(self.encode_tool_result(result, binding.as_dict()))
                payload["messages"] = messages
        payload["model"] = endpoint.model_name or str(payload.get("model") or "")
        payload["stream"] = True
        payload.setdefault("stream_options", {"include_usage": True})
        return TransportRequest(
            url=self.build_url(endpoint),
            headers=self.build_headers(endpoint, api_key),
            payload=payload,
            protocol_family=self.protocol_family,
        )

    @staticmethod
    def _reasoning_text(delta: Mapping[str, Any]) -> str:
        for key in ("reasoning_content", "reasoning"):
            value = delta.get(key)
            if isinstance(value, str):
                return value
        details = delta.get("reasoning_details")
        if isinstance(details, list):
            return "".join(str(item.get("text") or "") for item in details if isinstance(item, Mapping))
        return ""

    def consume_stream_event(self, state: StreamState, event: Mapping[str, Any]) -> tuple[str, str]:
        state.raw_events += 1
        usage = event.get("usage")
        if isinstance(usage, Mapping) and usage:
            state.usage = dict(usage)
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            return "", ""
        choice = choices[0] if isinstance(choices[0], Mapping) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), Mapping) else {}
        text = content_text(delta.get("content"))
        reasoning = self._reasoning_text(delta)
        if text:
            state.visible_parts.append(text)
        if reasoning:
            state.reasoning_parts.append(reasoning)
        finish = choice.get("finish_reason")
        if finish:
            state.finish_reason = str(finish)
            state.stop_semantics = str(finish)
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for seq, raw in enumerate(tool_calls):
                if not isinstance(raw, Mapping):
                    continue
                index = raw.get("index")
                key = str(index if index is not None else raw.get("id") or seq)
                current = state.tool_items.setdefault(key, {
                    "id": "",
                    "name": "",
                    "arguments_text": "",
                    "sequence_index": int(index if isinstance(index, int) else seq),
                })
                if raw.get("id"):
                    current["id"] = str(raw.get("id"))
                fn = raw.get("function") if isinstance(raw.get("function"), Mapping) else {}
                if fn.get("name"):
                    current["name"] = str(fn.get("name"))
                if fn.get("arguments") is not None:
                    current["arguments_text"] += str(fn.get("arguments") or "")
        return text, reasoning

    @staticmethod
    def _arguments(text: str) -> Any:
        try:
            return json.loads(text) if text else {}
        except Exception:
            return {"_raw_arguments": text}

    def finalize_turn(self, endpoint: ModelEndpointConfig, state: StreamState) -> ProviderTurnEnvelope:
        calls: list[dict[str, Any]] = []
        bindings: list[ToolCallBinding] = []
        replay_tool_calls: list[dict[str, Any]] = []
        for seq, item in enumerate(sorted(state.tool_items.values(), key=lambda row: int(row.get("sequence_index") or 0))):
            provider_id = str(item.get("id") or f"call_{stable_hash(item)[:16]}")
            name = str(item.get("name") or "omni_body")
            args_text = str(item.get("arguments_text") or "")
            calls.append({"id": provider_id, "name": name, "arguments": self._arguments(args_text)})
            replay_tool_calls.append({
                "id": provider_id,
                "type": "function",
                "function": {"name": name, "arguments": args_text or "{}"},
            })
            bindings.append(ToolCallBinding(
                canonical_call_id=provider_id,
                provider_call_id=provider_id,
                tool_name=name,
                protocol_family=self.protocol_family,
                binding_type="openai_tool_call",
                sequence_index=seq,
            ))
        legacy = _legacy_wire(state.visible_text, calls)
        opaque: dict[str, Any] = {}
        if replay_tool_calls:
            opaque["assistant_tool_calls"] = replay_tool_calls
        if state.private_reasoning:
            opaque["assistant_reasoning_content"] = state.private_reasoning
        continuation = ProviderContinuationState(
            mode="local_replay" if opaque else "none",
            opaque_payload=opaque or None,
            provider_identity=endpoint.provider_identity,
            protocol_family=self.protocol_family,
            model_id=endpoint.model_name,
        )
        return ProviderTurnEnvelope(
            legacy,
            provider_identity=endpoint.provider_identity,
            service_preset=endpoint.service_preset,
            protocol_family=self.protocol_family,
            optimization_family=endpoint.optimization_family,
            model_id=endpoint.model_name,
            visible_text=state.visible_text,
            tool_calls=calls,
            tool_call_bindings=bindings,
            private_reasoning=state.private_reasoning,
            provider_continuation_state=continuation,
            finish_reason=state.finish_reason,
            stop_semantics=state.stop_semantics,
            usage=state.usage,
            stream_metadata={"event_count": state.raw_events},
        )

    def encode_tool_result(self, result: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
        call_id = str(binding.get("provider_call_id") or binding.get("canonical_call_id") or "").strip()
        if not call_id:
            raise ValueError("OpenAI Chat tool result requires provider call id")
        return {"role": "tool", "tool_call_id": call_id, "content": json_output(result)}

    def probe_payload(self, endpoint: ModelEndpointConfig) -> dict[str, Any]:
        return {
            "model": endpoint.model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }


def _legacy_wire(visible_text: str, calls: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if visible_text:
        parts.append(visible_text)
    for call in calls:
        body = {"action": (call.get("arguments") or {}).get("action") if isinstance(call.get("arguments"), dict) else "", **(call.get("arguments") if isinstance(call.get("arguments"), dict) else {})}
        parts.append(
            "<tool_call><name>"
            + str(call.get("name") or "omni_body")
            + "</name><arguments>"
            + json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            + "</arguments></tool_call>"
        )
    return "\n".join(parts)
