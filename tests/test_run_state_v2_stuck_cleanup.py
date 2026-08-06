"""对抗测试修复回归：指纹去噪、run_state v2/CAS、启动对账、决策计数语义。

对应 2026-08-06 三路对抗审查的 P0-1/P0-2/P0-3 与 P1-1~P1-9 修复。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


def _make_payload(path: str, content: str, round_no: int, repeat_count: int) -> dict:
    from v3.zongdiaodu import _simple_chain_new_run_state, _simple_chain_quality_gate_payload

    rs = _simple_chain_new_run_state(f"req-exp-{round_no}", "sess")
    rs["round"] = round_no
    result = {"ok": True, "path": path, "neirong": content, "readback": {"ok": True}}
    payload = _simple_chain_quality_gate_payload(
        "req-exp",
        "请读取文件并核对内容。",
        "omni_body",
        {"action": "file.read", "args": {"path": path}},
        result,
        repeat_count,
        run_state=rs,
    )
    payload["run_state"] = __import__("v3.zongdiaodu", fromlist=["_simple_chain_run_state_view"])._simple_chain_run_state_view(rs)
    payload["_ts"] = f"2026-08-06T10:00:{round_no:02d}"
    return payload


class FingerprintDenoiseTests(unittest.TestCase):
    def test_repeat_noise_does_not_change_fingerprint(self) -> None:
        from v3.zongdiaodu import _simple_chain_progress_fingerprint

        base = _make_payload("C:/x/a.txt", "同一内容", 1, 0)
        noise = _make_payload("C:/x/a.txt", "同一内容", 2, 1)
        self.assertEqual(
            _simple_chain_progress_fingerprint("请读取文件并核对内容。", [base], []),
            _simple_chain_progress_fingerprint("请读取文件并核对内容。", [noise], []),
        )

    def test_substantive_changes_do_change_fingerprint(self) -> None:
        from v3.zongdiaodu import _simple_chain_progress_fingerprint

        base = _make_payload("C:/x/a.txt", "内容A", 1, 0)
        path_chg = _make_payload("C:/x/b.txt", "内容A", 2, 0)
        content_chg = _make_payload("C:/x/a.txt", "内容B", 3, 0)
        base_fp = _simple_chain_progress_fingerprint("请读取文件并核对内容。", [base], [])
        self.assertNotEqual(base_fp, _simple_chain_progress_fingerprint("请读取文件并核对内容。", [path_chg], []))
        self.assertNotEqual(base_fp, _simple_chain_progress_fingerprint("请读取文件并核对内容。", [content_chg], []))

    def test_monitor_catches_monotonic_repeat(self) -> None:
        from v3.zongdiaodu import _SimpleChainProgressMonitor, _simple_chain_progress_fingerprint

        monitor = _SimpleChainProgressMonitor()
        hit = None
        intents = ["继续处理", "再试一次", "换个思路", "接着来", "继续", "再执行一次", "重试", "继续", "换种方式", "再来"] * 3
        for i in range(1, 31):
            payload = _make_payload("C:/x/a.txt", "同一内容", i, max(0, i - 1))
            stuck, reason = monitor.update(
                _simple_chain_progress_fingerprint("请读取文件并核对内容。", [payload], []),
                intents[i - 1],
            )
            if stuck:
                hit = (i, reason)
                break
        self.assertIsNotNone(hit, "同一调用单调重复必须在 30 轮内被判卡死")
        self.assertLessEqual(hit[0], 7)


class RunStateV2Tests(unittest.TestCase):
    def test_new_run_state_v2_fields(self) -> None:
        from v3.zongdiaodu import _simple_chain_new_run_state

        rs = _simple_chain_new_run_state("req-v2", "sess")
        self.assertEqual(rs["schema"], "tiangong.v3.simple_chain.run_state.v2")
        self.assertEqual(rs["schema_version"], 2)
        self.assertEqual(rs["version"], 1)
        self.assertEqual(rs["owner_pid"], os.getpid())
        self.assertIn("budget", rs)
        self.assertIn("terminal_reason", rs)
        self.assertIn("last_transition", rs)
        self.assertIn("persistence_degraded", rs)

    def test_save_load_version_increment_and_cas(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_load_run_state,
            _simple_chain_mark_terminal,
            _simple_chain_new_run_state,
            _simple_chain_run_state_path,
            _simple_chain_save_run_state,
        )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": tmp},
            clear=False,
        ):
            rs = _simple_chain_new_run_state("req-cas", "sess")
            _simple_chain_save_run_state(rs)
            self.assertEqual(rs["version"], 2)
            _simple_chain_save_run_state(rs)
            self.assertEqual(rs["version"], 3)
            loaded = _simple_chain_load_run_state("req-cas")
            self.assertEqual(loaded["version"], 3)
            # CAS：另一实例已写入更高版本，本实例不得覆盖。
            path = _simple_chain_run_state_path("req-cas")
            newer = json.loads(path.read_text(encoding="utf-8"))
            newer["version"] = 99
            path.write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
            _simple_chain_save_run_state(rs)
            self.assertTrue(rs.get("persistence_degraded"))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 99)
            # 终态标记落盘。
            _simple_chain_mark_terminal("req-cas", "force_stopped", "[terminal_model_error] test")
            final = _simple_chain_load_run_state("req-cas")
            self.assertEqual(final["status"], "force_stopped")
            self.assertTrue(final["terminal_reason"].startswith("[terminal_model_error]"))
            self.assertEqual(final["last_transition"]["source"], "system")


class CleanupStaleRunStateTests(unittest.TestCase):
    def _isolated_env(self, tmp: str) -> dict:
        # 必须隔离全部根目录：USERPROFILE/HOME/APPDATA 也会被清理函数扫描，
        # 否则测试会误删真实用户目录（2026-08-06 已发生一次，务必保持隔离）。
        return {
            "TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": str(Path(tmp) / "simple-chain"),
            "TIANGONG_RUN_STATE_DIR": str(Path(tmp) / "run-state"),
            "USERPROFILE": str(Path(tmp) / "home"),
            "HOME": str(Path(tmp) / "home"),
            "APPDATA": str(Path(tmp) / "appdata"),
        }

    def test_cleanup_marks_stale_and_skips_live_owner(self) -> None:
        from v3 import zongdiaodu as zd

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            self._isolated_env(tmp),
            clear=False,
        ):
            root = Path(tmp) / "simple-chain"
            root.mkdir(parents=True, exist_ok=True)
            stale = {"schema": "tiangong.v3.simple_chain.run_state.v1", "run_id": "stale", "status": "observing", "round": 7}
            (root / "stale.json").write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
            live = dict(stale, run_id="live", status="observing", owner_pid=os.getpid())
            (root / "live.json").write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")

            zd.Zongdiaodu._cleanup_stale_run_states(object())

            stale_after = json.loads((root / "stale.json").read_text(encoding="utf-8"))
            live_after = json.loads((root / "live.json").read_text(encoding="utf-8"))
            self.assertEqual(stale_after["status"], "interrupted")
            self.assertEqual(stale_after["terminal_reason"], "[process_restart] run interrupted at startup")
            self.assertEqual(live_after["status"], "observing", "owner 存活时不得误杀")

    def test_cleanup_retention_keeps_newest(self) -> None:
        from v3 import zongdiaodu as zd

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            self._isolated_env(tmp),
            clear=False,
        ), mock.patch.object(zd, "_SIMPLE_CHAIN_RUN_STATE_RETAIN_COUNT", 1):
            root = Path(tmp) / "simple-chain"
            root.mkdir(parents=True, exist_ok=True)
            for name in ("old.json", "new.json"):
                (root / name).write_text(
                    json.dumps({"status": "complete", "run_id": name}, ensure_ascii=False),
                    encoding="utf-8",
                )
            now = time.time()
            os.utime(root / "old.json", (now - 2 * 86400, now - 2 * 86400))
            os.utime(root / "new.json", (now - 86400, now - 86400))

            zd.Zongdiaodu._cleanup_stale_run_states(object())

            self.assertFalse((root / "old.json").exists())
            self.assertTrue((root / "new.json").exists())


class ContinueDecisionResetSemanticsTests(unittest.TestCase):
    def test_progress_reset_keeps_legit_tasks_and_stops_stuck(self) -> None:
        from v3.zongdiaodu import (
            _SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES,
            _simple_chain_progress_fingerprint,
        )

        stuck = [_make_payload("C:/x/a.txt", "同一内容", 1, 0) for _ in range(25)]
        legit = [_make_payload(f"C:/x/f{i}.txt", f"内容{i}", i, 0) for i in range(1, 31)]

        def simulate(seq):
            last_fp = None
            count = 0
            for payload in seq:
                fp = _simple_chain_progress_fingerprint("请读取文件并核对内容。", [payload], [])
                if last_fp is not None and fp != last_fp:
                    count = 0
                count += 1
                last_fp = fp
                if count >= _SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES:
                    return "stopped", count
            return "never", count

        self.assertEqual(simulate(stuck)[0], "stopped")
        self.assertEqual(simulate(stuck)[1], _SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES)
        self.assertEqual(simulate(legit)[0], "never")


if __name__ == "__main__":
    unittest.main()
