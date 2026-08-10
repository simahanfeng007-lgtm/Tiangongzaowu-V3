"""Deterministic GIT_CODE compiler for normalized repository observations.

Only empirical repository facts become strong Known records here. No parser or
LLM inference is promoted to Git authority, and this compiler cannot authorize
or execute anything.
"""
from __future__ import annotations

import json

from contracts.canonical import canonical_json_bytes, canonical_sha256
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.repository import RepositoryObservation, RepositoryPathChange

from .base import DeterministicSourceCompiler, make_direct_known


def decode_repository_observation(envelope: WorldIngressEnvelope) -> RepositoryObservation:
    if envelope.source_kind != "GIT_CODE":
        raise ValueError("repository observation requires GIT_CODE ingress")
    payload = envelope.payload_inline
    if not isinstance(payload, dict):
        raise ValueError("GIT_CODE requires an inline repository observation")
    return RepositoryObservation.model_validate_json(canonical_json_bytes(payload))


def _file_subject(repository_id: str, path: str) -> str:
    return "gitfile." + canonical_sha256({"repository_id": repository_id, "path": path})[:48]


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _path_change_text(change: RepositoryPathChange) -> str:
    return _compact_json(change.model_dump(mode="json"))


class GitCodeCompiler(DeterministicSourceCompiler):
    """Compile one immutable RepositoryObservation into deterministic Direct Known."""

    def __call__(self, envelope: WorldIngressEnvelope):
        observation = decode_repository_observation(envelope)
        repo = observation.identity
        revision = observation.revision
        state = observation.working_tree_state
        rows = [
            make_direct_known(envelope,self.spec,proposition_type="REPOSITORY_IDENTITY",predicate="git.repository_identity",subject_ref=repo.repository_id,object_text=repo.repository_root_ref),
            make_direct_known(envelope,self.spec,proposition_type="WORKTREE_IDENTITY",predicate="git.worktree_identity",subject_ref=repo.worktree_id,object_text=repo.worktree_root_ref),
            make_direct_known(envelope,self.spec,proposition_type="REPOSITORY_HEAD_AT",predicate="git.head_at",subject_ref=repo.repository_id,object_text=revision.head_commit),
            make_direct_known(envelope,self.spec,proposition_type="REPOSITORY_BRANCH_AT",predicate="git.branch_at",subject_ref=repo.worktree_id,object_text=revision.branch),
            make_direct_known(
                envelope,self.spec,proposition_type="REPOSITORY_DIRTY_STATE",predicate="git.working_tree_state",subject_ref=repo.worktree_id,
                object_text=_compact_json({
                    "clean": state.clean,
                    "state_sha256": state.state_sha256,
                    "staged_paths": state.staged_paths,
                    "modified_paths": state.modified_paths,
                    "deleted_paths": state.deleted_paths,
                    "renamed_paths": [item.model_dump(mode="json") for item in state.renamed_paths],
                    "untracked_paths": state.untracked_paths,
                    "conflicted_paths": state.conflicted_paths,
                }),
            ),
        ]
        identity_paths = {
            change.new_path
            for change in observation.changes
            if change.change_kind in {"ADD", "MODIFY"} and change.new_path is not None
        }
        for change in observation.changes:
            path = change.new_path or change.old_path or "unknown"
            rows.append(make_direct_known(
                envelope,self.spec,proposition_type="GIT_PATH_CHANGE_OBSERVED",predicate="git.path_change",
                subject_ref=_file_subject(repo.repository_id,path),object_text=_path_change_text(change),
            ))
        for file_observation in observation.files:
            subject = _file_subject(repo.repository_id,file_observation.path)
            # Rename/delete continuity is owned by GitCommitDelta. Seeding a new
            # FILE_IDENTITY at the destination before the delta runs would create
            # a second path-derived File entity and destroy rename continuity.
            if file_observation.path in identity_paths:
                rows.append(make_direct_known(
                    envelope,self.spec,proposition_type="FILE_IDENTITY",predicate="git.file_identity",
                    subject_ref=subject,object_text=file_observation.path,
                ))
            rows.append(make_direct_known(
                envelope,self.spec,proposition_type="FILE_EXISTS",predicate="git.file_exists",
                subject_ref=subject,object_text="true" if file_observation.exists else "false",
            ))
            if file_observation.blob_sha is not None:
                rows.append(make_direct_known(
                    envelope,self.spec,proposition_type="FILE_HASH_AT",predicate="git.blob_sha",
                    subject_ref=subject,object_text=file_observation.blob_sha,
                ))
            if file_observation.content_sha256 is not None:
                rows.append(make_direct_known(
                    envelope,self.spec,proposition_type="FILE_HASH_AT",predicate="filesystem.sha256",
                    subject_ref=subject,object_text=file_observation.content_sha256,
                    authority_domain="FILESYSTEM_ARTIFACT",authority_ceiling_milli=1000,empirical_evidence_weight_milli=1000,
                ))
        return tuple(rows)


__all__ = ["GitCodeCompiler", "decode_repository_observation"]
