"""The explicit CI installer is scoped to the current disposable tool cache."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "ci_python_compat_installer", ROOT / "scripts/install-python-appcontainer-compat.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root = tmp_path / "source"
    canonical = root / "src/omni_body_skill/tools/windows_python_compat.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes((ROOT / "src/omni_body_skill/tools/windows_python_compat.py").read_bytes())
    cache = tmp_path / "tool-cache"
    runtime = cache / "Python/3.12.10/x64"
    site = runtime / "Lib/site-packages"
    site.mkdir(parents=True)
    executable = runtime / "python.exe"
    executable.write_bytes(b"fixture-only-not-executed")
    fake_sys = SimpleNamespace(platform="win32", version_info=(3, 12, 10),
        flags=SimpleNamespace(no_site=0), prefix=str(runtime), base_prefix=str(runtime),
        executable=str(executable), modules={}, path=[str(runtime / "Lib"), str(site)])
    monkeypatch.setattr(installer, "sys", fake_sys)
    monkeypatch.setattr(installer, "sysconfig", SimpleNamespace(get_path=lambda _: str(site)))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(cache))
    return installer, root, runtime, site


def test_ci_install_preserves_existing_startup_and_checks_exact_source_bytes(setup, tmp_path):
    installer, root, runtime, site = setup
    original = b"# existing CI startup\r\nexisting_value = 42\r\n"
    startup = site / "sitecustomize.py"
    startup.write_bytes(original)
    assert installer.install(root, ci_current_runtime=True, check=True)["ok"] is False
    result = installer.install(root, ci_current_runtime=True, backup_dir=tmp_path / "backups")
    assert result["ok"] and result["runtime_kind"] == "ci-current-tool-cache"
    assert result["runtime"] == str(runtime)
    assert startup.read_bytes() == original + installer.BLOCK
    helper = site / "_tiangong_windows_python_compat.py"
    assert helper.read_bytes() == (root / "src/omni_body_skill/tools/windows_python_compat.py").read_bytes()
    assert installer.install(root, ci_current_runtime=True, check=True)["ok"]
    before = startup.read_bytes()
    assert installer.install(root, ci_current_runtime=True)["ok"]
    assert startup.read_bytes() == before
    assert any(path.read_bytes() == original for path in (tmp_path / "backups").iterdir())
    helper.write_bytes(b"stale helper")
    assert installer.install(root, ci_current_runtime=True, check=True)["ok"] is False


def test_ci_loaded_startup_inside_runtime_is_preserved_at_its_actual_location(setup):
    installer, root, runtime, site = setup
    startup = runtime / "Lib/sitecustomize.py"
    startup.write_bytes(b"# earlier startup\n")
    installer.sys.modules["sitecustomize"] = SimpleNamespace(__file__=str(startup))
    result = installer.install(root, ci_current_runtime=True)
    assert result["sitecustomize"] == str(startup)
    assert startup.read_bytes().startswith(b"# earlier startup\n")
    assert (startup.parent / "_tiangong_windows_python_compat.py").is_file()
    assert not (site / "sitecustomize.py").exists()


@pytest.mark.parametrize("case", ["no_actions", "no_cache", "outside_cache", "venv",
    "wrong_executable", "no_site", "outside_site", "outside_startup", "linux", "wrong_version",
    "startup_not_importable", "compiled_startup"])
def test_ci_installer_rejects_non_ci_or_unbound_runtime_before_any_write(setup, tmp_path, monkeypatch, case):
    installer, root, runtime, site = setup
    outside = tmp_path / "user-python"
    outside.mkdir()
    if case == "no_actions": monkeypatch.delenv("GITHUB_ACTIONS")
    elif case == "no_cache": monkeypatch.delenv("RUNNER_TOOL_CACHE")
    elif case == "outside_cache": monkeypatch.setenv("RUNNER_TOOL_CACHE", str(outside))
    elif case == "venv": installer.sys.base_prefix = str(outside)
    elif case == "wrong_executable":
        (outside / "python.exe").write_bytes(b"other")
        installer.sys.executable = str(outside / "python.exe")
    elif case == "no_site": installer.sys.flags.no_site = 1
    elif case == "outside_site": installer.sysconfig.get_path = lambda _: str(outside)
    elif case == "outside_startup":
        startup = outside / "sitecustomize.py"
        startup.write_bytes(b"# user startup must not change\n")
        installer.sys.modules["sitecustomize"] = SimpleNamespace(__file__=str(startup))
    elif case == "linux": installer.sys.platform = "linux"
    elif case == "wrong_version": installer.sys.version_info = (3, 13, 0)
    elif case == "startup_not_importable": installer.sys.path = [str(runtime)]
    elif case == "compiled_startup":
        startup = site / "sitecustomize.pyc"
        startup.write_bytes(b"compiled startup is not replaceable text")
        installer.sys.modules["sitecustomize"] = SimpleNamespace(__file__=str(startup))
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        installer.install(root, ci_current_runtime=True)
    after = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_ci_environment_does_not_change_default_bundled_target(setup):
    installer, root, runtime, site = setup
    bundled = root / "app/runtime/python312"
    bundled.mkdir(parents=True)
    (bundled / "python312._pth").write_bytes(b".\nimport site\n")
    result = installer.install(root)
    assert result["runtime"] == str(bundled)
    assert result["runtime_kind"] == "bundled"
    assert (bundled / "sitecustomize.py").exists()
    assert not (site / "sitecustomize.py").exists()
