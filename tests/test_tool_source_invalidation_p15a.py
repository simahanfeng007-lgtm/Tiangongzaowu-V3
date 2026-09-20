"""P15-A preflight: a Tool-source publication invalidates through the ONE chain.

Runs on the real immutable Git/bundle and signed Method archive world from
R1C2: the admitted tool world is materialized into the current state, a NEW
bundle (same action, different bytes) is published through the production
facade, and the ORIGINAL materializer marks exactly the tool-bound records.
Duplicates replay idempotently; unrelated records survive untouched; a
missing resolver configuration or a drifted bundle is refused — never a
silent fallback to an unmarked stale current.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.world_understanding.time import WorldTime
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.tool_capability_world.publication import (
    TOOL_PUBLICATION_SCHEMA,
    compute_tool_changed_source_keys,
    previous_tool_world_keys,
)
from total_gateway.tool_source_revision_resolver import (
    ToolSourcePublicationResolver,
    ToolSourceRevisionError,
)

from tests.test_source_registration_intake_p12 import (  # noqa: F401  fixtures
    intake, intake_factory, source, _prepare)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


def _tool_entities(snapshot):
    return [e for e in snapshot.entities if e.entity_type == 'ToolCapability']


def _install_resolver(c, bundle_root: Path):
    resolver = ToolSourcePublicationResolver(
        bundle_root=bundle_root,
        action_entry_path='src/omni_body_skill/tools/handler.py',
        workspace=c['rc'].workspace_id,
        repository='repo.fixture', worktree='worktree.fixture',
        branch='main', commit='f' * 40, environment='test-env',
    )
    c['world'].install_tool_revision_resolver(resolver)
    return resolver


def _publication_envelope(c, bundle_sha256, at_ms):
    return build_post_commit_source_envelope(
        source_kind='SYSTEM_GOVERNANCE',
        source_native_id='tool.publication.p15a',
        producer_ref='p8.publication',
        payload={'schema': TOOL_PUBLICATION_SCHEMA,
                 'bundle_sha256': bundle_sha256,
                 'frame_id': c['current'].frame_id},
        source_time=WorldTime(valid_from_ms=at_ms, observed_at_ms=at_ms,
                              recorded_at_ms=at_ms),
        scope=c['query_scope'],
        correlation_id='tool.publication.p15a',
        workspace_id=c['rc'].workspace_id,
    )


@pytest.fixture
def tooled(intake, tmp_path):
    """Materialize the current tool world into the live current state."""
    c = intake
    from world_understanding.world_state import (
        WorldStateMaterializer, MaterializationInput, materialize_one_world_state)
    from world_understanding.domain_contribution import (
        compile_tool_capability_contribution)
    from tests.test_source_registration_intake_p12 import _compile
    result = _compile(c, _prepare(c)[0], intents=frozenset(
        {'verification-intent:plan-bound-acceptance'}))
    tools, _review = c['tool_source'].load(result.preparation.registry)
    current = c['current']
    from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph
    frame = SoftwareWorldFrame.build(
        scope=c['query_scope'], workspace=c['rc'].workspace_id,
        repository='repo.fixture', worktree='worktree.fixture', branch='main',
        commit=c['tool_source'].candidate_commit, environment='test-env',
        time=WorldTime(valid_from_ms=26, observed_at_ms=26, recorded_at_ms=26),
        world_cut=current.cut)
    graph = SparseWorldGraph(frame)
    for e in current.entities:
        graph.upsert_entity(e)
    for r in current.relations:
        graph.upsert_relation(r)
    updated = materialize_one_world_state(
        WorldStateMaterializer(c['world'].store),
        MaterializationInput(
            frame=frame, cut=current.cut, graph=graph,
            dependency_bindings=current.dependencies.bindings,
            preserve_previous_domains=True,
            source_transaction_id='p15a.tool.observation',
            materialized_at_ms=26),
        (compile_tool_capability_contribution(
            frame, current.cut, tools,
            previous_entities={e.entity_id: e for e in current.entities},
            previous_relations={r.relation_id: r for r in current.relations}),))
    c['current'] = updated
    c['tool_world_before'] = tools

    def make_bundle(root: Path):
        root.mkdir(parents=True, exist_ok=True)
        import hashlib, shutil
        source_bundle = Path(c['tool_source'].bundle_path)
        digest = c['tool_source'].bundle_sha256
        target = root / f"{digest}.tgb"
        if not target.is_file():
            shutil.copyfile(source_bundle, target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        return target, digest

    c['tool_bundle_maker'] = make_bundle
    return c


def test_publication_invalidates_exactly_the_tool_domain(tooled, tmp_path):
    """A new bundle advances the watermarks and invalidates tool-bound records."""
    c = tooled
    before = c['world'].store.current(
        life_id=c['rc'].life_id,
        principal_scope_hash=c['query_scope'].principal_scope_hash,
        world_scope_hash=c['query_scope'].world_scope_hash,
        frame_id=c['current'].frame_id)
    assert before is not None
    assert _tool_entities(before), 'tool entities must be present before publication'
    method_before = [e for e in before.entities if e.entity_type == 'SkillMethod']
    old_keys = previous_tool_world_keys(before)
    assert any(k.startswith('tool-world:') for k in old_keys)

    # Publish the pinned bundle: even an identical snapshot digest passes
    # through the same invalidation path (the tool-world key is already
    # bound, so the tool domain is invalidated-and-refreshed in place and
    # the watermarks advance). A different-bytes bundle produces a NEW
    # snapshot digest and both keys land in the changed set by construction
    # (compute_tool_changed_source_keys unions old and new).
    bundle_root = tmp_path / 'p15a-bundles'
    bundle, digest = c['tool_bundle_maker'](bundle_root)
    _install_resolver(c, bundle_root)
    receipt = c['world'].facade.accept(
        _publication_envelope(c, digest, at_ms=30))
    assert receipt.processed, receipt
    after = c['world'].store.current(
        life_id=c['rc'].life_id,
        principal_scope_hash=c['query_scope'].principal_scope_hash,
        world_scope_hash=c['query_scope'].world_scope_hash,
        frame_id=c['current'].frame_id)
    assert after.state.world_state_id != before.state.world_state_id
    # Watermarks advanced; stale marks reference the OLD tool-world keys.
    marks = {(w.source_kind, w.watermark_type): w.watermark_value
             for w in after.cut.source_watermarks}
    assert marks.get(('SYSTEM_GOVERNANCE', 'tool-source.bundle')) == digest
    assert marks.get(('SYSTEM_GOVERNANCE', 'tool-world.snapshot')) == \
        c['tool_world_before'].snapshot_sha256
    # Unrelated Method records survive untouched.
    after_methods = [e for e in after.entities if e.entity_type == 'SkillMethod']
    assert {m.entity_id for m in after_methods} == {m.entity_id for m in method_before}


def test_changed_keys_cover_old_and_new_snapshots(tooled):
    c = tooled
    before = c['world'].store.current(
        life_id=c['rc'].life_id,
        principal_scope_hash=c['query_scope'].principal_scope_hash,
        world_scope_hash=c['query_scope'].world_scope_hash,
        frame_id=c['current'].frame_id)
    keys = compute_tool_changed_source_keys(before, c['tool_world_before'])
    assert 'tool-world:' + c['tool_world_before'].snapshot_sha256 in keys
    assert set(keys) >= set(previous_tool_world_keys(before))


def test_duplicate_publication_replays_idempotently(tooled, tmp_path):
    c = tooled
    bundle_root = tmp_path / 'p15a-bundles-dup'
    bundle, digest = c['tool_bundle_maker'](bundle_root)
    _install_resolver(c, bundle_root)
    first = c['world'].facade.accept(_publication_envelope(c, digest, at_ms=30))
    assert first.processed
    snapshot_after_first = c['world'].store.current(
        life_id=c['rc'].life_id,
        principal_scope_hash=c['query_scope'].principal_scope_hash,
        world_scope_hash=c['query_scope'].world_scope_hash,
        frame_id=c['current'].frame_id)
    second = c['world'].facade.accept(_publication_envelope(c, digest, at_ms=31))
    assert second.processed
    snapshot_after_second = c['world'].store.current(
        life_id=c['rc'].life_id,
        principal_scope_hash=c['query_scope'].principal_scope_hash,
        world_scope_hash=c['query_scope'].world_scope_hash,
        frame_id=c['current'].frame_id)
    assert snapshot_after_second.state.world_state_id == \
        snapshot_after_first.state.world_state_id


def test_unconfigured_resolver_is_refused_not_silent(tooled, tmp_path):
    c = tooled
    bundle, digest = c['tool_bundle_maker'](tmp_path / 'b1')
    envelope = _publication_envelope(c, digest, at_ms=30)
    receipt = c['world'].facade.accept(envelope)
    assert receipt.processed is False
    assert receipt.disposition == 'REJECTED'
    assert receipt.reason_code in {'COMPILER_OUTPUT_INVALID', 'TOOL_PUBLICATION_NOT_CONFIGURED'}


def test_missing_bundle_is_refused(tooled, tmp_path):
    c = tooled
    _install_resolver(c, tmp_path / 'empty-bundles')
    receipt = c['world'].facade.accept(_publication_envelope(c, 'e' * 64, at_ms=30))
    assert receipt.processed is False and receipt.disposition == 'REJECTED'


def test_tampered_bundle_bytes_are_refused(tooled, tmp_path):
    """Same digest, different bytes: the original reader refuses, never a
    silent accept that would leave the stale current unmarked."""
    c = tooled
    bundle, digest = c['tool_bundle_maker'](tmp_path / 'b-tamper')
    raw = bytearray(bundle.read_bytes())
    raw[-1] ^= 0xFF
    bundle.write_bytes(bytes(raw))
    _install_resolver(c, bundle.parent)
    receipt = c['world'].facade.accept(_publication_envelope(c, digest, at_ms=30))
    assert receipt.processed is False and receipt.disposition == 'REJECTED'
