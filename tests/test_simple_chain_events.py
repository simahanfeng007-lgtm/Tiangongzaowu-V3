"""简单链事件流回归测试（纯增量，不允许影响现有系统）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _isolated_env(tmp: str) -> dict:
    return {
        "TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": str(Path(tmp) / "simple-chain"),
        "TIANGONG_RUN_STATE_DIR": str(Path(tmp) / "run-state"),
        "USERPROFILE": str(Path(tmp) / "home"),
        "HOME": str(Path(tmp) / "home"),
        "APPDATA": str(Path(tmp) / "appdata"),
        "TIANGONG_SIMPLE_CHAIN_EVENTS_ROOT": "",
    }


class EventWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        import v3.simple_chain_events as ev

        ev._cached_root = None

    def _events_lines(self, tmp: str) -> list[dict]:
        import v3.simple_chain_events as ev

        root = ev.events_root()
        files = sorted(Path(root).glob("events-*.jsonl"))
        lines: list[dict] = []
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    lines.append(json.loads(line))
        return lines

    def test_pointer_is_auto_generated_and_reused(self) -> None:
        import v3.simple_chain_events as ev

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            root1 = ev.events_root()
            self.assertTrue(root1.is_dir())
            pointer = Path(tmp) / "simple_chain_events_location.json"
            self.assertTrue(pointer.exists())
            data = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(Path(data["root"]), root1)
            root2 = ev.events_root()
            self.assertEqual(root1, root2)

    def test_append_event_valid_jsonl_with_monotonic_seq(self) -> None:
        import v3.simple_chain_events as ev

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            self.assertTrue(ev.append_event({
                "type": "chain_started",
                "run_id": "req-1",
                "request_id": "req-1",
                "session_id": "sess",
                "round": 0,
                "reason": "run created",
                "source": "system",
            }))
            self.assertTrue(ev.append_event({
                "type": "force_stopped",
                "run_id": "req-1",
                "request_id": "req-1",
                "session_id": "sess",
                "round": 6,
                "reason": "no progress",
                "source": "system",
            }))
            lines = self._events_lines(tmp)
            self.assertEqual([item["seq"] for item in lines], [1, 2])
            self.assertEqual(lines[0]["type"], "chain_started")
            self.assertEqual(lines[1]["type"], "force_stopped")
            self.assertEqual(lines[1]["round"], 6)

    def test_unknown_type_and_failure_are_tolerated(self) -> None:
        import v3.simple_chain_events as ev

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            self.assertFalse(ev.append_event({"type": "bogus", "run_id": "x"}))
            with mock.patch.object(ev, "events_root", side_effect=OSError("disk full")):
                self.assertFalse(ev.append_event({"type": "chain_started", "run_id": "x"}))

    def test_terminal_mapping(self) -> None:
        import v3.simple_chain_events as ev

        self.assertEqual(ev.event_type_for("interrupted", ["user_cancel"]), "run_interrupted")
        self.assertEqual(ev.event_type_for("force_stopped", ["[terminal_model_error] x"]), "turn.failed")
        self.assertEqual(ev.event_type_for("force_stopped", ["[loop_budget_exhausted] x"]), "budget_limited")
        self.assertEqual(ev.event_type_for("force_stopped", ["no effective progress"]), "force_stopped")
        self.assertEqual(ev.event_type_for("complete", []), "chain_completed")


class EmissionPointTests(unittest.TestCase):
    def setUp(self) -> None:
        import v3.simple_chain_events as ev

        ev._cached_root = None

    def test_closeout_record_emits_terminal_event(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_closeout_record,
            _simple_chain_new_run_state,
        )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            rs = _simple_chain_new_run_state("req-closeout", "sess")
            _simple_chain_closeout_record(rs, "force_stopped", ["[terminal_model_error] api down"], "model")
            events_dir = Path(tmp) / "simple_chain_events"
            lines = []
            for path in events_dir.glob("events-*.jsonl"):
                lines.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            self.assertTrue(any(item["type"] == "turn.failed" and item["run_id"] == "req-closeout" for item in lines))

    def test_mark_terminal_emits_interrupted_event(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_mark_interrupted,
            _simple_chain_new_run_state,
            _simple_chain_save_run_state,
        )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            rs = _simple_chain_new_run_state("req-cancel", "sess")
            _simple_chain_save_run_state(rs)
            _simple_chain_mark_interrupted("req-cancel", "user_cancel")
            events_dir = Path(tmp) / "simple_chain_events"
            lines = []
            for path in events_dir.glob("events-*.jsonl"):
                lines.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            self.assertTrue(any(item["type"] == "run_interrupted" and item["run_id"] == "req-cancel" for item in lines))

    def test_startup_backfill_appends_missing_terminal_event(self) -> None:
        from v3 import zongdiaodu as zd

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, _isolated_env(tmp), clear=False
        ):
            root = Path(tmp) / "simple-chain"
            root.mkdir(parents=True, exist_ok=True)
            terminal = {
                "schema": "tiangong.v3.simple_chain.run_state.v2",
                "run_id": "req-backfill",
                "request_id": "req-backfill",
                "status": "force_stopped",
                "stage": "force_stopped",
                "round": 3,
                "last_transition": {
                    "type": "force_stopped",
                    "reason": "no effective progress",
                    "source": "model",
                },
            }
            (root / "req-backfill.json").write_text(json.dumps(terminal, ensure_ascii=False), encoding="utf-8")

            zd.Zongdiaodu._cleanup_stale_run_states(object())

            events_dir = Path(tmp) / "simple_chain_events"
            lines = []
            for path in events_dir.glob("events-*.jsonl"):
                lines.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            self.assertTrue(any(
                item["type"] == "force_stopped" and item["run_id"] == "req-backfill"
                for item in lines
            ))


if __name__ == "__main__":
    unittest.main()
