"""Immutable presentation projections of observed facts for the model judge.

These objects preserve receipts; they do not certify their semantic content or
create replacement execution facts. Only references in this run's checkpoint
can be read. Model-supplied filesystem paths are never accepted.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import stat
import time

from total_gateway.object_store import ContentAddressedObjectStore


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=str)


def identity(state):
    return {**{key: state.get(key) for key in ("request_id", "run_id", "generation", "session_id")},
            "authority": state.get("review_authority_identity") or {}}


def scope(state):
    return hashlib.sha256(canonical(identity(state)).encode()).hexdigest()


@contextmanager
def objects(state):
    from .simple_chain.kernel import _simple_chain_run_state_path
    root = _simple_chain_run_state_path(str(state.get("run_id") or "run")).parent / "review_objects"
    store = ContentAddressedObjectStore.open(root.resolve(), now_ms=time.time_ns() // 1_000_000)
    try:
        yield store
    finally:
        store.close()


def record_observation(state, payload):
    from .adversarial_review import OBSERVATION_KEYS
    from .run_context import current_run_context
    context = current_run_context()
    authority = {key: getattr(context, key) for key in ("request_id", "run_id", "generation", "session_id")}
    if context.run_id:
        if state.setdefault("review_authority_identity", authority) != authority:
            raise ValueError("review_evidence_authority_changed")
    sequence = int(state.get("round") or 0)
    data = {key: payload[key] for key in OBSERVATION_KEYS if key in payload}
    envelope = {"schema": "tiangong.review-evidence.v1", "identity": identity(state),
                "sequence": sequence, "data": data}
    encoded = canonical(envelope).encode()
    with objects(state) as store:
        ref = store.put_bytes(encoded, kind="payload", tenant_id="desktop-review",
            link_account_id="runtime-observation", conversation_scope_hash=scope(state),
            created_at_ms=time.time_ns() // 1_000_000).reference
    row = {"ref": ref.object_id, "sha256": ref.sha256, "sequence": sequence,
           "tool_action": str(payload.get("tool_action") or ""), "ok": payload.get("ok"),
           "chars": len(canonical(data)), "scope": scope(state)}
    # Persisted before any compact checkpoint/history projection. The full
    # receipt includes its native Fact and artifact-version/hash bindings.
    state.setdefault("review_evidence_index", []).append(row)
    return row


def record_candidate(state, candidate):
    """Retain an unapproved candidate for an explicit resume, never delivery."""
    data = canonical({"identity": identity(state), "candidate": str(candidate)}).encode()
    with objects(state) as store:
        ref = store.put_bytes(data, kind="payload", tenant_id="desktop-review",
            link_account_id="candidate-delivery", conversation_scope_hash=scope(state),
            created_at_ms=time.time_ns() // 1_000_000).reference
    state["review_candidate"] = {"ref": ref.object_id, "sha256": ref.sha256, "scope": scope(state),
                                 "chars": len(str(candidate)), "approved": False}
    return state["review_candidate"]


def read_observation(state, row):
    if row not in state.get("review_evidence_index", []) or row.get("scope") != scope(state):
        raise ValueError("review_evidence_scope_changed")
    with objects(state) as store:
        ref = store.get_reference(row["ref"])
        if (ref is None or ref.kind != "payload" or ref.conversation_scope_hash != scope(state)
                or ref.sha256 != row["sha256"] or ref.tenant_id != "desktop-review"
                or ref.link_account_id != "runtime-observation"):
            raise ValueError("review_evidence_binding_changed")
        raw = store.read_bytes(row["ref"])
    envelope = json.loads(raw)
    if (hashlib.sha256(raw).hexdigest() != row["sha256"]
            or envelope["identity"] != identity(state) or envelope["sequence"] != row["sequence"]):
        raise ValueError("review_evidence_content_changed")
    return canonical(envelope["data"])


def page(state, reference, start, length):
    if type(start) is not int or start < 0 or type(length) is not int or not 1 <= length <= 12000:
        raise ValueError("review_evidence_range_invalid")
    row = next((r for r in state.get("review_evidence_index", []) if r["ref"] == reference), None)
    if row is None:
        raise ValueError("review_evidence_reference_unknown")
    text = read_observation(state, row)
    if start >= len(text):
        raise ValueError("review_evidence_range_invalid")
    return {"ref": reference, "start": start, "data_excerpt": text[start:start + length],
            "total_chars": len(text), "truncated": start > 0 or start + length < len(text)}


def artifact_versions(state):
    """Fresh bytes for host-observed output paths; no model-selected file reads.

    This is an invalidation check and is explicitly not a content-correctness
    verdict. The existing delivery authority still governs artifact release.
    """
    from pathlib import Path
    versions = []
    for item in state.get("generated_attachments") or []:
        path = Path(str(item.get("path") or ""))
        row = {"path": str(path)}
        if not path.is_absolute():
            row["state"] = "unresolved_path"
        else:
            try:
                if any(p.is_symlink() or bool(getattr(p, "is_junction", lambda: False)()) for p in (path, *path.parents)):
                    raise ValueError("artifact_link")
                digest = hashlib.sha256()
                size = 0
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("artifact_not_regular")
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        raise ValueError("artifact_changed_while_reading")
                row.update(state="observed", sha256=digest.hexdigest(), size_bytes=size)
            except (OSError, ValueError):
                row["state"] = "unavailable"
        versions.append(row)
    return versions
