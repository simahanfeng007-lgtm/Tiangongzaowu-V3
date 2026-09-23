"""Consumer-side path proof for an already-authorized read composition.

The workspace is supplied by the coordinator, never by invocation arguments.
This narrow boundary does not alter the generic BackendClient host-path guard.
"""
from __future__ import annotations

import os
from pathlib import Path
import unicodedata

from contracts import canonical_sha256
from contracts.composition_profile import composition_authority_allowed, composition_profile_fields, WORKSPACE_WRITE_ACTIONS
from runtime_security import verify_omni_capability_grant
from runtime_security.path_identity import resolve_existing_path, verify_relative_path
from runtime_security.composition_path import resolve_composition_path, probe_composition_write_target

from .impact_evaluator import probe_target_state


def probe_composition_target_state(target, workspace_root, *, action_id,
                                   profile_id=None, profile_sha256=None):
    if profile_id is not None and (action_id in WORKSPACE_WRITE_ACTIONS or action_id == "python.run"):
        from contracts.composition_profile import composition_profile_valid
        if not composition_profile_valid(profile_id, profile_sha256):
            raise ValueError("composition profile is invalid")
        state = probe_composition_write_target(target, Path(workspace_root))
        if action_id == "python.run" and (not state["exists"] or state["is_dir"] or Path(target).suffix.lower() != ".py"):
            raise ValueError("controlled Python requires an existing workspace .py script")
        return state
    return probe_target_state(target, workspace_root)


def validate_composition_workspace_target(
    *, ticket, grant, binding, trust_bundle, now_ms: int,
    workspace_root: Path, target: str,
) -> None:
    """Check signed read scope and current native path before dispatch/nonce."""
    # The caller has independently verified the ticket and its complete
    # intent/decision/claim/binding chain. The grant signature is also required:
    # its path_policy must not be accepted as an unsigned request preference.
    verify_omni_capability_grant(grant, trust_bundle, now_ms=now_ms)
    payload = grant.payload
    if (
        payload.path_policy != "workspace_only"
        or payload.risk_class != ticket.payload.risk_class
        or tuple(payload.allowed_side_effects) != tuple(ticket.payload.allowed_side_effects)
        or not composition_authority_allowed(action_id=payload.action_id,
            risk_class=payload.risk_class, allowed_side_effects=payload.allowed_side_effects,
            allow_shell=payload.allow_shell, allow_python=payload.allow_python,
            **composition_profile_fields(binding))
        or payload.allow_shell
        or not payload.allow_absolute_paths
        or payload.action_permission_sha256 != ticket.payload.action_permission_sha256
        or payload.composition_execution_binding != binding
        or ticket.payload.composition_execution_binding != binding
    ):
        raise ValueError("read-only workspace composition authority required")
    normalized = target.replace("\\", "/")
    if (
        not target or target != target.strip()
        or normalized.startswith("//")
        or normalized.casefold().startswith("file:")
        or ".." in normalized.split("/")
    ):
        raise ValueError("composition target path is invalid")
    # The shared native observation rejects links/reparse points at every
    # depth (including ancestors) and noncanonical device/stream spellings.
    if workspace_root.is_symlink() or not workspace_root.is_absolute():
        raise ValueError("composition workspace is unsafe")
    root = resolve_existing_path(workspace_root)
    lexical_target = Path(target)
    if not lexical_target.is_absolute():
        lexical_target = root / lexical_target
    writing = binding.execution_profile_id is not None and payload.action_id in WORKSPACE_WRITE_ACTIONS
    resolved_target = resolve_composition_path(root, target, writing=writing)
    if not root.is_dir():
        raise ValueError("composition workspace is not a directory")
    resolved_target.relative_to(root)
    normalized_root = os.path.normcase(unicodedata.normalize("NFC", str(root)))
    scope_hash = canonical_sha256({"normalized_workspace": normalized_root})
    if (scope_hash != payload.workspace_scope_hash
            or scope_hash != binding.workspace_scope_hash):
        raise ValueError("composition workspace binding mismatch")
    snapshot = probe_composition_target_state(str(resolved_target), root, action_id=payload.action_id,
                                               **composition_profile_fields(binding))
    if (snapshot is None or (not writing and not snapshot.get("exists"))
            or canonical_sha256(snapshot) != binding.target_snapshot_sha256):
        raise ValueError("composition target snapshot changed")
