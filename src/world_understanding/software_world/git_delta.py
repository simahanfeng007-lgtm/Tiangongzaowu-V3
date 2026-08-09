"""Explicit Git commit/diff delta contracts for incremental software-world updates.

No repository access occurs here. The caller must provide already-observed diff facts.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from .frame import SoftwareWorldFrame

GitChangeKind = Literal["ADD", "MODIFY", "DELETE", "RENAME", "MOVE"]

@dataclass(frozen=True, slots=True)
class GitPathChange:
    change_kind: GitChangeKind
    old_path: str | None = None
    new_path: str | None = None
    old_blob_sha: str | None = None
    new_blob_sha: str | None = None
    source_ref: WorldRecordRef | None = None
    explicit_identity_anchor: str | None = None

    def __post_init__(self) -> None:
        old = None if self.old_path is None else self.old_path.strip()
        new = None if self.new_path is None else self.new_path.strip()
        if self.change_kind == "ADD" and (old is not None or not new):
            raise ValueError("ADD requires only new_path")
        if self.change_kind == "DELETE" and (not old or new is not None):
            raise ValueError("DELETE requires only old_path")
        if self.change_kind == "MODIFY" and (not old or not new or old != new):
            raise ValueError("MODIFY requires identical old/new path")
        if self.change_kind in {"RENAME", "MOVE"} and (not old or not new or old == new):
            raise ValueError("RENAME/MOVE requires distinct old/new paths")

@dataclass(frozen=True, slots=True)
class GitCommitDelta:
    repository: str
    worktree: str
    branch: str
    parent_commit: str | None
    commit: str
    changes: tuple[GitPathChange, ...]
    delta_id: str

    @classmethod
    def build(cls, *, frame: SoftwareWorldFrame, parent_commit: str | None,
              changes: tuple[GitPathChange, ...]) -> "GitCommitDelta":
        payload = {
            "domain": "tiangong.world.git-commit-delta.v1",
            "frame_id": frame.frame_id,
            "repository": frame.repository,
            "worktree": frame.worktree,
            "branch": frame.branch,
            "parent_commit": parent_commit,
            "commit": frame.commit,
            "changes": [change.__dict__ if hasattr(change, "__dict__") else {
                "change_kind": change.change_kind,
                "old_path": change.old_path,
                "new_path": change.new_path,
                "old_blob_sha": change.old_blob_sha,
                "new_blob_sha": change.new_blob_sha,
                "source_ref": None if change.source_ref is None else change.source_ref.model_dump(mode="json"),
                "explicit_identity_anchor": change.explicit_identity_anchor,
            } for change in changes],
        }
        return cls(frame.repository, frame.worktree, frame.branch, parent_commit, frame.commit, changes, "gdelta_" + canonical_sha256(payload))

    def validate_frame(self, frame: SoftwareWorldFrame) -> None:
        if (self.repository, self.worktree, self.branch, self.commit) != (frame.repository, frame.worktree, frame.branch, frame.commit):
            raise ValueError("GIT_DELTA_FRAME_MISMATCH")

__all__ = ["GitChangeKind", "GitPathChange", "GitCommitDelta"]
