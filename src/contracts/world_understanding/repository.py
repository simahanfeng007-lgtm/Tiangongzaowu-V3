"""Immutable, side-effect-free contracts for repository reality observations.

The contracts describe already-observed repository facts.  They deliberately do
not run Git, read files, execute tools, own WorldState, or persist learning.
Repository providers implement the observation protocol outside this package.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from ..canonical import canonical_sha256
from ..models import Sha256
from ._base import WorldContractModel

REPOSITORY_OBSERVATION_SCHEMA = "tiangong.repository-observation.v1"
GitObservationChangeKind = Literal["ADD", "MODIFY", "DELETE", "RENAME", "MOVE"]

_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _nfc(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError(f"{label} must be NFC and contain no NUL")
    return value


def _relative_repo_path(value: str) -> str:
    value = _nfc(value, label="repository path").replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise ValueError("repository path must be repository-relative")
    parts = tuple(part for part in value.split("/") if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError("repository path may not escape the repository")
    normalized = "/".join(parts)
    if normalized != value:
        raise ValueError("repository path must already be canonically normalized")
    return value


def _git_object_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not _GIT_OBJECT_ID.fullmatch(value):
        raise ValueError("Git object id must be a lower-case SHA-1 or SHA-256 object id")
    return value


def _sorted_unique_paths(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(_relative_repo_path(item) for item in value)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError("repository path sets must be sorted and unique")
    return normalized


class RepositoryIdentity(WorldContractModel):
    provider_kind: str = Field(min_length=1, max_length=80)
    repository_id: str = Field(min_length=1, max_length=160)
    repository_root_ref: str = Field(min_length=1, max_length=32_768)
    worktree_id: str = Field(min_length=1, max_length=160)
    worktree_root_ref: str = Field(min_length=1, max_length=32_768)
    vcs_kind: Literal["git"] = "git"
    remote_identity_hash: Sha256 | None = None
    default_branch_hint: str | None = Field(default=None, max_length=512)

    @field_validator("provider_kind", "repository_id", "repository_root_ref", "worktree_id", "worktree_root_ref")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="repository identity field")

    @field_validator("default_branch_hint")
    @classmethod
    def validate_branch_hint(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="default branch hint")


class RepositoryRevision(WorldContractModel):
    branch: str = Field(min_length=1, max_length=512)
    head_commit: str
    parent_commit: str | None = None
    detached_head: bool
    observed_at_ms: int = Field(ge=0)

    _validate_head = field_validator("head_commit")(_git_object_id)
    _validate_parent = field_validator("parent_commit")(_git_object_id)

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        return _nfc(value, label="branch")

    @model_validator(mode="after")
    def validate_detached_branch(self) -> "RepositoryRevision":
        if self.detached_head and not self.branch.startswith("detached:"):
            raise ValueError("detached HEAD must use a detached:<sha> branch identity")
        if not self.detached_head and self.branch.startswith("detached:"):
            raise ValueError("attached HEAD may not use detached branch identity")
        return self


class RepositoryPathRename(WorldContractModel):
    old_path: str
    new_path: str

    _validate_old = field_validator("old_path")(_relative_repo_path)
    _validate_new = field_validator("new_path")(_relative_repo_path)

    @model_validator(mode="after")
    def validate_distinct(self) -> "RepositoryPathRename":
        if self.old_path == self.new_path:
            raise ValueError("rename paths must differ")
        return self


class RepositoryWorkingTreeState(WorldContractModel):
    clean: bool
    staged_paths: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    renamed_paths: tuple[RepositoryPathRename, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    conflicted_paths: tuple[str, ...] = ()
    state_sha256: Sha256

    _validate_staged = field_validator("staged_paths")(_sorted_unique_paths)
    _validate_modified = field_validator("modified_paths")(_sorted_unique_paths)
    _validate_deleted = field_validator("deleted_paths")(_sorted_unique_paths)
    _validate_untracked = field_validator("untracked_paths")(_sorted_unique_paths)
    _validate_conflicted = field_validator("conflicted_paths")(_sorted_unique_paths)

    @field_validator("renamed_paths")
    @classmethod
    def validate_renames(cls, value: tuple[RepositoryPathRename, ...]) -> tuple[RepositoryPathRename, ...]:
        keys = tuple((item.old_path, item.new_path) for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("renamed paths must be sorted and unique")
        return value

    def computed_state_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"state_sha256"}))

    @model_validator(mode="after")
    def validate_state(self) -> "RepositoryWorkingTreeState":
        if self.state_sha256 != self.computed_state_sha256():
            raise ValueError("repository working tree state hash mismatch")
        has_overlay = any((
            self.staged_paths,
            self.modified_paths,
            self.deleted_paths,
            self.renamed_paths,
            self.untracked_paths,
            self.conflicted_paths,
        ))
        if self.clean == has_overlay:
            raise ValueError("repository clean flag does not match observed overlay")
        return self

    @classmethod
    def build(
        cls,
        *,
        staged_paths: tuple[str, ...] = (),
        modified_paths: tuple[str, ...] = (),
        deleted_paths: tuple[str, ...] = (),
        renamed_paths: tuple[RepositoryPathRename, ...] = (),
        untracked_paths: tuple[str, ...] = (),
        conflicted_paths: tuple[str, ...] = (),
    ) -> "RepositoryWorkingTreeState":
        clean = not any((staged_paths, modified_paths, deleted_paths, renamed_paths, untracked_paths, conflicted_paths))
        candidate = cls.model_construct(
            clean=clean,
            staged_paths=staged_paths,
            modified_paths=modified_paths,
            deleted_paths=deleted_paths,
            renamed_paths=renamed_paths,
            untracked_paths=untracked_paths,
            conflicted_paths=conflicted_paths,
            state_sha256="0" * 64,
        )
        return cls(**{**candidate.model_dump(mode="python"), "state_sha256": candidate.computed_state_sha256()})


class RepositoryPathChange(WorldContractModel):
    change_kind: GitObservationChangeKind
    old_path: str | None = None
    new_path: str | None = None
    old_blob_sha: str | None = None
    new_blob_sha: str | None = None
    explicit_identity_anchor: str | None = Field(default=None, max_length=512)

    @field_validator("old_path", "new_path")
    @classmethod
    def validate_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _relative_repo_path(value)

    _validate_old_blob = field_validator("old_blob_sha")(_git_object_id)
    _validate_new_blob = field_validator("new_blob_sha")(_git_object_id)

    @field_validator("explicit_identity_anchor")
    @classmethod
    def validate_anchor(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="identity anchor")

    @model_validator(mode="after")
    def validate_shape(self) -> "RepositoryPathChange":
        old, new = self.old_path, self.new_path
        if self.change_kind == "ADD" and (old is not None or new is None):
            raise ValueError("ADD requires only new_path")
        if self.change_kind == "DELETE" and (old is None or new is not None):
            raise ValueError("DELETE requires only old_path")
        if self.change_kind == "MODIFY" and (old is None or new is None or old != new):
            raise ValueError("MODIFY requires identical old/new path")
        if self.change_kind in {"RENAME", "MOVE"} and (old is None or new is None or old == new):
            raise ValueError("RENAME/MOVE requires distinct old/new paths")
        return self

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.change_kind,
            self.old_path or "",
            self.new_path or "",
            self.old_blob_sha or "",
            self.new_blob_sha or "",
        )


class RepositoryFileObservation(WorldContractModel):
    path: str
    blob_sha: str | None = None
    content_sha256: Sha256 | None = None
    tracked: bool
    exists: bool
    mode: str | None = Field(default=None, max_length=32)
    size: int | None = Field(default=None, ge=0)
    language_hint: str | None = Field(default=None, max_length=80)

    _validate_path = field_validator("path")(_relative_repo_path)
    _validate_blob = field_validator("blob_sha")(_git_object_id)

    @field_validator("mode", "language_hint")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="file observation field")


class RepositoryObservation(WorldContractModel):
    schema: Literal["tiangong.repository-observation.v1"] = REPOSITORY_OBSERVATION_SCHEMA
    identity: RepositoryIdentity
    revision: RepositoryRevision
    working_tree_state: RepositoryWorkingTreeState
    changes: tuple[RepositoryPathChange, ...] = ()
    files: tuple[RepositoryFileObservation, ...] = ()
    observation_sha256: Sha256
    observed_at_ms: int = Field(ge=0)
    provider_version: str = Field(min_length=1, max_length=80)

    @field_validator("provider_version")
    @classmethod
    def validate_provider_version(cls, value: str) -> str:
        return _nfc(value, label="provider version")

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, value: tuple[RepositoryPathChange, ...]) -> tuple[RepositoryPathChange, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("repository changes must be sorted and unique")
        return value

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: tuple[RepositoryFileObservation, ...]) -> tuple[RepositoryFileObservation, ...]:
        paths = tuple(item.path for item in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("repository file observations must be sorted and unique by path")
        return value

    def computed_observation_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"observation_sha256", "observed_at_ms", "revision": {"observed_at_ms"}})
        )

    @model_validator(mode="after")
    def validate_observation_hash(self) -> "RepositoryObservation":
        if self.observed_at_ms != self.revision.observed_at_ms:
            raise ValueError("observation/revision observed_at_ms mismatch")
        if self.observation_sha256 != self.computed_observation_sha256():
            raise ValueError("repository observation hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        identity: RepositoryIdentity,
        revision: RepositoryRevision,
        working_tree_state: RepositoryWorkingTreeState,
        changes: tuple[RepositoryPathChange, ...] = (),
        files: tuple[RepositoryFileObservation, ...] = (),
        provider_version: str,
    ) -> "RepositoryObservation":
        payload = {
            "schema": REPOSITORY_OBSERVATION_SCHEMA,
            "identity": identity,
            "revision": revision,
            "working_tree_state": working_tree_state,
            "changes": changes,
            "files": files,
            "observation_sha256": "0" * 64,
            "observed_at_ms": revision.observed_at_ms,
            "provider_version": provider_version,
        }
        candidate = cls.model_construct(**payload)
        payload["observation_sha256"] = candidate.computed_observation_sha256()
        return cls(**payload)


class RepositoryProviderCapabilities(WorldContractModel):
    vcs_kind: Literal["git"] = "git"
    working_tree: bool = True
    staged_state: bool = True
    rename_detection: bool = True
    object_hashes: bool = True
    read_only: Literal[True] = True


@runtime_checkable
class RepositoryProvider(Protocol):
    """Read-only provider boundary.  Implementations may observe but never mutate."""

    def discover(self, workspace_root: str) -> RepositoryIdentity | None: ...
    def observe(self, identity: RepositoryIdentity) -> RepositoryObservation: ...
    def observe_delta(self, identity: RepositoryIdentity, previous_revision: RepositoryRevision) -> RepositoryObservation: ...
    def capabilities(self) -> RepositoryProviderCapabilities: ...


__all__ = [
    "REPOSITORY_OBSERVATION_SCHEMA",
    "GitObservationChangeKind",
    "RepositoryFileObservation",
    "RepositoryIdentity",
    "RepositoryObservation",
    "RepositoryPathChange",
    "RepositoryPathRename",
    "RepositoryProvider",
    "RepositoryProviderCapabilities",
    "RepositoryRevision",
    "RepositoryWorkingTreeState",
]
