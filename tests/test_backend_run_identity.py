from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class BackendRunIdentityTests(unittest.TestCase):
    def _module(self):
        return importlib.import_module("v3.duihua_qiaojie")

    def test_unsafe_request_ids_do_not_collide_as_snapshot_paths(self) -> None:
        module = self._module()
        first = module._safe_request_id("agent/a")
        second = module._safe_request_id("agent?a")
        self.assertNotEqual(first, second)
        self.assertNotIn("/", first)
        self.assertNotIn("?", second)

    def test_claim_rejects_non_opaque_caller_supplied_request_id(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"TIANGONG_RUN_STATE_DIR": temporary},
            clear=False,
        ):
            manager = module.RunControlManager()
            with self.assertRaisesRegex(ValueError, "invalid_request_id"):
                manager.claim("../agent-run", "hello")
            with self.assertRaisesRegex(ValueError, "invalid_request_id"):
                manager.claim("a" * 161, "hello")
            handle, disposition, _cached = manager.claim("agent-run:1", "hello")
            self.assertEqual(disposition, "started")
            self.assertEqual(handle.request_id, "agent-run:1")

    def test_contextvars_isolate_parallel_agent_expression_state(self) -> None:
        module = importlib.import_module("v3.run_context")
        barrier = __import__("threading").Barrier(2)
        results: dict[str, tuple[str, str]] = {}

        def run(owner: str) -> None:
            with module.bind_run_context({
                "request_id": f"req-{owner}",
                "run_id": f"run-{owner}",
                "life_id": f"life-{owner}",
                "agent_id": owner,
            }):
                module.set_last_expression({"owner": owner})
                barrier.wait(timeout=2)
                results[owner] = (
                    module.current_run_context().agent_id,
                    str(module.get_last_expression()["owner"]),
                )

        import threading
        threads = [threading.Thread(target=run, args=(owner,)) for owner in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results, {"A": ("A", "A"), "B": ("B", "B")})

    def test_natural_final_response_persists_after_failed_terminal_verdict(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"TIANGONG_RUN_STATE_DIR": temporary},
            clear=False,
        ):
            manager = module.RunControlManager()
            handle, disposition, _cached = manager.claim("agent-run:failed-closeout", "处理文件")
            self.assertEqual(disposition, "started")
            handle.finish(False, "结果检查未通过")
            manager.store_final_response(handle.request_id, "我已经核对过了，目前还差最终格式验收。")
            persisted = module.load_run_snapshot(handle.request_id)
            self.assertEqual(
                persisted["final_response"],
                "我已经核对过了，目前还差最终格式验收。",
            )

    def test_resume_snapshot_filename_cannot_escape_run_state_root(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"TIANGONG_RUN_STATE_DIR": temporary},
            clear=False,
        ):
            module._save_resume_snapshot({"snapshot_id": "../../escape", "value": 1})
            root = Path(temporary) / "run-state"
            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].parent, root)
            self.assertNotEqual(files[0].name, "escape.json")


if __name__ == "__main__":
    unittest.main()
