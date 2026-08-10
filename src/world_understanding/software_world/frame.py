"""L0 Software WorldFrame: stable branch/worktree identity plus commit/cut revision."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.common.scope import require_exact_scope

@dataclass(frozen=True, slots=True)
class SoftwareWorldFrame:
    scope: WorldScope
    workspace: str
    repository: str
    worktree: str
    branch: str
    commit: str
    environment: str
    time: WorldTime
    world_cut: WorldCut | None
    frame_id: str
    frame_revision_hash: str

    @classmethod
    def build(cls, *, scope: WorldScope, workspace: str, repository: str, worktree: str,
              branch: str, commit: str, environment: str, time: WorldTime,
              world_cut: WorldCut | None = None) -> "SoftwareWorldFrame":
        values = (workspace, repository, worktree, branch, commit, environment)
        if any(not str(value).strip() for value in values):
            raise ValueError("software world frame fields must be explicit")
        if world_cut is not None:
            require_exact_scope(scope, world_cut.scope)
        frame_id = "swf_" + canonical_sha256({
            "domain": "tiangong.world.software-frame-id.v1",
            "life_id": scope.life_id,
            "world_scope_hash": scope.world_scope_hash,
            "principal_scope_hash": scope.principal_scope_hash,
            "workspace": workspace,
            "repository": repository,
            "worktree": worktree,
            "branch": branch,
        })
        revision = canonical_sha256({
            "domain": "tiangong.world.software-frame-revision.v1",
            "frame_id": frame_id,
            "commit": commit,
            "environment": environment,
            "time": time.model_dump(mode="json"),
            "world_cut_sha256": None if world_cut is None else world_cut.cut_sha256,
        })
        return cls(scope, workspace, repository, worktree, branch, commit, environment, time, world_cut, frame_id, revision)

__all__ = ["SoftwareWorldFrame"]
