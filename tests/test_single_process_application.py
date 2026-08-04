from __future__ import annotations

import os
import importlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime
from total_gateway.bootstrap import GatewayConfig
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.runtime import GatewayRuntime


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "single-process-test-token-" + "x" * 40


class SingleProcessApplicationTests(unittest.TestCase):
    def _environment(self, temporary: str) -> dict[str, str]:
        root = Path(temporary)
        return {
            "APPDATA": str(root / "appdata"),
            "TIANGONG_DOCUMENTS_PATH": str(root / "documents"),
            "TIANGONG_LIFE_DATA_ROOT": str(root / "life-data"),
            "TIANGONG_LIFE_RUNTIME_ROOT": str(root / "life-runtime"),
        }

    def test_gateway_shutdown_keeps_epoch_and_ledgers_when_worker_does_not_quiesce(self) -> None:
        from total_gateway.runtime import GatewayRuntime

        calls: list[str] = []

        class Component:
            def __init__(self, name: str, *, fail: bool = False) -> None:
                self.name = name
                self.fail = fail

            def close(self) -> None:
                calls.append(self.name)
                if self.fail:
                    raise RuntimeError(self.name + "-busy")

        runtime = object.__new__(GatewayRuntime)
        runtime.cutover = Component("cutover")
        runtime.orchestration = Component("orchestration", fail=True)
        runtime.backend_service = Component("backend")
        runtime.communication_service = Component("communication")
        runtime.life_service = Component("life")
        runtime.facts = Component("facts")
        runtime.objects = Component("objects")
        runtime.store = Component("store")
        runtime.lease = Component("lease")
        with self.assertRaisesRegex(RuntimeError, "ingress failed to close"):
            runtime.close()
        self.assertEqual(calls, ["cutover", "orchestration"])
        self.assertNotIn("backend", calls)
        self.assertNotIn("communication", calls)
        self.assertNotIn("life", calls)
        self.assertNotIn("lease", calls)
        self.assertNotIn("store", calls)

    def test_gateway_shutdown_keeps_life_and_communication_when_backend_is_busy(self) -> None:
        calls: list[str] = []

        class Component:
            def __init__(self, name: str, *, fail: bool = False) -> None:
                self.name = name
                self.fail = fail

            def close(self) -> None:
                calls.append(self.name)
                if self.fail:
                    raise RuntimeError(self.name + "-busy")

        runtime = object.__new__(GatewayRuntime)
        runtime.cutover = Component("cutover")
        runtime.orchestration = Component("orchestration")
        runtime.backend_service = Component("backend", fail=True)
        runtime.communication_service = Component("communication")
        runtime.life_service = Component("life")
        runtime.facts = Component("facts")
        runtime.objects = Component("objects")
        runtime.store = Component("store")
        runtime.lease = Component("lease")
        with self.assertRaisesRegex(RuntimeError, "execution failed to close"):
            runtime.close()
        self.assertEqual(calls, ["cutover", "orchestration", "backend"])
        self.assertNotIn("communication", calls)
        self.assertNotIn("life", calls)
        self.assertNotIn("lease", calls)

    def test_production_refuses_legacy_multi_process_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                GatewayConfig(
                    environment="production",
                    deployment_mode="standalone_services",
                    port=7184,
                    state_root=root / "gateway",
                )

    def test_environment_defaults_to_embedded_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                **self._environment(temporary),
                "TIANGONG_GATEWAY_ENVIRONMENT": "test",
                "TIANGONG_GATEWAY_PORT": "0",
                "TIANGONG_GATEWAY_STATE_ROOT": str(root / "gateway"),
            }
            config = GatewayConfig.from_environment(env)
            self.assertEqual(config.deployment_mode, "embedded")

    def test_embedded_runtime_hosts_all_logical_systems_without_child_listeners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            self._environment(temporary),
            clear=False,
        ):
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            config = GatewayConfig(
                environment="test",
                deployment_mode="embedded",
                port=0,
                state_root=root / "gateway",
                min_free_bytes=1_048_576,
                backend_internal_token=TOKEN,
                release_source_root=ROOT,
                workspace_root=workspace,
                skill_root=ROOT / "app/backend/tiangong-backend/_internal/omni_body_skill",
            )
            runtime = GatewayRuntime.start(config)
            try:
                self.assertIsNotNone(runtime.backend_service)
                self.assertIsNotNone(runtime.life_service)
                self.assertIsNotNone(runtime.communication_service)
                self.assertIsNone(runtime.readiness_collector)

                for service in (
                    runtime.backend_service,
                    runtime.life_service,
                    runtime.communication_service,
                ):
                    health = service.health_payload()
                    self.assertEqual(health["deployment_mode"], "embedded")
                    self.assertIsNone(health.get("listener_port"))

                status, ready = runtime.ready_payload()
                self.assertEqual(status, 200, ready)
                self.assertEqual(ready["status"], "READY")
                self.assertEqual(ready["deployment_mode"], "embedded")

                backend_health = runtime.backend_service.health_payload()
                self.assertTrue(backend_health["legacy_life_scheduler_disabled"])
                self.assertEqual(backend_health["life_authority"], "embedded_life_kernel")
                self.assertFalse(runtime.backend_service.scheduler.xintiao.yunxing_zhong)
                self.assertIsNone(runtime.backend_service.scheduler.life_orchestrator)

                status, panel, _ = runtime.life_service.request(
                    "GET", "/api/v1/v3/life/panel", None
                )
                self.assertEqual(status, 200, panel)
                self.assertEqual(panel["api_contract"], "tiangong.life.api.v2")
                self.assertTrue(panel["ok"])
                self.assertTrue(panel["writer"]["active"])
                self.assertTrue(panel["scheduler"]["running"])

                # The same provider is mounted into the model-visible
                # life.body.state.query action.  Exercise the real embedded
                # backend + Life projection instead of a registry-only check.
                body_reader = runtime.backend_service._body_state_query_provider
                self.assertTrue(callable(body_reader))
                body_snapshot = body_reader({
                    "sections": ["identity", "health", "emotion", "context", "body", "summary"],
                    "recent_limit": 2,
                })
                self.assertTrue(body_snapshot["ok"], body_snapshot)
                self.assertTrue(body_snapshot["read_only"])
                self.assertEqual(
                    body_snapshot["life"]["projection_status"],
                    "authoritative",
                )
                self.assertIn("health", body_snapshot["runtime_body"]["sections"])
                self.assertEqual(len(body_snapshot["state_sha256"]), 64)
                body_log = (
                    Path(os.environ["APPDATA"])
                    / "tiangong-v3-qiyuan"
                    / "runtime"
                    / "logs"
                    / "gateway_body_state_reads.log"
                )
                self.assertTrue(body_log.is_file())
                self.assertIn(
                    '"action":"life.body.state.query"',
                    body_log.read_text(encoding="utf-8"),
                )

                # Prove the model-visible Omni action itself reaches the
                # provider mounted above, rather than only testing the helper.
                wrapper = importlib.import_module("v3.jineng.jirou_ceng")._load_omni_body_module()
                runtime_class, config_class, import_error = wrapper._import_runtime()
                self.assertIsNone(import_error)
                body_tool = runtime_class(config_class(
                    workspace=str(workspace),
                    fact_kernel_enabled=False,
                ))
                tool_snapshot = body_tool.run(
                    "life.body.state.query",
                    "",
                    {"sections": ["health", "context"], "recent_limit": 1},
                )
                self.assertTrue(tool_snapshot["success"], tool_snapshot)
                self.assertEqual(tool_snapshot["risk_level"], "A0")
                self.assertEqual(
                    tool_snapshot["life"]["projection_status"],
                    "authoritative",
                )
            finally:
                runtime.close()

    def test_embedded_backend_has_one_process_global_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            self._environment(temporary),
            clear=False,
        ):
            first = EmbeddedBackendRuntime.start(release_source_root=ROOT)
            try:
                with self.assertRaisesRegex(Exception, "process_owner_exists"):
                    EmbeddedBackendRuntime.start(release_source_root=ROOT)
            finally:
                first.close()
            second = EmbeddedBackendRuntime.start(release_source_root=ROOT)
            second.close()

    def test_run_control_bypasses_busy_model_execution_lane(self) -> None:
        core_lock = threading.RLock()
        started = threading.Event()
        release = threading.Event()

        class Bridge:
            _core_execution_lock = core_lock

            def chuli_duihua(self, _text, _user, _context):
                with core_lock:
                    started.set()
                    release.wait(5)
                    return json.dumps({"huifu": "done"}, ensure_ascii=False)

            def run_control(self, payload):
                return {"ok": True, "request_id": payload.get("request_id")}

        class Module:
            @staticmethod
            def _safe_bridge_json(value, *, source):
                del source
                return json.loads(value)

        backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
        backend._lock = threading.RLock()
        backend._closed = False
        backend.qiaojie = Bridge()
        backend._module = Module()
        chat_result = []
        thread = threading.Thread(
            target=lambda: chat_result.append(backend.request(
                "POST",
                "/api/v1/gateway/internal/inbound",
                {"text": "long task", "request_id": "req_busy"},
            )),
            daemon=True,
        )
        thread.start()
        self.assertTrue(started.wait(2))
        begin = time.monotonic()
        status, payload, _ = backend.request(
            "POST",
            "/api/v1/run/control",
            {"request_id": "req_busy", "action": "stop"},
        )
        elapsed = time.monotonic() - begin
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertLess(elapsed, 0.5)
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(chat_result[0][0], 200)

    def test_embedded_backend_exposes_model_optimization_projection(self) -> None:
        class Bridge:
            _core_execution_lock = threading.RLock()

        class Module:
            @staticmethod
            def _llm_optimization_status():
                return {
                    "ok": True,
                    "trace_rows": 7,
                    "active_provider": {
                        "provider": "minimax_m3",
                        "cache_hit_ratio": 0.75,
                    },
                }

        backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
        backend._lock = threading.RLock()
        backend._closed = False
        backend._closing = False
        backend.qiaojie = Bridge()
        backend._module = Module()

        status, payload, media_type = backend.request(
            "GET",
            "/api/v1/llm/optimization",
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["trace_rows"], 7)
        self.assertEqual(payload["active_provider"]["cache_hit_ratio"], 0.75)
        self.assertEqual(media_type, "application/json; charset=utf-8")

    def test_embedded_life_execution_commit_is_idempotent_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            payload = {
                "schema": "tiangong.life.execution-terminal.v1",
                "request_id": "req_single_process_life_commit",
                "run_id": "run_single_process_life_commit",
                "generation": 1,
                "life_id": str(life._active()["life_id"]),
                "session_scope_hash": "a" * 64,
                "status": "completed",
                "user_goal_sha256": "b" * 64,
                "final_result_sha256": "c" * 64,
                "fact_ids": ["fact_single_process_life_commit"],
                "completed_at_ms": 1_000,
            }
            try:
                first = life.commit_execution(payload)
                second = life.commit_execution(payload)
                self.assertFalse(first["duplicate"])
                self.assertTrue(second["duplicate"])
                status, journal, _ = life.request(
                    "GET", "/api/v1/v3/life/journal/verify", None
                )
                self.assertEqual(status, 200, journal)
                self.assertTrue(journal["valid"])
                self.assertEqual(journal["event_count"], 1)
                with self.assertRaises(EmbeddedLifeError) as caught:
                    life.commit_execution({**payload, "status": "failed"})
                self.assertEqual(caught.exception.code, "life.execution.commit_conflict")
            finally:
                life.close()

    def test_life_kernel_has_one_writer_across_embedded_and_standalone_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            embedded = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="embedded",
            )
            try:
                with self.assertRaises(EmbeddedLifeError) as caught:
                    EmbeddedLifeRuntime(
                        data_root=data_root,
                        runtime_root=runtime_root,
                        mode="standalone",
                    )
                self.assertEqual(caught.exception.code, "life.writer.already_owned")
            finally:
                embedded.close()

            standalone = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="standalone",
            )
            try:
                health = standalone.health_payload()
                self.assertEqual(health["deployment_mode"], "standalone")
                self.assertTrue(health["scheduler"]["running"])
            finally:
                standalone.close()


if __name__ == "__main__":
    unittest.main()
