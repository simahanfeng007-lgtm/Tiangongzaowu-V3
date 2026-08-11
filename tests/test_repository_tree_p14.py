from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.world_understanding.ingress import (
    WorldIngressEnvelope,
    derive_ingress_dedup_key,
    derive_ingress_envelope_id,
)
from contracts.world_understanding.repository import (
    RepositoryIdentity,
    RepositoryObservation,
    RepositoryRevision,
    RepositoryWorkingTreeState,
)
from contracts.world_understanding.repository_tree import (
    RepositoryTreeFile,
    RepositoryTreeManifest,
    materialize_repository_tree_nodes,
)
from contracts.world_understanding.scope import (
    ScopeBinding,
    WorldScope,
    derive_world_id,
    derive_world_scope_hash,
)
from contracts.world_understanding.time import WorldTime
from world_understanding.software_world.frame import SoftwareWorldFrame
from world_understanding.software_world.updater import SoftwareWorldUpdater
from world_understanding.source_compilers import build_p3_compilers


def _scope() -> WorldScope:
    life_id = "life.tree"
    bindings = (ScopeBinding(key="repository", value="repo.tree"),)
    world_id = derive_world_id(life_id=life_id, namespace_anchor="repo.tree")
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id=life_id,
            world_id=world_id,
            domain_id="software",
            scope_bindings=bindings,
        ),
        principal_scope_hash="1" * 64,
        privacy_scope="system",
    )


def _observation(*, state: RepositoryWorkingTreeState | None = None):
    return RepositoryObservation.build(
        identity=RepositoryIdentity(
            provider_kind="test-local",
            repository_id="repo.tree",
            repository_root_ref="C:/repo/tree",
            worktree_id="worktree.tree",
            worktree_root_ref="C:/repo/tree",
        ),
        revision=RepositoryRevision(
            branch="main",
            head_commit="a" * 40,
            detached_head=False,
            observed_at_ms=1000,
        ),
        working_tree_state=state or RepositoryWorkingTreeState.build(),
        provider_version="test-v1",
    )


def _manifest(files: tuple[RepositoryTreeFile, ...]) -> RepositoryTreeManifest:
    observation = _observation()
    return RepositoryTreeManifest.build(
        repository_id="repo.tree",
        worktree_id="worktree.tree",
        head_commit="a" * 40,
        working_tree_state_sha256=observation.working_tree_state.state_sha256,
        builder_version="test-tree.v1",
        inventory_complete=True,
        files=files,
    )


def _envelope(observation, manifest) -> WorldIngressEnvelope:
    scope = _scope()
    payload = {
        "repository_observation": observation.model_dump(mode="json"),
        "repository_tree": manifest.model_dump(mode="json"),
    }
    payload_sha = canonical_sha256(payload)
    dedup = derive_ingress_dedup_key(
        envelope_kind="SOURCE_RECORD",
        source_kind="GIT_CODE",
        source_native_id="tree." + manifest.tree_sha256[:24],
        payload_sha256=payload_sha,
        world_scope_hash=scope.world_scope_hash,
    )
    return WorldIngressEnvelope(
        envelope_id=derive_ingress_envelope_id(dedup_key=dedup),
        envelope_kind="SOURCE_RECORD",
        source_kind="GIT_CODE",
        source_native_id="tree." + manifest.tree_sha256[:24],
        producer_ref="repository.tree.test",
        payload_inline=payload,
        payload_sha256=payload_sha,
        source_time=WorldTime(
            valid_from_ms=1000,
            observed_at_ms=1000,
            recorded_at_ms=1000,
        ),
        life_id=scope.life_id,
        principal_scope_hash=scope.principal_scope_hash,
        scope_hint=scope,
        correlation_id="corr.tree",
        dedup_key=dedup,
    )


def test_total_part_tree_propagates_coverage_and_hashes() -> None:
    manifest = _manifest((
        RepositoryTreeFile(
            path="src/core/a.py",
            coverage_state="COMPLETE",
            source_fingerprint="2" * 64,
        ),
        RepositoryTreeFile(
            path="src/ui/b.ts",
            coverage_state="UNEXPANDED",
        ),
        RepositoryTreeFile(
            path="tests/test_a.py",
            coverage_state="COMPLETE",
            source_fingerprint="3" * 64,
        ),
    ))
    nodes = materialize_repository_tree_nodes(manifest)
    by_path = {node.path: node for node in nodes if node.path is not None}
    root = nodes[0]

    assert root.entity_type == "Repository"
    assert root.coverage_state == "PARTIAL"
    assert root.descendant_file_count == 3
    assert by_path["src"].coverage_state == "PARTIAL"
    assert by_path["src/core"].coverage_state == "COMPLETE"
    assert by_path["src/ui"].coverage_state == "UNEXPANDED"
    assert by_path["src/core/a.py"].parent_anchor == by_path["src/core"].stable_anchor

    changed = _manifest((
        RepositoryTreeFile(
            path="src/core/a.py",
            coverage_state="COMPLETE",
            source_fingerprint="4" * 64,
        ),
        *manifest.files[1:],
    ))
    changed_nodes = {
        node.path: node for node in materialize_repository_tree_nodes(changed)
    }
    assert changed_nodes["src/core/a.py"].subtree_sha256 != by_path["src/core/a.py"].subtree_sha256
    assert changed_nodes["src/core"].subtree_sha256 != by_path["src/core"].subtree_sha256
    assert materialize_repository_tree_nodes(changed)[0].subtree_sha256 != root.subtree_sha256


def test_tree_compiles_and_materializes_in_existing_software_world_graph() -> None:
    observation = _observation()
    manifest = _manifest((
        RepositoryTreeFile(
            path="src/core/a.py",
            coverage_state="COMPLETE",
            source_fingerprint="2" * 64,
        ),
        RepositoryTreeFile(
            path="src/ui/b.ts",
            coverage_state="UNEXPANDED",
        ),
    ))
    known = build_p3_compilers()["GIT_CODE"](_envelope(observation, manifest))
    frame = SoftwareWorldFrame.build(
        scope=_scope(),
        workspace="workspace.tree",
        repository="repo.tree",
        worktree="worktree.tree",
        branch="main",
        commit="a" * 40,
        environment="test",
        time=WorldTime(
            valid_from_ms=1000,
            observed_at_ms=1000,
            recorded_at_ms=1000,
        ),
    )
    output = SoftwareWorldUpdater().update(frame=frame, known_delta=known)
    graph = output.graph
    expected_nodes = materialize_repository_tree_nodes(manifest)

    assert len([row for row in graph.entities() if row.entity_type == "File"]) == 2
    assert len([
        row for row in graph.entities() if row.entity_type == "RepositoryBranch"
    ]) == 3
    assert len([row for row in graph.relations() if row.predicate == "CONTAINS"]) == len(expected_nodes) - 1
    file_entity = graph.resolve_token("src/core/a.py")[0]
    attrs = {
        item.key: item.value.string_value for item in file_entity.attributes
    }
    assert attrs["coverage_state"] == "COMPLETE"
    assert graph.resolve_token("a.py") == (file_entity,)


def test_tree_manifest_chunks_fail_closed_when_incomplete() -> None:
    observation = _observation()
    manifest = _manifest((
        RepositoryTreeFile(path="src/a.py", coverage_state="UNEXPANDED"),
    ))
    known = build_p3_compilers()["GIT_CODE"](_envelope(observation, manifest))
    chunk_rows = [
        row for row in known
        if row.proposition_type == "REPOSITORY_TREE_MANIFEST_CHUNK"
    ]
    assert chunk_rows
    incomplete = tuple(row for row in known if row is not chunk_rows[-1])
    frame = SoftwareWorldFrame.build(
        scope=_scope(),
        workspace="workspace.tree",
        repository="repo.tree",
        worktree="worktree.tree",
        branch="main",
        commit="a" * 40,
        environment="test",
        time=WorldTime(
            valid_from_ms=1000,
            observed_at_ms=1000,
            recorded_at_ms=1000,
        ),
    )
    output = SoftwareWorldUpdater().update(
        frame=frame, known_delta=incomplete
    )
    assert not any(
        item.entity_type in {"RepositoryBranch", "File"}
        for item in output.graph.entities()
    )
    assert "REPOSITORY_TREE_CHUNKS_INCOMPLETE" in output.diagnostics
