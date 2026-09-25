"""Actual Windows capability reuse, isolation and cancellable ACL provisioning."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows capability required")


def test_cold_provision_is_serialized_and_stale_receipt_does_not_authorize(tmp_path, monkeypatch):
    from omni_body_skill.tools import windows_appcontainer as native
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "child").mkdir()
    (runtime / "child/library.txt").write_text("readable library")
    update = native._run_acl_update
    calls = []

    def observe(*args, **kwargs):
        calls.append(args)
        return update(*args, **kwargs)

    monkeypatch.setattr(native, "_run_acl_update", observe)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: native.prepare_runtime_access(runtime), range(3)))
    assert all(row["ok"] for row in results)
    assert len(calls) == 1
    assert sum(not row["reused"] for row in results) == 1
    receipt = (runtime / native.RUNTIME_ACCESS_RECEIPT).read_bytes()
    with native._runtime_read_capability(runtime) as sid:
        # A stale receipt must not accept a broader permission on the real ACL.
        update(runtime, native._sid_string(sid), "(OI)(CI)M", deadline=time.monotonic() + 10)
    assert (runtime / native.RUNTIME_ACCESS_RECEIPT).read_bytes() == receipt
    assert not native.prepare_runtime_access(runtime, check=True)["ok"]
    assert native.prepare_runtime_access(runtime)["ok"]
    assert native.prepare_runtime_access(runtime, check=True)["ok"]


def test_repeated_parallel_execution_never_rewrites_runtime_and_cannot_write_it(tmp_path, monkeypatch):
    from omni_body_skill.tools import windows_appcontainer as native
    from omni_body_skill.tools.sandbox_runtime import SandboxLimits, SandboxRunner
    runtime = Path(sys.executable).resolve().parent
    assert native.prepare_runtime_access(runtime)["ok"]
    receipt = runtime / native.RUNTIME_ACCESS_RECEIPT
    original = receipt.read_bytes()
    update = native._run_acl_update

    def reject_runtime_mutation(path, *args, **kwargs):
        assert path.resolve() != runtime, "warm task attempted a shared runtime ACL mutation"
        return update(path, *args, **kwargs)

    monkeypatch.setattr(native, "_run_acl_update", reject_runtime_mutation)
    script = (
        "from pathlib import Path;import decimal,json;from PIL import Image\n"
        f"p=Path({str(receipt)!r});assert p.read_bytes()\n"
        "try:\n f=p.open('r+b')\n"
        "except PermissionError: pass\n"
        "else:\n f.close();raise AssertionError('runtime writable')\n"
        "Path('answer.json').write_text(json.dumps({'sum':int(decimal.Decimal(17)+25)}))\n"
    )

    def execute(index):
        root = tmp_path / str(index)
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        result = SandboxRunner(workspace, root / "state", root / "trash",
            SandboxLimits(timeout_seconds=12)).run([sys.executable, "-c", script], require_os_containment=True)
        assert result["returncode"] == 0, result
        assert result["containment"] == "windows-appcontainer"
        assert result["commit_state"] == "committed"
        assert json.loads((workspace / "answer.json").read_bytes()) == {"sum": 42}
        return result["sandbox_root"]

    roots = [execute(0), execute(1)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        roots += list(pool.map(execute, range(2, 5)))
    assert len(set(roots)) == 5
    assert receipt.read_bytes() == original


@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_acl_mutex_wait_obeys_cancellation_and_deadline(tmp_path, reason):
    from omni_body_skill.tools import windows_appcontainer as native
    ready, release = threading.Event(), threading.Event()

    def hold_lock():
        with native._acl_update_lock(tmp_path):
            ready.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(hold_lock)
        try:
            assert ready.wait(3)
            started = time.monotonic()
            deadline = started + (0.15 if reason == "deadline" else 5)
            cancelled = (lambda: time.monotonic() - started > 0.15) if reason == "cancel" else None
            with pytest.raises((RuntimeError, TimeoutError), match="sandbox_cancelled|sandbox_timeout"):
                with native._acl_update_lock(tmp_path, deadline=deadline, cancel_check=cancelled):
                    pytest.fail("entered another thread's ACL lock")
            assert time.monotonic() - started < 2
        finally:
            release.set()
            owner.result(timeout=3)
