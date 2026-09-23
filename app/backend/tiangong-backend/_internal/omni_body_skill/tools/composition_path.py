"""Native path observations for controlled composition file writes.

This application path boundary is not OS process containment. Consumers
repeat it immediately before mutation and retain signatures, nonce and fence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .path_identity import resolve_existing_path, verify_relative_path


def resolve_composition_path(workspace_root: Path, target: str, *, writing: bool = False) -> Path:
    if not isinstance(target, str) or not target or target != target.strip():
        raise ValueError("composition target path is invalid")
    normalized = target.replace("\\", "/")
    if normalized.startswith("//") or normalized.casefold().startswith("file:") or ".." in normalized.split("/"):
        raise ValueError("composition target path is invalid")
    root = resolve_existing_path(Path(workspace_root))
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("composition workspace is invalid")
    path = Path(target)
    if not path.is_absolute():
        path = root / path
    relative = path.relative_to(root)
    for part in relative.parts:
        if (part in {".", ".."} or part.rstrip(" .") != part or ":" in part
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("composition path component is unsafe")
    protected = {".audit", ".backups", ".trash", ".omni_audit", ".omni_backups", ".omni_trash",
                 ".omni_workspace.lock", ".tiangong_emergency_audit", ".tiangong_sandboxes"}
    if writing and (not relative.parts or any(part.casefold() in protected for part in relative.parts)):
        raise ValueError("composition cannot mutate a runtime root")
    if path.exists() or path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        verify_relative_path(root, path)
        resolved = resolve_existing_path(path)
        if resolved.is_file() and resolved.stat().st_nlink > 1:
            raise ValueError("composition target is a hard link")
        return resolved
    if not writing:
        raise ValueError("composition read target is missing")
    # A missing parent requires a preceding mkdir; validation never creates it.
    parent = resolve_existing_path(path.parent)
    verify_relative_path(root, parent)
    if not parent.is_dir():
        raise ValueError("composition output parent is not a directory")
    return parent / path.name


def probe_composition_write_target(target: str, workspace_root: Path) -> dict:
    path = resolve_composition_path(workspace_root, target, writing=True)
    parent = resolve_existing_path(path.parent)
    state = {"exists": path.exists(), "is_dir": path.is_dir(),
             "native_path": str(path), "parent_native_path": str(parent)}
    if state["exists"]:
        stat = path.stat()
        state["size_bytes"] = int(stat.st_size)
        if path.is_file():
            if stat.st_size > 1048576:
                raise ValueError("controlled write target exceeds size limit")
            state["content_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state
