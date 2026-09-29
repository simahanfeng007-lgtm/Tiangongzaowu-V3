from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

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


def test_embedded_python_uses_locked_setuptools_before_nonisolated_sdist_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _module()
    (tmp_path / "requirements-release.lock").write_text("-r requirements-source.lock\n", encoding="utf-8")
    (tmp_path / "requirements-source.lock").write_text("setuptools==81.0.0\nwheel==0.45.1\npyautogui==0.9.54\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools==81.0.0"]\nbuild-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "install_with_fallback", lambda arguments, *, label: calls.append(list(arguments)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT), "--embedded-python", "--upgrade-pip",
            "--requirements", str(tmp_path / "requirements-release.lock"),
            "--project", str(tmp_path),
        ],
    )

    assert module.main() == 0
    assert calls[0] == ["--disable-pip-version-check", "install", "--upgrade", "pip"]
    assert calls[1] == ["--disable-pip-version-check", "install", "--only-binary=:all:", "setuptools==81.0.0", "wheel==0.45.1"]
    assert calls[2][:3] == ["--disable-pip-version-check", "install", "--no-build-isolation"]
    assert calls[2][3:] == ["-r", str(tmp_path / "requirements-release.lock")]
    assert calls[3][:4] == ["--disable-pip-version-check", "install", "--no-build-isolation", "--no-deps"]
    assert calls[3][4:] == [str(tmp_path)]


def test_embedded_python_build_backend_matches_real_locks() -> None:
    module = _module()
    assert module._embedded_build_pins(ROOT / "requirements-release.lock", ROOT) == ("setuptools==81.0.0", "wheel==0.45.1")


def test_regular_python_installer_keeps_build_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _module()
    requirements = tmp_path / "requirements-release.lock"
    requirements.write_text("pyautogui==0.9.54\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "install_with_fallback", lambda arguments, *, label: calls.append(list(arguments)))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--requirements", str(requirements)])

    assert module.main() == 0
    assert calls == [["--disable-pip-version-check", "install", "-r", str(requirements)]]


def test_embedded_python_rejects_build_backend_pin_drift_before_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _module()
    (tmp_path / "requirements-release.lock").write_text("-r requirements-source.lock\n", encoding="utf-8")
    (tmp_path / "requirements-source.lock").write_text("setuptools==81.0.0\nwheel==0.45.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools==82.0.0"]\nbuild-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "install_with_fallback", lambda arguments, *, label: pytest.fail("pip ran before pin validation"))
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--embedded-python", "--upgrade-pip", "--requirements", str(tmp_path / "requirements-release.lock"), "--project", str(tmp_path)],
    )

    with pytest.raises(RuntimeError, match="differs from the project's build-system"):
        module.main()
