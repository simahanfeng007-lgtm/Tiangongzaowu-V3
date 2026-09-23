"""Real local Windows process isolation; no model, App or desktop required."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from omni_body_skill.tools import sandbox_runtime as sandbox


def runner(tmp_path, **limits):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return sandbox.SandboxRunner(workspace, tmp_path / "state", tmp_path / "trash",
                                 sandbox.SandboxLimits(timeout_seconds=12, **limits))


native = pytest.mark.skipif(os.name != "nt", reason="actual Windows AppContainer required")


@native
def test_native_network_host_file_environment_and_success_receipt(tmp_path, monkeypatch):
    run = runner(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic host-only fixture", encoding="utf-8")
    monkeypatch.setenv("S6_TEST_API_KEY", "synthetic-not-a-credential")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    code = f"""import os,socket
from pathlib import Path
assert os.getenv('S6_TEST_API_KEY') is None
try:
    Path({str(outside)!r}).read_text()
except (PermissionError,FileNotFoundError): pass
else: raise AssertionError('host file readable')
s=socket.socket();s.settimeout(1)
assert s.connect_ex(('127.0.0.1',{port})) != 0, 'loopback network escaped'
Path('result.txt').write_text('isolated',encoding='utf-8')
print('process-result')
"""
    try:
        result = run.run([sys.executable, "-c", code], require_os_containment=True)
    finally:
        listener.close()
    assert result["returncode"] == 0, json.dumps(result)
    assert result["containment"] == "windows-appcontainer"
    assert result["network"] == "denied"
    assert result["receipt_role"] == "execution"
    assert result["commit_state"] == "committed"
    assert (run.workspace / "result.txt").read_text() == "isolated"
    assert not Path(result["sandbox_root"]).exists()


@native
def test_native_failed_process_does_not_commit_and_preserves_full_bounded_output(tmp_path):
    run = runner(tmp_path)
    (run.workspace / "original.txt").write_text("original")
    code = "from pathlib import Path;Path('original.txt').write_text('bad');Path('new.txt').write_text('bad');print('x'*9000);raise SystemExit(7)"
    result = run.run([sys.executable, "-c", code], require_os_containment=True)
    assert result["returncode"] == 7, result
    assert result["ok"] is False and result["commit_state"] == "discarded"
    assert len(result["stdout"]) > 9000 and not result["outputs_truncated"]
    assert result["changed_files"] == []
    assert (run.workspace / "original.txt").read_text() == "original"
    assert not (run.workspace / "new.txt").exists()


@native
@pytest.mark.parametrize("mode", ["timeout", "cancel", "output"])
def test_native_limits_kill_job_children_and_do_not_commit(tmp_path, monkeypatch, mode):
    run = runner(tmp_path, max_output_bytes=4096)
    observed = {}
    original = sandbox._run_windows_appcontainer

    def instrument(command, cwd, env, limits, sandbox_root, **kwargs):
        observed["root"] = sandbox_root
        return original(command, cwd, env, limits, sandbox_root, **kwargs)

    monkeypatch.setattr(sandbox, "_run_windows_appcontainer", instrument)
    child_pid = []

    def check():
        root = observed.get("root")
        output = None if root is None else root / "stdout.bin"
        if output is not None and output.exists():
            text = output.read_bytes()[:100].decode("ascii", errors="ignore")
            if text.startswith("PID="):
                child_pid[:] = [int(text.splitlines()[0][4:])]
        return bool(child_pid) if mode == "cancel" else False

    code = """import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(40)'])
print('PID='+str(p.pid),flush=True)
Path('must-not-commit.txt').write_text('private')
""" + ("time.sleep(.15);print('X'*10000,flush=True);time.sleep(40)" if mode == "output" else "time.sleep(40)")
    with pytest.raises(sandbox.SandboxError, match={"timeout": "sandbox_timeout", "cancel": "sandbox_cancelled", "output": "sandbox_process_output_limit"}[mode]):
        run.run([sys.executable, "-c", code], timeout_seconds=1 if mode == "timeout" else 10,
                require_os_containment=True, cancel_check=check)
    assert child_pid, "native child must actually have run before limit/cancellation"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, child_pid[0])
    if handle:
        code_value = ctypes.c_ulong()
        assert kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code_value))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        assert code_value.value != 259, "child still running after Job cleanup"
    assert not (run.workspace / "must-not-commit.txt").exists()


def test_broker_rejects_concurrent_host_edit_before_any_commit(tmp_path):
    real, private = tmp_path / "real", tmp_path / "private"
    real.mkdir(); private.mkdir()
    for root in (real, private):
        (root / "a.txt").write_text("original")
    before = sandbox._snapshot(private)
    (private / "a.txt").write_text("private update")
    (real / "a.txt").write_text("concurrent host update")
    with pytest.raises(sandbox.SandboxError, match="sandbox_destination_changed"):
        sandbox._merge_changes(private, real, before, max_changed_bytes=1024, trash_root=tmp_path / "trash")
    assert (real / "a.txt").read_text() == "concurrent host update"


def test_started_appcontainer_failure_never_runs_compatibility_fallback(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows launcher import")
    from omni_body_skill.tools import windows_appcontainer
    failure = RuntimeError("sandbox_cancelled")
    failure.execution_started = True
    monkeypatch.setenv("TIANGONG_SANDBOX_COMPAT", "1")
    monkeypatch.setattr(windows_appcontainer, "run_appcontainer", lambda *a, **k: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(sandbox, "_run_portable", lambda *a, **k: pytest.fail("uncontained duplicate execution"))
    with pytest.raises(sandbox.SandboxError, match="sandbox_cancelled"):
        sandbox._run_windows_appcontainer(["unused"], tmp_path, {}, sandbox.SandboxLimits(), tmp_path)


@native
def test_concurrent_invocation_cannot_read_another_private_workspace(tmp_path, monkeypatch):
    first_root, second_root = tmp_path / 'first', tmp_path / 'second'
    first_root.mkdir(); second_root.mkdir()
    first, second = runner(first_root), runner(second_root)
    ready, release = threading.Event(), threading.Event()
    observed = {}
    launch = sandbox._run_windows_appcontainer

    def instrument(command, cwd, env, limits, sandbox_root, **kwargs):
        if 'S6_FIRST_INVOCATION' in str(command):
            observed['root'] = sandbox_root
        return launch(command, cwd, env, limits, sandbox_root, **kwargs)

    monkeypatch.setattr(sandbox, '_run_windows_appcontainer', instrument)

    def check():
        root = observed.get('root')
        out = None if root is None else root / 'stdout.bin'
        if out is not None and out.exists():
            text = out.read_bytes().decode('utf-8', errors='ignore').strip()
            if text:
                observed['path'] = text
                ready.set()
        return release.is_set()

    def first_run():
        with pytest.raises(sandbox.SandboxError, match='sandbox_cancelled'):
            first.run([sys.executable, '-c',
                "from pathlib import Path;import time;# S6_FIRST_INVOCATION\n"
                "p=Path('private.txt');p.write_text('synthetic private fixture');print(str(p.absolute()),flush=True);time.sleep(40)"],
                timeout_seconds=35, require_os_containment=True, cancel_check=check)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_run)
        try:
            assert ready.wait(15), 'first isolated process did not run'
            result = second.run([sys.executable, '-c',
                "from pathlib import Path\ntry:\n Path(" + repr(observed['path']) + ").read_text()\n"
                "except (PermissionError,FileNotFoundError): pass\nelse: raise AssertionError('cross-invocation read')"],
                require_os_containment=True)
            assert result['returncode'] == 0, result
        finally:
            release.set()
            future.result(timeout=15)


@native
def test_native_memory_and_process_budget_are_enforced(tmp_path):
    memory_root, process_root = tmp_path / 'memory', tmp_path / 'process'
    memory_root.mkdir(); process_root.mkdir()
    memory = runner(memory_root, max_memory_bytes=128*1024*1024)
    result = memory.run([sys.executable, '-c',
        "try:\n x=bytearray(256*1024*1024)\nexcept MemoryError: print('memory-bounded')\n"
        "else: raise AssertionError('memory limit missing')"], require_os_containment=True)
    assert result['returncode'] == 0 and 'memory-bounded' in result['stdout'], result
    process = runner(process_root, max_processes=2)
    code = """import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(40)'])
try:
    second=subprocess.Popen([sys.executable,'-c','import time;time.sleep(40)'])
except OSError:
    pass
else:
    time.sleep(.3)
    assert second.poll() is not None, 'process limit missing'
finally:
    p.terminate();p.wait()
print('process-bounded')
"""
    result = process.run([sys.executable, '-c', code], require_os_containment=True)
    assert result['returncode'] == 0 and 'process-bounded' in result['stdout'], result
