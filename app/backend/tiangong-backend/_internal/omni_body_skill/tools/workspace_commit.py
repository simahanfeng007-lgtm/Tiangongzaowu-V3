"""Recoverable broker commits under the existing workspace mutation lock.

The journal is private runtime state, never mounted into the tool process. It
stores old and new bytes before any host mutation. This provides crash recovery,
not instantaneous multi-file visibility to applications outside that lock.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_journal(path: Path, value: dict) -> None:
    temporary = path.with_name("~" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _target(root: Path, relative: str) -> Path:
    from .sandbox_runtime import SandboxError, _is_link_or_reparse, _SKIP_NAMES
    path = Path(relative)
    if (not relative or path.is_absolute() or path.as_posix() != relative
            or any(part in {".", ".."} or part.casefold() in _SKIP_NAMES for part in path.parts)):
        raise SandboxError("sandbox_transaction_path_invalid")
    target = root / path
    for parent in (target, *target.parents):
        if _is_link_or_reparse(parent):
            raise SandboxError("sandbox_transaction_link_forbidden")
        if parent == root:
            break
    return target


def version(path: Path):
    from .sandbox_runtime import _file_digest, _is_link_or_reparse
    if _is_link_or_reparse(path) or (path.exists() and not path.is_file()):
        return ["conflict", "not_regular_file"]
    return [path.stat().st_size, _file_digest(path)] if path.exists() else None


def _load(directory: Path, workspace: Path):
    from .sandbox_runtime import SandboxError, _is_link_or_reparse
    if _is_link_or_reparse(directory) or _is_link_or_reparse(directory / "journal.json"):
        raise SandboxError("sandbox_transaction_link_forbidden")
    record = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
    if record.get("schema") != "tiangong.workspace-commit.v1" or record.get("workspace") != str(workspace):
        raise SandboxError("sandbox_transaction_identity_invalid")
    for row in record["files"]:
        _target(workspace, row["path"])
    return record


def rollback(directory: Path, workspace: Path, record: dict) -> None:
    from .sandbox_runtime import SandboxError, _atomic_copy
    conflicts = []
    for row in reversed(record["files"]):
        target = _target(workspace, row["path"])
        actual = version(target)
        if actual == row["before"]:
            continue
        if actual != row["after"]:
            # Keep the external edit and both journal versions for resolution.
            conflicts.append(row["path"])
            continue
        try:
            if row["before"] is None:
                target.unlink()
                sync_directory(target.parent)
            else:
                backup = _target(directory / "before", row["path"])
                if version(backup) != row["before"]:
                    raise SandboxError("sandbox_transaction_backup_changed")
                _atomic_copy(backup, target)
        except OSError:
            # Disk full/locked files may also prevent immediate rollback. A
            # later recovery retries; never report the partial commit as success.
            conflicts.append(row["path"])
    record.update(state="CONFLICT" if conflicts else "ROLLED_BACK", conflicts=conflicts)
    write_journal(directory / "journal.json", record)
    if conflicts:
        raise SandboxError("sandbox_transaction_conflict:" + directory.name)


def recover(root: Path, workspace: Path) -> None:
    """Must run before copying a new workspace, with its mutation lock held."""
    if not root.exists():
        return
    for directory in sorted(root.iterdir()):
        if not (directory / "journal.json").exists():
            # Preparation without a durable journal has never touched the host.
            continue
        record = _load(directory, workspace)
        if record["state"] not in {"COMMITTED", "ROLLED_BACK"}:
            rollback(directory, workspace, record)


def replay(root: Path, workspace: Path, operation: str, input_digest: str):
    from .sandbox_runtime import SandboxError
    directory = root / ("tx_" + hashlib.sha256(operation.encode()).hexdigest()[:32])
    if not (directory / "journal.json").exists():
        return None
    record = _load(directory, workspace)
    if record["input_sha256"] != input_digest:
        raise SandboxError("sandbox_transaction_input_changed")
    if record["state"] != "COMMITTED":
        raise SandboxError("sandbox_transaction_previous_attempt_rolled_back")
    for row in record["files"]:
        if version(_target(workspace, row["path"])) != row["after"]:
            raise SandboxError("sandbox_transaction_committed_output_changed")
    return {**record["receipt"], "transaction_replayed": True}


def commit(*, source: Path, workspace: Path, before: dict, after: dict,
           changed: list, deleted: list, root: Path, operation: str,
           input_digest: str, receipt: dict, cancel_check=None) -> dict:
    from .sandbox_runtime import SandboxError, _atomic_copy
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ("tx_" + hashlib.sha256(operation.encode()).hexdigest()[:32])
    directory.mkdir(exist_ok=True)
    rows = [{"path": rel, "before": list(before[rel]) if rel in before else None,
             "after": list(after[rel]) if rel in after else None} for rel in sorted(set(changed + deleted))]
    # Prepare and verify every previous version and output BEFORE PREPARED.
    for row in rows:
        target = _target(workspace, row["path"])
        if version(target) != row["before"]:
            raise SandboxError("sandbox_destination_changed:" + row["path"])
        for kind, origin in (("before", target), ("after", _target(source, row["path"]))):
            if row[kind] is not None:
                stored = _target(directory / kind, row["path"])
                _atomic_copy(origin, stored)
                if version(stored) != row[kind]:
                    raise SandboxError("sandbox_transaction_preparation_changed")
    receipt = {**receipt, "transaction_id": directory.name, "transaction_state": "COMMITTED",
               "file_versions": rows}
    record = {"schema": "tiangong.workspace-commit.v1", "workspace": str(workspace),
              "state": "PREPARED", "input_sha256": input_digest, "files": rows, "receipt": receipt}
    write_journal(directory / "journal.json", record)
    sync_directory(root)
    try:
        for row in rows:
            if cancel_check and cancel_check():
                raise SandboxError("sandbox_cancelled")
            target = _target(workspace, row["path"])
            if version(target) != row["before"]:
                raise SandboxError("sandbox_destination_changed:" + row["path"])
            if row["after"] is None:
                target.unlink()
                sync_directory(target.parent)
            else:
                _atomic_copy(_target(directory / "after", row["path"]), target)
        if cancel_check and cancel_check():
            raise SandboxError("sandbox_cancelled")
        # Recheck the whole set. External applications do not take our lock.
        if any(version(_target(workspace, r["path"])) != r["after"] for r in rows):
            raise SandboxError("sandbox_destination_changed_during_commit")
        record["state"] = "COMMITTED"
        write_journal(directory / "journal.json", record)
    except Exception:
        rollback(directory, workspace, record)
        raise
    return receipt
