"""R3 real local Git/store/ingress tests with synthetic review-only test keys.

These exercise actual disk replacement and source reads, not actual human
approval, Windows AppContainer containment, model calls or product acceptance.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from contracts import canonical_json_bytes, canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.time import WorldTime
from total_gateway.method_source_publication import (
    PUBLICATION_DOMAIN, MethodPublicationResolver, build_method_publication_body,
    stage_method_publication, method_publication_envelope,
)
from total_gateway.method_source_review import MethodSourceReviewError, prepare_reviewed_method_world_revision
from world_understanding.skill_method_world.compiler import compile_native_method_source
from world_understanding.skill_method_world.publication import ARCHIVE_WATERMARK, method_marker
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.software_world import SoftwareWorldFrame
from world_understanding.world_state import WorldStateStore
from tests.test_method_source_review_p9 import _inputs, _source, _sign_report, _bind_review
from tests.test_skill_method_source_lifecycle_p9 import _snapshot, _candidate
from tests.test_world_understanding_p13_1_production_activation import _source as _event, _scope


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.STDOUT).decode().strip()


@pytest.fixture
def context(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test fixture")
    (repo / "source-ownership.json").write_bytes((Path(__file__).parents[1] / "source-ownership.json").read_bytes())
    documents = {}
    for mid in ("native_0", "native_1", "acceptance_review", "decompose_goal"):
        for version in ("v1", "v2", "v3"):
            _, primitive = _source(mid, version)
            doc, _ = _source(mid, version)
            path = f"src/world_understanding/skill_method_world/sources/{mid}.{version}.json"
            target = repo / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(doc[1])
            documents[(mid, version)] = (path, doc[1])
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "immutable method data fixtures")
    commit = _git(repo, "rev-parse", "HEAD")
    archive = tmp_path / "archives"; archive.mkdir()
    args, reviewer, observer = _inputs()
    resolver = MethodPublicationResolver(
        archive, repo, "repo.fixture", "worktree.fixture", commit, _snapshot().snapshot_sha256,
        reviewer.public_key().public_bytes_raw(), observer.public_key().public_bytes_raw(), lambda: 30,
    )
    def frame_factory(envelope, cut):
        return SoftwareWorldFrame.build(scope=envelope.scope_hint, workspace="workspace.main",
            repository="repo.fixture", worktree="worktree.fixture", branch="fixture-main", commit=commit,
            environment="test-env", time=envelope.source_time, world_cut=cut)
    store = WorldStateStore(root=tmp_path / "state")
    runtime = ProductionWorldUnderstandingRuntime(store=store, frame_factory=frame_factory,
                                                 method_revision_resolver=resolver)
    assert runtime.facade.accept(_event("genesis", 1)).processed
    value = dict(repo=repo, commit=commit, documents=documents, archive=archive, runtime=runtime,
                 resolver=resolver, reviewer=reviewer, observer=observer, frame_factory=frame_factory, tmp_path=tmp_path)
    return value


def _current(c):
    return c["runtime"].store.current_candidates(life_id=_scope().life_id,
        principal_scope_hash=_scope().principal_scope_hash, world_scope_hash=_scope().world_scope_hash)[0]


def _frame(c, state):
    return c["frame_factory"](_event("frame", state.cut.time.recorded_at_ms), state.cut)


def _publication(c, operations=("ADD",), *, at_ms=25, base=None, source_base=None):
    base = base or _current(c)
    source_base = source_base or (c["resolver"].load(base) if method_marker(base, ARCHIVE_WATERMARK) else _snapshot())
    args, _, _ = _inputs(operations, base=source_base, reviewer=c["reviewer"], observer=c["observer"])
    candidates = []
    for candidate in args["candidates"]:
        if candidate.primitive is not None:
            path, raw = c["documents"][(candidate.method_id, candidate.primitive.version)]
            primitive = compile_native_method_source(path, raw, expected_source_sha256=hashlib.sha256(raw).hexdigest())
            candidate = replace(candidate, primitive=primitive).with_computed_sha256()
            args["source_documents"][candidate.method_id] = (path, raw)
        proof = _sign_report(candidate, c["observer"])
        candidate = replace(candidate, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256()
        args["simulation_evidence"][candidate.method_id] = proof
        candidates.append(candidate)
    args["candidates"] = tuple(candidates); _bind_review(args, c["reviewer"])
    body = build_method_publication_body(frame=_frame(c, base), previous=base, review_inputs=args, publication_at_ms=at_ms)
    signature = c["reviewer"].sign(PUBLICATION_DOMAIN + body)
    digest = stage_method_publication(archive_root=c["archive"], body=body, publication_signature=signature)
    envelope = method_publication_envelope(archive_sha256=digest, frame=_frame(c, base), at_ms=at_ms)
    return envelope, args, body


def test_real_git_bytes_enter_same_ingress_store_and_current_graph(context):
    c = context; prior = _current(c)
    envelope, args, body = _publication(c)
    # Staging a signed archive alone did not update WorldState.
    assert _current(c) == prior
    receipt = c["runtime"].facade.accept(envelope)
    assert receipt.processed, receipt
    assert receipt.reason_code == "METHOD_REVISION_MATERIALIZED"
    current = _current(c)
    assert current.state.world_sequence == prior.state.world_sequence + 1
    assert c["runtime"].store is context["runtime"].store
    assert any(e.entity_type == "SkillMethod" and "native_0" in e.aliases for e in current.entities)
    assert set(prior.entity_heads.refs).issubset(current.entity_heads.refs)
    actual = c["runtime"].method_world_for_state(current.state_ref, scope=_scope())
    assert actual == prepare_reviewed_method_world_revision(**args).snapshot
    assert len(c["runtime"]._streams) == 1
    assert c["runtime"].facade.accept(envelope) is receipt
    assert _current(c) == current


def test_add_update_remove_retrieval_and_old_snapshot_sources_do_not_drift(context):
    c = context
    for ops, at_ms in [(("ADD",), 25), (("UPDATE",), 26), (("REMOVE",), 27)]:
        event, _, _ = _publication(c, ops, at_ms=at_ms)
        receipt = c["runtime"].facade.accept(event)
        assert receipt.processed, receipt
        if at_ms == 25:
            pinned = _current(c)
            old_sources = c["runtime"].method_world_for_state(pinned.state_ref, scope=_scope())
    current = _current(c)
    methods = c["runtime"].method_world_for_state(current.state_ref, scope=_scope())
    assert "acceptance_review" not in {p.method_id for p in methods.primitives}
    assert not any("acceptance_review" in e.aliases for e in current.entities)
    assert c["runtime"].method_world_for_state(pinned.state_ref, scope=_scope()) == old_sources
    assert old_sources.primitives[0].version == "v1"
    assert {r.subject_ref.record_id for r in current.relations if r.predicate.startswith("method.")} <= {
        e.entity_id for e in current.entities if e.entity_type == "SkillMethod"}
    assert current.delta.removed_refs


def test_restart_reads_same_archive_and_duplicate_does_not_republish(context):
    c = context; event, _, _ = _publication(c)
    assert c["runtime"].facade.accept(event).processed
    before = _current(c); before_methods = c["resolver"].load(before)
    restarted = ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=c["tmp_path"] / "state"),
        frame_factory=c["frame_factory"], method_revision_resolver=replace(c["resolver"], clock_ms=lambda: 150))
    # Expired admission review remains valid as historical evidence, not a new grant.
    assert restarted.method_world_for_state(before.state_ref, scope=_scope()) == before_methods
    receipt = restarted.facade.accept(event)
    assert receipt.processed and receipt.reason_code == "METHOD_REVISION_ALREADY_MATERIALIZED"
    assert restarted.store.get(before.state.world_state_id) == before


@pytest.mark.parametrize("failure", ["archive", "signature", "missing", "writable", "source_git"])
def test_invalid_archive_or_source_never_advances_world(context, failure):
    c = context; event, _, body = _publication(c); before = _current(c)
    digest = event.payload_inline["archive_sha256"]; path = c["archive"] / (digest + ".json")
    if failure == "archive":
        path.chmod(0o644); path.write_bytes(path.read_bytes() + b" "); path.chmod(0o444)
    elif failure == "signature":
        digest = stage_method_publication(archive_root=c["archive"], body=body, publication_signature=b"x" * 64)
        event = method_publication_envelope(archive_sha256=digest, frame=_frame(c, before), at_ms=25)
    elif failure == "missing":
        # Fault injection only: Windows cannot unlink the read-only fixture.
        # Production archives stay sealed; create an actually missing file
        # before checking that the existing ingress rejects its publication.
        path.chmod(0o644)
        path.unlink()
        assert not path.exists()
    elif failure == "writable": path.chmod(0o644)
    elif failure == "source_git":
        # Remove the pinned object database; do not substitute working-tree bytes.
        c["runtime"]._method_revision_resolver = replace(c["resolver"], source_repository=c["tmp_path"])
    receipt = c["runtime"].facade.accept(event)
    assert not receipt.processed
    assert _current(c) == before


def test_worktree_mutation_does_not_replace_the_pinned_git_source(context):
    c = context; event, _, _ = _publication(c)
    path, _ = c["documents"][("native_0", "v1")]
    (c["repo"] / path).write_text("malicious working-tree change", encoding="utf-8")
    assert c["runtime"].facade.accept(event).processed


def test_persist_failure_rolls_back_head_and_live_graph_and_same_event_can_retry(context, monkeypatch):
    c = context; event, _, _ = _publication(c); before = _current(c)
    old_stream = dict(c["runtime"]._streams)
    real = WorldStateStore._atomic_json
    def fail_index(path, payload):
        if path.name == "index.json": raise OSError("injected index failure")
        return real(path, payload)
    with monkeypatch.context() as m:
        m.setattr(WorldStateStore, "_atomic_json", staticmethod(fail_index))
        assert not c["runtime"].facade.accept(event).processed
    assert _current(c) == before and c["runtime"]._streams == old_stream
    assert c["runtime"].facade.accept(event).processed


def test_competing_publications_compare_exact_head_and_only_one_wins(context):
    c = context; first, _, _ = _publication(c, ("ADD",)); second, _, _ = _publication(c, ("UPDATE",))
    before = _current(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(c["runtime"].facade.accept, (first, second)))
    assert sum(r.processed for r in results) == 1
    assert _current(c).state.world_sequence == before.state.world_sequence + 1


def test_stale_head_even_with_valid_review_does_not_publish(context):
    c = context; event, _, _ = _publication(c); c["runtime"].facade.accept(_event("other-source", 2))
    before = _current(c)
    assert not c["runtime"].facade.accept(event).processed
    assert _current(c) == before


def test_scope_and_frame_swaps_are_rejected_without_new_stream(context):
    c = context; event, _, _ = _publication(c); before = _current(c)
    frame = _frame(c, before)
    other = SoftwareWorldFrame.build(scope=frame.scope, workspace=frame.workspace,
        repository=frame.repository, worktree=frame.worktree, branch="other-branch", commit=frame.commit,
        environment=frame.environment, time=frame.time, world_cut=frame.world_cut)
    swapped = method_publication_envelope(archive_sha256=event.payload_inline["archive_sha256"], frame=other, at_ms=25)
    assert not c["runtime"].facade.accept(swapped).processed
    assert _current(c) == before and len(c["runtime"]._streams) == 1


def test_no_operator_resolver_means_no_publication(context):
    c = context; event, _, _ = _publication(c); before = _current(c)
    runtime = ProductionWorldUnderstandingRuntime(store=c["runtime"].store, frame_factory=c["frame_factory"])
    assert not runtime.facade.accept(event).processed
    assert _current(c) == before


def test_history_unavailable_never_falls_back_to_current(context):
    c = context; event, _, _ = _publication(c); assert c["runtime"].facade.accept(event).processed
    state = _current(c)
    absent = state.state_ref.model_copy(update={"record_id": "wst_" + "f" * 64})
    with pytest.raises(ValueError, match="PINNED_WORLD_UNAVAILABLE"):
        c["runtime"].method_world_for_state(absent, scope=_scope())


def test_stage_reuses_exact_immutable_archive_and_does_not_heal_partial(context):
    c = context; event, _, body = _publication(c)
    sig = c["reviewer"].sign(PUBLICATION_DOMAIN + body)
    digest = stage_method_publication(archive_root=c["archive"], body=body, publication_signature=sig)
    assert digest == event.payload_inline["archive_sha256"]
    path = c["archive"] / (digest + ".json")
    path.chmod(0o644); path.write_bytes(b"partial"); path.chmod(0o444)
    with pytest.raises(MethodSourceReviewError, match="identity differs"):
        stage_method_publication(archive_root=c["archive"], body=body, publication_signature=sig)
    assert path.read_bytes() == b"partial"


@pytest.mark.parametrize("mutation", ["extra", "time", "frame", "base", "previous_archive", "candidate_extra"])
def test_signed_archive_cannot_override_canonical_target_contract(context, mutation):
    c = context; _event0, _, raw = _publication(c); before = _current(c)
    value = json.loads(raw)
    if mutation == "extra": value["may_publish"] = True
    if mutation == "time": value["publication_at_ms"] = True
    if mutation == "frame": value["frame"]["branch"] = "different-branch"
    if mutation == "base": value["expected_state_ref"]["sha256"] = "b" * 64
    if mutation == "previous_archive": value["previous_archive_sha256"] = "b" * 64
    if mutation == "candidate_extra": value["inputs"]["candidates"][0]["handler"] = "run"
    raw = canonical_json_bytes(value)
    digest = stage_method_publication(archive_root=c["archive"], body=raw,
        publication_signature=c["reviewer"].sign(PUBLICATION_DOMAIN + raw))
    event = method_publication_envelope(archive_sha256=digest, frame=_frame(c, before), at_ms=25)
    assert not c["runtime"].facade.accept(event).processed
    assert _current(c) == before


def test_verified_candidate_cannot_claim_bytes_not_in_pinned_git_commit(context):
    c = context; _, args, _ = _publication(c); before = _current(c)
    candidate = args["candidates"][0]; path, raw = args["source_documents"][candidate.method_id]
    value = json.loads(raw); value["title"] += " not in Git"
    raw = canonical_json_bytes(value)
    primitive = compile_native_method_source(path, raw, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    candidate = replace(candidate, primitive=primitive).with_computed_sha256()
    proof = _sign_report(candidate, c["observer"])
    candidate = replace(candidate, simulation_evidence_sha256=hashlib.sha256(proof.report_bytes).hexdigest()).with_computed_sha256()
    args["candidates"] = (candidate,)
    args["source_documents"][candidate.method_id] = (path, raw)
    args["simulation_evidence"][candidate.method_id] = proof
    _bind_review(args, c["reviewer"])
    body = build_method_publication_body(frame=_frame(c, before), previous=before, review_inputs=args, publication_at_ms=25)
    digest = stage_method_publication(archive_root=c["archive"], body=body,
        publication_signature=c["reviewer"].sign(PUBLICATION_DOMAIN + body))
    event = method_publication_envelope(archive_sha256=digest, frame=_frame(c, before), at_ms=25)
    assert not c["runtime"].facade.accept(event).processed
    assert _current(c) == before


def test_after_restart_without_git_read_exact_old_revision_in_real_child_process(context):
    import sys
    c = context; event, _, _ = _publication(c); assert c["runtime"].facade.accept(event).processed
    pinned = _current(c); old = c["resolver"].load(pinned)
    event, _, _ = _publication(c, ("UPDATE",), at_ms=26); assert c["runtime"].facade.accept(event).processed
    config = dict(archive=str(c["archive"]), root=str(c["tmp_path"] / "state"), commit=c["commit"],
        state_ref=pinned.state_ref.model_dump(mode="json"), scope=_scope().model_dump(mode="json"),
        bootstrap=_snapshot().snapshot_sha256, reviewer=c["resolver"].trusted_reviewer_public_key.hex(),
        observer=c["resolver"].trusted_observer_public_key.hex())
    code = '''
import json,sys
from pathlib import Path
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import WorldScope
from total_gateway.method_source_publication import MethodPublicationResolver
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.world_state import WorldStateStore
v=json.loads(sys.argv[1])
r=MethodPublicationResolver(Path(v['archive']),Path(v['root'])/'NOT_A_REPOSITORY',
    'repo.fixture','worktree.fixture',v['commit'],v['bootstrap'],bytes.fromhex(v['reviewer']),
    bytes.fromhex(v['observer']),lambda:999)
w=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=v['root']),
    frame_factory=lambda *a: None,method_revision_resolver=r)
p=w.method_world_for_state(WorldRecordRef.model_validate(v['state_ref']),scope=WorldScope.model_validate_json(json.dumps(v['scope'])))
print(p.snapshot_sha256)
'''
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", code, json.dumps(config)], cwd=Path(__file__).parents[1],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == old.snapshot_sha256


def test_dependent_record_stays_stale_across_an_unrelated_source_event(context):
    from world_understanding.world_state import DependencyBinding, MaterializationInput, WorldStateMaterializer
    from world_understanding.software_world import SparseWorldGraph
    from tests.test_world_understanding_p9_world_state import eref, cog
    c = context; first, _, _ = _publication(c); assert c["runtime"].facade.accept(first).processed
    base = _current(c); frame = _frame(c, base)
    graph = SparseWorldGraph(frame)
    for entity in base.entities: graph.upsert_entity(entity)
    for relation in base.relations: graph.upsert_relation(relation)
    from contracts.world_understanding.entity import derive_entity_id
    template = next(e for e in base.entities if e.entity_type != "SkillMethod")
    anchor = "c" * 64
    independent = template.model_copy(update={
        "entity_id": derive_entity_id(life_id=template.scope.life_id, domain_id=template.scope.domain_id, identity_anchor_hash=anchor),
        "identity_anchor_hash": anchor, "canonical_name": "independent-derived-record", "aliases": (),
    }).with_computed_hash()
    graph.upsert_entity(independent)
    source = next(p for p in c["resolver"].load(base).primitives if p.method_id == "acceptance_review")
    binding = DependencyBinding(eref(independent), ("source:" + source.source_sha256,))
    uncertainty_ref = WorldRecordRef(record_type="uncertainty", record_id="uncertainty:test", revision=1, sha256="f" * 64)
    cognition = cog(_scope())
    staged = WorldStateMaterializer(c["runtime"].store).materialize(MaterializationInput(
        frame=frame, cut=base.cut, graph=graph,
        dependency_bindings=base.dependencies.bindings + (binding,), stable_cognition=(cognition,),
        uncertainty_refs=(uncertainty_ref,), conflict_refs=(uncertainty_ref,), source_transaction_id="test.dependencies",
        materialized_at_ms=25))
    # Seeded canonical data is read through a fresh runtime, not a stale test cache.
    c["runtime"] = ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=c["tmp_path"] / "state"),
        frame_factory=c["frame_factory"], method_revision_resolver=c["resolver"])
    # A normal source event must not erase Method/dependent source bindings.
    assert c["runtime"].facade.accept(_event("normal-before-update", 25)).processed
    assert _current(c).dependencies.binding_for(binding.ref) is not None
    update, _, _ = _publication(c, ("UPDATE",), at_ms=26)
    receipt = c["runtime"].facade.accept(update)
    assert receipt.processed, receipt
    after = _current(c)
    assert binding.ref in after.state.stale_refs
    assert after.cognition_heads == staged.cognition_heads
    assert after.uncertainty == staged.uncertainty
    assert after.state.unresolved_conflict_refs == staged.state.unresolved_conflict_refs
    assert c["runtime"].facade.accept(_event("normal-after-update", 27)).processed
    assert binding.ref in _current(c).state.stale_refs


def test_production_singleton_operator_hook_and_pinned_reader(context, monkeypatch):
    from types import SimpleNamespace
    from v3 import world_understanding_production as module
    c = context; event, _, _ = _publication(c); assert c["runtime"].facade.accept(event).processed
    state = _current(c)
    monkeypatch.setattr(module, "_runtime", c["runtime"])
    module.configure_production_method_publication(c["resolver"])
    context = SimpleNamespace(life_id="life.main", principal_scope_hash="a" * 64,
        workspace_id="workspace.main", run_id="run.test", request_id="req.test",
        session_id="", conversation_id="")
    assert module.production_method_world_for_state(state.state_ref, context) == c["resolver"].load(state)
    assert module.production_world_understanding_runtime() is c["runtime"]
    with pytest.raises(TypeError): module.configure_production_method_publication({})


def test_source_scope_cannot_be_swapped_on_pinned_historical_read(context):
    c = context; event, _, _ = _publication(c); assert c["runtime"].facade.accept(event).processed
    state = _current(c)
    scope = _scope().model_copy(update={"principal_scope_hash": "b" * 64})
    with pytest.raises(ValueError, match="PINNED_WORLD_UNAVAILABLE"):
        c["runtime"].method_world_for_state(state.state_ref, scope=scope)


def test_unchanged_stale_refs_survive_later_unrelated_cut():
    from world_understanding.world_state import DependencyBinding, MaterializationInput, WorldStateMaterializer
    from tests.test_world_understanding_p9_world_state import cut, graph_for, advance, eref
    store = WorldStateStore(); materializer = WorldStateMaterializer(store)
    c1 = cut(); frame, graph = graph_for(c1)
    binding = DependencyBinding(eref(graph.entities()[0]), ("GIT_CODE:git.commit",))
    def put(cut, frame):
        return materializer.materialize(MaterializationInput(frame=frame, cut=cut, graph=graph,
            dependency_bindings=(binding,), materialized_at_ms=cut.time.recorded_at_ms))
    put(c1, frame)
    c2 = cut(git="c2", gseq=2, t=2); second = put(c2, advance(graph, c2))
    assert binding.ref in second.state.stale_refs
    c3 = cut(git="c2", gseq=2, runtime="r2", rseq=2, t=3)
    third = put(c3, advance(graph, c3))
    assert binding.ref in third.state.stale_refs


def test_store_compare_failure_does_not_enter_publication_transaction(context):
    c = context; prior = _current(c)
    event, _, _ = _publication(c); assert c["runtime"].facade.accept(event).processed
    with pytest.raises(ValueError, match="COMPARE_FAILED"):
        with c["runtime"].store.publication_transaction(prior):
            pytest.fail("a stale writer entered publication")


def test_forged_archive_path_is_rejected_before_filesystem_access(context):
    c = context
    from total_gateway.method_source_publication import _read_archive
    with pytest.raises(MethodSourceReviewError, match="digest"):
        _read_archive(c["archive"], "../index")


def test_archive_symlink_is_rejected_even_when_target_bytes_match(context):
    c = context; event, _, _ = _publication(c); before = _current(c)
    path = c["archive"] / (event.payload_inline["archive_sha256"] + ".json")
    relocated = c["tmp_path"] / "relocated.json"
    path.rename(relocated)
    try:
        path.symlink_to(relocated)
    except OSError:
        # No containment PASS is inferred when the host cannot create links.
        # Exercise the existing link observer explicitly on these hosts.
        from unittest.mock import patch
        path.write_bytes(relocated.read_bytes()); path.chmod(0o444)
        real = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda p: p == path or real(p)):
            assert not c["runtime"].facade.accept(event).processed
    else:
        assert not c["runtime"].facade.accept(event).processed
    assert _current(c) == before
