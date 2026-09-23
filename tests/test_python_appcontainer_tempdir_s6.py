"""Real bundled Python startup, nested children, and 0700 AppContainer ACLs."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from omni_body_skill.tools import sandbox_runtime as sandbox

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="actual Windows AppContainer required")
@pytest.mark.parametrize("entry", ["script", "module"])
def test_private_script_and_unittest_can_import_sibling_without_host_sys_path(tmp_path, entry):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sibling.py").write_text("VALUE = 42\n", encoding="utf-8")
    (workspace / "test_sibling.py").write_text(
        "import sibling,unittest,sys,os\n"
        "from pathlib import Path\n"
        "assert all(not str(p).startswith(" + repr(str(workspace)) + ") for p in sys.path)\n"
        "class Check(unittest.TestCase):\n"
        " def test_value(self): self.assertEqual(sibling.VALUE,42)\n"
        "if __name__ == '__main__': unittest.main()\n", encoding="utf-8")
    runner = sandbox.SandboxRunner(workspace, tmp_path/"state", tmp_path/"trash", sandbox.SandboxLimits(timeout_seconds=20))
    args = [str(workspace/"test_sibling.py")] if entry == "script" else ["-m","unittest","test_sibling","-v"]
    result = runner.run([sys.executable,*args],require_os_containment=True)
    assert result["returncode"] == 0, result["stderr"]
    assert "Ran 1 test" in result["stderr"] and "OK" in result["stderr"]
    assert result["containment"] == "windows-appcontainer"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows AppContainer required")
def test_native_temporary_directory_and_nested_python_use_private_acl(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "host-only.txt"
    outside.write_text("must stay private")
    script = r'''
import os,sys,json,tempfile,pathlib,subprocess
assert getattr(os.mkdir, '_tiangong_appcontainer_private_acl', False)
try: os.mkdir(S6_FORBIDDEN_DIRECTORY,0o700)
except (PermissionError,FileNotFoundError): pass
else: raise AssertionError('0700 compatibility granted host write access')
def check_temp():
    with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
        p=pathlib.Path(folder,'nested')
        p.mkdir(mode=0o700)
        f=p/'data.json'
        f.write_text('{"value":42}',encoding='utf-8')
        assert json.loads(f.read_text())['value']==42
        assert list(p.iterdir()) == [f]
        try: os.mkdir(p,0o700)
        except FileExistsError: pass
        else: raise AssertionError('existing directory error lost')
    assert not pathlib.Path(folder).exists()
check_temp()
if '--child' not in sys.argv:
    child=subprocess.run([sys.executable,__file__,'--child'],capture_output=True,text=True,timeout=15)
    assert child.returncode==0,(child.stdout,child.stderr)
    assert 'child-tempdir-ok' in child.stdout
    pathlib.Path('result.json').write_text('{"nested_child_passed":true}',encoding='utf-8')
print('child-tempdir-ok')
'''
    script = script.replace("S6_FORBIDDEN_DIRECTORY", repr(str(tmp_path / "forbidden-host-directory")))
    (workspace / "run.py").write_text(script, encoding="utf-8")
    runner = sandbox.SandboxRunner(workspace, tmp_path / "state", tmp_path / "trash", sandbox.SandboxLimits(timeout_seconds=30))
    result = runner.run([sys.executable, str(workspace / "run.py")], require_os_containment=True)
    assert result["returncode"] == 0, result
    assert result["containment"] == "windows-appcontainer"
    assert result["network"] == "denied"
    assert result["changed_files"] == ["result.json"]
    assert json.loads((workspace / "result.json").read_text())["nested_child_passed"] is True
    assert not (tmp_path / "forbidden-host-directory").exists()


def test_bundled_host_python_retains_native_mkdir():
    result = subprocess.run([sys.executable,"-B","-c","import os;assert os.mkdir.__module__ == 'nt' if os.name == 'nt' else True;assert not getattr(os.mkdir,'_tiangong_appcontainer_private_acl',False);print('host-unmodified')"],
                            capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    assert "host-unmodified" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="actual Windows AppContainer required")
def test_private_0700_directories_are_mutually_denied_between_concurrent_sids(tmp_path, monkeypatch):
    observed = {}
    launch = sandbox._run_windows_appcontainer
    def instrument(command, cwd, env, limits, sandbox_root, **kwargs):
        marker = next(value for value in ("S6_SID_LEFT", "S6_SID_RIGHT") if value in str(command))
        observed[marker] = (cwd, sandbox_root / "stdout.bin")
        return launch(command, cwd, env, limits, sandbox_root, **kwargs)
    monkeypatch.setattr(sandbox, "_run_windows_appcontainer", instrument)
    def run(marker):
        base = tmp_path / marker
        workspace = base / "workspace"
        workspace.mkdir(parents=True)
        runner = sandbox.SandboxRunner(workspace, base/"state", base/"trash", sandbox.SandboxLimits(timeout_seconds=25))
        code = r'''
import tempfile,pathlib,json,time,shutil
folder=tempfile.mkdtemp(dir=pathlib.Path.cwd())
secret=pathlib.Path(folder,'private.txt');secret.write_text('same SID only')
print(json.dumps(str(secret)),flush=True)
peer=pathlib.Path('peer.json')
while not peer.exists():time.sleep(.025)
target=json.loads(peer.read_text())
try:pathlib.Path(target).read_text()
except (PermissionError,FileNotFoundError):pass
else:raise AssertionError('another AppContainer SID accessed a 0700 directory')
assert secret.read_text()=='same SID only'
peer.unlink();shutil.rmtree(folder)
pathlib.Path('passed.txt').write_text('peer denied')
'''
        return runner.run([sys.executable,"-c",code+"\n# "+marker],require_os_containment=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, key) for key in ("S6_SID_LEFT", "S6_SID_RIGHT")]
        secrets = {}
        deadline = time.monotonic()+15
        while len(secrets)<2 and time.monotonic()<deadline:
            for marker, (_, output) in tuple(observed.items()):
                if output.exists():
                    content=output.read_text(encoding="utf-8").strip()
                    if content: secrets[marker]=json.loads(content.splitlines()[0])
            time.sleep(.025)
        if len(secrets) != 2:
            pytest.fail(json.dumps([{k: f.result()[k] for k in ("returncode", "stdout", "stderr")} for f in futures if f.done()], ensure_ascii=False))
        for marker, (cwd, _) in tuple(observed.items()):
            other = "S6_SID_RIGHT" if marker=="S6_SID_LEFT" else "S6_SID_LEFT"
            (cwd/"peer.json").write_text(json.dumps(secrets[other]),encoding="utf-8")
        for future in futures:
            result=future.result(timeout=30)
            assert result["returncode"]==0, result
            assert result["containment"]=="windows-appcontainer"
            assert result["changed_files"]==["passed.txt"]


def test_runtime_installer_preserves_prior_sitecustomize_and_is_idempotent(tmp_path):
    spec = importlib.util.spec_from_file_location("install_compat_test", ROOT / "scripts/install-python-appcontainer-compat.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    runtime = tmp_path / "app/runtime/python312"
    runtime.mkdir(parents=True)
    (runtime / "python312._pth").write_text(".\nimport site\n")
    original = b"# existing application startup\r\nexisting_value = 42\r\n"
    startup = runtime / "sitecustomize.py"
    startup.write_bytes(original)
    canonical = tmp_path / "src/omni_body_skill/tools/windows_python_compat.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes((ROOT / "src/omni_body_skill/tools/windows_python_compat.py").read_bytes())
    assert installer.install(tmp_path, check=True)["ok"] is False
    assert installer.install(tmp_path, backup_dir=tmp_path/"backup")["ok"]
    assert startup.read_bytes().startswith(original)
    first = startup.read_bytes()
    assert installer.install(tmp_path)["ok"]
    assert startup.read_bytes() == first
    assert installer.install(tmp_path, check=True)["ok"]
    assert any(p.read_bytes()==original for p in (tmp_path/"backup").iterdir())
