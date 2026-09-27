"""Closed Gateway stores must not remain callable through the embedded kernel."""
from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import DEFAULT, Mock

import pytest


def test_epoch_providers_survive_failed_close_and_detach_before_store_close(tmp_path, monkeypatch):
    from total_gateway.bootstrap import GatewayConfig
    from total_gateway.runtime import GatewayRuntime
    from v3.run_context import bind_run_context
    from v3.runtime_turn_orchestration import TurnLoopState
    from v3.simple_chain import kernel

    root = Path(__file__).resolve().parents[1]
    for name, child in (
        ("APPDATA", "appdata"), ("TIANGONG_DOCUMENTS_PATH", "documents"),
        ("TIANGONG_LIFE_DATA_ROOT", "life"), ("TIANGONG_LIFE_RUNTIME_ROOT", "life-runtime"),
        ("TIANGONG_WORLD_STATE_ROOT", "world"),
        ("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT", "run-state"),
    ):
        monkeypatch.setenv(name, str(tmp_path / child))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = GatewayRuntime.start(GatewayConfig(
        environment="test", deployment_mode="embedded", port=0,
        state_root=tmp_path / "gateway", min_free_bytes=1_048_576,
        backend_internal_token="epoch-close-test-" + "x" * 48,
        release_source_root=root, workspace_root=workspace, skill_root=root / "dictionaries",
    ))
    continuity = kernel._SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER
    regenerative = kernel._SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER
    assert callable(continuity) and callable(regenerative)
    closed_store = Mock(wraps=runtime.store.close)
    monkeypatch.setattr(runtime.store, "close", closed_store)
    try:
        # Simulate failure to acquire the execution lane; the real shutdown
        # ordering must leave its authorities and callbacks usable for retry.
        busy = Mock()
        busy.acquire.return_value = False
        with monkeypatch.context() as blocked:
            blocked.setattr(runtime.backend_service.qiaojie, "_core_execution_lock", busy)
            with pytest.raises(RuntimeError, match="execution failed to close"):
                runtime.close()
        busy.release.assert_not_called()
        closed_store.assert_not_called()
        assert kernel._SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER is continuity
        assert kernel._SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER is regenerative

        def assert_detached():
            assert kernel._SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER is None
            assert kernel._SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER is None
            return DEFAULT

        closed_store.side_effect = assert_detached
        runtime.close()
        closed_store.assert_called_once()
        # Re-closing the old backend must not erase a later owner's bindings.
        later = Mock()
        with monkeypatch.context() as rebound:
            rebound.setattr(kernel, "_SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER", later)
            rebound.setattr(kernel, "_SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER", later)
            runtime.backend_service.close()
            assert kernel._SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER is later
            assert kernel._SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER is later

        # A subsequent local checkpoint must persist and roll over without
        # calling the already closed Gateway. This is the original CI failure.
        with bind_run_context(None):
            state = TurnLoopState(action_rounds=75, epoch_action_rounds=75)
            run = kernel._simple_chain_new_run_state("after-closed-gateway", "session")
            assert kernel._simple_chain_checkpoint_continue(
                run, state, requested=0, loop_started_at=time.monotonic(), source="epoch_turn_budget",
            ), run.get("continuation")
        assert state.action_rounds == 75 and state.epoch_index == 1
        saved = kernel._simple_chain_load_run_state("after-closed-gateway")
        assert saved["continuation"]["status"] == "continued"
        assert saved["budget"]["global_tool_rounds"] == 75
    finally:
        closed_store.side_effect = None
        runtime.close()
