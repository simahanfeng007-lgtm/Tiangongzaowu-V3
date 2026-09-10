"""P10 R3-B: external legacy compatibility surfaces forward usage to one Life journal."""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts import canonical_sha256
from life_service.legacy_learning_migration import (
    EXTERNAL_COMPATIBILITY_ENTRYPOINTS,
    LEGACY_MUTATION_ENTRYPOINTS,
    R3A_LEGACY_MUTATION_ENTRYPOINTS,
    apply_usage_coverage,
    apply_usage_window,
    legacy_migration_summary,
)
from tests.test_learning_publication_freeze_p10 import life


def _stop(value):
    if value.scheduler is not None:
        value.scheduler.stop(timeout_seconds=2)


@pytest.fixture(autouse=True)
def _clear_backend_observer():
    from v3.legacy_learning_telemetry import set_legacy_learning_usage_observer
    set_legacy_learning_usage_observer(None)
    try:
        yield
    finally:
        set_legacy_learning_usage_observer(None)


def test_r3b_coverage_extension_is_not_backdated():
    scope = {}
    apply_usage_window(scope, {
        "schema": "tiangong.life.legacy-learning-usage-window.v1",
        "started_at_ms": 100,
        "coverage_sha256": canonical_sha256(tuple(sorted(R3A_LEGACY_MUTATION_ENTRYPOINTS))),
    })
    first = legacy_migration_summary(scope, now_ms=500)
    assert set(first["instrumented_entrypoints"]) == set(R3A_LEGACY_MUTATION_ENTRYPOINTS)
    assert set(first["uninstrumented_compatibility_surfaces"]) == set(EXTERNAL_COMPATIBILITY_ENTRYPOINTS)
    assert first["full_coverage_window_ms"] == 0
    assert first["zero_usage_proven"] is False

    assert apply_usage_coverage(scope, {
        "schema": "tiangong.life.legacy-learning-usage-coverage.v1",
        "started_at_ms": 300,
        "entrypoints": sorted(EXTERNAL_COMPATIBILITY_ENTRYPOINTS),
    }) is True
    second = legacy_migration_summary(scope, now_ms=500)
    assert set(second["instrumented_entrypoints"]) == set(LEGACY_MUTATION_ENTRYPOINTS)
    assert second["uninstrumented_compatibility_surfaces"] == ()
    assert second["full_coverage_window_ms"] == 200
    assert second["zero_usage_proven"] is False
    assert second["zero_usage_blockers"] == ("INDEPENDENT_REVIEWED_OBSERVATION_WINDOW_REQUIRED",)
    # A replay/retry can never move an already measured surface backwards.
    assert apply_usage_coverage(scope, {
        "schema": "tiangong.life.legacy-learning-usage-coverage.v1",
        "started_at_ms": 150,
        "entrypoints": sorted(EXTERNAL_COMPATIBILITY_ENTRYPOINTS),
    }) is False
    assert set(legacy_migration_summary(scope, now_ms=500)["coverage_started_at_ms"].values()) == {100, 300}


def test_external_bridge_records_all_four_known_surfaces_in_existing_life_journal(life, tmp_path):
    from v3.duihua_qiaojie import QIAOJIE
    from v3.jineng.jirou_ceng import JirouCeng
    from v3.l0_ability_projection import build_l0_projection
    from v3.legacy_learning_telemetry import set_legacy_learning_usage_observer
    from v3.zhili.nengli_zhuche import NengliDingyi, NengliZhuche

    set_legacy_learning_usage_observer(life.observe_legacy_compatibility_entry)
    before_events = len(life.system.journal.events(life._active()["life_id"]))

    result = QIAOJIE.create_learning_card_from_request({"user_text": "legacy"})
    assert result["publication_frozen"] is True
    result = JirouCeng._xuexi_liucheng(topic="legacy")
    assert result["publication_frozen"] is True

    registry = NengliZhuche(tmp_path / "legacy-registry.json")
    with pytest.raises(ValueError, match="legacy_publication_frozen"):
        registry.zhuce_nengli(NengliDingyi("legacy", "qita"))

    projected = build_l0_projection({"id": "legacy-a", "status": "draft", "origin": "life_learning"})
    assert projected["schema"] == "tiangong.v3.l0_ability_projection.v1"

    summary = legacy_migration_summary(life._scope_state(), now_ms=10**15)
    assert summary["uninstrumented_compatibility_surfaces"] == ()
    for surface in EXTERNAL_COMPATIBILITY_ENTRYPOINTS:
        assert summary["by_entrypoint"][surface] >= 1
    assert summary["legacy_mutation_call_count"] >= 4
    assert len(life.system.journal.events(life._active()["life_id"])) >= before_events + 5
    assert summary["zero_usage_proven"] is False
    assert "OBSERVED_LEGACY_USAGE" in summary["zero_usage_blockers"]


def test_backend_telemetry_failure_never_changes_frozen_or_projection_behavior(tmp_path):
    from v3.duihua_qiaojie import QIAOJIE
    from v3.jineng.jirou_ceng import JirouCeng
    from v3.l0_ability_projection import build_l0_projection
    from v3.legacy_learning_telemetry import set_legacy_learning_usage_observer
    from v3.zhili.nengli_zhuche import NengliDingyi, NengliZhuche

    def broken(_surface):
        raise RuntimeError("telemetry unavailable")

    set_legacy_learning_usage_observer(broken)
    assert QIAOJIE.confirm_learning_card({"learning_id": "legacy"})["publication_frozen"] is True
    assert JirouCeng._xuexi_liucheng(topic="legacy")["publication_frozen"] is True
    with pytest.raises(ValueError, match="legacy_publication_frozen"):
        NengliZhuche(tmp_path / "registry.json").zhuce_nengli(NengliDingyi("legacy", "qita"))
    assert build_l0_projection({"id": "a", "status": "draft"})["schema"] == "tiangong.v3.l0_ability_projection.v1"


def test_external_usage_replays_after_projection_loss(life):
    from v3.duihua_qiaojie import QIAOJIE
    from v3.legacy_learning_telemetry import set_legacy_learning_usage_observer
    from life_service.embedded_runtime import EmbeddedLifeRuntime
    import json

    set_legacy_learning_usage_observer(life.observe_legacy_compatibility_entry)
    QIAOJIE.create_learning_card_from_request({"user_text": "legacy"})
    expected = legacy_migration_summary(life._scope_state(), now_ms=10**15)
    life_id = life._active()["life_id"]
    data_root, runtime_root = life.paths.data_root, life.paths.runtime_root
    # Locate the authoritative state file from the runtime itself; remove only projections.
    state_file = life.paths.state_file
    _stop(life)
    life.close()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    scope = saved["identity_states"][life_id]
    scope.pop("legacy_learning_usage", None)
    state_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    reopened = EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode="embedded")
    _stop(reopened)
    try:
        actual = legacy_migration_summary(reopened._scope_state(life_id), now_ms=10**15)
        assert actual["legacy_mutation_call_count"] == expected["legacy_mutation_call_count"]
        assert actual["by_entrypoint"] == expected["by_entrypoint"]
        assert actual["coverage_started_at_ms"] == expected["coverage_started_at_ms"]
    finally:
        reopened.close()


def test_gateway_start_installs_external_observer_and_full_coverage(tmp_path, monkeypatch):
    from total_gateway.bootstrap import GatewayConfig
    from total_gateway.runtime import GatewayRuntime

    root = Path(__file__).parents[1]
    for key, sub in [("APPDATA", "appdata"), ("TIANGONG_DOCUMENTS_PATH", "documents"),
                     ("TIANGONG_LIFE_DATA_ROOT", "life-data"), ("TIANGONG_LIFE_RUNTIME_ROOT", "life-runtime"),
                     ("TIANGONG_WORLD_STATE_ROOT", "world-state")]:
        monkeypatch.setenv(key, str(tmp_path / sub))
    workspace = tmp_path / "workspace"; workspace.mkdir()
    config = GatewayConfig(environment="test", deployment_mode="embedded", port=0,
        state_root=tmp_path / "gateway-root", min_free_bytes=1_048_576,
        backend_internal_token="test-r3b-token-" + ("x" * 48), release_source_root=root,
        workspace_root=workspace, skill_root=root / "app/backend/tiangong-backend/_internal/omni_body_skill")
    runtime = GatewayRuntime.start(config)
    try:
        summary = legacy_migration_summary(runtime.life_service._scope_state(), now_ms=10**15)
        assert summary["uninstrumented_compatibility_surfaces"] == ()
        assert set(summary["instrumented_entrypoints"]) == set(LEGACY_MUTATION_ENTRYPOINTS)
        assert summary["full_coverage_window_ms"] > 0
        assert summary["zero_usage_proven"] is False
        # Actual legacy backend call must land in the same Life journal.
        result = runtime.backend_service.qiaojie.create_learning_card_from_request({"user_text": "legacy"})
        assert result["publication_frozen"] is True
        after = legacy_migration_summary(runtime.life_service._scope_state(), now_ms=10**15)
        assert after["by_entrypoint"]["v3.duihua_qiaojie.legacy_learning_callbacks"] >= 1
    finally:
        runtime.close()
    from v3.legacy_learning_telemetry import observe_legacy_learning_usage
    assert observe_legacy_learning_usage("v3.duihua_qiaojie.legacy_learning_callbacks") is False


def test_usage_window_rejects_a_coverage_hash_for_a_different_surface_set():
    with pytest.raises(ValueError, match="legacy_usage_window_invalid"):
        apply_usage_window({}, {
            "schema": "tiangong.life.legacy-learning-usage-window.v1",
            "started_at_ms": 100,
            "coverage_sha256": canonical_sha256(tuple(sorted(LEGACY_MUTATION_ENTRYPOINTS))),
        })


def test_gateway_start_is_not_blocked_when_external_coverage_activation_temporarily_fails(tmp_path, monkeypatch):
    from life_service.embedded_runtime import EmbeddedLifeRuntime
    from total_gateway.bootstrap import GatewayConfig
    from total_gateway.runtime import GatewayRuntime

    original = EmbeddedLifeRuntime.activate_legacy_compatibility_telemetry
    calls = {"count": 0}
    def flaky(self, entrypoints=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected telemetry persistence failure")
        return original(self, entrypoints)
    monkeypatch.setattr(EmbeddedLifeRuntime, "activate_legacy_compatibility_telemetry", flaky)

    root = Path(__file__).parents[1]
    for key, sub in [("APPDATA", "appdata"), ("TIANGONG_DOCUMENTS_PATH", "documents"),
                     ("TIANGONG_LIFE_DATA_ROOT", "life-data"), ("TIANGONG_LIFE_RUNTIME_ROOT", "life-runtime"),
                     ("TIANGONG_WORLD_STATE_ROOT", "world-state")]:
        monkeypatch.setenv(key, str(tmp_path / sub))
    workspace = tmp_path / "workspace"; workspace.mkdir()
    config = GatewayConfig(environment="test", deployment_mode="embedded", port=0,
        state_root=tmp_path / "gateway-root", min_free_bytes=1_048_576,
        backend_internal_token="test-r3b-token-" + ("y" * 48), release_source_root=root,
        workspace_root=workspace, skill_root=root / "app/backend/tiangong-backend/_internal/omni_body_skill")
    runtime = GatewayRuntime.start(config)
    try:
        before = legacy_migration_summary(runtime.life_service._scope_state(), now_ms=10**15)
        assert set(before["uninstrumented_compatibility_surfaces"]) == set(EXTERNAL_COMPATIBILITY_ENTRYPOINTS)
        result = runtime.backend_service.qiaojie.create_learning_card_from_request({"user_text": "legacy"})
        assert result["publication_frozen"] is True
        after = legacy_migration_summary(runtime.life_service._scope_state(), now_ms=10**15)
        assert "v3.duihua_qiaojie.legacy_learning_callbacks" not in after["uninstrumented_compatibility_surfaces"]
        assert set(after["uninstrumented_compatibility_surfaces"]) == (
            set(EXTERNAL_COMPATIBILITY_ENTRYPOINTS) - {"v3.duihua_qiaojie.legacy_learning_callbacks"}
        )
        assert after["by_entrypoint"]["v3.duihua_qiaojie.legacy_learning_callbacks"] >= 1
        assert after["full_coverage_window_ms"] == 0
        assert calls["count"] >= 2
    finally:
        runtime.close()
