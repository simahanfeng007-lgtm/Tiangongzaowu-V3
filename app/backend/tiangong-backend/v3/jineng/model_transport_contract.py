"""P18.1 provider-native model transport contract.

Transports translate wire protocol only. They do not own task continuity,
checkpoints, tool execution, permission decisions, retries of side effects, or
provider selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..model_endpoint import ModelEndpointConfig
from ..model_protocol_contract import ProviderTurnEnvelope, ToolCallBinding


@dataclass(slots=True)
class TransportRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]
    protocol_family: str


@dataclass(slots=True)
class StreamState:
    visible_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    stop_semantics: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_events: int = 0

    @property
    def visible_text(self) -> str:
        return "".join(self.visible_parts)

    @property
    def private_reasoning(self) -> str:
        return "".join(self.reasoning_parts)


@dataclass(frozen=True, slots=True)
class NativeRoundtripContext:
    """Ephemeral same-provider binding for exactly one tool-result continuation.

    It is built from the immediately preceding ProviderTurnEnvelope and the
    Runtime's already-produced canonical results.  It is never persisted and
    never becomes task/continuity authority.
    """

    turn: ProviderTurnEnvelope
    bindings: tuple[ToolCallBinding, ...]
    results: tuple[dict[str, Any], ...]


class ModelTransport(Protocol):
    protocol_family: str

    def build_request(
        self,
        endpoint: ModelEndpointConfig,
        api_key: str,
        canonical_payload: Mapping[str, Any],
    ) -> TransportRequest: ...

    def consume_stream_event(self, state: StreamState, event: Mapping[str, Any]) -> tuple[str, str]: ...

    def finalize_turn(self, endpoint: ModelEndpointConfig, state: StreamState) -> ProviderTurnEnvelope: ...

    def encode_tool_result(self, result: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]: ...

    def probe_payload(self, endpoint: ModelEndpointConfig) -> dict[str, Any]: ...


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(value or "")


def json_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def bounded_items(items: Iterable[Mapping[str, Any]], limit: int = 32) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            out.append(dict(item))
        if len(out) >= max(1, limit):
            break
    return out


def extract_native_roundtrip_context(
    payload: dict[str, Any],
    endpoint: ModelEndpointConfig,
) -> NativeRoundtripContext | None:
    """Remove internal metadata and return a verified exact binding context.

    Failure is deliberately conservative: internal keys are always stripped,
    and no native continuation is emitted unless provider/protocol/model match
    and the number of Runtime results exactly equals the number of provider
    ToolCallBindings.  There is no text/name based call-id guessing.
    """
    turn = payload.pop("__provider_turn", None)
    raw_results = payload.pop("__provider_tool_results", None)
    if not isinstance(turn, ProviderTurnEnvelope) or not isinstance(raw_results, Sequence):
        return None
    results = tuple(dict(item) for item in raw_results if isinstance(item, Mapping))
    bindings = tuple(sorted(turn.tool_call_bindings, key=lambda item: int(item.sequence_index)))
    if not results or not bindings or len(results) != len(bindings):
        return None
    if (
        turn.provider_identity != endpoint.provider_identity
        or turn.protocol_family != endpoint.protocol_family
        or turn.model_id != endpoint.model_name
    ):
        return None
    continuation = turn.provider_continuation_state
    if not continuation.compatible_with(
        provider_identity=endpoint.provider_identity,
        protocol_family=endpoint.protocol_family,
        model_id=endpoint.model_name,
    ):
        return None
    return NativeRoundtripContext(turn=turn, bindings=bindings, results=results)


def drop_last_role_messages(
    messages: Sequence[Any],
    *,
    role: str,
    count: int,
) -> list[Any]:
    """Drop the newest N legacy messages only after native binding is verified."""
    output = list(messages)
    remaining = max(0, int(count))
    if not remaining:
        return output
    for index in range(len(output) - 1, -1, -1):
        item = output[index]
        if isinstance(item, Mapping) and str(item.get("role") or "") == role:
            del output[index]
            remaining -= 1
            if remaining <= 0:
                break
    return output
