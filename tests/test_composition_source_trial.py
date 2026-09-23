"""Persistent operator-selected source trial routing, without App or model calls."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.composition_profile import WORKSPACE_PYTHON_PROFILE_ID
from total_gateway.composition_source_trial import (
    install_source_trial_profile, load_source_trial_profile, is_dictionary_request,
)


def test_source_trial_survives_reload_and_preserves_explicit_route(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    assert load_source_trial_profile(state_root=state, workspace_root=workspace) is None
    path = install_source_trial_profile(state_root=state, workspace_root=workspace)
    first = load_source_trial_profile(state_root=state, workspace_root=workspace)
    second = load_source_trial_profile(state_root=state, workspace_root=workspace)
    assert first == second
    assert first.execution_profile_id == WORKSPACE_PYTHON_PROFILE_ID
    assert first.activation_kind == "source-installation-trial"
    assert first.route == "explicit_dictionary"
    assert path.is_file()


@pytest.mark.parametrize("text,expected", [
    ("字典任务：写入README.md", True), (" 字典任务:读取文件", True),
    ("/dictionary read file", True), ("请写代码并运行", False),
    ("解释字典任务：如何工作", False), ("dictionary", False),
])
def test_only_explicit_source_trial_route(text, expected):
    assert is_dictionary_request(text) is expected


def test_profile_tamper_and_other_workspace_fail_closed(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    state = tmp_path / "state"
    file = install_source_trial_profile(state_root=state, workspace_root=old)
    with pytest.raises(ValueError, match="workspace binding"):
        load_source_trial_profile(state_root=state, workspace_root=new)
    value = json.loads(file.read_text("utf-8"))
    value["workspace_root"] = str(new)
    file.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        load_source_trial_profile(state_root=state, workspace_root=new)
    with pytest.raises(ValueError, match="supported fixed"):
        install_source_trial_profile(state_root=state, workspace_root=old, profile_id="model.allow-all")


def test_ordinary_request_does_not_load_trial_or_acquire_worker(tmp_path, monkeypatch):
    from total_gateway.desktop_composition import InstalledDesktopCompositionPlanner
    monkeypatch.setattr("total_gateway.composition_mode_runtime.load_mode_config", lambda: None)
    monkeypatch.setattr("total_gateway.composition_mode_runtime.current_turn_policy",
                        lambda: SimpleNamespace(may_prepare=True, may_register=True))
    monkeypatch.setattr("total_gateway.composition_source_trial.load_source_trial_profile",
                        lambda **_: pytest.fail("ordinary message must not select source trial"))
    monkeypatch.setenv("TIANGONG_COMPOSITION_DESKTOP_SCOPE", "all")
    planner = InstalledDesktopCompositionPlanner(config=SimpleNamespace(state_root=tmp_path, workspace_root=tmp_path),
        store=None, backend=None, worker_provider=lambda: pytest.fail("ordinary message acquired dictionary worker"))
    assert planner(SimpleNamespace(envelope=SimpleNamespace(text="请写代码并运行")), None) is False
