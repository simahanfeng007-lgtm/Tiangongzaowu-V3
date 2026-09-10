"""R3-C representative local evidence; never a production zero-use certificate.

Historical records and signed Method sources are test fixtures. Calls, migration,
journal replay and Gateway/World retention use their real implementations. Wall
clock timestamps are observed, never advanced to imply a longer review window.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from contracts import canonical_sha256
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.legacy_learning_migration import (
    EXTERNAL_COMPATIBILITY_ENTRYPOINTS, LEGACY_MUTATION_ENTRYPOINTS,
    R3A_LEGACY_MUTATION_ENTRYPOINTS, classify_legacy_records, legacy_migration_summary,
)
from tests.test_learning_publication_freeze_p10 import life, seed_history
from tests.test_learning_legacy_migration_telemetry_p10 import _seed_approved, _stop
from tests import test_method_run_retention_p9 as retention


@pytest.fixture
def same_life_bound(life, tmp_path, monkeypatch):
    root = tmp_path / 'method-retention'; root.mkdir()
    generator = retention.unregistered_bound.__wrapped__(
        root, monkeypatch, life_id=life._active()['life_id'])
    material = next(generator)
    try:
        yield retention.bound.__wrapped__(material)
    finally:
        # Generator.close() skips statements after the imported fixture's yield.
        # Close the owned SQLite store explicitly, including on assertion failure.
        try:
            material[1].close()
        finally:
            generator.close()


def _snapshot(life):
    scope = life._scope_state()
    records = {family: deepcopy(scope[family])
               for family in ('learning', 'capabilities', 'capability_pointers')}
    rows = classify_legacy_records(scope)
    now_ms = time.time_ns() // 1_000_000
    return {'observed_at_ms': now_ms, 'life_id': life._active()['life_id'],
            'record_hashes': {family: {key: canonical_sha256(value)
                                     for key, value in sorted(items.items())}
                              for family, items in records.items()},
            'ownership': {family: {key: value.get('life_id')
                                  for key, value in sorted(items.items())}
                          for family, items in records.items()},
            'disposition_counts': dict(sorted(Counter(row['disposition'] for row in rows).items())),
            'classified_records': rows,
            'summary': legacy_migration_summary(scope, now_ms=now_ms)}


def _legacy_workload(life, card, artifact, tmp_path):
    from v3.duihua_qiaojie import QIAOJIE
    from v3.jineng.jirou_ceng import JirouCeng
    from v3.l0_ability_projection import build_l0_projection
    from v3.zhili.nengli_zhuche import NengliDingyi, NengliZhuche

    responses = {}
    for path in sorted(R3A_LEGACY_MUTATION_ENTRYPOINTS):
        code, result, _ = life.request('POST', path, {
            'learning_id': card['learning_id'], 'artifact_id': artifact['artifact_id'],
            'lineage_id': artifact['lineage_id'],
        })
        responses[path] = {'status': code, 'ok': result.get('ok')}
        assert code in {200, 400, 409}
    assert QIAOJIE.create_learning_card_from_request({'user_text': 'representative legacy call'})['publication_frozen']
    assert JirouCeng._xuexi_liucheng(topic='representative legacy call')['publication_frozen']
    with pytest.raises(ValueError, match='legacy_publication_frozen'):
        NengliZhuche(tmp_path / 'registry.json').zhuce_nengli(NengliDingyi('legacy', 'qita'))
    assert build_l0_projection({'id': 'legacy', 'status': 'draft'})['schema'].endswith('.v1')
    return responses


def test_representative_window_preserves_inventory_and_same_life_inflight_pin(
        life, same_life_bound, tmp_path):
    from v3.legacy_learning_telemetry import set_legacy_learning_usage_observer
    from world_understanding.world_state import WorldStateStore
    from tests.test_method_source_lifecycle_wiring_p9 import _claim

    c, gateway, plan, reader = same_life_bound
    pinned_world = retention._read(reader, plan)
    pins = c['runtime'].store.retained_states()
    assert len(pins) == 1 and pins[0].scope.life_id == life._active()['life_id']
    claim = _claim(plan)
    assert gateway.claim_effect(claim)[1]
    gateway.mark_effect_started(claim.effect_id, started_at_ms=1701)
    assert gateway.get_effect(claim.effect_id).state == 'SIDE_EFFECT_STARTED'

    card = _seed_approved(life)
    artifact, pointer = seed_history(life)
    scope = life._scope_state()
    pending = deepcopy(pointer)
    pending['health']['patch_pending'] = {
        'round': 1, 'from_artifact_id': artifact['artifact_id'],
        'to_artifact_id': 'missing-patch', 'to_artifact_sha256': 'a' * 64,
        'proposed_at_ms': 2,
    }
    pending['pointer_sha256'] = canonical_sha256({k: v for k, v in pending.items() if k != 'pointer_sha256'})
    orphan = {**deepcopy(pending), 'lineage_id': 'unknown-lineage', 'current_artifact_id': 'missing-artifact'}
    orphan['pointer_sha256'] = canonical_sha256({k: v for k, v in orphan.items() if k != 'pointer_sha256'})
    scope['capability_pointers'].update({artifact['lineage_id']: pending, 'unknown-lineage': orphan})
    original = _snapshot(life)
    life._migrate_legacy_learning_records(life_id=card['life_id'])
    assert retention._read(reader, plan) == pinned_world

    set_legacy_learning_usage_observer(life.observe_legacy_compatibility_entry)
    try:
        life.activate_legacy_compatibility_telemetry(tuple(EXTERNAL_COMPATIBILITY_ENTRYPOINTS))
        start = _snapshot(life)
        assert len(start['summary']['instrumented_entrypoints']) == 18
        assert start['observed_at_ms'] >= max(start['summary']['coverage_started_at_ms'].values())
        start_events = life.system.journal.events(card['life_id'])
        event_start = len(start_events)
        responses = _legacy_workload(life, card, artifact, tmp_path)
        event, _, _ = retention._publication(c, ('UPDATE',), at_ms=26)
        assert c['runtime'].facade.accept(event).processed
        retention._advance(c['runtime'], 70)
        assert retention._current(c).state_ref != pins[0].state_ref
        assert retention._read(reader, plan) == pinned_world
        assert reader.reconcile() == ()
        assert gateway.get_effect(claim.effect_id).state == 'SIDE_EFFECT_STARTED'
        assert c['runtime'].store.retained_states() == pins
        assert WorldStateStore(root=c['tmp_path'] / 'state').retained_states() == pins
        end = _snapshot(life)
        assert end['record_hashes'] == start['record_hashes']
        assert end['ownership'] == start['ownership'] == original['ownership']
        assert end['disposition_counts'] == start['disposition_counts']
        assert end['summary']['unknown_ownership_count'] >= 1
        assert end['summary']['pending_migration_count'] >= 1
        delta = {path: end['summary']['by_entrypoint'].get(path, 0) - start['summary']['by_entrypoint'].get(path, 0)
                 for path in LEGACY_MUTATION_ENTRYPOINTS}
        assert all(count >= 1 for count in delta.values())
        assert end['summary']['legacy_mutation_call_count'] - start['summary']['legacy_mutation_call_count'] == sum(delta.values())
        assert end['summary']['zero_usage_proven'] is False
        assert 'OBSERVED_LEGACY_USAGE' in end['summary']['zero_usage_blockers']
        events = life.system.journal.events(card['life_id'])[event_start:]
        observations = [event['payload']['observation'] for event in events
                        if event['event_type'] == 'learning.legacy_usage_observed']
        assert dict(Counter(item['entrypoint'] for item in observations)) == delta
        assert all(start['observed_at_ms'] <= item['observed_at_ms'] <= end['observed_at_ms']
                   for item in observations)
        life._persist(card['life_id'], force=True)
    finally:
        set_legacy_learning_usage_observer(None)

    # Rebuild only the disposable fixture's usage projection through normal replay.
    state_file = life.paths.state_file
    data_root, runtime_root = life.paths.data_root, life.paths.runtime_root
    life.close()
    saved = json.loads(state_file.read_text(encoding='utf-8'))
    saved['identity_states'][card['life_id']].pop('legacy_learning_usage')
    state_file.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    reopened = EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode='embedded')
    _stop(reopened)
    try:
        after_replay = _snapshot(reopened)
        assert after_replay['record_hashes'] == end['record_hashes']
        assert after_replay['summary']['by_entrypoint'] == end['summary']['by_entrypoint']
        assert after_replay['summary']['coverage_started_at_ms'] == end['summary']['coverage_started_at_ms']
        assert retention._read(reader, plan) == pinned_world
        assert gateway.get_effect(claim.effect_id).state == 'SIDE_EFFECT_STARTED'
    finally:
        reopened.close()

    report = {
        'schema': 'tiangong.p10.r3c-representative-test-evidence.v1',
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], text=True).strip()),
        'input_sha256': {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
            'tests/test_learning_observation_window_p10.py', 'tests/test_method_run_retention_p9.py',
            'src/life_service/embedded_runtime.py', 'src/life_service/legacy_learning_migration.py',
            'src/total_gateway/method_source_run_binding.py')},
        'environment': 'disposable local fixtures; no model or production traffic',
        'window_start': start, 'window_end': end, 'before_migration': original,
        'after_replay': after_replay, 'calls_by_entrypoint': delta, 'route_responses': responses,
        'observed_window_ms': end['observed_at_ms'] - start['observed_at_ms'],
        'journal_window': {'start_event_count': event_start,
                           'start_events_sha256': canonical_sha256(start_events),
                           'events_read_by_strict_journal_reader': events},
        'source_pin': {'life_id': pins[0].scope.life_id, 'owner_id': pins[0].owner_id,
                       'effect_id': claim.effect_id, 'effect_state': 'SIDE_EFFECT_STARTED',
                       'world_state_ref': pins[0].state_ref.model_dump(mode='json'),
                       'method_snapshot_sha256': pinned_world.snapshot_sha256,
                       'method_source_refs': [item.source_ref.model_dump(mode='json') for item in pinned_world.primitives],
                       'preserved_across_migration_update_pruning_and_replay': True},
        'production_zero_usage_proven': False, 'independent_window_review_completed': False,
        'r3_exit_ready': False,
    }
    out = Path(os.environ.get('P10_OBSERVATION_ARTIFACT_DIR', str(tmp_path / 'evidence')))
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'representative-window.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
