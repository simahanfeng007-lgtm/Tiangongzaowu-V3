"""P9 R2 protocol tests; test-only keys and simulated observations, not a release."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import canonical_json_bytes, canonical_sha256
from total_gateway.method_source_review import (
    METHOD_REVIEW_DOMAIN, METHOD_SIMULATION_DOMAIN, MethodSimulationEvidenceV1,
    MethodSourceReviewError, method_simulation_subject_sha256,
    prepare_reviewed_method_world_revision, verify_reviewed_method_world_revision,
)
from world_understanding.skill_method_world.compiler import (
    NATIVE_METHOD_SOURCE_SCHEMA, compile_native_method_source,
    observe_legacy_skill_method_corpus,
)
from world_understanding.skill_method_world import SkillMethodWorldError, compile_method_source_lifecycle
from tests.test_skill_method_source_lifecycle_p9 import _snapshot, _candidate
from tests.test_skill_method_world_p3_production import _production_inputs


def _source(method_id="native_method", version="v1", **updates):
    base = _snapshot().primitives[0].model_dump(mode="json")
    for key in ("schema_version", "source_ref", "source_sha256", "descriptor_sha256"):
        base.pop(key)
    base.update(schema=NATIVE_METHOD_SOURCE_SCHEMA, method_id=method_id, version=version,
                method_steps=[f"method-step:{method_id}:01", f"method-step:{method_id}:02"])
    base.update(updates)
    raw = canonical_json_bytes(base)
    path = f"method_sources/{method_id}.json"
    primitive = compile_native_method_source(path, raw, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    return (path, raw), primitive


def _sign_report(candidate, observer, *, kinds=("positive", "negative")):
    subject = method_simulation_subject_sha256(candidate)
    observations, rows = [], []
    for i, kind in enumerate(kinds):
        outcome = "ACCEPT" if kind == "positive" else "REJECT"
        raw = canonical_json_bytes({
            "schema": "tiangong.method-source-case-observation.v1", "subject_sha256": subject,
            "case_id": f"case:{i}", "kind": kind, "expected": outcome, "observed": outcome,
        })
        observations.append(raw)
        rows.append({"case_id": f"case:{i}", "kind": kind, "passed": True,
                     "observation_sha256": hashlib.sha256(raw).hexdigest()})
    raw = canonical_json_bytes({
        "schema": "tiangong.method-source-simulation.v1", "subject_sha256": subject,
        "status": "METHOD_SIMULATION_OBSERVED", "observed_at_ms": 10, "cases": rows,
    })
    return MethodSimulationEvidenceV1(raw, observer.sign(METHOD_SIMULATION_DOMAIN + raw), tuple(observations))


def _bind_review(args, reviewer):
    plan = compile_method_source_lifecycle(args["base_snapshot"], args["candidates"])
    raw = canonical_json_bytes({
        "schema": "tiangong.method-source-review.v1", "decision": "APPROVE",
        "base_snapshot_sha256": args["base_snapshot"].snapshot_sha256,
        "plan_sha256": plan.plan_sha256, "candidate_sha256s": list(plan.candidate_sha256s),
        "reviewed_at_ms": 20, "expires_at_ms": 100,
    })
    args.update(review_bytes=raw, review_signature=reviewer.sign(METHOD_REVIEW_DOMAIN + raw))


def _inputs(operations=("ADD",), *, base=None, reviewer=None, observer=None):
    base = base or _snapshot()
    reviewer, observer = reviewer or Ed25519PrivateKey.generate(), observer or Ed25519PrivateKey.generate()
    docs, evidence, candidates = {}, {}, []
    for i, op in enumerate(operations):
        old = None if op == "ADD" else base.primitives[i]
        method_id = f"native_{i}" if old is None else old.method_id
        primitive = None
        if op != "REMOVE":
            doc, primitive = _source(method_id, "v1" if old is None else f"v{int(old.version[1:]) + 1}")
            docs[method_id] = doc
        candidate = _candidate(base, operation=op, method_id=method_id, primitive=primitive, old=old)
        proof = _sign_report(candidate, observer)
        candidate = replace(candidate, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256()
        evidence[method_id] = proof
        candidates.append(candidate)
    index, index_sha, sources = _production_inputs()
    args = dict(
        base_snapshot=base, candidates=tuple(candidates), expected_base_snapshot_sha256=base.snapshot_sha256,
        corpus=observe_legacy_skill_method_corpus(index, index_source_sha256=index_sha, skill_source_hashes=sources),
        source_documents=docs, simulation_evidence=evidence, now_ms=30,
        trusted_reviewer_public_key=reviewer.public_key().public_bytes_raw(),
        trusted_observer_public_key=observer.public_key().public_bytes_raw(),
    )
    _bind_review(args, reviewer)
    return args, reviewer, observer


def _replace_evidence(args, reviewer, proof):
    c = args["candidates"][0]
    args["candidates"] = (replace(c, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256(),)
    args["simulation_evidence"] = {c.method_id: proof}
    _bind_review(args, reviewer)


@pytest.mark.parametrize("operations", [("ADD",), ("UPDATE",), ("REMOVE",), ("ADD", "UPDATE", "REMOVE")])
def test_review_compiles_add_update_remove_without_cutover(operations):
    args, _, _ = _inputs(operations)
    before = canonical_json_bytes(args["base_snapshot"].payload())
    result = prepare_reviewed_method_world_revision(**args)
    assert type(result.snapshot) is type(args["base_snapshot"])
    assert result.revision_sha256 == result.computed_sha256()
    assert result.snapshot.has_valid_sha256()
    assert result.may_publish is result.may_authorize is result.may_execute is False
    assert canonical_json_bytes(args["base_snapshot"].payload()) == before
    assert result.payload()["current_world_changed"] is False
    native_ids = {b.method_id for b in result.snapshot.reviewed_source_bindings}
    for c in args["candidates"]:
        assert (c.method_id in native_ids) == (c.operation != "REMOVE")
    reopened = verify_reviewed_method_world_revision(expected_revision_sha256=result.revision_sha256, **args)
    assert result == reopened
    reverse = {**args, "candidates": tuple(reversed(args["candidates"]))}
    assert prepare_reviewed_method_world_revision(**reverse) == result


def test_native_revision_can_be_used_as_next_review_base():
    args, reviewer, observer = _inputs(("UPDATE",))
    first = prepare_reviewed_method_world_revision(**args)
    follow, _, _ = _inputs(("UPDATE",), base=first.snapshot, reviewer=reviewer, observer=observer)
    second = prepare_reviewed_method_world_revision(**follow)
    assert second.snapshot.primitives[0].version == "v3"
    remove, _, _ = _inputs(("REMOVE",), base=second.snapshot, reviewer=reviewer, observer=observer)
    third = prepare_reviewed_method_world_revision(**remove)
    assert not third.snapshot.reviewed_source_bindings
    assert second.snapshot.primitives[0].method_id not in {p.method_id for p in third.snapshot.primitives}
    assert second.snapshot.primitives[0].version == "v3"


@pytest.mark.parametrize("field", ["trusted_reviewer_public_key", "trusted_observer_public_key"])
def test_rehashed_evidence_cannot_supply_its_own_trust_key(field):
    args, _, _ = _inputs()
    args[field] = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(MethodSourceReviewError, match="signature"):
        prepare_reviewed_method_world_revision(**args)


def test_same_observer_reviewer_key_is_rejected():
    args, _, _ = _inputs()
    args["trusted_observer_public_key"] = args["trusted_reviewer_public_key"]
    with pytest.raises(MethodSourceReviewError, match="distinct"):
        prepare_reviewed_method_world_revision(**args)


@pytest.mark.parametrize("field,value", [
    ("decision", "REJECT"), ("decision", True), ("plan_sha256", "f" * 64),
    ("candidate_sha256s", []), ("base_snapshot_sha256", "f" * 64),
    ("reviewed_at_ms", True), ("expires_at_ms", False), ("reviewed_at_ms", 31),
])
def test_valid_signature_does_not_replace_scope_or_schema_validation(field, value):
    args, reviewer, _ = _inputs()
    review = json.loads(args["review_bytes"]); review[field] = value
    raw = canonical_json_bytes(review)
    args.update(review_bytes=raw, review_signature=reviewer.sign(METHOD_REVIEW_DOMAIN + raw))
    with pytest.raises(MethodSourceReviewError, match="decision, scope"):
        prepare_reviewed_method_world_revision(**args)


@pytest.mark.parametrize("now", [True, -1, 19, 100, 101])
def test_review_time_window_and_clock_types_fail_closed(now):
    args, _, _ = _inputs(); args["now_ms"] = now
    with pytest.raises(MethodSourceReviewError):
        prepare_reviewed_method_world_revision(**args)


def test_review_signature_domain_cannot_be_a_simulation_signature():
    args, reviewer, _ = _inputs()
    args["review_signature"] = reviewer.sign(METHOD_SIMULATION_DOMAIN + args["review_bytes"])
    with pytest.raises(MethodSourceReviewError, match="signature"):
        prepare_reviewed_method_world_revision(**args)


@pytest.mark.parametrize("raw", [b'{"schema":"x","schema":"x"}', b'{"x":NaN}', b'[]', b'{ "x": 1 }', b'\xff', b'{"x":1e999}', b'{}\n'])
def test_signed_duplicate_noncanonical_and_nonfinite_json_is_rejected(raw):
    args, reviewer, _ = _inputs()
    args.update(review_bytes=raw, review_signature=reviewer.sign(METHOD_REVIEW_DOMAIN + raw))
    with pytest.raises(SkillMethodWorldError):
        prepare_reviewed_method_world_revision(**args)


def test_simulation_pass_claim_cannot_override_raw_failure_even_when_both_signed():
    args, reviewer, observer = _inputs()
    proof = next(iter(args["simulation_evidence"].values()))
    report = json.loads(proof.report_bytes)
    failed = json.loads(proof.case_observations[1]); failed["observed"] = "ACCEPT"
    raw_case = canonical_json_bytes(failed)
    report["cases"][1]["observation_sha256"] = hashlib.sha256(raw_case).hexdigest()
    raw_report = canonical_json_bytes(report)
    forged_pass = MethodSimulationEvidenceV1(raw_report, observer.sign(METHOD_SIMULATION_DOMAIN + raw_report),
                                            (proof.case_observations[0], raw_case))
    _replace_evidence(args, reviewer, forged_pass)
    with pytest.raises(MethodSourceReviewError, match="contradicts PASS"):
        prepare_reviewed_method_world_revision(**args)


@pytest.mark.parametrize("mutate", ["status", "passed", "duplicate", "coverage", "subject", "time", "extra_key", "missing_bytes", "swapped_bytes"])
def test_simulation_observation_adversarial_matrix(mutate):
    args, reviewer, observer = _inputs()
    c = args["candidates"][0]; proof = args["simulation_evidence"][c.method_id]
    if mutate == "coverage":
        proof = _sign_report(c, observer, kinds=("positive", "positive"))
    report = json.loads(proof.report_bytes); observations = proof.case_observations
    if mutate == "status": report["status"] = "FAILED"
    if mutate == "passed": report["cases"][1]["passed"] = False
    if mutate == "duplicate": report["cases"][1] = report["cases"][0]
    if mutate == "subject": report["subject_sha256"] = "b" * 64
    if mutate == "time": report["observed_at_ms"] = 21
    if mutate == "extra_key": report["trusted_public_key"] = "self-issued-key"
    if mutate == "missing_bytes": observations = observations[:1]
    if mutate == "swapped_bytes": observations = (observations[0], observations[0])
    raw = canonical_json_bytes(report)
    proof = MethodSimulationEvidenceV1(raw, observer.sign(METHOD_SIMULATION_DOMAIN + raw), observations)
    _replace_evidence(args, reviewer, proof)
    with pytest.raises(MethodSourceReviewError):
        prepare_reviewed_method_world_revision(**args)


@pytest.mark.parametrize("scope", ["missing_source", "extra_source", "missing_evidence", "extra_evidence"])
def test_all_and_only_changed_sources_need_evidence(scope):
    args, _, _ = _inputs()
    field = "source_documents" if "source" in scope else "simulation_evidence"
    if scope.startswith("missing"): args[field] = {}
    else: args[field]["undeclared"] = next(iter(args[field].values()))
    with pytest.raises(MethodSourceReviewError, match="scope"):
        prepare_reviewed_method_world_revision(**args)


def test_actual_source_bytes_must_match_candidate_not_just_review_hash():
    args, _, _ = _inputs(); mid = args["candidates"][0].method_id
    path, raw = args["source_documents"][mid]
    doc = json.loads(raw); doc["title"] += " tampered"
    args["source_documents"][mid] = (path, canonical_json_bytes(doc))
    with pytest.raises(SkillMethodWorldError, match="byte identity"):
        prepare_reviewed_method_world_revision(**args)


def test_changed_candidate_cannot_reuse_signed_simulation_for_another_subject():
    args, reviewer, _ = _inputs()
    c = replace(args["candidates"][0], candidate_id="candidate:changed").with_computed_sha256()
    args["candidates"] = (c,); _bind_review(args, reviewer)
    with pytest.raises(MethodSourceReviewError, match="simulation identity"):
        prepare_reviewed_method_world_revision(**args)


def test_even_signed_version_rollback_is_rejected():
    args, reviewer, observer = _inputs(("UPDATE",))
    first = prepare_reviewed_method_world_revision(**args)
    args, _, _ = _inputs(("UPDATE",), base=first.snapshot, reviewer=reviewer, observer=observer)
    c = args["candidates"][0]; doc, primitive = _source(c.method_id, "v1")
    c = replace(c, primitive=primitive).with_computed_sha256()
    args["candidates"] = (c,); args["source_documents"][c.method_id] = doc
    proof = _sign_report(c, observer); _replace_evidence(args, reviewer, proof)
    with pytest.raises(MethodSourceReviewError, match="roll back"):
        prepare_reviewed_method_world_revision(**args)


def test_current_world_external_pin_and_reconstructed_graph_are_required():
    args, _, _ = _inputs()
    with pytest.raises(MethodSourceReviewError, match="trusted current"):
        prepare_reviewed_method_world_revision(**{**args, "expected_base_snapshot_sha256": "b" * 64})
    bad = replace(args["base_snapshot"], relations=())
    bad = replace(bad, snapshot_sha256=canonical_sha256(bad.payload()))
    with pytest.raises(MethodSourceReviewError, match="graph or provenance"):
        prepare_reviewed_method_world_revision(**{**args, "base_snapshot": bad, "expected_base_snapshot_sha256": bad.snapshot_sha256})


def test_revision_reopen_uses_external_pin_not_self_report():
    args, _, _ = _inputs()
    with pytest.raises(MethodSourceReviewError, match="external pin"):
        verify_reviewed_method_world_revision(expected_revision_sha256="f" * 64, **args)


@pytest.mark.parametrize("field", ["handler", "allowed_action_ids", "permission", "grant", "ticket", "runtime_route", "may_execute"])
def test_native_source_cannot_declare_execution_fields(field):
    document, _ = _source(); raw = json.loads(document[1]); raw[field] = True
    encoded = canonical_json_bytes(raw)
    with pytest.raises(SkillMethodWorldError, match="schema"):
        compile_native_method_source(document[0], encoded, expected_source_sha256=hashlib.sha256(encoded).hexdigest())


@pytest.mark.parametrize("path", ["../escape.json", "/absolute.json", "C:/drive.json", "C:relative.json", "file.json:ads", "a\\b.json", "a//b.json", "a/../b.json", "a/./b.json", "a/b .json ", "a/nul.json", "x\x00.json", "source.py"])
def test_native_source_paths_are_portable_non_executable_data(path):
    document, _ = _source()
    with pytest.raises(SkillMethodWorldError, match="path"):
        compile_native_method_source(path, document[1], expected_source_sha256=hashlib.sha256(document[1]).hexdigest())


def test_reviewed_revision_reuses_p4_and_p6_without_expanding_permissions():
    from tests.test_one_world_context_p6 import _capability_worlds
    from tests.test_world_understanding_p9_world_state import cut, graph_for
    from world_understanding.domain_contribution import compile_skill_method_contribution
    from world_understanding.capability_composition import build_candidate_snapshot
    from world_understanding.skill_method_world import PRODUCTION_METHOD_SEEDS_SHA256
    before_seeds = PRODUCTION_METHOD_SEEDS_SHA256
    args, _, _ = _inputs(); result = prepare_reviewed_method_world_revision(**args)
    tools, _ = _capability_worlds()
    candidates = build_candidate_snapshot(tools, result.snapshot, method_ids=("native_0",), action_ids=("file.read",))
    assert candidates.method_candidates[0].primitive.source_ref.source_kind == "SKILL_METHOD"
    assert candidates.action_candidates[0].primitive.action_id == "file.read"
    assert candidates.may_execute is candidates.may_authorize is False
    world_cut = cut(); frame, _ = graph_for(world_cut)
    contribution = compile_skill_method_contribution(frame, world_cut, result.snapshot)
    assert any(r.semantic_id == "native_0" for r in contribution.source_revision_refs)
    assert before_seeds == PRODUCTION_METHOD_SEEDS_SHA256


def test_unmodified_native_provenance_survives_another_method_update():
    args, reviewer, observer = _inputs()
    first = prepare_reviewed_method_world_revision(**args)
    follow, _, _ = _inputs(("UPDATE",), base=first.snapshot, reviewer=reviewer, observer=observer)
    second = prepare_reviewed_method_world_revision(**follow)
    retained = next(b for b in second.snapshot.reviewed_source_bindings if b.method_id == "native_0")
    assert retained == first.snapshot.reviewed_source_bindings[0]
    assert len(second.snapshot.reviewed_source_bindings) == 2


def test_native_only_world_does_not_fabricate_legacy_migration_bindings():
    args, reviewer, observer = _inputs()
    first = prepare_reviewed_method_world_revision(**args)
    by_id = {p.method_id: p for p in first.snapshot.primitives}
    candidates, proofs = [], {}
    for binding in first.snapshot.migration_bindings:
        old = by_id[binding.method_id]
        c = _candidate(first.snapshot, operation="REMOVE", method_id=old.method_id, old=old)
        proof = _sign_report(c, observer)
        c = replace(c, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256()
        candidates.append(c); proofs[c.method_id] = proof
    follow = {**args, "base_snapshot": first.snapshot,
              "expected_base_snapshot_sha256": first.snapshot.snapshot_sha256,
              "candidates": tuple(candidates), "simulation_evidence": proofs, "source_documents": {}}
    _bind_review(follow, reviewer)
    last = prepare_reviewed_method_world_revision(**follow)
    assert tuple(p.method_id for p in last.snapshot.primitives) == ("native_0",)
    assert not last.snapshot.migration_bindings
    assert last.snapshot.schema == "tiangong.skill-method-world.v2"
    assert not any(r.relation_type == "DERIVED_FROM_LEGACY_SKILL" for r in last.snapshot.relations)


def test_conflicting_native_documents_cannot_claim_the_same_repository_path():
    args, reviewer, observer = _inputs(("ADD", "ADD"))
    c = args["candidates"][1]
    same_path = args["source_documents"]["native_0"][0]
    _, raw = args["source_documents"][c.method_id]
    primitive = compile_native_method_source(same_path, raw, expected_source_sha256=c.primitive.source_sha256)
    c = replace(c, primitive=primitive).with_computed_sha256()
    proof = _sign_report(c, observer)
    c = replace(c, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256()
    args["candidates"] = (args["candidates"][0], c)
    args["source_documents"][c.method_id] = (same_path, raw)
    args["simulation_evidence"][c.method_id] = proof
    _bind_review(args, reviewer)
    with pytest.raises(SkillMethodWorldError, match="paths collide"):
        prepare_reviewed_method_world_revision(**args)


def test_method_step_collisions_are_rejected_before_world_ingest():
    args, reviewer, observer = _inputs()
    c = args["candidates"][0]
    document, primitive = _source(c.method_id, method_steps=list(args["base_snapshot"].primitives[0].method_steps))
    args["source_documents"][c.method_id] = document
    args["candidates"] = (replace(c, primitive=primitive).with_computed_sha256(),)
    _replace_evidence(args, reviewer, _sign_report(args["candidates"][0], observer))
    with pytest.raises(SkillMethodWorldError, match="step identities collide"):
        prepare_reviewed_method_world_revision(**args)


def test_candidate_semantics_cannot_drift_from_verified_source_bytes():
    from world_understanding.skill_method_world import computed_skill_method_descriptor_sha256
    args, reviewer, observer = _inputs(); c = args["candidates"][0]
    primitive = c.primitive.model_copy(update={"title": "a different but self-hashed title"})
    digest = computed_skill_method_descriptor_sha256(primitive)
    primitive = primitive.model_copy(update={"descriptor_sha256": digest,
        "source_ref": primitive.source_ref.model_copy(update={"descriptor_sha256": digest})})
    c = replace(c, primitive=primitive).with_computed_sha256()
    args["candidates"] = (c,)
    _replace_evidence(args, reviewer, _sign_report(c, observer))
    with pytest.raises(MethodSourceReviewError, match="independently parsed source"):
        prepare_reviewed_method_world_revision(**args)


def test_prepare_has_no_process_file_or_runtime_side_effect(monkeypatch):
    import subprocess
    from pathlib import Path
    args, _, _ = _inputs()
    def denied(*a, **kw):
        raise AssertionError("unexpected IO or process execution")
    with monkeypatch.context() as m:
        m.setattr(Path, "open", denied)
        m.setattr(subprocess, "Popen", denied)
        result = prepare_reviewed_method_world_revision(**args)
    assert result.payload()["current_world_changed"] is False
