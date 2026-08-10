"""Per-run authority and identity context for the frozen v3 backend.

The HTTP bridge is threaded, while the legacy core was historically a singleton.
This module provides immutable ContextVar state so request/run identity, tool
authority and visible expression data cannot leak between threads or tasks.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Iterator, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RunContext:
    request_id: str = ""
    run_id: str = ""
    generation: int = 0
    life_id: str = ""
    agent_id: str = "qiyuan"
    session_id: str = ""
    conversation_id: str = ""
    principal_scope_hash: str = ""
    outer_execution_ticket_id: str = ""
    workspace_id: str = ""
    gateway_url: str = "http://127.0.0.1:7184"
    learning_intent_verified: bool = False
    # Prompt construction input only. Deliberately excluded from audit_metadata()
    # so raw user text is not copied into run/audit identity surfaces.
    current_user_text: str = ""

    def identity_scope(self) -> str:
        material = {
            "life_id": self.life_id or "life-default",
            "agent_id": self.agent_id or "qiyuan",
        }
        digest = hashlib.sha256(
            repr(tuple(sorted(material.items()))).encode("utf-8", errors="strict")
        ).hexdigest()
        return f"life-{digest[:24]}"

    def audit_metadata(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "life_id": self.life_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "principal_scope_hash": self.principal_scope_hash,
            "outer_execution_ticket_id": self.outer_execution_ticket_id,
            "workspace_id": self.workspace_id,
            "learning_intent_verified": self.learning_intent_verified,
        }


_CONTEXT: contextvars.ContextVar[RunContext] = contextvars.ContextVar(
    "tiangong_v3_run_context", default=RunContext()
)
_LAST_EXPRESSION: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tiangong_v3_last_expression", default=None
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def from_conversation_context(value: Mapping[str, Any] | None) -> RunContext:
    context = _mapping(value)
    metadata = _mapping(context.get("metadata"))
    life = _mapping(context.get("life_context"))
    if not life:
        life = _mapping(metadata.get("life_context"))
    generation_raw = context.get("generation", metadata.get("generation", 0))
    generation = generation_raw if type(generation_raw) is int and generation_raw >= 0 else 0
    principal = _text(
        context.get("principal_scope_hash"),
        metadata.get("principal_scope_hash"),
    )
    if principal and not _SHA256.fullmatch(principal):
        principal = ""
    current_user_text = _text(
        context.get("current_user_message"),
        context.get("raw_user_text"),
        metadata.get("raw_user_text"),
        metadata.get("original_user_message"),
    )
    return RunContext(
        request_id=_text(context.get("request_id"), context.get("active_id"), metadata.get("request_id")),
        run_id=_text(context.get("run_id"), metadata.get("run_id")),
        generation=generation,
        life_id=_text(context.get("life_id"), life.get("life_id"), metadata.get("life_id")),
        agent_id=_text(context.get("agent_id"), metadata.get("agent_id"), "qiyuan"),
        session_id=_text(context.get("session_id"), metadata.get("session_id")),
        conversation_id=_text(context.get("conversation_id"), metadata.get("conversation_id")),
        principal_scope_hash=principal,
        outer_execution_ticket_id=_text(
            context.get("execution_ticket_id"), metadata.get("execution_ticket_id")
        ),
        workspace_id=_text(context.get("workspace_id"), metadata.get("workspace_id")),
        gateway_url=_text(context.get("gateway_url"), metadata.get("gateway_url"), "http://127.0.0.1:7184"),
        current_user_text=current_user_text,
    )


@contextlib.contextmanager
def bind_run_context(value: Mapping[str, Any] | RunContext | None) -> Iterator[RunContext]:
    context = value if isinstance(value, RunContext) else from_conversation_context(value)
    token = _CONTEXT.set(context)
    expression_token = _LAST_EXPRESSION.set(None)
    try:
        yield context
    finally:
        _LAST_EXPRESSION.reset(expression_token)
        _CONTEXT.reset(token)


def current_run_context() -> RunContext:
    return _CONTEXT.get()


def update_run_context(**changes: Any) -> RunContext:
    current = current_run_context()
    updated = replace(current, **changes)
    _CONTEXT.set(updated)
    return updated


def set_last_expression(value: Mapping[str, Any] | None) -> None:
    _LAST_EXPRESSION.set(dict(value or {}))


def get_last_expression() -> dict[str, Any] | None:
    value = _LAST_EXPRESSION.get()
    return None if value is None else dict(value)


__all__ = [
    "RunContext",
    "bind_run_context",
    "current_run_context",
    "from_conversation_context",
    "get_last_expression",
    "set_last_expression",
    "update_run_context",
]
