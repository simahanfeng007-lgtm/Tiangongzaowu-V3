"""Immutable contracts for rebuildable repository structure perception.

These records contain deterministic parser output and cache metadata only.
They do not read source files, run Git, execute tools, own WorldState, or
persist long-term learning.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..canonical import canonical_sha256
from ..models import Sha256
from ._base import WorldContractModel

REPOSITORY_STRUCTURE_SCHEMA = "tiangong.repository-structure.v1"

StructureLanguage = Literal["python", "javascript", "typescript", "tsx"]
StructureNodeKind = Literal["Class", "Function", "Method"]
StructureParseStatus = Literal[
    "PARSED",
    "SYNTAX_ERROR",
    "SKIPPED_SECRET",
    "SKIPPED_BINARY",
    "SKIPPED_LARGE",
    "PARSER_UNAVAILABLE",
]
StructureDeltaStatus = Literal["APPLIED", "NOOP", "FAILED_OPEN"]

_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _nfc(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError(f"{label} must be NFC and contain no NUL")
    return value


def _repo_path(value: str) -> str:
    value = _nfc(value, label="repository path").replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise ValueError("repository path must be repository-relative")
    parts = tuple(part for part in value.split("/") if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError("repository path may not escape the repository")
    if "/".join(parts) != value:
        raise ValueError("repository path must already be canonically normalized")
    return value


def _git_object_id(value: str) -> str:
    if not _GIT_OBJECT_ID.fullmatch(value):
        raise ValueError("Git object id must be a lower-case SHA-1 or SHA-256 object id")
    return value


def _sorted_unique_strings(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(_nfc(item, label="structure string") for item in value)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError("structure string sets must be sorted and unique")
    return normalized


class RepositorySourceSpan(WorldContractModel):
    path: str
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)

    _validate_path = field_validator("path")(_repo_path)

    @model_validator(mode="after")
    def validate_order(self) -> "RepositorySourceSpan":
        if self.end_byte < self.start_byte:
            raise ValueError("source span end_byte precedes start_byte")
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("source span end position precedes start position")
        return self


class RepositoryStructureNode(WorldContractModel):
    node_id: str = Field(min_length=1, max_length=160)
    stable_anchor: str = Field(min_length=1, max_length=160)
    file_key: str = Field(min_length=1, max_length=160)
    kind: StructureNodeKind
    name: str = Field(min_length=1, max_length=1024)
    qualified_name: str = Field(min_length=1, max_length=4096)
    parent_anchor: str | None = Field(default=None, max_length=160)
    span: RepositorySourceSpan
    syntax_sha256: Sha256

    @field_validator("node_id", "stable_anchor", "file_key", "name", "qualified_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="structure node field")

    @field_validator("parent_anchor")
    @classmethod
    def validate_parent(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="parent anchor")

    def sort_key(self) -> tuple[int, int, str, str]:
        return (self.span.start_byte, self.span.end_byte, self.kind, self.node_id)


class RepositoryImportDeclaration(WorldContractModel):
    import_id: str = Field(min_length=1, max_length=160)
    file_key: str = Field(min_length=1, max_length=160)
    source_anchor: str = Field(min_length=1, max_length=160)
    module: str = Field(min_length=1, max_length=4096)
    imported_names: tuple[str, ...] = ()
    resolved_path: str | None = None
    resolved_module_name: str | None = Field(default=None, max_length=4096)
    span: RepositorySourceSpan
    syntax_sha256: Sha256

    @field_validator("import_id", "file_key", "source_anchor", "module")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="import field")

    _validate_names = field_validator("imported_names")(_sorted_unique_strings)

    @field_validator("resolved_path")
    @classmethod
    def validate_resolved_path(cls, value: str | None) -> str | None:
        return None if value is None else _repo_path(value)

    @field_validator("resolved_module_name")
    @classmethod
    def validate_resolved_module(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="resolved module name")

    def sort_key(self) -> tuple[int, int, str]:
        return (self.span.start_byte, self.span.end_byte, self.import_id)


class RepositoryStructureFile(WorldContractModel):
    path: str
    file_key: str = Field(min_length=1, max_length=160)
    module_anchor: str = Field(min_length=1, max_length=160)
    module_name: str = Field(min_length=1, max_length=4096)
    content_sha256: Sha256
    source_fingerprint: Sha256
    size: int = Field(ge=0)
    language: StructureLanguage
    parser_kind: str = Field(min_length=1, max_length=160)
    parser_version: str = Field(min_length=1, max_length=160)
    parse_status: StructureParseStatus
    nodes: tuple[RepositoryStructureNode, ...] = ()
    imports: tuple[RepositoryImportDeclaration, ...] = ()

    _validate_path = field_validator("path")(_repo_path)

    @field_validator(
        "file_key", "module_anchor", "module_name", "parser_kind", "parser_version"
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="structure file field")

    @field_validator("nodes")
    @classmethod
    def validate_nodes(
        cls, value: tuple[RepositoryStructureNode, ...]
    ) -> tuple[RepositoryStructureNode, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("structure nodes must be sorted and unique")
        return value

    @field_validator("imports")
    @classmethod
    def validate_imports(
        cls, value: tuple[RepositoryImportDeclaration, ...]
    ) -> tuple[RepositoryImportDeclaration, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("import declarations must be sorted and unique")
        return value


class RepositoryStructureRetirement(WorldContractModel):
    entity_type: Literal["Module", "Class", "Function", "Method"]
    stable_anchor: str = Field(min_length=1, max_length=160)
    canonical_name: str = Field(min_length=1, max_length=4096)

    @field_validator("stable_anchor", "canonical_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="retired structure field")

    def sort_key(self) -> tuple[str, str, str]:
        return (self.entity_type, self.stable_anchor, self.canonical_name)


class RepositoryStructureSnapshot(WorldContractModel):
    """Full rebuildable cache view. It is not a repository truth store."""

    schema: Literal["tiangong.repository-structure.v1"] = REPOSITORY_STRUCTURE_SCHEMA
    repository_id: str = Field(min_length=1, max_length=160)
    worktree_id: str = Field(min_length=1, max_length=160)
    head_commit: str
    working_tree_state_sha256: Sha256
    builder_version: str = Field(min_length=1, max_length=160)
    truncated: bool
    candidate_path_count: int = Field(ge=0)
    files: tuple[RepositoryStructureFile, ...] = ()
    view_sha256: Sha256
    built_at_ms: int = Field(ge=0)
    build_ms: int = Field(ge=0)

    _validate_head = field_validator("head_commit")(_git_object_id)

    @field_validator("repository_id", "worktree_id", "builder_version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="structure snapshot field")

    @field_validator("files")
    @classmethod
    def validate_files(
        cls, value: tuple[RepositoryStructureFile, ...]
    ) -> tuple[RepositoryStructureFile, ...]:
        paths = tuple(item.path for item in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("structure snapshot files must be sorted and unique")
        return value

    def computed_view_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("view_sha256", None)
        payload.pop("built_at_ms", None)
        payload.pop("build_ms", None)
        return canonical_sha256(payload)

    @model_validator(mode="after")
    def validate_hash(self) -> "RepositoryStructureSnapshot":
        if self.view_sha256 != self.computed_view_sha256():
            raise ValueError("repository structure view hash mismatch")
        return self

    @classmethod
    def build(
        cls,
        *,
        repository_id: str,
        worktree_id: str,
        head_commit: str,
        working_tree_state_sha256: str,
        builder_version: str,
        truncated: bool,
        candidate_path_count: int,
        files: tuple[RepositoryStructureFile, ...],
        built_at_ms: int,
        build_ms: int,
    ) -> "RepositoryStructureSnapshot":
        payload = {
            "schema": REPOSITORY_STRUCTURE_SCHEMA,
            "repository_id": repository_id,
            "worktree_id": worktree_id,
            "head_commit": head_commit,
            "working_tree_state_sha256": working_tree_state_sha256,
            "builder_version": builder_version,
            "truncated": truncated,
            "candidate_path_count": candidate_path_count,
            "files": files,
            "view_sha256": "0" * 64,
            "built_at_ms": built_at_ms,
            "build_ms": build_ms,
        }
        candidate = cls.model_construct(**payload)
        payload["view_sha256"] = candidate.computed_view_sha256()
        return cls(**payload)


class RepositoryStructureDelta(WorldContractModel):
    """Bounded structure notification carried by one RepositoryObservation."""

    schema: Literal["tiangong.repository-structure.v1"] = REPOSITORY_STRUCTURE_SCHEMA
    repository_id: str = Field(min_length=1, max_length=160)
    worktree_id: str = Field(min_length=1, max_length=160)
    head_commit: str
    working_tree_state_sha256: Sha256
    builder_version: str = Field(min_length=1, max_length=160)
    status: StructureDeltaStatus
    base_view_sha256: Sha256 | None = None
    new_view_sha256: Sha256
    full_rescan: bool
    truncated: bool
    candidate_path_count: int = Field(ge=0)
    changed_paths: tuple[str, ...] = ()
    parsed_file_count: int = Field(ge=0)
    reused_file_count: int = Field(ge=0)
    upsert_files: tuple[RepositoryStructureFile, ...] = ()
    retirements: tuple[RepositoryStructureRetirement, ...] = ()
    retired_file_keys: tuple[str, ...] = ()
    delta_sha256: Sha256
    built_at_ms: int = Field(ge=0)
    build_ms: int = Field(ge=0)

    _validate_head = field_validator("head_commit")(_git_object_id)

    @field_validator("repository_id", "worktree_id", "builder_version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="structure delta field")

    @field_validator("changed_paths")
    @classmethod
    def validate_changed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_repo_path(item) for item in value)
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("changed paths must be sorted and unique")
        return normalized

    @field_validator("upsert_files")
    @classmethod
    def validate_upserts(
        cls, value: tuple[RepositoryStructureFile, ...]
    ) -> tuple[RepositoryStructureFile, ...]:
        paths = tuple(item.path for item in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("structure upserts must be sorted and unique by path")
        return value

    @field_validator("retirements")
    @classmethod
    def validate_retirements(
        cls, value: tuple[RepositoryStructureRetirement, ...]
    ) -> tuple[RepositoryStructureRetirement, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("structure retirements must be sorted and unique")
        return value

    _validate_retired_files = field_validator("retired_file_keys")(_sorted_unique_strings)

    def computed_delta_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("delta_sha256", None)
        payload.pop("built_at_ms", None)
        payload.pop("build_ms", None)
        return canonical_sha256(payload)

    @model_validator(mode="after")
    def validate_delta(self) -> "RepositoryStructureDelta":
        if self.delta_sha256 != self.computed_delta_sha256():
            raise ValueError("repository structure delta hash mismatch")
        if self.status == "FAILED_OPEN":
            if self.upsert_files or self.retirements or self.retired_file_keys:
                raise ValueError("failed-open structure delta may not publish partial mutations")
            if self.base_view_sha256 != self.new_view_sha256:
                raise ValueError("failed-open structure delta must preserve the prior coherent view")
        return self

    @classmethod
    def build(
        cls,
        *,
        repository_id: str,
        worktree_id: str,
        head_commit: str,
        working_tree_state_sha256: str,
        builder_version: str,
        status: StructureDeltaStatus,
        base_view_sha256: str | None,
        new_view_sha256: str,
        full_rescan: bool,
        truncated: bool,
        candidate_path_count: int,
        changed_paths: tuple[str, ...],
        parsed_file_count: int,
        reused_file_count: int,
        upsert_files: tuple[RepositoryStructureFile, ...],
        retirements: tuple[RepositoryStructureRetirement, ...],
        retired_file_keys: tuple[str, ...],
        built_at_ms: int,
        build_ms: int,
    ) -> "RepositoryStructureDelta":
        payload = {
            "schema": REPOSITORY_STRUCTURE_SCHEMA,
            "repository_id": repository_id,
            "worktree_id": worktree_id,
            "head_commit": head_commit,
            "working_tree_state_sha256": working_tree_state_sha256,
            "builder_version": builder_version,
            "status": status,
            "base_view_sha256": base_view_sha256,
            "new_view_sha256": new_view_sha256,
            "full_rescan": full_rescan,
            "truncated": truncated,
            "candidate_path_count": candidate_path_count,
            "changed_paths": changed_paths,
            "parsed_file_count": parsed_file_count,
            "reused_file_count": reused_file_count,
            "upsert_files": upsert_files,
            "retirements": retirements,
            "retired_file_keys": retired_file_keys,
            "delta_sha256": "0" * 64,
            "built_at_ms": built_at_ms,
            "build_ms": build_ms,
        }
        candidate = cls.model_construct(**payload)
        payload["delta_sha256"] = candidate.computed_delta_sha256()
        return cls(**payload)


__all__ = [
    "REPOSITORY_STRUCTURE_SCHEMA",
    "RepositoryImportDeclaration",
    "RepositorySourceSpan",
    "RepositoryStructureDelta",
    "RepositoryStructureFile",
    "RepositoryStructureNode",
    "RepositoryStructureRetirement",
    "RepositoryStructureSnapshot",
    "StructureDeltaStatus",
    "StructureLanguage",
    "StructureNodeKind",
    "StructureParseStatus",
]
