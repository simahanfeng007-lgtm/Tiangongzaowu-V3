"""Thin P8 adapter over the existing V3 HttpKehuduan LLM channel.

This module does not create an HTTP client, read API credentials, expose tools, or
attach to Runtime/Gateway.  A caller injects the already-owned V3 client.  The
adapter enters that client's public ``scoped_tools(disable_tools=True)`` context
before invoking ``llm_diaoyong``.
"""
from __future__ import annotations
from dataclasses import dataclass
import time
from typing import Any, Callable
from contracts.canonical import canonical_sha256
from .model import (
    SEMANTIC_OUTPUT_SCHEMA_GUIDE,
    SemanticModelRequest,
    SemanticModelResponse,
    SemanticModelUnavailable,
)


def _estimate_tokens(text: str) -> int:
    """Deterministic fallback only; never represented as provider usage."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _model_descriptor(*, provider_id: str, model_name: str) -> tuple[str, str]:
    digest = canonical_sha256({
        "domain": "tiangong.world.semantic-model-descriptor.v1",
        "provider_id": provider_id,
        "model_name": model_name,
        "adapter": "v3.HttpKehuduan.llm_diaoyong",
    })
    # OpaqueId-safe and intentionally not a claim about inaccessible model weights.
    return f"model.semantic.{digest[:24]}", digest


@dataclass(slots=True)
class V3HttpSemanticModel:
    """SemanticModel implementation using the existing V3 model client only."""
    client: Any
    provider_id: str
    model_name: str
    token_estimator: Callable[[str], int] = _estimate_tokens

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id required")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name required")
        if not callable(self.token_estimator):
            raise ValueError("token_estimator must be callable")
        self.provider_id = self.provider_id.strip()
        self.model_name = self.model_name.strip()

    @property
    def model_ref(self) -> str:
        return _model_descriptor(provider_id=self.provider_id, model_name=self.model_name)[0]

    @property
    def model_sha256(self) -> str:
        return _model_descriptor(provider_id=self.provider_id, model_name=self.model_name)[1]

    def is_available(self) -> bool:
        return (
            self.client is not None
            and callable(getattr(self.client, "llm_diaoyong", None))
            and callable(getattr(self.client, "scoped_tools", None))
        )

    def generate(self, request: SemanticModelRequest) -> SemanticModelResponse:
        if not self.is_available():
            raise SemanticModelUnavailable("existing V3 LLM client unavailable")
        user_prompt = (
            f"prompt_version={request.prompt_version}\n"
            f"schema_version={request.schema_version}\n"
            "Return exactly one JSON object matching this shape; no markdown or prose:\n"
            + SEMANTIC_OUTPUT_SCHEMA_GUIDE
            + "\n\nWORLD_RECORDS_DATA_BEGIN\n"
            + request.payload_json
            + "\nWORLD_RECORDS_DATA_END"
        )
        started = time.perf_counter()
        try:
            # This is the decisive P8 execution boundary: the existing V3 client
            # receives an empty tool surface for semantic interpretation calls.
            with self.client.scoped_tools(disable_tools=True):
                output = self.client.llm_diaoyong(
                    request.system_instruction,
                    user_prompt,
                    provider_id=self.provider_id,
                )
        except Exception as exc:
            raise SemanticModelUnavailable("existing V3 LLM call failed") from exc
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        if not isinstance(output, str) or not output.strip():
            raise SemanticModelUnavailable("existing V3 LLM returned no text")
        stripped = output.lstrip()
        if stripped.startswith("[LLM错误:") or stripped.startswith("[空响应"):
            raise SemanticModelUnavailable("existing V3 LLM reported unavailable/error")
        prompt_tokens = max(0, int(self.token_estimator(request.system_instruction + "\n" + user_prompt)))
        completion_tokens = max(0, int(self.token_estimator(output)))
        return SemanticModelResponse(
            model_ref=self.model_ref,
            model_sha256=self.model_sha256,
            output_text=output,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            token_measurement="ESTIMATED",
        )


__all__ = ["V3HttpSemanticModel"]
