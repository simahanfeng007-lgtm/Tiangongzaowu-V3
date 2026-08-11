from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.world_understanding.life_learning import LifeLearningObservation
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from life_service.activity_scope import build_activity_scope, normalize_repository_evidence
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.learning_executor import execute_learning_preview
from world_understanding.post_commit import install_native_post_commit_observer
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.source_compilers.p3 import build_p3_compilers
from total_gateway.orchestration import GatewayOrchestrationWorker


def _scope() -> WorldScope:
    bindings = (
        ScopeBinding(key="frame_kind", value="v3_runtime_workspace"),
        ScopeBinding(key="workspace_id", value="workspace.main"),
    )
    world_id = derive_world_id(life_id="life.main", namespace_anchor="workspace:workspace.main")
    return WorldScope(
        life_id="life.main",
        world_id=world_id,
        domain_id="software_runtime",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id="life.main",
            world_id=world_id,
            domain_id="software_runtime",
            scope_bindings=bindings,
        ),
        principal_scope_hash="a" * 64,
        privacy_scope="system",
    )


def _repository_evidence() -> dict:
    return {
        "schema": "tiangong.life.repository-evidence.v1",
        "frame_id": "swf.frame",
        "frame_revision_hash": "1" * 64,
        "repository_id": "repo.main",
        "worktree_id": "worktree.main",
        "branch": "agent/p14",
        "commit": "2" * 64,
        "observed_at_ms": 10,
        "entity_refs": [
            {"record_id": "went.file", "revision": 3, "sha256": "3" * 64},
        ],
        "secret": "must-not-cross-boundary",
    }


def test_repository_evidence_is_reference_only_and_enters_life_learning_scope() -> None:
    evidence = normalize_repository_evidence(_repository_evidence())
    assert evidence is not None
    assert "secret" not in evidence
    scope = build_activity_scope(
        life_id="life.main",
        soul={},
        scope={
            "memories": {},
            "autonomy": {"tasks": {}},
            "capabilities": {},
            "learning": {},
            "executions": {"request.main": {"repository_evidence": evidence}},
            "settings": {},
        },
    )
    assert scope["repository_evidence"][0]["frame_id"] == "swf.frame"
    assert scope["source_refs"]["repository_evidence_refs"] == ["swf.frame", "went.file"]

    preview = execute_learning_preview(
        {
            "learning_id": "learn.main",
            "target": "knowledge",
            "title": "repository lesson",
            "summary": "learn from committed repository reality",
            "risk_level": "A1",
            "draft_artifact": {"content": "bounded lesson"},
            "learning_plan": [],
        },
        activity_scope=scope,
    )
    source = preview["evidence"]["source"]
    assert source["kind"] == "user_memory_and_repository"
    assert source["repository_evidence"][0]["frame_revision_hash"] == "1" * 64


def test_life_learning_observation_has_dedicated_deterministic_compiler() -> None:
    observation = LifeLearningObservation(
        life_id="life.main",
        learning_id="learn.main",
        artifact_id="art.main",
        artifact_kind="skill",
        lineage_id="lineage.main",
        status="activated",
        learned_subject_refs=("swf.frame", "went.file"),
        safe_summary="bounded self reality",
        evidence_refs=("evt.main",),
        confidence_milli=1000,
        epistemic_status="verified",
        prior_revision=3,
        new_revision=4,
        occurred_at_ms=20,
        observation_sha256="0" * 64,
    ).with_computed_hash()
    envelope = build_post_commit_source_envelope(
        source_kind="LIFE_LEARNING",
        source_native_id="lifelearn." + observation.observation_sha256[:48],
        producer_ref="life_service.learning.post_commit",
        payload=observation.model_dump(mode="json"),
        source_time=WorldTime(valid_from_ms=20, observed_at_ms=20, recorded_at_ms=20),
        scope=_scope(),
        correlation_id="corr.life-learning",
        workspace_id="workspace.main",
    )
    rows = build_p3_compilers()["LIFE_LEARNING"](envelope)
    assert {row.predicate for row in rows} >= {
        "life.artifact_identity",
        "life.capability_status",
        "life.capability_lineage",
        "life.learning_identity",
        "life.learned_subject",
    }
    assert all(row.authority_domain == "LIFE_CAPABILITY_STATE" for row in rows)


def test_embedded_life_emits_only_bounded_post_commit_self_reality() -> None:
    captured = []
    install_native_post_commit_observer(captured.append)
    try:
        runtime = object.__new__(EmbeddedLifeRuntime)
        runtime._world_identity_provider = lambda _life_id: {
            "principal_scope_hash": "a" * 64,
            "workspace_id": "workspace.main",
        }
        runtime._notify_life_learning_post_commit(
            life_id="life.main",
            event={"event_id": "evt.main", "event_sha256": "4" * 64, "sequence": 7},
            artifact={
                "artifact_id": "art.main",
                "kind": "knowledge",
                "lineage_id": "lineage.main",
                "artifact_sha256": "5" * 64,
                "summary": "safe summary api_key=top-secret",
            },
            learning={
                "learning_id": "learn.main",
                "learning_evidence": {
                    "evidence_sha256": "6" * 64,
                    "source": {
                        "memory_refs": ["mem.main"],
                        "repository_evidence": [{
                            "frame_id": "swf.frame",
                            "entity_refs": [{"record_id": "went.file"}],
                        }],
                    },
                },
            },
            status="published",
        )
    finally:
        install_native_post_commit_observer(None)
    assert len(captured) == 1
    event = captured[0]
    assert event.source_kind == "LIFE_LEARNING"
    assert event.payload["learned_subject_refs"] == ["mem.main", "swf.frame", "went.file"]
    assert "top-secret" not in event.payload["safe_summary"]
    assert event.payload["prior_revision"] == 6
    assert event.payload["new_revision"] == 7


def test_life_learning_observation_hash_binds_all_safe_fields() -> None:
    base = {
        "life_id": "life.main",
        "artifact_id": "art.main",
        "artifact_kind": "knowledge",
        "lineage_id": "lineage.main",
        "status": "published",
        "confidence_milli": 1000,
        "epistemic_status": "verified",
        "prior_revision": 0,
        "new_revision": 1,
        "occurred_at_ms": 1,
    }
    first = LifeLearningObservation(**base, observation_sha256="0" * 64).with_computed_hash()
    second = LifeLearningObservation(
        **{**base, "safe_summary": "changed"}, observation_sha256="0" * 64
    ).with_computed_hash()
    assert first.observation_sha256 != second.observation_sha256
    assert first.observation_sha256 == canonical_sha256(
        first.model_dump(mode="json", exclude={"observation_sha256"})
    )


def test_repository_inquiry_aliases_remain_read_only_gateway_actions() -> None:
    assert GatewayOrchestrationWorker._world_observation({
        "action": "repository.status", "target": ".", "args": {}
    })["action"] == "git.status"
    head = GatewayOrchestrationWorker._world_observation({
        "action": "repository.head", "target": ".", "args": {"limit": 99}
    })
    assert head == {"action": "git.log", "target": ".", "args": {"limit": 1}}
    assert GatewayOrchestrationWorker._world_observation({
        "action": "repository.read_source_window",
        "target": "src/module.py",
        "args": {"offset": 10, "limit": 20},
    })["action"] == "file.read"
