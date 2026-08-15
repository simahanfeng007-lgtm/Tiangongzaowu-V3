"""OpenAI Responses API transport for P18.1."""
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
from .model_transport_openai_chat import _legacy_wire


class OpenAIResponsesTransport:
    protocol_family = ProtocolFamily.OPENAI_RESPONSES.value

    def build_url(self, endpoint: ModelEndpointConfig) -> str:
        return f"{endpoint.base_url.rstrip('/')}/responses"

    def build_headers(self, endpoint: ModelEndpointConfig, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _convert_tools(tools: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in tools if isinstance(tools, list) else []:
            if not isinstance(tool, Mapping):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), Mapping):
                fn = tool["function"]
                row = {
                    "type": "function",
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "parameters": dict(fn.get("parameters") or {}),
                }
                if "strict" in fn:
                    row["strict"] = bool(fn.get("strict"))
                out.append(row)
            elif tool.get("type") == "function" and tool.get("name"):
                out.append(dict(tool))
        return out

    @staticmethod
    def _convert_input(messages: Any) -> tuple[str, list[dict[str, Any]]]:
        instructions: list[str] = []
        items: list[dict[str, Any]] = []
        for message in messages if isinstance(messages, list) else []:
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "user")
            if role in {"system", "developer"}:
                instructions.append(content_text(message.get("content")))
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if call_id:
                    items.append({"type": "function_call_output", "call_id": call_id, "output": content_text(message.get("content"))})
                continue
            if role not in {"user", "assistant"}:
                continue
            items.append({"role": role, "content": content_text(message.get("content"))})
        return "\n\n".join(part for part in instructions if part), items

    def build_request(self, endpoint: ModelEndpointConfig, api_key: str, canonical_payload: Mapping[str, Any]) -> TransportRequest:
        canonical = dict(canonical_payload)
        native = extract_native_roundtrip_context(canonical, endpoint)
        messages = canonical.get("messages") if isinstance(canonical.get("messages"), list) else []
        if native is not None:
            # Gutong currently records the Runtime result as a legacy assistant
            # observation. Remove only the newest result slots after exact
            # ToolCallBinding verification, then add provider-native items.
            messages = drop_last_role_messages(messages, role="assistant", count=len(native.results))

        payload: dict[str, Any] = {
            "model": endpoint.model_name or str(canonical.get("model") or ""),
            "stream": True,
        }
        instructions, input_items = self._convert_input(messages)
        if instructions:
            payload["instructions"] = instructions

        if native is not None:
            continuation = native.turn.provider_continuation_state
            opaque = continuation.opaque_payload if isinstance(continuation.opaque_payload, Mapping) else {}
            replay_items = opaque.get("output_items") if isinstance(opaque.get("output_items"), list) else []
            if replay_items:
                input_items.extend(dict(item) for item in replay_items if isinstance(item, Mapping))
                for binding, result in zip(native.bindings, native.results, strict=True):
                    input_items.append(self.encode_tool_result(result, binding.as_dict()))
            # Remote response state is an optional transport optimization only.
            # It is OFF by default; local replay remains sufficient for the
            # same Run when provider state disappears.
            use_remote = bool(endpoint.endpoint_overrides.get("responses_use_previous_response_id", False))
            previous_response_id = str(opaque.get("previous_response_id") or "").strip()
            if use_remote and previous_response_id:
                payload["previous_response_id"] = previous_response_id

        payload["input"] = input_items
        tools = self._convert_tools(canonical.get("tools"))
        if tools:
            payload["tools"] = tools
        if canonical.get("tool_choice") is not None:
            payload["tool_choice"] = canonical.get("tool_choice")
        if canonical.get("parallel_tool_calls") is not None:
            payload["parallel_tool_calls"] = bool(canonical.get("parallel_tool_calls"))
        effort = canonical.get("reasoning_effort")
        if effort:
            payload["reasoning"] = {"effort": str(effort)}
        elif isinstance(canonical.get("reasoning"), Mapping):
            payload["reasoning"] = dict(canonical["reasoning"])
        max_output = canonical.get("max_output_tokens") or canonical.get("max_completion_tokens") or canonical.get("max_tokens")
        if max_output:
            payload["max_output_tokens"] = int(max_output)
        # Remote storage is never task authority. SCNet and the generic path
        # default false; endpoint override may only enable provider storage as
        # soft continuation state.
        payload["store"] = bool(endpoint.endpoint_overrides.get("responses_store", False))
        return TransportRequest(self.build_url(endpoint), self.build_headers(endpoint, api_key), payload, self.protocol_family)

    @staticmethod
    def _remember_item(state: StreamState, item: Mapping[str, Any]) -> dict[str, Any] | None:
        if str(item.get("type") or "") != "function_call":
            return None
        call_id = str(item.get("call_id") or item.get("id") or "").strip()
        item_id = str(item.get("id") or "").strip()
        key = call_id or item_id or f"seq_{len(state.tool_items)}"
        row = state.tool_items.setdefault(key, {
            "id": call_id,
            "provider_item_id": item_id,
            "name": str(item.get("name") or ""),
            "arguments_text": "",
            "sequence_index": len(state.tool_items),
        })
        if call_id:
            row["id"] = call_id
        if item_id:
            row["provider_item_id"] = item_id
        if item.get("name"):
            row["name"] = str(item.get("name"))
        if item.get("arguments") is not None:
            row["arguments_text"] = str(item.get("arguments") or "")
        return row

    def consume_stream_event(self, state: StreamState, event: Mapping[str, Any]) -> tuple[str, str]:
        state.raw_events += 1
        etype = str(event.get("type") or "")
        text = ""
        reasoning = ""
        if etype == "response.output_text.delta":
            text = str(event.get("delta") or "")
            if text:
                state.visible_parts.append(text)
        elif etype in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            reasoning = str(event.get("delta") or "")
            if reasoning:
                state.reasoning_parts.append(reasoning)
        elif etype in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
            self._remember_item(state, item)
        elif etype == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or "")
            call_id = str(event.get("call_id") or "")
            target = None
            for row in state.tool_items.values():
                if (item_id and row.get("provider_item_id") == item_id) or (call_id and row.get("id") == call_id):
                    target = row
                    break
            if target is None:
                key = call_id or item_id or f"seq_{len(state.tool_items)}"
                target = state.tool_items.setdefault(key, {
                    "id": call_id,
                    "provider_item_id": item_id,
                    "name": "",
                    "arguments_text": "",
                    "sequence_index": len(state.tool_items),
                })
            target["arguments_text"] += str(event.get("delta") or "")
        elif etype == "response.function_call_arguments.done":
            item_id = str(event.get("item_id") or "")
            call_id = str(event.get("call_id") or "")
            for row in state.tool_items.values():
                if (item_id and row.get("provider_item_id") == item_id) or (call_id and row.get("id") == call_id):
                    if event.get("arguments") is not None:
                        row["arguments_text"] = str(event.get("arguments") or "")
                    break
        elif etype == "response.completed":
            response = event.get("response") if isinstance(event.get("response"), Mapping) else {}
            state.finish_reason = str(response.get("status") or "completed")
            state.stop_semantics = state.finish_reason
            usage = response.get("usage")
            if isinstance(usage, Mapping):
                state.usage = dict(usage)
            if response.get("id"):
                state.metadata["response_id"] = str(response.get("id"))
        elif etype in {"response.failed", "response.incomplete"}:
            response = event.get("response") if isinstance(event.get("response"), Mapping) else {}
            state.finish_reason = str(response.get("status") or etype.rsplit(".", 1)[-1])
            state.stop_semantics = state.finish_reason
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
        replay_items: list[dict[str, Any]] = []
        for seq, item in enumerate(sorted(state.tool_items.values(), key=lambda row: int(row.get("sequence_index") or 0))):
            call_id = str(item.get("id") or f"call_{stable_hash(item)[:16]}")
            name = str(item.get("name") or "omni_body")
            args_text = str(item.get("arguments_text") or "")
            args = self._arguments(args_text)
            calls.append({"id": call_id, "name": name, "arguments": args})
            replay_items.append({
                "type": "function_call",
                "id": str(item.get("provider_item_id") or ""),
                "call_id": call_id,
                "name": name,
                "arguments": args_text or "{}",
            })
            bindings.append(ToolCallBinding(
                canonical_call_id=call_id,
                provider_call_id=call_id,
                tool_name=name,
                protocol_family=self.protocol_family,
                binding_type="responses_function_call",
                provider_item_id=str(item.get("provider_item_id") or ""),
                sequence_index=seq,
            ))
        response_id = str(state.metadata.get("response_id") or "")
        opaque: dict[str, Any] = {}
        if replay_items:
            opaque["output_items"] = replay_items
        if response_id:
            opaque["previous_response_id"] = response_id
        mode = "remote_optional" if response_id else ("opaque_client_state" if opaque else "none")
        continuation = ProviderContinuationState(
            mode=mode,
            opaque_payload=opaque or None,
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
        call_id = str(binding.get("provider_call_id") or binding.get("canonical_call_id") or "").strip()
        if not call_id:
            raise ValueError("OpenAI Responses tool result requires call_id")
        return {"type": "function_call_output", "call_id": call_id, "output": json_output(result)}

    def probe_payload(self, endpoint: ModelEndpointConfig) -> dict[str, Any]:
        return {"model": endpoint.model_name, "input": "ping", "max_output_tokens": 1, "store": False}
