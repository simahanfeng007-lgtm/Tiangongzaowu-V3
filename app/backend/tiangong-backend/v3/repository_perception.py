"""Bounded, read-only repository perception adapter for the V3 workspace.

This module is a sensor, not a Runtime or executor. It never mutates Git and it
never owns WorldState, memory, learning, scheduling, or execution authority.
"""
from __future__ import annotations

import os
import subprocess
import time
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Iterable

from contracts.canonical import canonical_sha256
from contracts.world_understanding.repository import (
    RepositoryFileObservation,
    RepositoryIdentity,
    RepositoryObservation,
    RepositoryPathChange,
    RepositoryPathRename,
    RepositoryProviderCapabilities,
    RepositoryRevision,
    RepositoryWorkingTreeState,
)
from world_understanding.post_commit import NativePostCommitEvent, notify_native_post_commit

from .run_context import current_run_context
from .workspace_settings import duqu_workspace_root

_PROVIDER_KIND = "local-git"
_PROVIDER_VERSION = "v0.1"
_GIT_TIMEOUT_SECONDS = 5.0
_MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_STATUS_ENTRIES = 2048
_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
_READ_ONLY_GIT_COMMANDS = frozenset({"rev-parse", "status", "hash-object", "diff"})


class RepositoryObservationError(RuntimeError):
    """Bounded sensor failure. Callers should normally treat it as fail-open."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _root_text(path: Path) -> str:
    return _nfc(str(path.resolve(strict=False)))


def _repo_path(value: str) -> str:
    return _nfc(value.replace("\\", "/"))


def _run_git(cwd: Path, args: tuple[str, ...], *, allow_failure: bool = False) -> bytes:
    if not args or args[0] not in _READ_ONLY_GIT_COMMANDS:
        raise RepositoryObservationError("repository provider command is not read-only allowlisted")
    if args[0] == "hash-object" and any(arg in {"-w", "--write"} for arg in args[1:]):
        raise RepositoryObservationError("git hash-object write mode is forbidden")
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    })
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryObservationError("bounded Git observation failed") from exc
    if len(completed.stdout) > _MAX_GIT_OUTPUT_BYTES or len(completed.stderr) > _MAX_GIT_OUTPUT_BYTES:
        raise RepositoryObservationError("Git observation exceeded bounded output")
    if completed.returncode != 0 and not allow_failure:
        raise RepositoryObservationError("Git read command failed")
    return completed.stdout if completed.returncode == 0 else b""


def _decode_line(value: bytes) -> str:
    try:
        return _nfc(value.decode("utf-8", errors="strict").strip())
    except UnicodeDecodeError as exc:
        raise RepositoryObservationError("Git output is not valid UTF-8") from exc


def _decode_path(value: bytes) -> str:
    try:
        return _repo_path(value.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise RepositoryObservationError("Git path is not valid UTF-8") from exc


def _change_kind(old_path: str, new_path: str) -> str:
    return "MOVE" if PurePosixPath(old_path).parent != PurePosixPath(new_path).parent else "RENAME"


def _working_blob(root: Path, path: str) -> str | None:
    target = root.joinpath(*PurePosixPath(path).parts)
    if not target.is_file():
        return None
    value = _decode_line(_run_git(root, ("hash-object", "--", path), allow_failure=True))
    return value or None


def _blob_at(root: Path, revision: str, path: str) -> str | None:
    value = _decode_line(_run_git(root, ("rev-parse", "--verify", f"{revision}:{path}"), allow_failure=True))
    return value or None


def _status_records(raw: bytes) -> tuple[tuple[str, str, str, str | None], ...]:
    fields = raw.split(b"\x00")
    rows: list[tuple[str, str, str, str | None]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 3 or field[2:3] != b" ":
            raise RepositoryObservationError("unexpected Git porcelain record")
        code = field[:2].decode("ascii", errors="strict")
        path = _decode_path(field[3:])
        origin: str | None = None
        if "R" in code or "C" in code:
            if index >= len(fields) or not fields[index]:
                raise RepositoryObservationError("truncated Git rename record")
            origin = _decode_path(fields[index])
            index += 1
        rows.append((code[0], code[1], path, origin))
        if len(rows) > _MAX_STATUS_ENTRIES:
            raise RepositoryObservationError("repository status exceeded bounded entry count")
    return tuple(rows)


def _diff_records(raw: bytes) -> tuple[tuple[str, str, str | None], ...]:
    fields = raw.split(b"\x00")
    rows: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(fields):
        status_raw = fields[index]
        index += 1
        if not status_raw:
            continue
        status = status_raw.decode("ascii", errors="strict")
        kind = status[:1]
        if kind in {"R", "C"}:
            if index + 1 >= len(fields):
                raise RepositoryObservationError("truncated Git diff rename record")
            old_path = _decode_path(fields[index])
            new_path = _decode_path(fields[index + 1])
            index += 2
            rows.append((status, new_path, old_path))
        else:
            if index >= len(fields):
                raise RepositoryObservationError("truncated Git diff record")
            path = _decode_path(fields[index])
            index += 1
            rows.append((status, path, None))
        if len(rows) > _MAX_STATUS_ENTRIES:
            raise RepositoryObservationError("repository diff exceeded bounded entry count")
    return tuple(rows)


def _overlay_changes(
    root: Path,
    records: tuple[tuple[str, str, str, str | None], ...],
) -> tuple[RepositoryPathChange, ...]:
    changes: dict[tuple[str, str, str], RepositoryPathChange] = {}
    for x, y, path, origin in records:
        code = x + y
        if code in _CONFLICT_CODES:
            continue
        if origin is not None and (x in {"R", "C"} or y in {"R", "C"}):
            row = RepositoryPathChange(
                change_kind=_change_kind(origin, path),
                old_path=origin,
                new_path=path,
                old_blob_sha=_blob_at(root, "HEAD", origin),
                new_blob_sha=_working_blob(root, path),
            )
        elif code == "??" or x == "A":
            row = RepositoryPathChange(
                change_kind="ADD",
                new_path=path,
                new_blob_sha=_working_blob(root, path),
            )
        elif x == "D" or y == "D":
            row = RepositoryPathChange(
                change_kind="DELETE",
                old_path=path,
                old_blob_sha=_blob_at(root, "HEAD", path),
            )
        else:
            row = RepositoryPathChange(
                change_kind="MODIFY",
                old_path=path,
                new_path=path,
                old_blob_sha=_blob_at(root, "HEAD", path),
                new_blob_sha=_working_blob(root, path),
            )
        changes[(row.change_kind, row.old_path or "", row.new_path or "")] = row
    return tuple(sorted(changes.values(), key=lambda item: item.sort_key()))


def _commit_changes(root: Path, previous_commit: str, current_commit: str) -> tuple[RepositoryPathChange, ...]:
    if previous_commit == current_commit:
        return ()
    raw = _run_git(root, ("diff", "--name-status", "-z", "-M", previous_commit, current_commit, "--"))
    rows: list[RepositoryPathChange] = []
    for status, path, origin in _diff_records(raw):
        code = status[:1]
        if code == "A":
            rows.append(RepositoryPathChange(
                change_kind="ADD",
                new_path=path,
                new_blob_sha=_blob_at(root, current_commit, path),
            ))
        elif code == "D":
            rows.append(RepositoryPathChange(
                change_kind="DELETE",
                old_path=path,
                old_blob_sha=_blob_at(root, previous_commit, path),
            ))
        elif code in {"R", "C"} and origin is not None:
            rows.append(RepositoryPathChange(
                change_kind=_change_kind(origin, path),
                old_path=origin,
                new_path=path,
                old_blob_sha=_blob_at(root, previous_commit, origin),
                new_blob_sha=_blob_at(root, current_commit, path),
            ))
        else:
            rows.append(RepositoryPathChange(
                change_kind="MODIFY",
                old_path=path,
                new_path=path,
                old_blob_sha=_blob_at(root, previous_commit, path),
                new_blob_sha=_blob_at(root, current_commit, path),
            ))
    return tuple(sorted(rows, key=lambda item: item.sort_key()))


def _working_tree_state(
    records: tuple[tuple[str, str, str, str | None], ...],
) -> RepositoryWorkingTreeState:
    staged: set[str] = set()
    modified: set[str] = set()
    deleted: set[str] = set()
    renamed: set[tuple[str, str]] = set()
    untracked: set[str] = set()
    conflicted: set[str] = set()
    for x, y, path, origin in records:
        code = x + y
        if code == "??":
            untracked.add(path)
            continue
        if code in _CONFLICT_CODES:
            conflicted.add(path)
        if x not in {" ", "?", "!"}:
            staged.add(path)
        if y not in {" ", "?", "!"}:
            modified.add(path)
        if x == "D" or y == "D":
            deleted.add(path)
        if origin is not None:
            renamed.add((origin, path))
    return RepositoryWorkingTreeState.build(
        staged_paths=tuple(sorted(staged)),
        modified_paths=tuple(sorted(modified)),
        deleted_paths=tuple(sorted(deleted)),
        renamed_paths=tuple(
            RepositoryPathRename(old_path=old, new_path=new)
            for old, new in sorted(renamed)
        ),
        untracked_paths=tuple(sorted(untracked)),
        conflicted_paths=tuple(sorted(conflicted)),
    )


def _file_observations(
    root: Path,
    changes: Iterable[RepositoryPathChange],
    *,
    untracked_paths: frozenset[str],
) -> tuple[RepositoryFileObservation, ...]:
    paths: dict[str, tuple[bool, str | None]] = {}
    for change in changes:
        if change.old_path is not None and change.change_kind in {"DELETE", "RENAME", "MOVE"}:
            paths[change.old_path] = (False, change.old_blob_sha)
        if change.new_path is not None:
            paths[change.new_path] = (True, change.new_blob_sha)
    rows: list[RepositoryFileObservation] = []
    for path, (exists_hint, historical_blob) in sorted(paths.items()):
        target = root.joinpath(*PurePosixPath(path).parts)
        exists = bool(exists_hint and target.is_file())
        blob_sha = _working_blob(root, path) if exists else historical_blob
        rows.append(RepositoryFileObservation(
            path=path,
            blob_sha=blob_sha,
            tracked=path not in untracked_paths,
            exists=exists,
            size=target.stat().st_size if exists else None,
        ))
    return tuple(rows)


class LocalGitRepositoryProvider:
    """Strictly read-only local Git implementation of RepositoryProvider."""

    def capabilities(self) -> RepositoryProviderCapabilities:
        return RepositoryProviderCapabilities()

    def discover(self, workspace_root: str) -> RepositoryIdentity | None:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        top_raw = _run_git(workspace, ("rev-parse", "--show-toplevel"), allow_failure=True)
        if not top_raw:
            return None
        worktree = Path(_decode_line(top_raw)).resolve(strict=False)
        common_raw = _run_git(worktree, ("rev-parse", "--git-common-dir"))
        common_text = _decode_line(common_raw)
        common_dir = Path(common_text)
        if not common_dir.is_absolute():
            common_dir = (worktree / common_dir).resolve(strict=False)
        repo_key = _root_text(common_dir)
        worktree_key = _root_text(worktree)
        return RepositoryIdentity(
            provider_kind=_PROVIDER_KIND,
            repository_id="repo." + canonical_sha256({"vcs": "git", "common_dir": repo_key})[:48],
            repository_root_ref=worktree_key,
            worktree_id="worktree." + canonical_sha256({"repository": repo_key, "worktree": worktree_key})[:44],
            worktree_root_ref=worktree_key,
            remote_identity_hash=None,
            default_branch_hint=None,
        )

    def _revision(self, root: Path, observed_at_ms: int) -> RepositoryRevision:
        head = _decode_line(_run_git(root, ("rev-parse", "--verify", "HEAD")))
        branch_name = _decode_line(_run_git(root, ("rev-parse", "--abbrev-ref", "HEAD")))
        detached = branch_name == "HEAD"
        parent_raw = _run_git(root, ("rev-parse", "--verify", "HEAD^"), allow_failure=True)
        return RepositoryRevision(
            branch="detached:" + head[:12] if detached else branch_name,
            head_commit=head,
            parent_commit=_decode_line(parent_raw) or None,
            detached_head=detached,
            observed_at_ms=observed_at_ms,
        )

    def _observe(
        self,
        identity: RepositoryIdentity,
        previous_revision: RepositoryRevision | None,
    ) -> RepositoryObservation:
        root = Path(identity.worktree_root_ref).resolve(strict=False)
        observed_at_ms = time.time_ns() // 1_000_000
        revision = self._revision(root, observed_at_ms)
        status_raw = _run_git(root, ("status", "--porcelain=v1", "-z", "--untracked-files=all"))
        records = _status_records(status_raw)
        state = _working_tree_state(records)
        overlay = _overlay_changes(root, records)
        committed = (
            ()
            if previous_revision is None
            else _commit_changes(root, previous_revision.head_commit, revision.head_commit)
        )
        merged = {item.sort_key(): item for item in (*committed, *overlay)}
        changes = tuple(merged[key] for key in sorted(merged))
        files = _file_observations(
            root,
            changes,
            untracked_paths=frozenset(state.untracked_paths),
        )
        return RepositoryObservation.build(
            identity=identity,
            revision=revision,
            working_tree_state=state,
            changes=changes,
            files=files,
            provider_version=_PROVIDER_VERSION,
        )

    def observe(self, identity: RepositoryIdentity) -> RepositoryObservation:
        return self._observe(identity, None)

    def observe_delta(
        self,
        identity: RepositoryIdentity,
        previous_revision: RepositoryRevision,
    ) -> RepositoryObservation:
        return self._observe(identity, previous_revision)


def observe_active_repository(
    provider: LocalGitRepositoryProvider | None = None,
) -> RepositoryObservation | None:
    """Observe the workspace authority once; no scheduler or watcher is created."""
    sensor = provider or LocalGitRepositoryProvider()
    root = duqu_workspace_root()
    identity = sensor.discover(str(root))
    return None if identity is None else sensor.observe(identity)


def publish_active_repository_observation(
    provider: LocalGitRepositoryProvider | None = None,
) -> object | None:
    """Publish one bounded Git + structure reality notification through native ingress."""
    try:
        observation = observe_active_repository(provider)
    except RepositoryObservationError:
        return None
    if observation is None:
        return None

    payload: dict = observation.model_dump(mode="json")
    native_hash = observation.observation_sha256
    producer_ref = "repository.local-git.v0.1"
    try:
        from .repository_structure import repository_structure_index

        structure_delta = repository_structure_index().update(observation)
        payload = {
            "repository_observation": observation.model_dump(mode="json"),
            "structure_delta": structure_delta.model_dump(mode="json"),
        }
        native_hash = canonical_sha256({
            "repository_observation_sha256": observation.observation_sha256,
            "structure_delta_sha256": structure_delta.delta_sha256,
        })
        producer_ref = "repository.local-git-structure.v0.2"
    except Exception:
        pass

    context = current_run_context()
    raw_identity = {
        "life_id": context.life_id,
        "principal_scope_hash": context.principal_scope_hash,
        "workspace_id": context.workspace_id,
        "run_id": context.run_id,
        "request_id": context.request_id,
        "session_id": context.session_id,
        "conversation_id": context.conversation_id,
    }
    identity = {key: str(value) for key, value in raw_identity.items() if value}
    return notify_native_post_commit(NativePostCommitEvent(
        source_kind="GIT_CODE",
        source_native_id="repoobs." + native_hash[:48],
        producer_ref=producer_ref,
        payload=payload,
        occurred_at_ms=observation.observed_at_ms,
        identity=identity,
    ))


__all__ = [
    "LocalGitRepositoryProvider",
    "RepositoryObservationError",
    "observe_active_repository",
    "publish_active_repository_observation",
]
