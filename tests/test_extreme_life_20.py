from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import life_service.embedded_runtime as embedded_module
from contracts import canonical_sha256
from life_service.complete_scheduler import EmbeddedLifeScheduler
from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime, LifeWriterLease


class ExtremeLife20(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "life-data"
        self.runtime_root = self.root / "runtime"
        self.environment = mock.patch.dict(
            os.environ,
            {"TIANGONG_LIFE_HEARTBEAT_SECONDS": "3600"},
            clear=False,
        )
        self.environment.start()
        self.runtime: EmbeddedLifeRuntime | None = EmbeddedLifeRuntime(
            data_root=self.data_root,
            runtime_root=self.runtime_root,
            mode="embedded",
        )

    def tearDown(self) -> None:
        runtime = self.runtime
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        self.environment.stop()
        self.temporary.cleanup()

    def _active_id(self) -> str:
        assert self.runtime is not None
        return str(self.runtime.system.identities.active(required=True)["life_id"])

    def _request(self, method: str, path: str, payload=None):
        assert self.runtime is not None
        return self.runtime.request(method, path, payload or {})

    @staticmethod
    def _memory(memory_id: str, content, *, life_id: str = "") -> dict:
        value = {
            "memory_id": memory_id,
            "memory_type": "semantic",
            "content": content,
            "provenance": {"source": "extreme-test"},
            "relations": [],
            "epistemic_status": "user_asserted",
            "confidence_milli": 900,
            "priority": 1000,
            "actor": "user",
        }
        if life_id:
            value["life_id"] = life_id
        return value

    def _execution(self, suffix: str, *, result_hash: str = "b" * 64) -> dict:
        return {
            "schema": "tiangong.life.execution-terminal.v1",
            "request_id": f"request-{suffix}",
            "run_id": f"run-{suffix}",
            "generation": 1,
            "life_id": self._active_id(),
            "session_scope_hash": canonical_sha256({"session": suffix}),
            "status": "completed",
            "user_goal_sha256": canonical_sha256({"goal": suffix}),
            "final_result_sha256": result_hash,
            "fact_ids": [f"fact-{suffix}"],
            "completed_at_ms": 1_780_000_000_000,
        }

    def _journal_path(self, life_id: str | None = None) -> Path:
        assert self.runtime is not None
        return self.runtime.system.journal._path(life_id or self._active_id())

    @staticmethod
    def _rewrite_lines(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                for row in rows
            ),
            encoding="utf-8",
            newline="\n",
        )

    def test_l01_second_writer_same_root_is_rejected(self):
        with self.assertRaisesRegex(EmbeddedLifeError, "life.writer.already_owned"):
            EmbeddedLifeRuntime(
                data_root=self.data_root,
                runtime_root=self.root / "runtime-second",
                mode="standalone",
            )

    def test_l02_standalone_can_take_over_only_after_embedded_close(self):
        assert self.runtime is not None
        self.runtime.close()
        self.runtime = None
        takeover = EmbeddedLifeRuntime(
            data_root=self.data_root,
            runtime_root=self.root / "runtime-standalone",
            mode="standalone",
        )
        try:
            self.assertTrue(takeover.health_payload()["writer_lease_active"])
            self.assertEqual(takeover.health_payload()["deployment_mode"], "standalone")
        finally:
            takeover.close()

    def test_l03_precreated_writer_lock_symlink_is_rejected(self):
        assert self.runtime is not None
        self.runtime.close()
        self.runtime = None
        lock_path = self.data_root / "life.writer.lock"
        lock_path.unlink()
        outside = self.root / "attacker-selected.lock"
        outside.write_text("attacker", encoding="utf-8")
        try:
            os.symlink(outside, lock_path)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(EmbeddedLifeError, "life.writer.lock_unsafe"):
            EmbeddedLifeRuntime(
                data_root=self.data_root,
                runtime_root=self.root / "runtime-symlink",
                mode="embedded",
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "attacker")

    def test_l04_partial_initialization_failure_releases_writer_lease(self):
        assert self.runtime is not None
        self.runtime.close()
        self.runtime = None
        with mock.patch.object(
            embedded_module.LifeShadowStore,
            "open",
            side_effect=RuntimeError("injected-store-open-failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected-store-open-failure"):
                EmbeddedLifeRuntime(
                    data_root=self.data_root,
                    runtime_root=self.root / "runtime-failed",
                    mode="embedded",
                )
        recovered = EmbeddedLifeRuntime(
            data_root=self.data_root,
            runtime_root=self.root / "runtime-recovered",
            mode="embedded",
        )
        recovered.close()

    def test_l05_corrupt_persistent_state_fails_closed_and_releases_lease(self):
        assert self.runtime is not None
        self.runtime.close()
        self.runtime = None
        self.runtime_root.joinpath("embedded-life-state.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(EmbeddedLifeError, "life.state.corrupt"):
            EmbeddedLifeRuntime(
                data_root=self.data_root,
                runtime_root=self.runtime_root,
                mode="embedded",
            )
        lease = LifeWriterLease.acquire(self.data_root, mode="repair")
        lease.release()

    def test_l06_v1_unscoped_state_migrates_only_to_active_identity(self):
        assert self.runtime is not None
        active = self._active_id()
        self.runtime.close()
        self.runtime = None
        legacy = {
            "schema": "tiangong.life.embedded-state.v1",
            "revision": 7,
            "memories": {
                "legacy-memory": {
                    "memory_id": "legacy-memory",
                    "memory_type": "semantic",
                    "content": {"value": "legacy"},
                    "status": "active",
                    "revision": 1,
                }
            },
            "affect": {"valence": 0.6, "arousal": 0.1, "dominance": 0.2},
            "updated_at": "2026-07-21T00:00:00+00:00",
        }
        self.runtime_root.joinpath("embedded-life-state.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        self.runtime = EmbeddedLifeRuntime(
            data_root=self.data_root,
            runtime_root=self.runtime_root,
            mode="embedded",
        )
        self.assertEqual(self.runtime._state["schema"], "tiangong.life.embedded-state.v2")
        self.assertIn("legacy-memory", self.runtime._state["identity_states"][active]["memories"])
        status, created, _ = self._request("POST", "/api/v1/v3/life/identity/create", {"name": "第二生命"})
        self.assertEqual(status, 200)
        second = created["identity"]["life_id"]
        self.assertEqual(self.runtime._state["identity_states"][second]["memories"], {})

    def test_l07_two_identities_concurrent_pinned_memory_and_affect_are_isolated(self):
        first = self._active_id()
        status, created, _ = self._request("POST", "/api/v1/v3/life/identity/create", {"name": "第二生命"})
        self.assertEqual(status, 200)
        second = str(created["identity"]["life_id"])

        def write(pair: tuple[str, int]):
            life_id, index = pair
            return self._request(
                "POST",
                "/api/v1/v3/life/memory/assert",
                self._memory(f"mem-{life_id[-8:]}-{index}", {"owner": life_id, "index": index}, life_id=life_id),
            )[0]

        jobs = [(first, index) for index in range(20)] + [(second, index) for index in range(20)]
        with ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(list(pool.map(write, jobs)), [200] * 40)

        self._request("POST", "/api/v1/v3/life/identity/activate", {"life_id": first})
        self._request("POST", "/api/v1/v3/life/affect/appraise", {"valence": 0.75})
        first_stats = self._request("GET", "/api/v1/v3/life/memory/stats")[1]
        first_affect = self._request("GET", "/api/v1/v3/life/affect")[1]["state"]

        self._request("POST", "/api/v1/v3/life/identity/activate", {"life_id": second})
        self._request("POST", "/api/v1/v3/life/affect/appraise", {"valence": -0.65})
        second_stats = self._request("GET", "/api/v1/v3/life/memory/stats")[1]
        second_affect = self._request("GET", "/api/v1/v3/life/affect")[1]["state"]

        self.assertEqual(first_stats["total"], 20)
        self.assertEqual(second_stats["total"], 20)
        self.assertEqual(first_affect["valence"], 0.75)
        self.assertEqual(second_affect["valence"], -0.65)
        self.assertTrue(self.runtime.system.journal.verify(first)["valid"])
        self.assertTrue(self.runtime.system.journal.verify(second)["valid"])

    def test_l08_exact_execution_commit_retry_is_idempotent(self):
        payload = self._execution("l08")
        first = self._request("POST", "/api/v1/v3/life/execution/commit", payload)
        second = self._request("POST", "/api/v1/v3/life/execution/commit", payload)
        self.assertEqual(first[0], 200)
        self.assertFalse(first[1]["duplicate"])
        self.assertEqual(second[0], 200)
        self.assertTrue(second[1]["duplicate"])
        events = self.runtime.system.journal.events(self._active_id())
        self.assertEqual(sum(row["event_type"] == "execution.committed" for row in events), 1)

    def test_l09_same_request_changed_terminal_result_is_conflict(self):
        payload = self._execution("l09", result_hash="c" * 64)
        self.assertEqual(self._request("POST", "/api/v1/v3/life/execution/commit", payload)[0], 200)
        changed = dict(payload)
        changed["final_result_sha256"] = "d" * 64
        status, value, _ = self._request("POST", "/api/v1/v3/life/execution/commit", changed)
        self.assertEqual(status, 409)
        self.assertEqual(value["error_code"], "life.execution.commit_conflict")

    def test_l10_same_memory_id_same_semantics_is_idempotent(self):
        payload = self._memory("mem-l10", {"stable": True})
        first = self._request("POST", "/api/v1/v3/life/memory/assert", payload)
        before = len(self.runtime.system.journal.events(self._active_id()))
        second = self._request("POST", "/api/v1/v3/life/memory/assert", payload)
        after = len(self.runtime.system.journal.events(self._active_id()))
        self.assertEqual(first[0], 200)
        self.assertFalse(first[1]["duplicate"])
        self.assertEqual(second[0], 200)
        self.assertTrue(second[1]["duplicate"])
        self.assertEqual(before, after)

    def test_l11_same_memory_id_changed_semantics_is_rejected(self):
        self.assertEqual(
            self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l11", {"v": 1}))[0],
            200,
        )
        status, value, _ = self._request(
            "POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l11", {"v": 2})
        )
        self.assertEqual(status, 409)
        self.assertEqual(value["error_code"], "life.memory.id_conflict")

    def test_l12_journal_payload_tamper_is_detected_by_event_hash(self):
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l12", {"value": "original"}))
        path = self._journal_path()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["payload"]["assertion"]["content"] = {"value": "tampered"}
        self._rewrite_lines(path, rows)
        verify = self.runtime.system.journal.verify(self._active_id())
        self.assertFalse(verify["valid"])
        self.assertEqual(verify["reason_code"], "journal_event_hash_invalid")

    def test_l13_journal_truncated_final_line_is_detected(self):
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l13", {"value": 13}))
        path = self._journal_path()
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        path.write_bytes(raw[:-1])
        verify = self.runtime.system.journal.verify(self._active_id())
        self.assertFalse(verify["valid"])
        self.assertEqual(verify["reason_code"], "journal_truncated")

    def test_l14_journal_tail_deletion_is_detected_by_signed_head_anchor(self):
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l14-a", {"value": "a"}))
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l14-b", {"value": "b"}))
        path = self._journal_path()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(rows), 2)
        # A plain hash chain cannot detect removal of its last event.  The
        # separately signed head checkpoint must make this valid-prefix attack
        # observable.
        self._rewrite_lines(path, rows[:-1])
        verify = self.runtime.system.journal.verify(self._active_id())
        self.assertFalse(verify["valid"])
        self.assertEqual(verify["reason_code"], "journal_head_mismatch")

    def test_l15_corrupted_dormant_identity_cannot_be_activated(self):
        first = self._active_id()
        status, created, _ = self._request("POST", "/api/v1/v3/life/identity/create", {"name": "待损坏生命"})
        self.assertEqual(status, 200)
        second = str(created["identity"]["life_id"])
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l15", {"v": 15}))
        self._request("POST", "/api/v1/v3/life/identity/activate", {"life_id": first})
        path = self._journal_path(second)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["payload"] = {"tampered": True}
        self._rewrite_lines(path, rows)
        status, value, _ = self._request("POST", "/api/v1/v3/life/identity/activate", {"life_id": second})
        self.assertEqual(status, 409)
        self.assertEqual(value["error_code"], "journal_event_hash_invalid")
        self.assertEqual(self._active_id(), first)

    def test_l16_readiness_fails_immediately_after_active_journal_corruption(self):
        self._request("POST", "/api/v1/v3/life/memory/assert", self._memory("mem-l16", {"v": 16}))
        path = self._journal_path()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[-1]["event_sha256"] = "0" * 64
        self._rewrite_lines(path, rows)
        status, ready = self.runtime.ready_payload()
        self.assertEqual(status, 503)
        self.assertEqual(ready["status"], "NOT_READY")
        self.assertIn("journal_event_hash_invalid", ready["reason_codes"])
        self.assertFalse(self.runtime.health_payload()["life_ready"])

    def test_l17_scheduler_survives_one_tick_exception_and_runs_next_tick(self):
        attempts = 0
        succeeded = threading.Event()

        def tick(_reason: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected-first-tick-failure")
            succeeded.set()

        scheduler = EmbeddedLifeScheduler(tick, interval_seconds=1.0)
        scheduler.interval_seconds = 0.02
        scheduler.start()
        try:
            self.assertTrue(succeeded.wait(1.0))
            status = scheduler.status()
            self.assertGreaterEqual(attempts, 2)
            self.assertGreaterEqual(status["tick_count"], 1)
            self.assertTrue(status["running"])
        finally:
            scheduler.stop(timeout_seconds=1.0)

    def test_l18_scheduler_stop_timeout_retains_writer_lease_until_retry(self):
        assert self.runtime is not None and self.runtime.scheduler is not None
        with mock.patch.object(
            self.runtime.scheduler,
            "stop",
            side_effect=TimeoutError("injected-stop-timeout"),
        ):
            with self.assertRaisesRegex(TimeoutError, "injected-stop-timeout"):
                self.runtime.close()
        self.assertTrue(self.runtime._lease.active)
        self.assertFalse(self.runtime._closed)
        with self.assertRaisesRegex(EmbeddedLifeError, "life.writer.already_owned"):
            EmbeddedLifeRuntime(
                data_root=self.data_root,
                runtime_root=self.root / "runtime-second-writer",
                mode="standalone",
            )
        self.runtime.close()
        self.runtime = None

    def test_l19_sixty_four_concurrent_memory_writes_have_no_loss_or_chain_fork(self):
        life_id = self._active_id()

        def write(index: int) -> int:
            return self._request(
                "POST",
                "/api/v1/v3/life/memory/assert",
                self._memory(f"mem-l19-{index}", {"index": index}, life_id=life_id),
            )[0]

        with ThreadPoolExecutor(max_workers=24) as pool:
            statuses = list(pool.map(write, range(64)))
        self.assertEqual(statuses, [200] * 64)
        stats = self._request("GET", "/api/v1/v3/life/memory/stats")[1]
        self.assertEqual(stats["total"], 64)
        verify = self.runtime.system.journal.verify(life_id)
        self.assertTrue(verify["valid"])
        events = self.runtime.system.journal.events(life_id)
        self.assertEqual(sum(row["event_type"] == "memory.asserted" for row in events), 64)

    def test_l20_shutdown_racing_terminal_commit_is_atomic_and_recoverable(self):
        assert self.runtime is not None
        payload = self._execution("l20")
        entered = threading.Event()
        release = threading.Event()
        original_persist = self.runtime._persist

        def blocking_persist(life_id: str = "") -> None:
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test release was not signalled")
            original_persist(life_id)

        self.runtime._persist = blocking_persist  # type: ignore[method-assign]
        commit_result: list[tuple] = []
        close_errors: list[BaseException] = []

        def commit() -> None:
            commit_result.append(self._request("POST", "/api/v1/v3/life/execution/commit", payload))

        def close() -> None:
            try:
                assert self.runtime is not None
                self.runtime.close()
            except BaseException as exc:  # evidence collection in adversarial test
                close_errors.append(exc)

        commit_thread = threading.Thread(target=commit, name="life-extreme-commit")
        close_thread = threading.Thread(target=close, name="life-extreme-close")
        commit_thread.start()
        self.assertTrue(entered.wait(1.0))
        close_thread.start()
        time.sleep(0.05)
        self.assertTrue(close_thread.is_alive(), "close must wait for the in-flight atomic commit")
        release.set()
        commit_thread.join(2.0)
        close_thread.join(2.0)
        self.assertFalse(commit_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertEqual(close_errors, [])
        self.assertEqual(commit_result[0][0], 200)
        self.runtime = None

        reopened = EmbeddedLifeRuntime(
            data_root=self.data_root,
            runtime_root=self.runtime_root,
            mode="embedded",
        )
        try:
            status, recovered, _ = reopened.request(
                "POST",
                "/api/v1/v3/life/execution/recover",
                {"request_id": payload["request_id"]},
            )
            self.assertEqual(status, 200)
            self.assertTrue(recovered["found"])
            self.assertEqual(recovered["execution"]["commit_sha256"], canonical_sha256({
                "domain": "tiangong.life.execution-commit.v1", "payload": payload
            }))
            self.assertTrue(reopened.system.journal.verify(payload["life_id"])["valid"])
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
