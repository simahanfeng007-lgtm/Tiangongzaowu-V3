"""Pure RepositoryObservation -> existing GitCommitDelta decoder.

This layer performs no Git/filesystem/network IO. It only validates already-
observed evidence against the current SoftwareWorldFrame.
"""
from __future__ import annotations

from contracts.canonical import canonical_json_bytes, canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord
from contracts.world_understanding.repository import RepositoryObservation

from .frame import SoftwareWorldFrame
from .git_delta import GitCommitDelta, GitPathChange


def _repository_payload(envelope: WorldIngressEnvelope) -> dict:
    if envelope.source_kind != "GIT_CODE" or not isinstance(
        envelope.payload_inline, dict
    ):
        raise ValueError("GIT_CODE repository observation payload required")
    nested = envelope.payload_inline.get("repository_observation")
    if nested is not None:
        if not isinstance(nested, dict):
            raise ValueError("repository_observation wrapper must be an object")
        return nested
    return envelope.payload_inline


def _decode(envelope: WorldIngressEnvelope) -> RepositoryObservation:
    return RepositoryObservation.model_validate_json(
        canonical_json_bytes(_repository_payload(envelope))
    )


def repository_frame_identity(
    envelope: WorldIngressEnvelope,
) -> tuple[str, str, str, str] | None:
    """Return real SoftwareWorldFrame identity only for normalized GIT_CODE reality."""
    if envelope.source_kind != "GIT_CODE":
        return None
    observation = _decode(envelope)
    return (
        observation.identity.repository_id,
        observation.identity.worktree_id,
        observation.revision.branch,
        observation.revision.head_commit,
    )


def _basis_ref(
    rows: tuple[DirectKnownRecord, ...], envelope: WorldIngressEnvelope
) -> WorldRecordRef:
    row = next(
        (item for item in rows if item.proposition_type == "REPOSITORY_IDENTITY"),
        rows[0],
    )
    return WorldRecordRef(
        record_type="known",
        record_id=row.known_id,
        revision=None,
        sha256=row.record_hash,
    )


def _file_anchor(repository_id: str, path: str) -> str:
    return "gitfile." + canonical_sha256(
        {"repository_id": repository_id, "path": path}
    )[:48]


def repository_observation_to_git_delta(
    *,
    envelope: WorldIngressEnvelope,
    frame: SoftwareWorldFrame,
    rows: tuple[DirectKnownRecord, ...],
) -> GitCommitDelta | None:
    """Build the already-existing delta contract from normalized ingress evidence."""
    if envelope.source_kind != "GIT_CODE":
        return None
    observation = _decode(envelope)
    expected = (
        observation.identity.repository_id,
        observation.identity.worktree_id,
        observation.revision.branch,
        observation.revision.head_commit,
    )
    actual = (frame.repository, frame.worktree, frame.branch, frame.commit)
    if actual != expected:
        raise ValueError("GIT_OBSERVATION_FRAME_MISMATCH")
    if not observation.changes:
        return None
    source_ref = _basis_ref(rows, envelope)
    changes = []
    for change in observation.changes:
        path = change.new_path or change.old_path or ""
        explicit_anchor = change.explicit_identity_anchor
        if change.change_kind in {"ADD", "MODIFY"}:
            explicit_anchor = _file_anchor(
                observation.identity.repository_id, path
            )
        changes.append(
            GitPathChange(
                change_kind=change.change_kind,
                old_path=change.old_path,
                new_path=change.new_path,
                old_blob_sha=change.old_blob_sha,
                new_blob_sha=change.new_blob_sha,
                source_ref=source_ref,
                explicit_identity_anchor=explicit_anchor,
            )
        )
    return GitCommitDelta.build(
        frame=frame,
        parent_commit=observation.revision.parent_commit,
        changes=tuple(changes),
    )


__all__ = ["repository_frame_identity", "repository_observation_to_git_delta"]
