"""Request-scoped failure projection, using the existing system status ledger."""
from __future__ import annotations

import re
from contracts import SystemStatusRecord, canonical_json_bytes, canonical_sha256
from .store import StoreConflictError

_PUBLIC_VALUE_ERRORS = {
    "COMPOSITION_SOURCE_CONTEXT_UNAVAILABLE": "desktop_composition.source_context_unavailable",
    "INSTALLED_SOURCE_GENESIS_UNAVAILABLE": "desktop_composition.source_initialization_failed",
}


def persist_desktop_request_error(*, store, objects, activation, error, at_ms):
    # Only exact, reviewed identifiers may cross from a raw exception into UI.
    # Unknown exception text can contain file content or provider credentials.
    public_value_error = _PUBLIC_VALUE_ERRORS.get(str(error)) if isinstance(error, ValueError) else None
    code = str(getattr(error, "code", None) or public_value_error or type(error).__name__).lower()[:160]
    code = re.sub(r"[^a-z0-9._:-]", "_", code)
    detail = ""
    if code == "desktop_composition.unsupported_task":
        detail = str(getattr(error, "detail", ""))[:300]
        detail = re.sub(r"(?i)\b(?:sk-|tp-)[a-z0-9_-]{12,}|bearer\s+\S+", "[已隐藏凭据]", detail)
    request_id = activation.entry.request_id
    generation = activation.generation
    scope = (request_id, generation.run_id, generation.generation)
    existing = store.list_system_statuses(request_id, run_id=scope[1], generation=scope[2])
    if any(item.source_component == "gateway.orchestration" and item.status_code == code for item in existing):
        return
    envelope = activation.envelope
    display = objects.put_bytes(canonical_json_bytes({"code": code, "detail": detail}),
        kind="payload", tenant_id=envelope.tenant_id,
        link_account_id=envelope.link_account_id,
        conversation_scope_hash=envelope.conversation_scope_hash, created_at_ms=at_ms).reference
    identity = canonical_sha256({"domain": "desktop.request.error.v1", "scope": list(scope), "code": code})
    status = SystemStatusRecord(
        system_status_id="sys_" + identity, request_id=request_id, run_id=scope[1],
        run_sequence=generation.run_sequence, generation=scope[2],
        response_episode_id="request-error-" + identity,
        status_code=code, severity="error", source_component="gateway.orchestration",
        display_object_ref=display.object_id, created_at_ms=at_ms,
        system_status_sha256="0" * 64,
    ).with_computed_status_sha256()
    try:
        store.put_system_status(status)
    except StoreConflictError:
        # Two failures may race before either sees the first status. Retain
        # only that same scoped diagnostic; never ignore an unrelated conflict.
        prior = store.list_system_statuses(request_id, run_id=scope[1], generation=scope[2])
        if not any(item.system_status_id == status.system_status_id
                   and item.source_component == status.source_component
                   and item.status_code == status.status_code for item in prior):
            raise
