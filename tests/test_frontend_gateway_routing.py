from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from total_gateway.desktop_api import DESKTOP_ROUTES, NATIVE_DESKTOP_ROUTES


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "app" / "frontend-v2" / "renderer" / "runtime"
PRIVILEGED_LIFE_BRIDGE_ROUTES = {
    ("POST", "/api/v1/v3/life/context/compile"),
    ("POST", "/api/v1/v3/life/context/compile-and-authorize"),
    ("POST", "/api/v1/v3/life/execution/prepare"),
    # Model-only learning routing needs the gateway to compose the backend
    # decision with the Life publication transaction; it is not a direct Life
    # upstream call.
    ("POST", "/api/v1/v3/life/learning/decide"),
}


def declared_life_api_routes() -> set[tuple[str, str]]:
    life_api_uri = (RUNTIME_ROOT / "life-api.mjs").as_uri()
    script = f"""
      import {{ LIFE_API_ROUTES }} from {json.dumps(life_api_uri)};
      process.stdout.write(JSON.stringify(Object.values(LIFE_API_ROUTES).map((item) => [item.method, item.path])));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    return {tuple(item) for item in json.loads(completed.stdout)}


class FrontendGatewayRoutingTests(unittest.TestCase):
    def test_setup_required_is_a_hard_send_gate_not_a_ready_life(self) -> None:
        kernel_uri = (RUNTIME_ROOT / "frontend-kernel.mjs").as_uri()
        script = f"""
          import {{ createFrontendKernel }} from {json.dumps(kernel_uri)};
          const responses = new Map([
            ["/health", {{ component_id: "tiangong-total-gateway", api_contract: "tiangong.total-gateway.api.v1", life_ready: false }}],
            ["/api/v1/v3/state", {{ setup_required: true, ui: {{ lifecycle: {{ available: false, phase: "unbound" }} }} }}],
            ["/api/v1/v3/life/panel", {{ setup_required: true }}],
          ]);
          const fetchImpl = async (url) => {{
            const payload = responses.get(new URL(String(url)).pathname) || {{ ok: true }};
            return {{ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(payload) }};
          }};
          const kernel = createFrontendKernel({{
            bridge: {{ getGatewayUrl: () => "http://127.0.0.1:7184", getGatewayHeaders: () => ({{}}) }},
            fetchImpl,
            locationRef: null,
          }});
          await kernel.boot();
          process.stdout.write(JSON.stringify(kernel.snapshot()));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        state = json.loads(completed.stdout)
        self.assertEqual(state["phase"], "setup_required")
        self.assertFalse(state["life"]["ready"])
        self.assertFalse(state["life"]["available"])
        self.assertEqual(state["lastError"]["code"], "life_setup_required")

    def test_kernel_preserves_structured_upstream_error_code(self) -> None:
        kernel_uri = (RUNTIME_ROOT / "frontend-kernel.mjs").as_uri()
        script = f"""
          import {{ createFrontendKernel }} from {json.dumps(kernel_uri)};
          const kernel = createFrontendKernel({{
            bridge: {{ getGatewayUrl: () => "http://127.0.0.1:7184", getGatewayHeaders: () => ({{}}) }},
            fetchImpl: async () => ({{
              ok: false,
              status: 503,
              statusText: "Unavailable",
              text: async () => JSON.stringify({{ error_code: "life.identity_schema_mismatch", detail: "identity rejected" }}),
            }}),
            locationRef: null,
          }});
          try {{ await kernel.request("/api/v1/v3/life/panel"); }}
          catch (error) {{ process.stdout.write(JSON.stringify({{ code: error.code, message: error.message }})); }}
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        error = json.loads(completed.stdout)
        self.assertEqual(error["code"], "life.identity_schema_mismatch")
        self.assertEqual(error["message"], "identity rejected")

    def test_kernel_sends_backend_life_and_communication_paths_only_to_7184(self) -> None:
        kernel_uri = (RUNTIME_ROOT / "frontend-kernel.mjs").as_uri()
        script = f"""
          import {{ createFrontendKernel }} from {json.dumps(kernel_uri)};
          const calls = [];
          const responses = new Map([
            ["/health", {{ component_id: "tiangong-total-gateway", api_contract: "tiangong.total-gateway.api.v1", status: "ALIVE" }}],
            ["/api/v1/v3/state", {{ ui: {{ lifecycle: {{ available: true, phase: "alive" }} }} }}],
            ["/api/v1/v3/life/panel", {{ setup_required: false }}],
          ]);
          const fetchImpl = async (url) => {{
            calls.push(String(url));
            const path = new URL(String(url)).pathname;
            const payload = responses.get(path) || {{ ok: true }};
            return {{ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(payload) }};
          }};
          const bridge = {{
            getGatewayUrl: () => "http://127.0.0.1:7184",
            getBackendUrl: () => "http://127.0.0.1:7174",
            getLifeUrl: () => "http://127.0.0.1:7175",
            getCommunicationUrl: () => "http://127.0.0.1:7176",
            getGatewayHeaders: () => ({{ "X-Tiangong-Token": "synthetic" }}),
          }};
          const kernel = createFrontendKernel({{ bridge, fetchImpl, locationRef: null }});
          await kernel.boot();
          await kernel.request("/api/v1/llm/status");
          await kernel.request("/api/v1/v3/life/panel");
          await kernel.request("/api/v1/gateway/links/status");
          process.stdout.write(JSON.stringify({{ calls, state: kernel.snapshot() }}));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        result = json.loads(completed.stdout)
        self.assertTrue(result["calls"])
        self.assertTrue(all(url.startswith("http://127.0.0.1:7184/") for url in result["calls"]))
        self.assertEqual(result["state"]["phase"], "ready")
        self.assertTrue(result["state"]["compatible"])

    def test_renderer_has_one_gateway_base_and_no_chat_fallback(self) -> None:
        preload = (ROOT / "app" / "preload.js").read_text(encoding="utf-8")
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        kernel = (RUNTIME_ROOT / "frontend-kernel.mjs").read_text(encoding="utf-8")
        runtime = (RUNTIME_ROOT / "http-runtime.mjs").read_text(encoding="utf-8")
        legacy_frontends = "\n".join(
            (ROOT / "app" / name).read_text(encoding="utf-8")
            for name in ("桌面宠物.html", "zhuomian.html", "对话窗口.html")
        )
        self.assertIn('ipcRenderer.sendSync("gateway:getBootstrap")', preload)
        self.assertIn('gatewayBootstrap.gatewayUrl || "http://127.0.0.1:7184"', preload)
        self.assertIn("const backendUrl = gatewayUrl;", preload)
        self.assertIn("const lifeUrl = gatewayUrl;", preload)
        self.assertIn("const communicationUrl = gatewayUrl;", preload)
        self.assertIn('const DEFAULT_API_BASE = "http://127.0.0.1:7184";', kernel)
        self.assertIn('const DEFAULT_API_BASE = "http://127.0.0.1:7184";', runtime)
        self.assertNotIn("DEFAULT_LIFE_API_BASE", kernel)
        self.assertNotIn("DEFAULT_COMMUNICATION_API_BASE", kernel)
        self.assertNotIn('apiJson("/chat"', runtime)
        self.assertNotIn('fetchSse("/chat"', runtime)
        self.assertNotIn('fetchSse("/api/v1/gateway/internal/inbound"', runtime)
        self.assertNotIn('apiJson("/api/v1/gateway/internal/inbound"', runtime)
        self.assertIn('apiJson("/api/v1/gateway/desktop/inbound"', runtime)
        self.assertIn("/api/v1/gateway/desktop/status", runtime)
        self.assertNotIn('backend: "v3_direct"', runtime)
        self.assertNotIn("LEGACY_FRONTEND_FILE", main)
        self.assertIn('throw new Error("primary_frontend_missing")', main)
        self.assertIn(
            "env.TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT = path.resolve(entry.pythonPath)",
            main,
        )
        self.assertNotIn("127.0.0.1:7174", legacy_frontends)
        self.assertNotIn("settingsApiJson('/chat'", legacy_frontends)
        self.assertNotIn("falling back to legacy chat", legacy_frontends)
        self.assertIn("/api/v1/gateway/internal/inbound", legacy_frontends)

    def test_renderer_transports_compiled_root_goal_across_auto_continuations(self) -> None:
        actions = (
            ROOT / "app" / "frontend-v2" / "renderer" / "core" / "actions.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("message: executionMessage,", actions)
        self.assertIn("runOptions.rootGoal", actions)
        self.assertIn("__autoContinuation: true", actions)

    def test_auto_continuation_is_awaited_instead_of_detached(self) -> None:
        actions = (
            ROOT / "app" / "frontend-v2" / "renderer" / "core" / "actions.mjs"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(actions.count("return await sendMessage("), 2)
        self.assertNotIn("autoContinuationPrompt(continuation),\n              [],", actions)

    def test_life_switch_consumes_authoritative_backend_chat_gate_without_status_probe(self) -> None:
        panel = (ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "life-panel.mjs").read_text(encoding="utf-8")
        self.assertIn('gate.schema !== "tiangong.life.chat-gate.v1"', panel)
        self.assertIn("function applyChatGateProjection", panel)
        self.assertIn("applyChatGateProjection(payload);", panel)
        self.assertNotIn("await actions?.refreshStatus?.();", panel)

    def test_every_life_panel_client_action_has_one_reviewed_gateway_route(self) -> None:
        life_api_uri = (RUNTIME_ROOT / "life-api.mjs").as_uri()
        script = f"""
          import {{ createLifeApiClient }} from {json.dumps(life_api_uri)};
          const calls = [];
          const api = createLifeApiClient({{
            request: async (path, options = {{}}) => {{
              calls.push([String(options.method || "GET").toUpperCase(), String(path)]);
              return {{ ok: true }};
            }},
          }});
          await api.getState();
          await api.getPanel();
          await api.createIdentity("Synthetic Life");
          await api.bindIdentity("C:/synthetic-life");
          await api.activateIdentity("life_synthetic");
          await api.unbindIdentity("life_synthetic");
          await api.deleteIdentity("life_synthetic");
          await api.getTemperament();
          await api.markInboxRead("message_synthetic");
          await api.deleteInboxMessage("message_synthetic");
          await api.ackProactiveChat("message_synthetic");
          await api.updateSettings({{ permission_mode: "full_access" }});
          await api.updateSoul({{ name: "Synthetic Life" }});
          await api.decideUpgrade("confirm", "upgrade_synthetic");
          await api.decideUpgrade("cancel", "upgrade_synthetic");
          await api.rollbackCapability("artifact_synthetic");
          for (const action of ["confirm", "process", "requestActivation", "activate", "release", "discard"]) {{
            await api.transitionLearning(action, "card_synthetic");
          }}
          process.stdout.write(JSON.stringify(calls));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        client_routes = {tuple(item) for item in json.loads(completed.stdout)}
        gateway_life_routes = {
            key for key, route in DESKTOP_ROUTES.items() if route.upstream == "life"
        }
        self.assertTrue(client_routes.issubset(gateway_life_routes))
        self.assertEqual(
            gateway_life_routes,
            declared_life_api_routes() - PRIVILEGED_LIFE_BRIDGE_ROUTES,
        )
        self.assertTrue(PRIVILEGED_LIFE_BRIDGE_ROUTES.isdisjoint(gateway_life_routes))

        plugin_source = "\n".join(
            (ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / name).read_text(
                encoding="utf-8"
            )
            for name in ("life-panel.mjs", "life-summary-block.mjs")
        )
        bound_client_methods = set(re.findall(r"lifeApi\.([A-Za-z_][A-Za-z0-9_]*)", plugin_source))
        self.assertEqual(
            bound_client_methods,
            {
                "ackProactiveChat",
                "getProactiveStatus",
                "activateIdentity",
                "bindIdentity",
                "capabilityDiscard",
                "createIdentity",
                "decideUpgrade",
                "deleteIdentity",
                "deleteInboxMessage",
                "getPanel",
                "getPendingProactiveChats",
                "markInboxRead",
                "reactivateCapability",
                "rollbackCapability",
                "transitionLearning",
                "unbindIdentity",
                "updateSettings",
                "updateSoul",
            },
        )

    def test_reviewed_gateway_allowlist_covers_every_active_frontend_business_route(self) -> None:
        expected = {
            ("GET", "/api/v1/llm/status"),
            ("GET", "/api/v1/llm/settings"),
            ("GET", "/api/v1/llm/optimization"),
            ("POST", "/api/v1/llm/settings"),
            ("GET", "/api/v1/character/state"),
            ("GET", "/api/v1/body/settings"),
            ("POST", "/api/v1/body/settings"),
            ("GET", "/api/v1/body/voice/capabilities"),
            ("POST", "/api/v1/body/voice/synthesize"),
            ("GET", "/api/v1/workspace/settings"),
            ("GET", "/api/v1/policy/status"),
            ("GET", "/api/v1/policy/settings"),
            ("POST", "/api/v1/policy/settings"),
            ("GET", "/api/v1/policy/confirm"),
            ("POST", "/api/v1/policy/confirm"),
            ("GET", "/api/v1/policy/confirm/archive"),
            ("GET", "/api/v1/v3/tools"),
            ("GET", "/api/v1/v3/skills"),
            ("GET", "/api/v1/v3/capabilities"),
            ("POST", "/api/v1/v3/skills/delete"),
            ("GET", "/api/v1/run/status"),
            ("POST", "/api/v1/knowledge/list"),
            ("POST", "/api/v1/knowledge/configure"),
            ("POST", "/api/v1/knowledge/import"),
            ("POST", "/api/v1/files/import"),
            ("POST", "/api/v1/knowledge/query"),
            ("POST", "/api/v1/knowledge/search"),
            ("POST", "/api/v1/knowledge/organize"),
            ("POST", "/api/v1/knowledge/export"),
            ("POST", "/api/v1/knowledge/remove"),
            ("GET", "/api/v1/knowledge/settings"),
            ("GET", "/api/v1/v3/state"),
            ("GET", "/api/v1/v3/life/panel"),
            ("POST", "/api/v1/v3/life/identity/create"),
            ("POST", "/api/v1/v3/life/identity/bind"),
            ("POST", "/api/v1/v3/life/identity/activate"),
            ("POST", "/api/v1/v3/life/identity/unbind"),
            ("POST", "/api/v1/v3/life/inbox/read"),
            ("POST", "/api/v1/v3/life/inbox/delete"),
            ("POST", "/api/v1/v3/life/proactive-chat/ack"),
            ("POST", "/api/v1/v3/life/settings"),
            ("POST", "/api/v1/v3/life/soul/update"),
            ("POST", "/api/v1/v3/life/upgrade/confirm"),
            ("POST", "/api/v1/v3/life/upgrade/cancel"),
            ("POST", "/api/v1/v3/life/capability/rollback"),
            ("POST", "/api/v1/v3/learning/confirm"),
            ("POST", "/api/v1/v3/learning/process-approved"),
            ("POST", "/api/v1/v3/learning/request-activation"),
            ("POST", "/api/v1/v3/learning/activate"),
            ("POST", "/api/v1/v3/learning/release"),
            ("POST", "/api/v1/v3/learning/discard"),
            ("GET", "/api/v1/gateway/links/status"),
            ("POST", "/api/v1/gateway/links/action"),
        }
        expected |= declared_life_api_routes() - PRIVILEGED_LIFE_BRIDGE_ROUTES
        self.assertEqual(set(DESKTOP_ROUTES), expected)
        self.assertTrue(
            {
                ("POST", "/api/v1/gateway/internal/inbound"),
                ("POST", "/api/v1/gateway/desktop/inbound"),
                ("GET", "/api/v1/gateway/desktop/status"),
                ("POST", "/api/v1/run/control"),
                ("POST", "/api/v1/conversation/events"),
                ("GET", "/api/v1/artifacts"),
                ("POST", "/api/v1/artifacts/open"),
            }.issubset(set(NATIVE_DESKTOP_ROUTES))
        )


if __name__ == "__main__":
    unittest.main()
