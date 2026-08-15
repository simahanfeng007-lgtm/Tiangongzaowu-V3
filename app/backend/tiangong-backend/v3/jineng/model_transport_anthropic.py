"""Anthropic Messages transport for P18.1."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..model_endpoint import ModelEndpointConfig, ProtocolFamily
from ..model_protocol_contract import ProviderContinuationState, ProviderTurnEnvelope, ToolCallBinding, stable_hash
from .model_transport_contract import StreamState, TransportRequest, content_text, json_output
from .model_transport_openai_chat import _legacy_wire


class AnthropicMessagesTransport:
    protocol_family = ProtocolFamily.ANTHROPIC_MESSAGES.value

    def build_url(self, endpoint: ModelEndpointConfig) -> str:
        return f"{endpoint.base_url.rstrip('/')}/v1/messages"

    def build_headers(self, endpoint: ModelEndpointConfig, api_key: str) -> dict[str, str]:
        overrides = endpoint.endpoint_overrides or {}
        schemes = overrides.get("auth_scheme_by_protocol") if isinstance(overrides.get("auth_scheme_by_protocol"), Mapping) else {}
        scheme = str(schemes.get(self.protocol_family) or overrides.get("auth_scheme") or "x-api-key").strip().lower()
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": str(overrides.get("anthropic_version") or "2023-06-01"),
        }
        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["x-api-key"] = api_key
        return headers

    @staticmethod
    def _convert_tools(tools: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for tool in tools if isinstance(tools, list) else []:
            if not isinstance(tool, Mapping):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), Mapping):
                fn = tool["function"]
                output.append({
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "input_schema": dict(fn.get("parameters") or {}),
                })
            elif tool.get("name") and isinstance(tool.get("input_schema"), Mapping):
                output.append(dict(tool))
        return output

    @staticmethod
    def _convert_messages(messages: Any) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        output: list[dict[str, Any]] = []
        for message in messages if isinstance(messages, list) else []:
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "user")
            if role in {"system", "developer"}:
                system_parts.append(content_text(message.get("content")))
                continue
            if role == "tool":
                tool_use_id = str(message.get("tool_call_id") or "").strip()
                if tool_use_id:
                    output.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content_text(message.get("content"))}],
                    })
                continue
            if role not in {"user", "assistant"}:
                continue
            output.append({"role": role, "content": content_text(message.get("content"))})
        return "\n\n".join(part for part in system_parts if part), output

    def build_request(self, endpoint: ModelEndpointConfig, api_key: str, canonical_payload: Mapping[str, Any]) -> TransportRequest:
        system, messages = self._convert_messages(canonical_payload.get("messages"))
        payload: dict[str, Any] = {
            "model": endpoint.model_name or str(canonical_payload.get("model") or ""),
            "messages": messages,
            "max_tokens": int(canonical_payload.get("max_tokens") or canonical_payload.get("max_output_tokens") or 8192),
            "stream": True,
        }
        if system:
            payload["system"] = system
        tools = self._convert_tools(canonical_payload.get("tools"))
        if tools:
            payload["tools"] = tools
        choice = canonical_payload.get("tool_choice")
        if choice == "auto":
            payload["tool_choice"] = {"type": "auto"}
        elif choice == "required":
            payload["tool_choice"] = {"type": "any"}
        elif isinstance(choice, Mapping):
            if choice.get("type") == "function" and isinstance(choice.get("function"), Mapping):
                payload["tool_choice"] = {"type": "tool", "name": str(choice["function"].get("name") or "")}
            else:
                payload["tool_choice"] = dict(choice)
        thinking = canonical_payload.get("thinking")
        if isinstance(thinking, Mapping):
            # Only forward provider-native-looking thinking controls. Unknown
            # raw modes are deliberately left absent by capability resolution.
            payload["thinking"] = dict(thinking)
        return TransportRequest(self.build_url(endpoint), self.build_headers(endpoint, api_key), payload, self.protocol_family)

    def consume_stream_event(self, state: StreamState, event: Mapping[str, Any]) -> tuple[str, str]:
        state.raw_events += 1
        etype = str(event.get("type") or "")
        text = ""
        reasoning = ""
        if etype == "message_start":
            message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
            usage = message.get("usage")
            if isinstance(usage, Mapping):
                state.usage.update(dict(usage))
            if message.get("id"):
                state.metadata["message_id"] = str(message.get("id"))
        elif etype == "content_block_start":
            index = int(event.get("index") or 0)
            block = event.get("content_block") if isinstance(event.get("content_block"), Mapping) else {}
            btype = str(block.get("type") or "")
            if btype == "tool_use":
                tool_id = str(block.get("id") or "")
                key = tool_id or f"index_{index}"
                state.tool_items[key] = {
                    "id": tool_id,
                    "name": str(block.get("name") or ""),
                    "arguments_text": json.dumps(block.get("input") or {}, ensure_ascii=False, separators=(",", ":")) if block.get("input") else "",
                    "sequence_index": index,
                }
        elif etype == "content_block_delta":
            index = int(event.get("index") or 0)
            delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
            dtype = str(delta.get("type") or "")
            if dtype == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    state.visible_parts.append(text)
            elif dtype in {"thinking_delta", "reasoning_delta"}:
                reasoning = str(delta.get("thinking") or delta.get("text") or "")
                if reasoning:
                    state.reasoning_parts.append(reasoning)
            elif dtype == "input_json_delta":
                target = None
                for row in state.tool_items.values():
                    if int(row.get("sequence_index") or 0) == index:
                        target = row
                        break
                if target is None:
                    key = f"index_{index}"
                    target = state.tool_items.setdefault(key, {"id": "", "name": "", "arguments_text": "", "sequence_index": index})
                # Anthropic sends partial_json chunks that reconstruct one JSON object.
                if target.get("arguments_text") in {"{}", ""}:
                    target["arguments_text"] = ""
                target["arguments_text"] += str(delta.get("partial_json") or "")
        elif etype == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
            if delta.get("stop_reason"):
                state.finish_reason = str(delta.get("stop_reason"))
                state.stop_semantics = state.finish_reason
            usage = event.get("usage")
            if isinstance(usage, Mapping):
                state.usage.update(dict(usage))
        elif etype == "message_stop":
            state.finish_reason = state.finish_reason or "end_turn"
            state.stop_semantics = state.stop_semantics or state.finish_reason
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
        replay_blocks: list[dict[str, Any]] = []
        for seq, item in enumerate(sorted(state.tool_items.values(), key=lambda row: int(row.get("sequence_index") or 0))):
            tool_use_id = str(item.get("id") or f"toolu_{stable_hash(item)[:16]}")
            name = str(item.get("name") or "omni_body")
            args = self._arguments(str(item.get("arguments_text") or ""))
            calls.append({"id": tool_use_id, "name": name, "arguments": args})
            replay_blocks.append({"type": "tool_use", "id": tool_use_id, "name": name, "input": args})
            bindings.append(ToolCallBinding(
                canonical_call_id=tool_use_id,
                provider_call_id=tool_use_id,
                tool_name=name,
                protocol_family=self.protocol_family,
                binding_type="anthropic_tool_use",
                sequence_index=seq,
            ))
        continuation = ProviderContinuationState(
            mode="local_replay" if replay_blocks else "none",
            opaque_payload={"assistant_content_blocks": replay_blocks} if replay_blocks else None,
            provider_identity=endpoint.provider_identity,
            protocol_family=self.protocol_family,
            model_id=endpoint.model_name,
        )
        return ProviderTurnEnvelope(
            _legacy_wire(state.visible_text, calls),
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
            stream_metadata={"event_count": state.raw_events, **state.metadata},
        )

    def encode_tool_result(self, result: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
        tool_use_id = str(binding.get("provider_call_id") or binding.get("canonical_call_id") or "").strip()
        if not tool_use_id:
            raise ValueError("Anthropic tool result requires tool_use_id")
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": json_output(result)}

    def probe_payload(self, endpoint: ModelEndpointConfig) -> dict[str, Any]:
        return {"model": endpoint.model_name, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1, "stream": False}
