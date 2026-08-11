"""Compact, complete repository total-part tree contracts.

The wire manifest carries every discovered source path but not a second
repository database.  Directory nodes, coverage and subtree hashes are
deterministically rebuilt from the manifest by the existing GIT_CODE chain.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..canonical import canonical_sha256
from ..models import Sha256
from ._base import WorldContractModel

REPOSITORY_TREE_SCHEMA = "tiangong.repository-tree.v1"

RepositoryTreeCoverage = Literal["UNEXPANDED", "PARTIAL", "COMPLETE"]
RepositoryTreeEntityType = Literal["Repository", "RepositoryBranch", "File"]

_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _nfc(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError(f"{label} must be NFC and contain no NUL")
    return value


def _repo_path(value: str) -> str:
    value = _nfc(value, label="repository tree path").replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise ValueError("repository tree path must be repository-relative")
    parts = tuple(part for part in value.split("/") if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError("repository tree path may not escape the repository")
    if "/".join(parts) != value:
        raise ValueError("repository tree path must be canonically normalized")
    return value


def repository_tree_file_anchor(repository_id: str, path: str) -> str:
    return "gitfile." + canonical_sha256(
        {"repository_id": repository_id, "path": _repo_path(path)}
    )[:48]


def repository_tree_branch_anchor(
    repository_id: str, worktree_id: str, path: str
) -> str:
    return "repobranch." + canonical_sha256({
        "repository_id": repository_id,
        "worktree_id": worktree_id,
        "path": _repo_path(path),
    })[:48]


def repository_tree_branch_name(repository_id: str, path: str) -> str:
    return f"repository-branch:{repository_id}:{_repo_path(path)}"


class RepositoryTreeFile(WorldContractModel):
    path: str
    coverage_state: RepositoryTreeCoverage
    source_fingerprint: Sha256 | None = None

    _validate_path = field_validator("path")(_repo_path)

    def sort_key(self) -> str:
        return self.path


class RepositoryTreeRetirement(WorldContractModel):
    entity_type: RepositoryTreeEntityType
    stable_anchor: str = Field(min_length=1, max_length=160)
    canonical_name: str = Field(min_length=1, max_length=4096)

    @field_validator("stable_anchor", "canonical_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="repository tree retirement")

    def sort_key(self) -> tuple[str, str, str]:
        return (self.entity_type, self.stable_anchor, self.canonical_name)


class RepositoryTreeNode(WorldContractModel):
    entity_type: RepositoryTreeEntityType
    stable_anchor: str = Field(min_length=1, max_length=160)
    parent_anchor: str | None = Field(default=None, max_length=160)
    canonical_name: str = Field(min_length=1, max_length=4096)
    path: str | None = None
    coverage_state: RepositoryTreeCoverage
    child_count: int = Field(ge=0)
    descendant_file_count: int = Field(ge=0)
    subtree_sha256: Sha256

    @field_validator("stable_anchor", "canonical_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="repository tree node")

    @field_validator("parent_anchor")
    @classmethod
    def validate_parent(cls, value: str | None) -> str | None:
        return None if value is None else _nfc(value, label="repository tree parent")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return None if value is None else _repo_path(value)


class RepositoryTreeManifest(WorldContractModel):
    schema: Literal["tiangong.repository-tree.v1"] = REPOSITORY_TREE_SCHEMA
    repository_id: str = Field(min_length=1, max_length=160)
    worktree_id: str = Field(min_length=1, max_length=160)
    head_commit: str
    working_tree_state_sha256: Sha256
    builder_version: str = Field(min_length=1, max_length=160)
    inventory_complete: bool
    candidate_path_count: int = Field(ge=0)
    expanded_file_count: int = Field(ge=0)
    files: tuple[RepositoryTreeFile, ...] = ()
    retirements: tuple[RepositoryTreeRetirement, ...] = ()
    tree_sha256: Sha256

    @field_validator("repository_id", "worktree_id", "builder_version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nfc(value, label="repository tree manifest")

    @field_validator("head_commit")
    @classmethod
    def validate_head(cls, value: str) -> str:
        if not _GIT_OBJECT_ID.fullmatch(value):
            raise ValueError("Git object id must be a lower-case SHA-1 or SHA-256 object id")
        return value

    @field_validator("files")
    @classmethod
    def validate_files(
        cls, value: tuple[RepositoryTreeFile, ...]
    ) -> tuple[RepositoryTreeFile, ...]:
        paths = tuple(item.path for item in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("repository tree files must be sorted and unique")
        return value

    @field_validator("retirements")
    @classmethod
    def validate_retirements(
        cls, value: tuple[RepositoryTreeRetirement, ...]
    ) -> tuple[RepositoryTreeRetirement, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("repository tree retirements must be sorted and unique")
        return value

    def computed_tree_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("tree_sha256", None)
        return canonical_sha256(payload)

    @model_validator(mode="after")
    def validate_manifest(self) -> "RepositoryTreeManifest":
        if self.candidate_path_count != len(self.files):
            raise ValueError("repository tree candidate count must equal its path inventory")
        expanded = sum(item.coverage_state != "UNEXPANDED" for item in self.files)
        if self.expanded_file_count != expanded:
            raise ValueError("repository tree expanded count mismatch")
        if self.tree_sha256 != self.computed_tree_sha256():
            raise ValueError("repository tree manifest hash mismatch")
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
        inventory_complete: bool,
        files: tuple[RepositoryTreeFile, ...],
        retirements: tuple[RepositoryTreeRetirement, ...] = (),
    ) -> "RepositoryTreeManifest":
        payload = {
            "schema": REPOSITORY_TREE_SCHEMA,
            "repository_id": repository_id,
            "worktree_id": worktree_id,
            "head_commit": head_commit,
            "working_tree_state_sha256": working_tree_state_sha256,
            "builder_version": builder_version,
            "inventory_complete": inventory_complete,
            "candidate_path_count": len(files),
            "expanded_file_count": sum(
                item.coverage_state != "UNEXPANDED" for item in files
            ),
            "files": files,
            "retirements": retirements,
            "tree_sha256": "0" * 64,
        }
        candidate = cls.model_construct(**payload)
        payload["tree_sha256"] = candidate.computed_tree_sha256()
        return cls(**payload)


def materialize_repository_tree_nodes(
    manifest: RepositoryTreeManifest,
) -> tuple[RepositoryTreeNode, ...]:
    """Derive the complete root/branch/file tree and propagated hashes."""
    file_by_path = {item.path: item for item in manifest.files}
    child_paths: dict[str, set[str]] = {"": set()}
    directory_paths: set[str] = set()
    for file in manifest.files:
        parts = PurePosixPath(file.path).parts
        parent = ""
        for part in parts[:-1]:
            current = "/".join((*PurePosixPath(parent).parts, part)) if parent else part
            directory_paths.add(current)
            child_paths.setdefault(parent, set()).add(current)
            child_paths.setdefault(current, set())
            parent = current
        child_paths.setdefault(parent, set()).add(file.path)

    built: dict[str, RepositoryTreeNode] = {}
    for path in sorted(file_by_path):
        file = file_by_path[path]
        parent_path = PurePosixPath(path).parent.as_posix()
        if parent_path == ".":
            parent_path = ""
        parent_anchor = (
            manifest.repository_id
            if not parent_path
            else repository_tree_branch_anchor(
                manifest.repository_id, manifest.worktree_id, parent_path
            )
        )
        stable_anchor = repository_tree_file_anchor(manifest.repository_id, path)
        built[path] = RepositoryTreeNode(
            entity_type="File",
            stable_anchor=stable_anchor,
            parent_anchor=parent_anchor,
            canonical_name=path,
            path=path,
            coverage_state=file.coverage_state,
            child_count=0,
            descendant_file_count=1,
            subtree_sha256=canonical_sha256({
                "entity_type": "File",
                "stable_anchor": stable_anchor,
                "path": path,
                "coverage_state": file.coverage_state,
                "source_fingerprint": file.source_fingerprint,
            }),
        )

    for path in sorted(directory_paths, key=lambda item: (-item.count("/"), item)):
        children = tuple(sorted(child_paths.get(path, ())))
        child_nodes = tuple(built[item] for item in children)
        states = {item.coverage_state for item in child_nodes}
        coverage: RepositoryTreeCoverage
        if states == {"COMPLETE"}:
            coverage = "COMPLETE"
        elif states == {"UNEXPANDED"}:
            coverage = "UNEXPANDED"
        else:
            coverage = "PARTIAL"
        parent_path = PurePosixPath(path).parent.as_posix()
        if parent_path == ".":
            parent_path = ""
        stable_anchor = repository_tree_branch_anchor(
            manifest.repository_id, manifest.worktree_id, path
        )
        built[path] = RepositoryTreeNode(
            entity_type="RepositoryBranch",
            stable_anchor=stable_anchor,
            parent_anchor=(
                manifest.repository_id
                if not parent_path
                else repository_tree_branch_anchor(
                    manifest.repository_id, manifest.worktree_id, parent_path
                )
            ),
            canonical_name=repository_tree_branch_name(manifest.repository_id, path),
            path=path,
            coverage_state=coverage,
            child_count=len(child_nodes),
            descendant_file_count=sum(
                item.descendant_file_count for item in child_nodes
            ),
            subtree_sha256=canonical_sha256({
                "entity_type": "RepositoryBranch",
                "stable_anchor": stable_anchor,
                "path": path,
                "coverage_state": coverage,
                "children": tuple(
                    (item.stable_anchor, item.subtree_sha256) for item in child_nodes
                ),
            }),
        )

    root_children = tuple(sorted(child_paths.get("", ())))
    root_child_nodes = tuple(built[item] for item in root_children)
    root_states = {item.coverage_state for item in root_child_nodes}
    if not root_child_nodes:
        root_coverage: RepositoryTreeCoverage = "COMPLETE"
    elif root_states == {"COMPLETE"} and manifest.inventory_complete:
        root_coverage = "COMPLETE"
    elif root_states == {"UNEXPANDED"}:
        root_coverage = "UNEXPANDED"
    else:
        root_coverage = "PARTIAL"
    root = RepositoryTreeNode(
        entity_type="Repository",
        stable_anchor=manifest.repository_id,
        parent_anchor=None,
        canonical_name="local-repository:" + manifest.repository_id,
        path=None,
        coverage_state=root_coverage,
        child_count=len(root_child_nodes),
        descendant_file_count=len(manifest.files),
        subtree_sha256=canonical_sha256({
            "entity_type": "Repository",
            "stable_anchor": manifest.repository_id,
            "coverage_state": root_coverage,
            "inventory_complete": manifest.inventory_complete,
            "children": tuple(
                (item.stable_anchor, item.subtree_sha256) for item in root_child_nodes
            ),
        }),
    )
    return (root, *(built[path] for path in sorted(built)))


__all__ = [
    "REPOSITORY_TREE_SCHEMA",
    "RepositoryTreeCoverage",
    "RepositoryTreeEntityType",
    "RepositoryTreeFile",
    "RepositoryTreeManifest",
    "RepositoryTreeNode",
    "RepositoryTreeRetirement",
    "materialize_repository_tree_nodes",
    "repository_tree_branch_anchor",
    "repository_tree_branch_name",
    "repository_tree_file_anchor",
]
