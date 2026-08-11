"""Deterministic GIT_CODE compiler for repository and structure observations.

Only empirical Git/filesystem/parser facts become strong Known records here.
No LLM inference is promoted to repository authority, and this compiler cannot
authorize or execute anything.
"""
from __future__ import annotations

import base64
import json

from contracts.canonical import canonical_json_bytes, canonical_sha256
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.repository import RepositoryObservation
from contracts.world_understanding.repository_structure import RepositoryStructureDelta
from contracts.world_understanding.repository_tree import (
    RepositoryTreeManifest,
)

from .base import DeterministicSourceCompiler, make_direct_known


def _repository_payload(envelope: WorldIngressEnvelope) -> dict:
    if envelope.source_kind != "GIT_CODE":
        raise ValueError("repository observation requires GIT_CODE ingress")
    payload = envelope.payload_inline
    if not isinstance(payload, dict):
        raise ValueError("GIT_CODE requires an inline repository observation")
    nested = payload.get("repository_observation")
    if nested is not None:
        if not isinstance(nested, dict):
            raise ValueError("repository_observation wrapper must be an object")
        return nested
    return payload


def decode_repository_observation(envelope: WorldIngressEnvelope) -> RepositoryObservation:
    return RepositoryObservation.model_validate_json(
        canonical_json_bytes(_repository_payload(envelope))
    )


def decode_repository_structure_delta(
    envelope: WorldIngressEnvelope,
) -> RepositoryStructureDelta | None:
    payload = envelope.payload_inline
    if not isinstance(payload, dict):
        return None
    raw = payload.get("structure_delta")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("structure_delta wrapper must be an object")
    return RepositoryStructureDelta.model_validate_json(canonical_json_bytes(raw))


def decode_repository_tree_manifest(
    envelope: WorldIngressEnvelope,
) -> RepositoryTreeManifest | None:
    payload = envelope.payload_inline
    if not isinstance(payload, dict):
        return None
    raw = payload.get("repository_tree")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("repository_tree wrapper must be an object")
    return RepositoryTreeManifest.model_validate_json(canonical_json_bytes(raw))


def _file_subject(repository_id: str, path: str) -> str:
    return "gitfile." + canonical_sha256(
        {"repository_id": repository_id, "path": path}
    )[:48]


def _compact_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _path_change_text(change) -> str:
    return _compact_json(change.model_dump(mode="json"))


def _structure_rows(
    compiler: "GitCodeCompiler",
    envelope: WorldIngressEnvelope,
    observation: RepositoryObservation,
) -> list:
    delta = decode_repository_structure_delta(envelope)
    if delta is None:
        return []
    if (
        delta.repository_id != observation.identity.repository_id
        or delta.worktree_id != observation.identity.worktree_id
        or delta.head_commit != observation.revision.head_commit
        or delta.working_tree_state_sha256
        != observation.working_tree_state.state_sha256
    ):
        raise ValueError("repository structure delta does not match Git observation")
    if delta.status == "FAILED_OPEN":
        return []

    add_paths = {
        change.new_path
        for change in observation.changes
        if change.change_kind == "ADD" and change.new_path is not None
    }
    rows = []
    for file in delta.upsert_files:
        if delta.full_rescan and file.path not in add_paths:
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type="FILE_IDENTITY",
                    predicate="filesystem.source_file_identity",
                    subject_ref=_file_subject(
                        observation.identity.repository_id, file.path
                    ),
                    object_text=file.path,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=1000,
                    empirical_evidence_weight_milli=1000,
                )
            )

        if file.parse_status in {
            "SKIPPED_SECRET",
            "SKIPPED_BINARY",
            "SKIPPED_LARGE",
            "PARSER_UNAVAILABLE",
        }:
            continue

        rows.append(
            make_direct_known(
                envelope,
                compiler.spec,
                proposition_type="MODULE_IDENTITY",
                predicate="parser.module_identity",
                subject_ref=file.module_anchor,
                object_text=file.module_name,
                authority_domain="FILESYSTEM_ARTIFACT",
                authority_ceiling_milli=700,
                empirical_evidence_weight_milli=650,
            )
        )
        rows.append(
            make_direct_known(
                envelope,
                compiler.spec,
                proposition_type="DEFINES",
                predicate="parser.defines",
                subject_ref=_file_subject(
                    observation.identity.repository_id, file.path
                ),
                object_text=file.module_name,
                authority_domain="FILESYSTEM_ARTIFACT",
                authority_ceiling_milli=700,
                empirical_evidence_weight_milli=650,
            )
        )

        if file.parse_status != "PARSED":
            continue

        anchor_to_name = {file.module_anchor: file.module_name}
        parser_weight = 900 if file.parser_kind in {"python-ast", "tree-sitter"} else 600
        for node in file.nodes:
            anchor_to_name[node.stable_anchor] = node.qualified_name
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type=node.kind.upper() + "_IDENTITY",
                    predicate="parser." + node.kind.lower() + "_identity",
                    subject_ref=node.stable_anchor,
                    object_text=node.qualified_name,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=900,
                    empirical_evidence_weight_milli=parser_weight,
                )
            )
            parent_name = (
                file.module_name
                if node.parent_anchor is None
                else anchor_to_name.get(node.parent_anchor)
            )
            if parent_name is not None:
                rows.append(
                    make_direct_known(
                        envelope,
                        compiler.spec,
                        proposition_type="DEFINES",
                        predicate="parser.defines",
                        subject_ref=parent_name,
                        object_text=node.qualified_name,
                        authority_domain="FILESYSTEM_ARTIFACT",
                        authority_ceiling_milli=800,
                        empirical_evidence_weight_milli=750,
                    )
                )
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type="SOURCE_SPAN_OBSERVED",
                    predicate="parser.source_span",
                    subject_ref=node.stable_anchor,
                    object_text=_compact_json(node.span.model_dump(mode="json")),
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=900,
                    empirical_evidence_weight_milli=900,
                )
            )
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type="SYNTAX_NODE_HASH_AT",
                    predicate="parser.syntax_sha256",
                    subject_ref=node.stable_anchor,
                    object_text=node.syntax_sha256,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=1000,
                    empirical_evidence_weight_milli=1000,
                )
            )

        for item in file.imports:
            if item.resolved_module_name is None:
                continue
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type="IMPORTS",
                    predicate="parser.imports",
                    subject_ref=file.module_name,
                    object_text=item.resolved_module_name,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=500,
                    empirical_evidence_weight_milli=250,
                )
            )

        for item in file.semantic_relations:
            # Ambiguous/unresolved parser observations remain rebuildable cache
            # evidence and are never promoted into the Software World Graph.
            if item.resolved_target_name is None:
                continue
            rows.append(
                make_direct_known(
                    envelope,
                    compiler.spec,
                    proposition_type=item.predicate,
                    predicate="parser." + item.predicate.lower(),
                    subject_ref=anchor_to_name.get(
                        item.source_anchor, file.module_name
                    ),
                    object_text=item.resolved_target_name,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=min(900, item.confidence_milli),
                    empirical_evidence_weight_milli=item.confidence_milli,
                )
            )

    for retired in delta.retirements:
        rows.append(
            make_direct_known(
                envelope,
                compiler.spec,
                proposition_type="STRUCTURE_ENTITY_RETIRED",
                predicate="parser.structure_entity_retired",
                subject_ref=retired.stable_anchor,
                object_text=_compact_json(
                    {
                        "entity_type": retired.entity_type,
                        "canonical_name": retired.canonical_name,
                    }
                ),
                authority_domain="FILESYSTEM_ARTIFACT",
                authority_ceiling_milli=800,
                empirical_evidence_weight_milli=750,
            )
        )
    return rows


def _tree_rows(
    compiler: "GitCodeCompiler",
    envelope: WorldIngressEnvelope,
    observation: RepositoryObservation,
) -> list:
    manifest = decode_repository_tree_manifest(envelope)
    if manifest is None:
        return []
    if (
        manifest.repository_id != observation.identity.repository_id
        or manifest.worktree_id != observation.identity.worktree_id
        or manifest.head_commit != observation.revision.head_commit
        or manifest.working_tree_state_sha256
        != observation.working_tree_state.state_sha256
    ):
        raise ValueError("repository tree manifest does not match Git observation")

    # Known has a deliberately finite active cut. Publishing one Known per
    # node would overflow it on ordinary repositories. Carry the already-
    # validated complete manifest as bounded integrity chunks, then let the
    # existing SoftwareWorldUpdater deterministically expand it in the same
    # transaction. The header's provenance hashes the entire ingress payload.
    raw = canonical_json_bytes(manifest.model_dump(mode="json"))
    chunk_bytes = 12_000
    chunks = tuple(
        raw[offset: offset + chunk_bytes]
        for offset in range(0, len(raw), chunk_bytes)
    ) or (b"",)
    subject = "repotree." + manifest.tree_sha256
    rows = [make_direct_known(
        envelope,
        compiler.spec,
        proposition_type="REPOSITORY_TREE_MANIFEST_IDENTITY",
        predicate="filesystem.repository_tree_manifest_identity",
        subject_ref=subject,
        object_text=_compact_json({
            "tree_sha256": manifest.tree_sha256,
            "chunk_count": len(chunks),
            "candidate_path_count": manifest.candidate_path_count,
            "expanded_file_count": manifest.expanded_file_count,
            "inventory_complete": manifest.inventory_complete,
        }),
        authority_domain="FILESYSTEM_ARTIFACT",
        authority_ceiling_milli=1000,
        empirical_evidence_weight_milli=1000,
    )]
    for index, chunk in enumerate(chunks):
        rows.append(make_direct_known(
            envelope,
            compiler.spec,
            proposition_type="REPOSITORY_TREE_MANIFEST_CHUNK",
            predicate="filesystem.repository_tree_manifest_chunk",
            subject_ref=f"{subject}.{index:05d}",
            object_text=_compact_json({
                "tree_sha256": manifest.tree_sha256,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "data_base64": base64.b64encode(chunk).decode("ascii"),
            }),
            authority_domain="FILESYSTEM_ARTIFACT",
            authority_ceiling_milli=1000,
            empirical_evidence_weight_milli=1000,
        ))
    return rows


class GitCodeCompiler(DeterministicSourceCompiler):
    """Compile one immutable repository observation into deterministic Known."""

    def __call__(self, envelope: WorldIngressEnvelope):
        observation = decode_repository_observation(envelope)
        repo = observation.identity
        revision = observation.revision
        state = observation.working_tree_state
        rows = [
            make_direct_known(
                envelope, self.spec,
                proposition_type="REPOSITORY_IDENTITY",
                predicate="git.repository_identity",
                subject_ref=repo.repository_id,
                object_text="local-repository:" + repo.repository_id,
            ),
            make_direct_known(
                envelope, self.spec,
                proposition_type="WORKTREE_IDENTITY",
                predicate="git.worktree_identity",
                subject_ref=repo.worktree_id,
                object_text="local-worktree:" + repo.worktree_id,
            ),
            make_direct_known(
                envelope, self.spec,
                proposition_type="REPOSITORY_HEAD_AT",
                predicate="git.head_at",
                subject_ref=repo.repository_id,
                object_text=revision.head_commit,
            ),
            make_direct_known(
                envelope, self.spec,
                proposition_type="REPOSITORY_BRANCH_AT",
                predicate="git.branch_at",
                subject_ref=repo.worktree_id,
                object_text=revision.branch,
            ),
            make_direct_known(
                envelope, self.spec,
                proposition_type="REPOSITORY_DIRTY_STATE",
                predicate="git.working_tree_state",
                subject_ref=repo.worktree_id,
                object_text=_compact_json({
                    "clean": state.clean,
                    "state_sha256": state.state_sha256,
                    "staged_paths": state.staged_paths,
                    "modified_paths": state.modified_paths,
                    "deleted_paths": state.deleted_paths,
                    "renamed_paths": [
                        item.model_dump(mode="json") for item in state.renamed_paths
                    ],
                    "untracked_paths": state.untracked_paths,
                    "conflicted_paths": state.conflicted_paths,
                }),
            ),
        ]

        identity_paths = {
            change.new_path
            for change in observation.changes
            if change.change_kind == "ADD" and change.new_path is not None
        }
        for change in observation.changes:
            path = change.new_path or change.old_path or "unknown"
            rows.append(make_direct_known(
                envelope, self.spec,
                proposition_type="GIT_PATH_CHANGE_OBSERVED",
                predicate="git.path_change",
                subject_ref=_file_subject(repo.repository_id, path),
                object_text=_path_change_text(change),
            ))
        for file_observation in observation.files:
            subject = _file_subject(repo.repository_id, file_observation.path)
            if file_observation.path in identity_paths:
                rows.append(make_direct_known(
                    envelope, self.spec,
                    proposition_type="FILE_IDENTITY",
                    predicate="git.file_identity",
                    subject_ref=subject,
                    object_text=file_observation.path,
                ))
            rows.append(make_direct_known(
                envelope, self.spec,
                proposition_type="FILE_EXISTS",
                predicate="git.file_exists",
                subject_ref=subject,
                object_text="true" if file_observation.exists else "false",
            ))
            if file_observation.blob_sha is not None:
                rows.append(make_direct_known(
                    envelope, self.spec,
                    proposition_type="FILE_HASH_AT",
                    predicate="git.blob_sha",
                    subject_ref=subject,
                    object_text=file_observation.blob_sha,
                ))
            if file_observation.content_sha256 is not None:
                rows.append(make_direct_known(
                    envelope, self.spec,
                    proposition_type="FILE_HASH_AT",
                    predicate="filesystem.sha256",
                    subject_ref=subject,
                    object_text=file_observation.content_sha256,
                    authority_domain="FILESYSTEM_ARTIFACT",
                    authority_ceiling_milli=1000,
                    empirical_evidence_weight_milli=1000,
                ))

        rows.extend(_tree_rows(self, envelope, observation))
        rows.extend(_structure_rows(self, envelope, observation))
        # Repeated equivalent imports/definitions are valid source syntax but
        # Known IDs are semantic identities.  Collapse exact duplicates before
        # the uniform compiler boundary, while rejecting any hash collision.
        unique = {}
        for row in rows:
            prior = unique.get(row.known_id)
            if prior is not None:
                if prior.record_hash != row.record_hash:
                    raise ValueError("repository compiler known-id collision")
                continue
            unique[row.known_id] = row
        return tuple(unique.values())


__all__ = [
    "GitCodeCompiler",
    "decode_repository_observation",
    "decode_repository_structure_delta",
    "decode_repository_tree_manifest",
]
