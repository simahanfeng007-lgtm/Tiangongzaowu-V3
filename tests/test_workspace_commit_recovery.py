"""Inject failures in real disk commits; reopen journals in a fresh process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from omni_body_skill.tools import sandbox_runtime as s
from omni_body_skill.tools import workspace_commit as tx


@pytest.fixture
def tree(tmp_path):
    host, private = tmp_path / "host", tmp_path / "private"
    host.mkdir(); private.mkdir()
    for name in ("a", "b", "deleted"):
        (host / name).write_text("old")
        (private / name).write_text("new")
    (private / "deleted").unlink()
    (private / "new").write_text("new")
    return host, private, tmp_path / "commits"


def merge(tree, **kwargs):
    host, private, root = tree
    return s._merge_changes(private, host, {name: (3, __import__('hashlib').sha256(b'old').hexdigest())
        for name in ('a', 'b', 'deleted')}, max_changed_bytes=1000,
        trash_root=root, transaction_root=root, operation="op1", input_digest="input", **kwargs)


@pytest.mark.parametrize("at", ["a", "b", "new"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_partial_commit_rolls_back_all_files(tree, monkeypatch, at, after_replace):
    host, _, root = tree
    copy = s._atomic_copy
    def fail(source, destination):
        if destination == host / at and source.parent.name == "after":
            if after_replace:
                copy(source, destination)
            raise OSError("injected disk error")
        return copy(source, destination)
    monkeypatch.setattr(s, "_atomic_copy", fail)
    with pytest.raises(OSError):
        merge(tree)
    assert {p.name: p.read_text() for p in host.iterdir()} == {k: "old" for k in ("a", "b", "deleted")}
    assert json.loads(next(root.glob('*/journal.json')).read_text())["state"] == "ROLLED_BACK"


def test_reopen_after_process_death_restores_interrupted_commit(tree):
    host, private, root = tree
    code = '''from pathlib import Path
from omni_body_skill.tools import sandbox_runtime as s
import os,sys
host,private,root=map(Path,sys.argv[1:])
copy=s._atomic_copy
def die(source,target):
    copy(source,target)
    if target == host/'b': os._exit(71)
s._atomic_copy=die
s._merge_changes(private,host,s._snapshot(host),max_changed_bytes=1000,trash_root=root,transaction_root=root,operation='op1',input_digest='input')
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    child = subprocess.run([sys.executable, "-c", code, str(host), str(private), str(root)], env=env)
    assert child.returncode == 71
    assert (host / "a").read_text() == "new"
    result = subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; from omni_body_skill.tools.workspace_commit import recover; recover(Path(sys.argv[1]),Path(sys.argv[2]))", str(root), str(host)], env=env)
    assert result.returncode == 0
    assert {p.name: p.read_text() for p in host.iterdir()} == {k: "old" for k in ("a", "b", "deleted")}


def test_external_edit_is_preserved_with_both_transaction_versions(tree, monkeypatch):
    host, _, root = tree
    copy = s._atomic_copy
    def fail(source, target):
        if target == host / "b":
            (host / "a").write_text("user edit")
            raise OSError("locked")
        return copy(source, target)
    monkeypatch.setattr(s, "_atomic_copy", fail)
    with pytest.raises(s.SandboxError, match="transaction_conflict"):
        merge(tree)
    assert (host / "a").read_text() == "user edit"
    journal = next(root.glob("*/journal.json"))
    assert json.loads(journal.read_text())["conflicts"] == ["a"]
    assert (journal.parent / "before/a").read_text() == "old"
    assert (journal.parent / "after/a").read_text() == "new"
    with pytest.raises(s.SandboxError, match="transaction_conflict"):
        tx.recover(root, host)


def test_committed_receipt_survives_response_loss_and_rejects_stale_replay(tree):
    host, _, root = tree
    receipt = merge(tree, receipt={"ok": True, "returncode": 0})
    tx.recover(root, host)
    assert tx.replay(root, host, "op1", "input") == {**receipt, "transaction_replayed": True}
    with pytest.raises(s.SandboxError, match="input_changed"):
        tx.replay(root, host, "op1", "different command")
    (host / "a").write_text("later version")
    with pytest.raises(s.SandboxError, match="output_changed"):
        tx.replay(root, host, "op1", "input")


def test_cancel_during_commit_restores_all_previous_files(tree):
    host, _, _ = tree
    with pytest.raises(s.SandboxError, match="cancelled"):
        merge(tree, cancel_check=lambda: (host / "a").read_text() == "new")
    assert {p.name: p.read_text() for p in host.iterdir()} == {k: "old" for k in ("a", "b", "deleted")}


def test_runner_replays_commit_without_reexecuting_command(tmp_path):
    host = tmp_path / "host"; host.mkdir()
    runner = s.SandboxRunner(host, tmp_path / "state", tmp_path / "trash")
    command = [sys.executable, "-c", "from pathlib import Path; p=Path('count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]
    first = runner.run(command, op_id="stable_operation")
    second = runner.run(command, op_id="stable_operation")
    assert first["ok"] and second["transaction_replayed"]
    assert (host / "count").read_text() == "1"


def test_fact_record_loss_reuses_the_committed_operation(tmp_path, monkeypatch):
    from v3.fact_kernel import FactExecutionKernel
    host = tmp_path / 'workspace'; host.mkdir()
    kernel = FactExecutionKernel(host, tmp_path / 'facts', 'run', request_id='request')
    runner = s.SandboxRunner(host, tmp_path / 'state', tmp_path / 'trash')
    command = [sys.executable, '-c', "from pathlib import Path; p=Path('count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]
    def execute(operation):
        return runner.run(command, op_id=operation)
    original = kernel._atomic_json
    with monkeypatch.context() as patch:
        patch.setattr(kernel, '_atomic_json', lambda *args: (_ for _ in ()).throw(OSError('fact disk error')))
        with pytest.raises(OSError):
            kernel.execute('python.run', '', {'code': 'counter'}, execute, idempotency_key='same-request')
    assert (host / 'count').read_text() == '1'
    result = kernel.execute('python.run', '', {'code': 'counter'}, execute, idempotency_key='same-request')
    assert result['ok'] and result['transaction_replayed']
    assert (host / 'count').read_text() == '1'
    with pytest.raises(ValueError, match='binding_changed'):
        kernel.execute('python.run', '', {'code': 'different'}, execute, idempotency_key='same-request')
