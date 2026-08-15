"""P18.1 provider-native model transport contract.

Transports translate wire protocol only. They do not own task continuity,
checkpoints, tool execution, permission decisions, retries of side effects, or
provider selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Protocol

from ..model_endpoint import ModelEndpointConfig
from ..model_protocol_contract import ProviderTurnEnvelope


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
