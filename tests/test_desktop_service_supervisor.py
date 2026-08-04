from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"


class DesktopServiceSupervisorTests(unittest.TestCase):
    def run_node(self, source: str) -> dict[str, object]:
        completed = subprocess.run(
            ["node", "-e", source],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        return json.loads(completed.stdout)

    def test_supervisor_coalesces_restart_tracks_readiness_and_drains_in_reverse(self) -> None:
        module_path = json.dumps(str(APP_ROOT / "service-supervisor.js"))
        result = self.run_node(
            f"""
            const {{ ServiceSupervisor }} = require({module_path});
            (async () => {{
              let startCount = 0;
              let stopCount = 0;
              let healthy = true;
              const transitions = [];
              const supervisor = new ServiceSupervisor({{
                services: [{{
                  name: "one",
                  start: async () => {{ startCount += 1; healthy = true; return true; }},
                  health: async () => healthy,
                  ready: async () => false,
                  stop: async () => {{ stopCount += 1; }},
                }}],
                failureThreshold: 2,
                restartDelayMs: 0,
                onTransition: (event) => transitions.push(event.status),
              }});
              await Promise.all([supervisor.start("one"), supervisor.start("one")]);
              const afterStart = supervisor.snapshot().one;
              healthy = false;
              await supervisor.poll();
              const afterFirstFailure = supervisor.snapshot().one;
              await supervisor.poll();
              const afterRestart = supervisor.snapshot().one;
              await supervisor.drainAll("test-complete");

              const order = [];
              const ordered = new ServiceSupervisor({{
                restartDelayMs: 0,
                services: [
                  {{ name: "backend", phase: 0, start: async () => {{ order.push("start:backend"); return true; }}, health: async () => true, stop: async () => order.push("stop:backend") }},
                  {{ name: "life", phase: 0, start: async () => {{ order.push("start:life"); return true; }}, health: async () => true, stop: async () => order.push("stop:life") }},
                  {{ name: "total-gateway", phase: 1, start: async () => {{ order.push("start:total-gateway"); return true; }}, health: async () => true, stop: async () => order.push("stop:total-gateway") }},
                  {{ name: "communication", phase: 2, start: async () => {{ order.push("start:communication"); return true; }}, health: async () => true, stop: async () => order.push("stop:communication") }},
                ],
              }});
              await ordered.startAll();
              await ordered.drainAll("ordered-test");

              const raceOrder = [];
              let releaseSlow;
              const slowGate = new Promise((resolve) => {{ releaseSlow = resolve; }});
              const raced = new ServiceSupervisor({{
                restartDelayMs: 0,
                services: [
                  {{ name: "slow", phase: 0, start: async () => {{ raceOrder.push("start:slow"); await slowGate; return true; }}, health: async () => true, stop: async () => raceOrder.push("stop:slow") }},
                  {{ name: "must-not-start", phase: 1, start: async () => {{ raceOrder.push("start:must-not-start"); return true; }}, health: async () => true, stop: async () => raceOrder.push("stop:must-not-start") }},
                ],
              }});
              const racedStart = raced.startAll();
              await Promise.resolve();
              const racedDrain = raced.drainAll("quit-during-start");
              releaseSlow();
              await Promise.all([racedStart, racedDrain]);
              process.stdout.write(JSON.stringify({{
                startCount, stopCount, afterStart, afterFirstFailure, afterRestart,
                final: supervisor.snapshot().one, transitions, order, raceOrder,
              }}));
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            """
        )
        self.assertEqual(result["startCount"], 2)
        self.assertEqual(result["stopCount"], 2)
        self.assertTrue(result["afterStart"]["running"])
        self.assertFalse(result["afterStart"]["ready"])
        self.assertEqual(result["afterFirstFailure"]["status"], "DEGRADED")
        self.assertEqual(result["afterFirstFailure"]["consecutiveFailures"], 1)
        self.assertTrue(result["afterRestart"]["running"])
        self.assertEqual(result["afterRestart"]["restartCount"], 1)
        self.assertEqual(result["final"]["status"], "STOPPED")
        self.assertEqual(
            result["order"][-4:],
            ["stop:communication", "stop:total-gateway", "stop:life", "stop:backend"],
        )
        self.assertNotIn("start:must-not-start", result["raceOrder"])
        self.assertEqual(result["raceOrder"][-2:], ["stop:must-not-start", "stop:slow"])

    def test_ready_failure_is_degraded_without_orphaning_the_live_service(self) -> None:
        module_path = json.dumps(str(APP_ROOT / "service-supervisor.js"))
        result = self.run_node(
            f"""
            const {{ ServiceSupervisor }} = require({module_path});
            (async () => {{
              let readyCalls = 0;
              const supervisor = new ServiceSupervisor({{
                restartDelayMs: 0,
                services: [{{
                  name: "svc",
                  start: async () => true,
                  health: async () => true,
                  ready: async () => {{ readyCalls += 1; if (readyCalls === 1) throw new Error("probe_broken"); return false; }},
                  stop: async () => {{}},
                }}],
              }});
              const started = await supervisor.start("svc");
              const afterStart = supervisor.snapshot().svc;
              await supervisor.poll();
              process.stdout.write(JSON.stringify({{ started, afterStart, afterPoll: supervisor.snapshot().svc }}));
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            """
        )
        self.assertTrue(result["started"]["running"])
        self.assertFalse(result["started"]["ready"])
        self.assertEqual(result["afterStart"]["status"], "DEGRADED")
        self.assertEqual(result["afterStart"]["lastError"], "probe_broken")
        self.assertEqual(result["afterPoll"]["status"], "DEGRADED")
        self.assertEqual(result["afterPoll"]["lastError"], "service_not_ready")

    def test_targeted_stop_does_not_enter_global_drain_or_stop_siblings(self) -> None:
        module_path = json.dumps(str(APP_ROOT / "service-supervisor.js"))
        result = self.run_node(
            f"""
            const {{ ServiceSupervisor }} = require({module_path});
            (async () => {{
              const calls = [];
              const service = (name) => ({{
                name,
                start: async () => {{ calls.push(`start:${{name}}`); return true; }},
                health: async () => true,
                ready: async () => true,
                stop: async (reason) => calls.push(`stop:${{name}}:${{reason}}`),
              }});
              const supervisor = new ServiceSupervisor({{
                restartDelayMs: 0,
                services: [service("backend"), service("communication")],
              }});
              await supervisor.startAll();
              const stopped = await supervisor.stop("backend", "workspace-change");
              process.stdout.write(JSON.stringify({{
                stopped,
                draining: supervisor.draining,
                snapshot: supervisor.snapshot(),
                calls,
              }}));
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            """
        )
        self.assertFalse(result["draining"])
        self.assertFalse(result["stopped"]["running"])
        self.assertEqual(result["snapshot"]["backend"]["status"], "STOPPED")
        self.assertEqual(result["snapshot"]["communication"]["status"], "RUNNING")
        self.assertNotIn("stop:communication:workspace-change", result["calls"])

    def test_restart_backoff_is_exponential_capped_and_resets_after_stability(self) -> None:
        module_path = json.dumps(str(APP_ROOT / "service-supervisor.js"))
        result = self.run_node(
            f"""
            const {{ ServiceSupervisor }} = require({module_path});
            (async () => {{
              const delays = [];
              let now = 10_000;
              const originalNow = Date.now;
              Date.now = () => now;
              try {{
                const supervisor = new ServiceSupervisor({{
                  restartDelayMs: 0,
                  restartBackoffBaseMs: 25,
                  restartBackoffMaxMs: 100,
                  healthyResetMs: 500,
                  delay: async (milliseconds) => delays.push(milliseconds),
                  services: [{{
                    name: "svc",
                    start: async () => true,
                    health: async () => true,
                    ready: async () => true,
                    stop: async () => {{}},
                  }}],
                }});
                await supervisor.start("svc");
                await supervisor.restart("svc", "failure-1");
                await supervisor.restart("svc", "failure-2");
                await supervisor.restart("svc", "failure-3");
                await supervisor.restart("svc", "failure-4");
                const beforeReset = delays.slice();

                now += 501;
                await supervisor.poll();
                await supervisor.restart("svc", "failure-after-stability");
                process.stdout.write(JSON.stringify({{
                  beforeReset,
                  delays,
                  snapshot: supervisor.snapshot().svc,
                }}));
              }} finally {{
                Date.now = originalNow;
              }}
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            """
        )
        self.assertEqual(result["beforeReset"], [25, 50, 100, 100])
        self.assertEqual(result["delays"], [25, 50, 100, 100, 25])
        self.assertEqual(result["snapshot"]["restartCount"], 5)
        self.assertEqual(result["snapshot"]["status"], "RUNNING")

    def test_main_has_one_gateway_process_without_legacy_rollback(self) -> None:
        main = (APP_ROOT / "main.js").read_text(encoding="utf-8")
        preload = (APP_ROOT / "preload.js").read_text(encoding="utf-8")
        server = (ROOT / "src" / "total_gateway" / "server.py").read_text(encoding="utf-8")
        self.assertIn('const DEFAULT_TOTAL_GATEWAY_PORT = "7184";', main)
        self.assertIn('const DEFAULT_BACKEND_PORT = "7174";', main)
        self.assertIn('const DEFAULT_LIFE_PORT = "7175";', main)
        self.assertIn('const DEFAULT_COMMUNICATION_PORT = "7176";', main)
        self.assertNotIn("process.env.TIANGONG_BACKEND_URL ||", main)
        self.assertNotIn("process.env.TIANGONG_LIFE_URL ||", main)
        self.assertNotIn("process.env.TIANGONG_COMMUNICATION_URL ||", main)
        self.assertNotIn('LEGACY_MULTI_PROCESS', main)
        self.assertNotIn('name: "backend"', main)
        self.assertNotIn('name: "life"', main)
        self.assertNotIn('name: "communication"', main)
        self.assertEqual(main.count('name: "total-gateway"'), 1)
        self.assertIn('TIANGONG_GATEWAY_DEPLOYMENT_MODE: "embedded"', main)
        self.assertIn(
            'TIANGONG_GATEWAY_ENVIRONMENT: entry.pythonPath ? "development" : "production"',
            main,
        )
        self.assertIn("serviceSupervisor.startAll()", main)
        self.assertIn('serviceSupervisor.drainAll("before-quit")', main)
        self.assertIn("function totalGatewayEntries()", main)
        self.assertIn('kind: "release-bound-executable"', main)
        self.assertIn('kind: "embedded-python-fallback"', main)
        self.assertIn("for (const [entryIndex, entry] of entries.entries())", main)
        self.assertLess(
            main.index('kind: "release-bound-executable"'),
            main.index('kind: "embedded-python-fallback"'),
        )
        self.assertIn('writeDesktopDiagnostic("total-gateway-entry-attempt"', main)
        self.assertIn('writeDesktopDiagnostic("total-gateway-entry-selected"', main)
        self.assertIn("child && child.exitCode !== null", main)
        self.assertIn("event.preventDefault();", main)
        self.assertIn("app.requestSingleInstanceLock()", main)
        self.assertIn('handleTrusted("services:getStatus"', main)
        self.assertIn('ipcRenderer.invoke("services:getStatus")', preload)
        self.assertIn('child.kill(process.platform === "win32" ? "SIGBREAK" : "SIGTERM")', main)
        self.assertIn('for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK")', server)
        self.assertIn('name="tiangong-gateway-drain"', server)

    def test_main_refuses_legacy_7176_and_separates_renderer_service_credentials(self) -> None:
        main = (APP_ROOT / "main.js").read_text(encoding="utf-8")
        preload = (APP_ROOT / "preload.js").read_text(encoding="utf-8")
        desktop_api = (ROOT / "src" / "total_gateway" / "desktop_api.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'const LEGACY_COMMUNICATION_EXE_SHA256 = "613f569ee889b1f365b4678f02a2f2dc12507a52858a91d6b8a553880e2d11f6";',
            main,
        )
        self.assertIn('args: ["-m", "communication_service"]', main)
        self.assertNotIn("communication_server.py", main)
        self.assertNotIn("communicationServiceDir", main)
        self.assertIn("legacy-communication-executable-refused", main)

        environment_start = main.index("function communicationServiceEnvironment")
        environment_end = main.index("async function startCommunicationService", environment_start)
        communication_environment = main[environment_start:environment_end]
        self.assertNotIn("const env = { ...process.env }", communication_environment)
        for forbidden in (
            "TIANGONG_BACKEND_URL",
            "TIANGONG_LIFE_URL",
            "TIANGONG_DESKTOP_TOKEN",
            "TIANGONG_ARTIFACT_OPEN_TOKEN",
            "TIANGONG_DESKTOP_WORKSPACE_ROOT",
        ):
            self.assertNotIn(forbidden, communication_environment)
        self.assertIn("TIANGONG_COMMUNICATION_TOTAL_GATEWAY_URL: TOTAL_GATEWAY_URL", main)

        self.assertIn("const BACKEND_INTERNAL_TOKEN = crypto.randomBytes(48)", main)
        self.assertIn("const LIFE_INTERNAL_TOKEN = crypto.randomBytes(48)", main)
        self.assertIn("const COMMUNICATION_GATEWAY_TOKEN = crypto.randomBytes(48)", main)
        self.assertIn('onTrusted("gateway:getBootstrap"', main)
        self.assertIn('ipcRenderer.sendSync("gateway:getBootstrap")', preload)
        self.assertNotIn("process.env.TIANGONG_DESKTOP_TOKEN", preload)
        self.assertNotIn('require("./', preload)
        self.assertIn("TIANGONG_BACKEND_INTERNAL_TOKEN: BACKEND_INTERNAL_TOKEN", main)
        self.assertIn("TIANGONG_LIFE_INTERNAL_TOKEN: LIFE_INTERNAL_TOKEN", main)
        backend_environment_start = main.index("async function startBackend")
        backend_environment_end = main.index("function startBackendWatchdog", backend_environment_start)
        backend_environment = main[backend_environment_start:backend_environment_end]
        self.assertNotIn("TIANGONG_LIFE_URL: TOTAL_GATEWAY_URL", backend_environment)
        self.assertIn("7174 only consumes the already-authorized life context", backend_environment)
        self.assertIn(
            "TIANGONG_COMMUNICATION_GATEWAY_TOKEN: COMMUNICATION_GATEWAY_TOKEN",
            main,
        )
        self.assertIn(
            "TIANGONG_GATEWAY_COMMUNICATION_TOKEN: COMMUNICATION_GATEWAY_TOKEN",
            main,
        )
        self.assertIn("backend_internal_token", desktop_api)
        self.assertIn("life_internal_token", desktop_api)
        self.assertNotIn("BACKEND_INTERNAL_TOKEN", preload)
        self.assertNotIn("LIFE_INTERNAL_TOKEN", preload)
        self.assertNotIn("COMMUNICATION_GATEWAY_TOKEN", preload)


class TotalGatewayShutdownTests(unittest.TestCase):
    def free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def wait_for_health(self, port: int, process: subprocess.Popen[bytes]) -> dict[str, object]:
        deadline = time.monotonic() + 10
        url = f"http://127.0.0.1:{port}/health"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=2)
                self.fail(f"gateway exited before health: {stderr.decode('utf-8', 'replace')}")
            try:
                with urllib.request.urlopen(url, timeout=0.25) as response:
                    if response.status == 200:
                        return json.loads(response.read())
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        self.fail("gateway did not become healthy")

    def start_gateway(self, state_root: Path, port: int) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("TIANGONG_GATEWAY_"):
                del env[name]
        env.update(
            {
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TIANGONG_GATEWAY_ENVIRONMENT": "test",
                "TIANGONG_GATEWAY_PORT": str(port),
                "TIANGONG_GATEWAY_STATE_ROOT": str(state_root),
            }
        )
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        return subprocess.Popen(
            [sys.executable, "-m", "total_gateway"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )

    def stop_gateway(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
            process.wait(timeout=10)
        process.communicate(timeout=2)

    def test_signal_drain_releases_single_instance_and_next_epoch_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "gateway"
            first_port = self.free_port()
            first = self.start_gateway(state_root, first_port)
            second: subprocess.Popen[bytes] | None = None
            try:
                first_health = self.wait_for_health(first_port, first)
            except Exception:
                if first.poll() is None:
                    first.kill()
                first.communicate(timeout=2)
                raise
            finally:
                if first.poll() is None:
                    self.stop_gateway(first)

            try:
                second_port = self.free_port()
                second = self.start_gateway(state_root, second_port)
                second_health = self.wait_for_health(second_port, second)
                self.assertEqual(first_health["gateway_epoch"], 1)
                self.assertEqual(second_health["gateway_epoch"], 2)
            finally:
                if second is not None and second.poll() is None:
                    self.stop_gateway(second)
