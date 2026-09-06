from __future__ import annotations

from dataclasses import replace

import pytest

from world_understanding.skill_method_world import (
    MethodSourceCandidateV1,
    SkillMethodWorldError,
    compile_method_source_lifecycle,
    compile_production_skill_method_world,
    computed_skill_method_descriptor_sha256,
)
from tests.test_skill_method_world_p3_production import _production_inputs


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def _snapshot():
    index, index_sha256, source_hashes = _production_inputs()
    return compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=source_hashes,
    )


def _primitive_from(base, *, method_id: str, version: str, source_sha256: str, title: str | None = None):
    source_ref = base.source_ref.model_copy(
        update={
            "semantic_id": method_id,
            "version": version,
            "source_files": (f"method_sources/{method_id}.json",),
            "source_spans": (),
            "source_sha256": source_sha256,
            "descriptor_sha256": "0" * 64,
            "manifest_sha256": None,
        }
    )
    primitive = base.model_copy(
        update={
            "method_id": method_id,
            "version": version,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "title": title or base.title,
            "descriptor_sha256": "0" * 64,
        }
    )
    descriptor = computed_skill_method_descriptor_sha256(primitive)
    return primitive.model_copy(
        update={
            "source_ref": source_ref.model_copy(update={"descriptor_sha256": descriptor}),
            "descriptor_sha256": descriptor,
        }
    )


def _candidate(snapshot, *, operation: str, method_id: str, primitive=None, old=None, evidence=H1):
    candidate = MethodSourceCandidateV1(
        candidate_id=f"p9:{operation.casefold()}:{method_id}",
        operation=operation,
        base_snapshot_sha256=snapshot.snapshot_sha256,
        method_id=method_id,
        base_descriptor_sha256=None if old is None else old.descriptor_sha256,
        primitive=primitive,
        simulation_evidence_sha256=evidence,
        candidate_sha256="0" * 64,
    )
    return candidate.with_computed_sha256()


def test_add_method_source_is_deterministic_and_non_authorizing():
    snapshot = _snapshot()
    base = snapshot.primitives[0]
    added = _primitive_from(base, method_id="diagnose_before_retry", version="v1", source_sha256=H2)
    candidate = _candidate(snapshot, operation="ADD", method_id=added.method_id, primitive=added)
    plan = compile_method_source_lifecycle(snapshot, (candidate,))

    assert plan.has_valid_sha256()
    assert plan.may_publish is False
    assert plan.may_authorize is False
    assert plan.may_execute is False
    assert added.method_id in {item.method_id for item in plan.next_primitives}
    assert plan.next_method_sources_sha256 != snapshot.method_sources_sha256
    assert snapshot.primitives == _snapshot().primitives
    assert "skill-method-world:current" in plan.invalidation_refs


def test_update_requires_new_version_source_and_descriptor():
    snapshot = _snapshot()
    old = snapshot.primitives[0]
    updated = _primitive_from(
        old,
        method_id=old.method_id,
        version="v2",
        source_sha256=H2,
        title=old.title + " v2",
    )
    candidate = _candidate(snapshot, operation="UPDATE", method_id=old.method_id, primitive=updated, old=old)
    plan = compile_method_source_lifecycle(snapshot, (candidate,))
    change = plan.changes[0]
    assert change.previous_version == old.version
    assert change.next_version == "v2"
    assert change.previous_source_sha256 == old.source_sha256
    assert change.next_source_sha256 == H2
    assert change.has_valid_sha256()

    same_version = _primitive_from(old, method_id=old.method_id, version=old.version, source_sha256=H3, title=old.title + " changed")
    with pytest.raises(SkillMethodWorldError, match="advance"):
        compile_method_source_lifecycle(snapshot, (_candidate(snapshot, operation="UPDATE", method_id=old.method_id, primitive=same_version, old=old),))


def test_remove_emits_invalidation_without_mutating_base_snapshot():
    snapshot = _snapshot()
    old = snapshot.primitives[-1]
    candidate = _candidate(snapshot, operation="REMOVE", method_id=old.method_id, old=old)
    plan = compile_method_source_lifecycle(snapshot, (candidate,))
    assert old.method_id not in {item.method_id for item in plan.next_primitives}
    assert old.method_id in {item.method_id for item in snapshot.primitives}
    assert f"method:{old.method_id}" in plan.invalidation_refs
    assert plan.changes[0].next_source_sha256 is None


def test_candidate_order_does_not_change_lifecycle_plan_identity():
    snapshot = _snapshot()
    first = snapshot.primitives[0]
    second = snapshot.primitives[1]
    update = _primitive_from(first, method_id=first.method_id, version="v2", source_sha256=H2, title=first.title + " v2")
    c1 = _candidate(snapshot, operation="UPDATE", method_id=first.method_id, primitive=update, old=first, evidence=H2)
    c2 = _candidate(snapshot, operation="REMOVE", method_id=second.method_id, old=second, evidence=H3)
    a = compile_method_source_lifecycle(snapshot, (c1, c2))
    b = compile_method_source_lifecycle(snapshot, (c2, c1))
    assert a.plan_sha256 == b.plan_sha256
    assert a.next_method_sources_sha256 == b.next_method_sources_sha256


@pytest.mark.parametrize("operation", ["UPDATE", "REMOVE"])
def test_missing_method_cannot_be_updated_or_removed(operation):
    snapshot = _snapshot()
    base = snapshot.primitives[0]
    primitive = None
    old = base.model_copy(update={"method_id": "missing_method"})
    if operation == "UPDATE":
        primitive = _primitive_from(base, method_id="missing_method", version="v2", source_sha256=H2)
    candidate = _candidate(snapshot, operation=operation, method_id="missing_method", primitive=primitive, old=old)
    with pytest.raises(SkillMethodWorldError, match=f"{operation} requires an existing"):
        compile_method_source_lifecycle(snapshot, (candidate,))


def test_add_cannot_replace_existing_method():
    snapshot = _snapshot()
    old = snapshot.primitives[0]
    candidate = _candidate(snapshot, operation="ADD", method_id=old.method_id, primitive=old)
    with pytest.raises(SkillMethodWorldError, match="ADD cannot replace"):
        compile_method_source_lifecycle(snapshot, (candidate,))


def test_stale_snapshot_and_previous_descriptor_fail_closed():
    snapshot = _snapshot()
    old = snapshot.primitives[0]
    updated = _primitive_from(old, method_id=old.method_id, version="v2", source_sha256=H2, title=old.title + " v2")
    stale = replace(
        _candidate(snapshot, operation="UPDATE", method_id=old.method_id, primitive=updated, old=old),
        base_snapshot_sha256=H3, candidate_sha256="0" * 64,
    ).with_computed_sha256()
    with pytest.raises(SkillMethodWorldError, match="stale World"):
        compile_method_source_lifecycle(snapshot, (stale,))

    wrong_old = replace(
        _candidate(snapshot, operation="UPDATE", method_id=old.method_id, primitive=updated, old=old),
        base_descriptor_sha256=H3, candidate_sha256="0" * 64,
    ).with_computed_sha256()
    with pytest.raises(SkillMethodWorldError, match="previous descriptor"):
        compile_method_source_lifecycle(snapshot, (wrong_old,))


def test_duplicate_method_change_and_candidate_hash_tamper_fail_closed():
    snapshot = _snapshot()
    old = snapshot.primitives[0]
    updated = _primitive_from(old, method_id=old.method_id, version="v2", source_sha256=H2, title=old.title + " v2")
    candidate = _candidate(snapshot, operation="UPDATE", method_id=old.method_id, primitive=updated, old=old)
    with pytest.raises(SkillMethodWorldError, match="at most once"):
        compile_method_source_lifecycle(snapshot, (candidate, candidate))
    with pytest.raises(SkillMethodWorldError, match="candidate hash"):
        compile_method_source_lifecycle(snapshot, (replace(candidate, candidate_sha256=H3),))


def test_method_source_identity_path_and_descriptor_drift_fail_closed():
    snapshot = _snapshot()
    base = snapshot.primitives[0]
    valid = _primitive_from(base, method_id="new_method", version="v1", source_sha256=H2)

    bad_kind = valid.model_copy(update={"source_ref": valid.source_ref.model_copy(update={"source_kind": "TOOL_ACTION"})})
    with pytest.raises(SkillMethodWorldError, match="identity"):
        _candidate(snapshot, operation="ADD", method_id="new_method", primitive=bad_kind)

    bad_path = valid.model_copy(update={"source_ref": valid.source_ref.model_copy(update={"source_files": ("../escape.json",)})})
    with pytest.raises(SkillMethodWorldError, match="path"):
        _candidate(snapshot, operation="ADD", method_id="new_method", primitive=bad_path)

    bad_descriptor = valid.model_copy(update={"descriptor_sha256": H3})
    with pytest.raises(SkillMethodWorldError, match="descriptor"):
        _candidate(snapshot, operation="ADD", method_id="new_method", primitive=bad_descriptor)


def test_remove_shape_and_simulation_evidence_are_strict():
    snapshot = _snapshot()
    old = snapshot.primitives[0]
    with pytest.raises(SkillMethodWorldError, match="REMOVE requires"):
        MethodSourceCandidateV1(
            candidate_id="p9:remove:bad",
            operation="REMOVE",
            base_snapshot_sha256=snapshot.snapshot_sha256,
            method_id=old.method_id,
            base_descriptor_sha256=old.descriptor_sha256,
            primitive=old,
            simulation_evidence_sha256=H1,
            candidate_sha256=H1,
        )
    with pytest.raises(SkillMethodWorldError, match="simulation evidence"):
        MethodSourceCandidateV1(
            candidate_id="p9:add:bad",
            operation="ADD",
            base_snapshot_sha256=snapshot.snapshot_sha256,
            method_id="new_method",
            base_descriptor_sha256=None,
            primitive=_primitive_from(old, method_id="new_method", version="v1", source_sha256=H2),
            simulation_evidence_sha256="bad",
            candidate_sha256=H1,
        )
