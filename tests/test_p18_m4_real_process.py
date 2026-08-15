"""P18-M4.4 real OS-process burn-in certification."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from total_gateway.store import GatewayStateStore


WORKER = Path(__file__).resolve().parent / "p18_m4_real_process_worker.py"
IDENTITY_FIELDS = (
    "request_id",
    "run_id",
    "generation",
    "life_id",
    "authority_hash",
    "root_goal_hash",
    "task_contract_hash",
)


def _wait_for(path: Path, *, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while not path.exists():
        if time.time() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.05)


def _popen(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(WORKER), *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _communicate_ok(proc: subprocess.Popen[str], *, timeout: float = 90.0) -> tuple[str, str]:
    stdout, stderr = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, f"worker failed rc={proc.returncode}\nstdout={stdout}\nstderr={stderr}"
    return stdout, stderr


def _identity(snapshot: dict) -> tuple[object, ...]:
    return tuple(snapshot[field] for field in IDENTITY_FIELDS)


def _run_kill_restart(
    tmp_path: Path,
    *,
    scenario: str,
    total: int,
    barrier_step: int,
    step_sleep: float = 0.0,
    lock_probe: Path | None = None,
    corrupt_checkpoint: bool = False,
    corrupt_ledger_tail: bool = False,
) -> tuple[dict, dict, Path]:
    db = tmp_path / f"{scenario}.sqlite3"
    state = tmp_path / f"{scenario}.json"
    barrier = tmp_path / f"{scenario}.barrier"
    artifact = tmp_path / f"{scenario}.artifact.txt"
    args = [
        "--mode", "longrun",
        "--db", str(db),
        "--state", str(state),
        "--artifact", str(artifact),
        "--scenario", scenario,
        "--total", str(total),
        "--checkpoint-interval", "50",
        "--barrier-step", str(barrier_step),
        "--barrier", str(barrier),
        "--step-sleep", str(step_sleep),
    ]
    if lock_probe is not None:
        args.extend(["--lock-probe", str(lock_probe)])
    first = _popen(args)
    try:
        _wait_for(barrier, timeout=45)
        before = json.loads(state.read_text(encoding="utf-8"))
        first.kill()
        first.wait(timeout=15)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)

    if corrupt_checkpoint:
        connection = sqlite3.connect(db)
        try:
            row = connection.execute(
                """
                SELECT checkpoint_id FROM regenerative_checkpoint
                WHERE request_id=? AND run_id=? AND generation=?
                ORDER BY checkpoint_seq DESC LIMIT 1
                """,
                (before["request_id"], before["run_id"], before["generation"]),
            ).fetchone()
            assert row is not None
            connection.execute(
                "UPDATE regenerative_checkpoint SET checkpoint_json=? WHERE checkpoint_id=?",
                ('{"corrupted":true}', row[0]),
            )
            connection.commit()
        finally:
            connection.close()

    if corrupt_ledger_tail:
        store = GatewayStateStore.open(db, now_ms=int(time.time() * 1000))
        try:
            event, created = store.append_execution_event(
                event_key=f"real-process-corrupt-tail:{scenario}",
                request_id=before["request_id"],
                run_id=before["run_id"],
                generation=before["generation"],
                epoch_index=barrier_step // 75,
                event_type="step.observed",
                payload={"corruption_probe": "tail-after-checkpoint"},
                created_at_ms=int(time.time() * 1000),
            )
            assert created is True
            tail_seq = event.ledger_seq
        finally:
            store.close()
        connection = sqlite3.connect(db)
        try:
            connection.execute(
                """
                UPDATE execution_ledger SET event_json=?
                WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq=?
                """,
                ('{"corrupted":true}', before["request_id"], before["run_id"], before["generation"], tail_seq),
            )
            connection.commit()
        finally:
            connection.close()

    barrier.unlink(missing_ok=True)
    resume_args = [
        "--mode", "longrun",
        "--db", str(db),
        "--state", str(state),
        "--artifact", str(artifact),
        "--scenario", scenario,
        "--total", str(total),
        "--checkpoint-interval", "50",
        "--step-sleep", "0",
    ]
    if lock_probe is not None:
        resume_args.extend(["--lock-probe", str(lock_probe)])
    second = _popen(resume_args)
    _communicate_ok(second, timeout=120)
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["completed"] is True
    assert after["step"] == total
    assert after["ledger_healthy"] is True
    assert _identity(after) == _identity(before)
    return before, after, artifact


def test_m4_real_process_case_a_file_engineering_500_steps_kill_restart(tmp_path: Path) -> None:
    before, after, artifact = _run_kill_restart(
        tmp_path,
        scenario="file_engineering",
        total=500,
        barrier_step=250,
    )
    assert before["step"] == 250
    assert after["metrics"]["recovery_count"] >= 1
    lines = [int(line) for line in artifact.read_text(encoding="utf-8").splitlines()]
    assert lines == list(range(1, 501))


def test_m4_real_process_case_b_code_edit_test_fix_1000_steps(tmp_path: Path) -> None:
    before, after, artifact = _run_kill_restart(
        tmp_path,
        scenario="code_edit_test_fix",
        total=1000,
        barrier_step=500,
    )
    assert before["metrics"]["test_failures_repaired"] == 1
    assert after["metrics"]["recovery_count"] >= 1
    lines = [int(line) for line in artifact.read_text(encoding="utf-8").splitlines()]
    assert lines == list(range(1, 1001))
    repaired = artifact.with_suffix(".py").read_text(encoding="utf-8")
    compile(repaired, str(artifact.with_suffix(".py")), "exec")


def test_m4_real_process_case_c_readonly_1000_steps_recovers_from_corrupt_current_checkpoint(tmp_path: Path) -> None:
    before, after, artifact = _run_kill_restart(
        tmp_path,
        scenario="readonly_investigation",
        total=1000,
        barrier_step=400,
        corrupt_checkpoint=True,
    )
    assert before["step"] == 400
    assert after["metrics"]["recovery_count"] >= 1
    assert not artifact.exists() or artifact.read_text(encoding="utf-8") == ""


def test_m4_real_process_case_d_high_fault_500_steps_with_network_timeouts_lock_and_torn_tail(tmp_path: Path) -> None:
    lock_file = tmp_path / "high_fault.lock"
    lock_ready = tmp_path / "high_fault.lock.ready"
    holder = _popen([
        "--mode", "hold-lock",
        "--artifact", str(lock_file),
        "--ready", str(lock_ready),
        "--hold-seconds", "4",
    ])
    try:
        _wait_for(lock_ready, timeout=20)
        before, after, _artifact = _run_kill_restart(
            tmp_path,
            scenario="high_fault",
            total=500,
            barrier_step=200,
            step_sleep=0.003,
            lock_probe=lock_file,
            corrupt_ledger_tail=True,
        )
    finally:
        if holder.poll() is None:
            holder.terminate()
        try:
            holder.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.communicate(timeout=5)
    metrics = before["metrics"]
    assert metrics["network_disconnects"] == 1
    assert metrics["api_timeouts"] == 1
    assert metrics["tool_timeouts"] == 1
    assert metrics["file_lock_blocks"] == 1
    assert metrics["sse_reconnects"] == 1
    assert metrics["provider_reconnects"] == 1
    assert after["metrics"]["recovery_count"] >= 1


@pytest.mark.skipif(sys.platform != "win32", reason="M4.4 Case E is the dedicated Windows long-run gate")
def test_m4_real_process_case_e_windows_1000_steps_kill_restart(tmp_path: Path) -> None:
    before, after, artifact = _run_kill_restart(
        tmp_path,
        scenario="windows_longrun",
        total=1000,
        barrier_step=500,
    )
    assert before["step"] == 500
    assert after["metrics"]["recovery_count"] >= 1
    lines = [int(line) for line in artifact.read_text(encoding="utf-8").splitlines()]
    assert lines == list(range(1, 1001))


def test_m4_real_process_case_f_two_subagents_same_artifact_only_one_dispatches(tmp_path: Path) -> None:
    db = tmp_path / "race.sqlite3"
    init_state = tmp_path / "race.init.json"
    init = _popen([
        "--mode", "init",
        "--db", str(db),
        "--state", str(init_state),
        "--scenario", "subagent_artifact_race",
    ])
    _communicate_ok(init)

    gate = tmp_path / "race.gate"
    artifact = tmp_path / "shared-artifact.txt"
    ready_a = tmp_path / "race.a.ready"
    ready_b = tmp_path / "race.b.ready"
    out_a = tmp_path / "race.a.json"
    out_b = tmp_path / "race.b.json"
    common = [
        "--mode", "race",
        "--db", str(db),
        "--scenario", "subagent_artifact_race",
        "--artifact", str(artifact),
        "--gate", str(gate),
    ]
    proc_a = _popen([*common, "--state", str(out_a), "--ready", str(ready_a)])
    proc_b = _popen([*common, "--state", str(out_b), "--ready", str(ready_b)])
    try:
        _wait_for(ready_a, timeout=20)
        _wait_for(ready_b, timeout=20)
        gate.write_text("go", encoding="utf-8")
        _communicate_ok(proc_a, timeout=30)
        _communicate_ok(proc_b, timeout=30)
    finally:
        for proc in (proc_a, proc_b):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    a = json.loads(out_a.read_text(encoding="utf-8"))
    b = json.loads(out_b.read_text(encoding="utf-8"))
    assert a["logical_effect_id"] == b["logical_effect_id"]
    assert a["effect_id"] == b["effect_id"]
    assert sum(item["dispatch_permitted"] for item in (a, b)) == 1
    assert sum(item["wrote"] for item in (a, b)) == 1
    assert artifact.read_text(encoding="utf-8") == "race-winner"
