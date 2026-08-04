"""7174-side consumer for the context already authorized by 7175 and 7184.

The backend must never compile life context or call the life service.  It only
validates redundant bindings and projects the authorized envelope into the
legacy execution input shape.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_attachments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in list(payload.get("attachments") or []) if isinstance(item, Mapping)]


def _interaction_mode(text: str, attachments: list[dict[str, Any]]) -> str:
    return "work" if attachments or len(text) > 600 else "chat"


def _related_skills(_text: str, _attachments: list[dict[str, Any]], _mode: str) -> list[dict[str, Any]]:
    return []


def _load_backend_history(_store: object, _session_id: str, _request_id: str) -> list[dict[str, Any]]:
    return []


def _attachment_manifest(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return attachments


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"authoritative {field} is required")
    return dict(value)


def _compile_life_context(store: object, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise RuntimeError("authoritative life_context is required")
    conversation = payload.get("conversation_context")
    conversation = dict(conversation) if isinstance(conversation, Mapping) else {}
    raw_context = payload.get("life_context") or conversation.get("life_context")
    life_context = _mapping(raw_context, "life_context")
    envelope = _mapping(life_context.get("context_envelope"), "life context envelope")

    request_id = _clean_text(payload.get("request_id"))
    session_id = _clean_text(payload.get("session_id"))
    if not request_id or not session_id:
        raise RuntimeError("request binding is incomplete")
    trigger = envelope.get("trigger")
    if isinstance(trigger, Mapping):
        trigger_ref = _clean_text(trigger.get("ref"))
        if trigger_ref and trigger_ref != request_id:
            raise RuntimeError("request binding mismatch")

    life_id = _clean_text(life_context.get("life_id"))
    cycle_id = _clean_text(life_context.get("cycle_id"))
    context_hash = _clean_text(life_context.get("context_hash"))
    if not life_id or not cycle_id or len(context_hash) != 64:
        raise RuntimeError("authoritative life context is malformed")
    if _clean_text(envelope.get("life_id")) not in {"", life_id}:
        raise RuntimeError("life identity binding mismatch")
    if _clean_text(envelope.get("cycle_id")) not in {"", cycle_id}:
        raise RuntimeError("cycle binding mismatch")
    if _clean_text(envelope.get("context_hash")) not in {"", context_hash}:
        raise RuntimeError("context hash binding mismatch")

    redundant_hashes = [
        payload.get("life_context_hash"),
        conversation.get("life_context_hash"),
    ]
    redundant_cycles = [payload.get("cycle_id"), conversation.get("cycle_id")]
    if any(value not in (None, "", context_hash) for value in redundant_hashes):
        raise RuntimeError("life context hash copies disagree")
    if any(value not in (None, "", cycle_id) for value in redundant_cycles):
        raise RuntimeError("life context cycle copies disagree")
    if life_context.get("lifecycle_state") != "authorized":
        raise RuntimeError("life context is not authorized")

    text = _clean_text(payload.get("text"))
    attachments = _normalize_attachments(payload)
    mode = _interaction_mode(text, attachments)
    history = _load_backend_history(store, session_id, request_id)
    result = {
        "life_id": life_id,
        "writer_epoch": life_context.get("writer_epoch"),
        "cycle_id": cycle_id,
        "context_hash": context_hash,
        "context_envelope": envelope,
        "lifecycle_state": "authorized",
        "request_id": request_id,
        "session_id": session_id,
        "text": text,
        "mode": mode,
        "attachments": _attachment_manifest(attachments),
        "related_skills": _related_skills(text, attachments, mode),
        "messages": history,
    }
    result["authority_binding_sha256"] = hashlib.sha256(
        json.dumps(
            {
                "context_hash": context_hash,
                "cycle_id": cycle_id,
                "life_id": life_id,
                "request_id": request_id,
                "session_id": session_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return result


__all__ = ["_compile_life_context"]
