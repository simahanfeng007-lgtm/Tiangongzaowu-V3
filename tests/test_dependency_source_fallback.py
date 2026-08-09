from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-python-dependencies.py"


def _module():
    spec = importlib.util.spec_from_file_location("tiangong_dependency_installer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_dependency_installer_uses_primary_before_tuna(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    calls: list[dict[str, str] | None] = []

    def fake_run(_arguments, *, env=None):
        calls.append(env)
        return 1 if len(calls) == 1 else 0

    monkeypatch.setattr(module, "_run_pip", fake_run)
    module.install_with_fallback(["install", "example"], label="fixture")
    assert calls[0] is None
    assert calls[1] is not None
    assert calls[1]["PIP_INDEX_URL"] == module.TUNA_PYPI_INDEX
    assert "PIP_EXTRA_INDEX_URL" not in calls[1]


def test_python_dependency_fallback_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("TIANGONG_DISABLE_DEPENDENCY_FALLBACK", "1")
    monkeypatch.setattr(module, "_run_pip", lambda _arguments, *, env=None: 1)
    with pytest.raises(RuntimeError, match="fallback is disabled"):
        module.install_with_fallback(["install", "example"], label="fixture")
