#!/usr/bin/env python3
"""Host-only, transactional rebind of an already enabled source trial profile.

No operation installs a missing profile. prepare is read-only; its returned
transaction lets the host undo apply even if the child loses its response.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import canonical_sha256
from total_gateway.composition_source_trial import (
    PROFILE_FILE, SourceTrialProfile, load_source_trial_profile,
)

TRANSACTION_SCHEMA = "tiangong.source-trial-workspace-rebind.v1"
TRANSACTION_KEYS = {"schema", "state_root", "previous_workspace", "workspace",
                    "previous_content", "next_content"}


def _source_only() -> None:
    if os.environ.get("TIANGONG_SOURCE_MODE") != "1":
        raise ValueError("source_profile_rebind_requires_source_mode")


def _directory(value: str | Path) -> Path:
    root = Path(value)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("source_profile_directory_invalid")
    # The Electron host already canonicalizes the user's selected directory.
    # Refuse links/reparse points again rather than trusting a string from IPC.
    for item in (root, *root.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("source_profile_directory_reparse")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("source_profile_directory_invalid")
    return resolved


def _read(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 8192
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("source_profile_file_invalid")
    payload = path.read_bytes()
    if len(payload) > 8192:
        raise ValueError("source_profile_file_invalid")
    return payload


def _profile(raw: str, workspace: Path) -> SourceTrialProfile:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
        raise ValueError("source_profile_transaction_invalid")
    profile = SourceTrialProfile.model_validate_json(raw)
    if profile.workspace_root != str(workspace):
        raise ValueError("source_profile_workspace_binding_mismatch")
    return profile


def prepare(*, state_root: Path, previous_workspace: Path, workspace: Path) -> dict | None:
    _source_only()
    state = _directory(state_root)
    original = _read(state / PROFILE_FILE)
    if original is None:
        return None
    previous, destination = _directory(previous_workspace), _directory(workspace)
    # Reuse the same fixed-profile and config-digest authority as requests.
    profile = load_source_trial_profile(state_root=state, workspace_root=previous)
    if profile is None or _read(state / PROFILE_FILE) != original:
        raise ValueError("source_profile_changed_during_prepare")
    payload = profile.model_dump(mode="json", exclude={"profile_config_sha256"})
    payload["workspace_root"] = str(destination)
    payload["profile_config_sha256"] = canonical_sha256(payload)
    rebound = SourceTrialProfile.model_validate(payload)
    return {"schema": TRANSACTION_SCHEMA, "state_root": str(state),
            "previous_workspace": str(previous), "workspace": str(destination),
            "previous_content": original.decode("utf-8"),
            "next_content": json.dumps(rebound.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"}


def _validate_transaction(transaction: dict, *, rollback: bool = False) -> tuple[Path, bytes, bytes]:
    _source_only()
    if (type(transaction) is not dict or set(transaction) != TRANSACTION_KEYS
            or transaction.get("schema") != TRANSACTION_SCHEMA
            or any(not isinstance(value, str) for value in transaction.values())):
        raise ValueError("source_profile_transaction_invalid")
    state = _directory(transaction["state_root"])
    # Rollback restores only the profile file. A newly selected directory may
    # have disappeared during a failed restart; that must not prevent undo.
    previous = Path(transaction["previous_workspace"])
    destination = Path(transaction["workspace"])
    if not previous.is_absolute() or not destination.is_absolute():
        raise ValueError("source_profile_transaction_invalid")
    if not rollback:
        previous, destination = _directory(previous), _directory(destination)
    old_profile = _profile(transaction["previous_content"], previous)
    new_profile = _profile(transaction["next_content"], destination)
    exclude = {"workspace_root", "profile_config_sha256"}
    if old_profile.model_dump(exclude=exclude) != new_profile.model_dump(exclude=exclude):
        raise ValueError("source_profile_transaction_changes_authority")
    return (state / PROFILE_FILE, transaction["previous_content"].encode("utf-8"),
            transaction["next_content"].encode("utf-8"))


def _atomic_replace(path: Path, payload: bytes, expected: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path) != expected:
            raise ValueError("source_profile_concurrent_change")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(transaction: dict, *, rollback: bool = False) -> bool:
    path, original, rebound = _validate_transaction(transaction, rollback=rollback)
    expected, destination = (rebound, original) if rollback else (original, rebound)
    current = _read(path)
    if current == destination:
        return False
    if current != expected:
        raise ValueError("source_profile_concurrent_change")
    _atomic_replace(path, destination, expected)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "apply", "rollback"))
    parser.add_argument("--state-root")
    parser.add_argument("--previous-workspace")
    parser.add_argument("--workspace")
    options = parser.parse_args()
    try:
        if options.operation == "prepare":
            if not all((options.state_root, options.previous_workspace, options.workspace)):
                raise ValueError("source_profile_rebind_arguments_missing")
            transaction = prepare(state_root=Path(options.state_root),
                                  previous_workspace=Path(options.previous_workspace),
                                  workspace=Path(options.workspace))
            result = {"ok": True, "enabled": transaction is not None, "transaction": transaction}
        else:
            raw = sys.stdin.read(32769)
            if len(raw) > 32768:
                raise ValueError("source_profile_transaction_too_large")
            changed = apply(json.loads(raw), rollback=options.operation == "rollback")
            result = {"ok": True, "changed": changed}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:
        # No configuration content or inherited environment reaches error logs.
        print(json.dumps({"ok": False, "error": "source_profile_rebind_failed",
                          "error_type": type(error).__name__}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
