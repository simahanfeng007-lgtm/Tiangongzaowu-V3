"""Real installed catalog/bytes through World and Gateway; no model or dispatch.

Only request identities and clocks are fixtures. No mocked capability manifest,
signed publication, source review, or execution success enters these tests.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from contracts import derive_run_identity
from total_gateway.action_registry import load_action_authority
from total_gateway.installed_composition_sources import InstalledCompositionSources
from total_gateway.store import GatewayStateStore
from world_understanding.context_output import ContextOutputPort, WorldContextProjector, WorldContextRequestHandler
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.world_state import WorldStateStore
from tests.test_gateway_worker_composition_resume_p7d2 import _envelope


@pytest.fixture(scope="module")
def source_copy(tmp_path_factory):
    """Freeze the actual source bytes so concurrent developers cannot race QA."""
    from total_gateway.installed_composition_sources import _installed_inputs
    production = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp("installed-source-copy")
    for row in _installed_inputs(production).files:
        path = root / row.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((production / row.path).read_bytes())
    policy = json.loads((production / "source-ownership.json").read_bytes())
    for row in policy["mappings"]:
        if (production / row["source"]).is_dir():
            (root / row["source"]).mkdir(parents=True, exist_ok=True)
        for excluded in row.get("generated_exclusions", ()):
            original = production / row["source"] / excluded
            target = root / row["source"] / excluded
            if original.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif original.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original.read_bytes())
    manifest = "src/omni_body_skill/registry/capability_manifest.generated.json"
    (root / manifest).write_bytes((production / manifest).read_bytes())
    for command in (("init", "-q"), ("add", "."), ("commit", "-qm", "Frozen production source bytes for integration QA")):
        subprocess.run(["git", "-C", str(root), "-c", "core.autocrlf=false",
            "-c", "user.name=Integration QA", "-c", "user.email=qa@example.invalid", *command],
            check=True, capture_output=True)
    return root


def _installed_for_root(tmp_path, monkeypatch, root):
    from v3 import world_understanding_production as production
    authority = load_action_authority(root / "src/omni_body_skill/registry/capability_manifest.generated.json", generated_at_ms=0)
    gateway = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=1000)
    inbound = _envelope("installed-dictionaries").model_copy(update={"text": "读取目录里的订单 CSV 文件，统计数据并保存汇总报告，然后验证结果"})
    request = gateway.register_request(inbound, ingress_sha256="b" * 64, created_at_ms=1100).entry.request_id
    run = derive_run_identity(request, 1).run_id
    gateway.acquire_generation_lease(request_id=request, run_id=run, run_sequence=1,
        generation=1, gateway_epoch=1, lease_id="lease.installed", owner_instance_id="gateway.installed",
        issued_at_ms=1200, lease_duration_ms=100000)
    rc = SimpleNamespace(request_id=request, run_id=run, generation=1, life_id="life.main",
        principal_scope_hash=inbound.principal_scope_hash, workspace_id="workspace.main",
        session_id=inbound.conversation_ref, conversation_id=inbound.conversation_ref)
    store = WorldStateStore(root=tmp_path / "world")
    port = ContextOutputPort()
    def resolve(query):
        state = store.get(query.basis_world_state_ref.record_id)
        return state if state and state.state_ref == query.basis_world_state_ref else None
    handler = WorldContextRequestHandler(state_resolver=resolve, projector=WorldContextProjector(), output_port=port)
    runtime = ProductionWorldUnderstandingRuntime(store=store, frame_factory=production._frame_factory,
        context_request_handler=handler)
    sources = InstalledCompositionSources.install(runtime=runtime, gateway=gateway,
        source_root=root, archive_root=tmp_path / "archives", registry=authority.registry,
        manifest=authority.manifest)
    monkeypatch.setattr(production, "_method_run_resolver", sources.resolver)
    yield SimpleNamespace(root=root, sources=sources, gateway=gateway, rc=rc, runtime=runtime,
        port=port, user=inbound.text, authority=authority)
    gateway.close()


@pytest.fixture
def installed(tmp_path, monkeypatch, source_copy):
    yield from _installed_for_root(tmp_path, monkeypatch, source_copy)


@pytest.fixture
def installed_dirty(tmp_path, monkeypatch, source_copy):
    """Keep real uncommitted changes; a clean committed copy missed live genesis."""
    root = tmp_path / "dirty-installed-source"
    shutil.copytree(source_copy, root)
    # copytree changes file identities/stat data compared with the copied Git
    # index. Refresh that fixture-only metadata before adding real dirty files;
    # do not widen the production provider's five-second read-only deadline.
    clean = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z",
                            "--untracked-files=all"], check=True, capture_output=True, timeout=30)
    assert clean.stdout == b""
    tracked = root / "src/total_gateway/installed_composition_sources.py"
    tracked.write_bytes(tracked.read_bytes() + b"\n# QA uncommitted source observation.\n")
    dirty = root / "local_uncommitted_inputs"
    dirty.mkdir()
    for ordinal in range(161):
        (dirty / f"observed_{ordinal:03}.py").write_text(
            f"VALUE = {ordinal}\n", encoding="utf-8")
    # Deliberately do not git-add/commit/reset this source checkout.
    yield from _installed_for_root(tmp_path, monkeypatch, root)


def test_actual_catalog_reaches_request_candidates_without_fake_publication(installed):
    from v3.world_context_integration import WorldContextIntegration
    c = installed
    snapshot = c.sources.ensure_world(c.rc, now_ms=2000)
    assert sum(e.entity_type == "ToolCapability" for e in snapshot.entities) == len(c.authority.registry.permissions)
    assert sum(e.entity_type == "SkillMethod" for e in snapshot.entities) == 5
    methods = c.runtime.method_world_for_state(snapshot.state_ref, scope=snapshot.state.scope)
    assert len(methods.primitives) == 5 and not methods.may_execute
    bridge = WorldContextIntegration(store=c.runtime.store, facade=c.runtime.facade, output_port=c.port,
        token_budget=16000, repository_snapshot_refresher=lambda rc: c.sources.ensure_world(rc, now_ms=2000))
    prepared, prompt = bridge.prepare_composition_for_turn(run_context=c.rc, user_text=c.user,
        tool_source=c.sources.tool_source, registry=c.authority.registry, now_ms=2100)
    assert prepared.candidates.action_candidates and prepared.candidates.method_candidates
    assert any(row.primitive.action_id == "file.read" for row in prepared.candidates.action_candidates)
    assert any(row.primitive.action_id == "file.list" for row in prepared.candidates.action_candidates)
    assert "system_compiler_required=true" in prompt
    assert c.gateway.get_executable_composition_plan_for_request(c.rc.request_id, run_id=c.rc.run_id, generation=1) is None
    assert c.sources.ensure_world(c.rc, now_ms=2200).state_ref == snapshot.state_ref
    with pytest.raises(ValueError, match="NO_PUBLICATION_AUTHORITY"):
        c.runtime._method_revision_resolver(None, snapshot)
    # A fresh repository observation advances the cut. Both dictionaries must
    # rebind together, retaining no obsolete per-entity dependency revisions.
    from contracts.world_understanding.time import WorldTime
    from world_understanding.source_adapters import build_post_commit_source_envelope
    from world_understanding.context_output.world_reference_context import _address
    event = build_post_commit_source_envelope(source_kind="GIT_CODE", source_native_id="installed-reobserve",
        producer_ref="test.readonly-sensor", payload={"repository_observation": c.sources.observation.model_dump(mode="json")},
        source_time=WorldTime(valid_from_ms=2300, observed_at_ms=2300, recorded_at_ms=2300),
        scope=snapshot.state.scope, correlation_id="installed-reobserve", workspace_id=c.rc.workspace_id)
    assert c.runtime.facade.accept(event).processed
    rebound = c.sources.ensure_world(c.rc, now_ms=2400)
    assert rebound.state_ref != snapshot.state_ref
    assert len(rebound.dependencies.bindings) == len(snapshot.dependencies.bindings)
    for entity in rebound.entities:
        if entity.entity_type in {"ToolCapability", "SkillMethod"}:
            _address(entity, rebound)


def test_dirty_installed_source_genesis_and_candidates_remain_exact(installed_dirty, record_property):
    from v3.world_context_integration import WorldContextIntegration
    c = installed_dirty
    observation = c.sources.observation
    assert len(observation.changes) >= 162
    assert any(row.path == "src/total_gateway/installed_composition_sources.py"
               for row in observation.files)
    assert sum(not row.tracked for row in observation.files) >= 161
    assert c.runtime._closure.max_records == 100_000
    started = time.monotonic()
    snapshot = c.sources.ensure_world(c.rc, now_ms=2000)
    elapsed = time.monotonic() - started
    record_property("dirty_change_count", len(observation.changes))
    record_property("genesis_and_catalog_seconds", round(elapsed, 6))
    assert sum(row.entity_type == "ToolCapability" for row in snapshot.entities) == len(c.authority.registry.permissions)
    assert sum(row.entity_type == "SkillMethod" for row in snapshot.entities) == 5
    assert c.sources.ensure_world(c.rc, now_ms=2100).state_ref == snapshot.state_ref
    bridge = WorldContextIntegration(store=c.runtime.store, facade=c.runtime.facade, output_port=c.port,
        token_budget=16000, repository_snapshot_refresher=lambda rc: c.sources.ensure_world(rc, now_ms=2200))
    prepared, prompt = bridge.prepare_composition_for_turn(run_context=c.rc, user_text=c.user,
        tool_source=c.sources.tool_source, registry=c.authority.registry, now_ms=2300)
    assert prepared.candidates.action_candidates and prepared.candidates.method_candidates
    assert "system_compiler_required=true" in prompt
    assert c.gateway.get_executable_composition_plan_for_request(c.rc.request_id,
        run_id=c.rc.run_id, generation=1) is None
    # Dirty does not mean mutable source authority: later byte drift still fails.
    target = c.root / "src/total_gateway/installed_composition_sources.py"
    target.write_bytes(target.read_bytes() + b"# changed after source archive\n")
    with pytest.raises(ValueError, match="CHECKOUT_CHANGED_RESTART_REQUIRED"):
        c.sources.tool_source.load(c.authority.registry)


def test_archive_tamper_and_wrong_principal_fail_closed(installed):
    c = installed
    wrong = SimpleNamespace(**{**vars(c.rc), "principal_scope_hash": "f" * 64})
    with pytest.raises(ValueError, match="REQUEST_SCOPE_UNAVAILABLE"):
        c.sources.ensure_world(wrong, now_ms=2000)
    archive = c.sources.tool_source.archive_path
    archive.chmod(0o644)
    archive.write_bytes(archive.read_bytes() + b"tamper")
    archive.chmod(0o444)
    with pytest.raises(ValueError, match="ARCHIVE_CHANGED"):
        c.sources.tool_source.load(c.authority.registry)


def test_source_drift_does_not_silently_upgrade_running_plan(installed, monkeypatch):
    import total_gateway.installed_composition_sources as module
    c = installed
    real = module._installed_inputs(c.root)
    monkeypatch.setattr(module, "_installed_inputs", lambda _root: replace(real, source_inputs_sha256="f" * 64))
    with pytest.raises(ValueError, match="CHECKOUT_CHANGED_RESTART_REQUIRED"):
        c.sources.tool_source.load(c.authority.registry)
