from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from world_understanding.skill_method_world import (
    PRODUCTION_METHOD_SEEDS,
    PRODUCTION_METHOD_SEEDS_SHA256,
    compile_production_skill_method_world,
)


EXPECTED_METHOD_IDS = (
    "acceptance_review",
    "decompose_goal",
    "finalize_verified_artifact",
    "generate_then_verify",
    "retry_after_diagnosis",
)


def _production_inputs() -> tuple[dict, str, dict[str, str]]:
    repository_root = Path(__file__).resolve().parents[1]
    skill_root = repository_root / "src" / "omni_body_skill"
    index_path = skill_root / "registry" / "skill_router_index.json"
    index_bytes = index_path.read_bytes()
    index = json.loads(index_bytes.decode("utf-8", errors="strict"))
    source_hashes: dict[str, str] = {}
    for raw in index["skills"]:
        source_path = skill_root.joinpath(*Path(raw["file"]).parts)
        relative = source_path.relative_to(repository_root).as_posix()
        source_hashes[relative] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return index, hashlib.sha256(index_bytes).hexdigest(), source_hashes


def _legacy_action_ids(index: dict) -> set[str]:
    result = set(index.get("actions") or ())
    fields = (
        "starter_actions",
        "inspection_actions",
        "production_actions",
        "quality_gates",
        "repair_actions",
        "final_actions",
    )
    for raw in index["skills"]:
        for field in fields:
            result.update(raw.get(field) or ())
    return result


def test_production_seed_catalog_is_reviewed_many_to_one_and_stable() -> None:
    assert tuple(seed.method_id for seed in PRODUCTION_METHOD_SEEDS) == EXPECTED_METHOD_IDS
    assert all(seed.has_valid_sha256() for seed in PRODUCTION_METHOD_SEEDS)
    assert all(seed.may_authorize is False for seed in PRODUCTION_METHOD_SEEDS)
    assert all(seed.may_execute is False for seed in PRODUCTION_METHOD_SEEDS)
    assert all(len(seed.legacy_skill_ids) >= 2 for seed in PRODUCTION_METHOD_SEEDS)
    assert len(PRODUCTION_METHOD_SEEDS_SHA256) == 64

    tampered = replace(
        PRODUCTION_METHOD_SEEDS[0],
        semantic_summary="tampered reviewed semantics",
    )
    assert tampered.has_valid_sha256() is False


def test_real_static_catalog_compiles_a_nonempty_production_method_world() -> None:
    index, index_sha256, source_hashes = _production_inputs()
    snapshot = compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=source_hashes,
    )
    assert snapshot.has_valid_sha256()
    assert snapshot.may_authorize is False
    assert snapshot.may_execute is False
    assert tuple(item.method_id for item in snapshot.primitives) == EXPECTED_METHOD_IDS
    assert len(snapshot.primitives) == len(PRODUCTION_METHOD_SEEDS)
    assert len(snapshot.primitives) < index["skill_count"]
    assert all(len(item.legacy_skill_ids) >= 2 for item in snapshot.migration_bindings)
    assert all(item.source_ref.manifest_sha256 is None for item in snapshot.primitives)


def test_production_method_world_is_deterministic_across_input_mapping_order() -> None:
    index, index_sha256, source_hashes = _production_inputs()
    first = compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=source_hashes,
    )
    second = compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=dict(reversed(tuple(source_hashes.items()))),
    )
    assert first.snapshot_sha256 == second.snapshot_sha256


def test_production_method_world_does_not_retain_action_or_execution_authority() -> None:
    index, index_sha256, source_hashes = _production_inputs()
    snapshot = compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=source_hashes,
    )
    serialized = json.dumps(snapshot.payload(), ensure_ascii=False, sort_keys=True)
    for action_id in _legacy_action_ids(index):
        assert action_id not in serialized

    forbidden_relation_types = {
        "EXECUTES",
        "ALLOWS_ACTION",
        "COMPILES_TO_ACTION",
        "GRANTS",
        "HANDLED_BY",
    }
    assert forbidden_relation_types.isdisjoint(
        {item.relation_type for item in snapshot.relations}
    )
    assert '"handler"' not in serialized
    assert '"allowed_action_ids"' not in serialized
    assert '"permission"' not in serialized
    assert '"grant"' not in serialized
    assert '"ticket"' not in serialized
