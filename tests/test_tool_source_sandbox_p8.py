from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_body_skill.tools import sandbox_runtime as sandbox


def fake_windows(monkeypatch, *, launch=None, storage=None):
    monkeypatch.setattr(sandbox, "os", SimpleNamespace(name="nt", environ={"TIANGONG_SANDBOX_COMPAT": "1"}))
    module = SimpleNamespace(run_appcontainer=launch, appcontainer_storage_root=storage)
    monkeypatch.setitem(sys.modules, "omni_body_skill.tools.windows_appcontainer", module)
    portable = Mock(side_effect=AssertionError("uncontained fallback must not run"))
    monkeypatch.setattr(sandbox, "_run_portable", portable)
    return portable


def test_required_os_containment_rejects_portable_before_workspace_copy(tmp_path, monkeypatch):
    runner = sandbox.SandboxRunner(tmp_path, tmp_path / "state", tmp_path / "trash")
    monkeypatch.setattr(sandbox, "os", SimpleNamespace(name="posix", environ={}))
    prepare = Mock(side_effect=AssertionError("preparation must not start"))
    monkeypatch.setattr(sandbox, "_copy_workspace", prepare)
    with pytest.raises(sandbox.SandboxError, match="os_containment_unavailable"):
        runner.run([sys.executable, "-c", "raise AssertionError('uncontained')"], require_os_containment=True)
    prepare.assert_not_called()


@pytest.mark.parametrize("invalid", [None, "true", 1])
def test_containment_requirement_is_an_exact_boolean(tmp_path, invalid):
    runner = sandbox.SandboxRunner(tmp_path, tmp_path / "state", tmp_path / "trash")
    with pytest.raises(sandbox.SandboxError, match="containment_requirement_invalid"):
        runner.run(["never-launch"], require_os_containment=invalid)


@pytest.mark.parametrize("launch", [
    Mock(side_effect=OSError("test containment unavailable")),
    Mock(return_value=(0xC0000142, b"", b"", "windows-appcontainer")),
])
def test_required_containment_never_uses_enabled_compatibility_fallback(tmp_path, monkeypatch, launch):
    portable = fake_windows(monkeypatch, launch=launch)
    with pytest.raises(sandbox.SandboxError, match="windows_appcontainer_unavailable"):
        sandbox._run_windows_appcontainer(["never-run-portable"], tmp_path, {}, sandbox.SandboxLimits(),
                                          tmp_path, require_os_containment=True)
    portable.assert_not_called()


def test_storage_failure_also_rejects_compatibility_before_launch(tmp_path, monkeypatch):
    runner = sandbox.SandboxRunner(tmp_path, tmp_path / "state", tmp_path / "trash")
    portable = fake_windows(monkeypatch, storage=Mock(side_effect=OSError("no storage")))
    with pytest.raises(sandbox.SandboxError, match="storage_unavailable"):
        runner.run(["never-run-portable"], require_os_containment=True)
    portable.assert_not_called()


def test_ordinary_legacy_compatibility_mode_is_unchanged(tmp_path, monkeypatch):
    fake_windows(monkeypatch, launch=Mock(side_effect=OSError("no containment")))
    portable = Mock(return_value=(0, b"result", b"", "portable-resource-sandbox"))
    monkeypatch.setattr(sandbox, "_run_portable", portable)
    result = sandbox._run_windows_appcontainer(["compat-command"], tmp_path, {}, sandbox.SandboxLimits(), tmp_path)
    assert result == (0, b"result", b"", "compat-workspace-job-sandbox")
    portable.assert_called_once()


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows AppContainer backend")
@pytest.mark.ci_fragile
def test_real_required_appcontainer_denies_host_source_and_parent_secret(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "host-only.txt"
    outside.write_text("host secret", encoding="utf-8")
    monkeypatch.setenv("P8_SOURCE_TEST_SECRET", "must-not-inherit")
    # Explicit compat must not override the source-build requirement.
    monkeypatch.setenv("TIANGONG_SANDBOX_COMPAT", "1")
    runner = sandbox.SandboxRunner(workspace, tmp_path / "state", tmp_path / "trash")
    code = (
        "import os\nfrom pathlib import Path\n"
        "assert os.getenv('P8_SOURCE_TEST_SECRET') is None\n"
        f"try:\n    Path({str(outside)!r}).read_text()\n"
        "except (PermissionError, FileNotFoundError):\n    pass\n"
        "else:\n    raise AssertionError('host file was readable')\n"
        "Path('isolated-result.txt').write_text('isolated', encoding='utf-8')\n"
    )
    result = runner.run([sys.executable, "-c", code], require_os_containment=True)
    assert result["ok"] is True, result
    assert result["containment"] == "windows-appcontainer"
    assert result["network"] == "denied"
    assert (workspace / "isolated-result.txt").read_text(encoding="utf-8") == "isolated"
    assert outside.read_text(encoding="utf-8") == "host secret"


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows AppContainer backend")
@pytest.mark.ci_fragile
def test_real_required_appcontainer_copies_and_reads_deep_source_without_machine_changes(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / ("a" * 55) / ("b" * 55) / "module.txt"
    source.parent.mkdir(parents=True)
    source.write_text("immutable input", encoding="utf-8")
    runner = sandbox.SandboxRunner(workspace, tmp_path / "state", tmp_path / "trash")
    code = (
        "from pathlib import Path; import sys; "
        "assert len(sys.argv[1]) > 260; "
        "assert sys.argv[1].startswith('\\\\\\\\?\\\\'); "
        "assert Path(sys.argv[1]).read_text(encoding='utf-8') == 'immutable input'; "
        "Path('result.txt').write_text('deep source read', encoding='utf-8')"
    )
    result = runner.run([sys.executable, "-c", code, str(source)], require_os_containment=True)
    assert result["ok"] is True, result
    assert result["containment"] == "windows-appcontainer"
    assert source.read_text(encoding="utf-8") == "immutable input"
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "deep source read"
    assert not sandbox._windows_long_path(Path(result["sandbox_root"])).exists()
