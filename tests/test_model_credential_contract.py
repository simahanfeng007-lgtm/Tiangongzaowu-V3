from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ELECTRON_MAIN = ROOT / "app" / "main.js"
BACKEND_CONFIG = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "peizhi.py"
FRONTEND_RUNTIME = ROOT / "app" / "frontend-v2" / "renderer" / "runtime" / "http-runtime.mjs"
SETTINGS_PANEL = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "settings-panel.mjs"
PAGES_CSS = ROOT / "app" / "frontend-v2" / "styles" / "pages.css"
PRELOAD = ROOT / "app" / "preload.js"
GATEWAY_API = ROOT / "src" / "total_gateway" / "desktop_api.py"
BACKEND_HTTP = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "duihua_qiaojie.py"
LIFE_RUNTIME = ROOT / "src" / "life_service" / "embedded_runtime.py"


class ModelCredentialContractTests(unittest.TestCase):
    def test_workspace_card_contains_workspace_permission_and_interface_groups(self) -> None:
        panel = SETTINGS_PANEL.read_text(encoding="utf-8")
        css = PAGES_CSS.read_text(encoding="utf-8")

        self.assertIn(
            'class="panel-card settings-card-workspace settings-card-composite"',
            panel,
        )
        for group in ("workspace", "permission", "interface"):
            self.assertEqual(
                panel.count(f'data-settings-group="{group}"'),
                1,
            )
        self.assertNotIn('class="panel-card settings-card-permission"', panel)
        self.assertNotIn('class="panel-card settings-card-ui"', panel)
        self.assertNotIn('class="panel-card settings-card-info"', panel)
        self.assertNotIn('class="panel-card settings-card-config"', panel)
        self.assertNotIn('id="backendConfigRows"', panel)
        self.assertNotIn('id="backendState"', panel)
        self.assertNotIn("function renderRuntime(", panel)
        self.assertIn(".settings-composite-list {", css)
        self.assertIn(".settings-composite-item {", css)
        self.assertIn(".settings-subgroup-title {", css)
        self.assertNotIn(".settings-info-grid {", css)

    def test_deepseek_vault_binding_matches_backend_reader(self) -> None:
        """The Electron provider id and backend credential lookup share one key."""
        electron = ELECTRON_MAIN.read_text(encoding="utf-8")
        backend = BACKEND_CONFIG.read_text(encoding="utf-8")

        self.assertIn('result.configured_provider || provider', electron)
        self.assertIn('providerApiKeyEnvName(provider)', electron)
        self.assertIn('"deepseek_v4": ("TIANGONG_DEEPSEEK_V4_API_KEY", "DEEPSEEK_API_KEY")', backend)
        self.assertIn('"minimax_m3": ("TIANGONG_MINIMAX_M3_API_KEY", "MINIMAX_API_KEY")', backend)
        self.assertIn('"glm_5_2": ("TIANGONG_GLM_5_2_API_KEY"', backend)
        self.assertIn('"mimo": ("TIANGONG_MIMO_API_KEY", "MIMO_API_KEY")', backend)
        self.assertIn('"gpt_5_6": ("TIANGONG_GPT_5_6_API_KEY", "OPENAI_API_KEY")', backend)

    def test_every_writable_settings_panel_group_has_an_authoritative_sink(self) -> None:
        """Prevent UI-only settings changes from silently losing their runtime mapping."""
        panel = SETTINGS_PANEL.read_text(encoding="utf-8")
        frontend = FRONTEND_RUNTIME.read_text(encoding="utf-8")
        preload = PRELOAD.read_text(encoding="utf-8")
        electron = ELECTRON_MAIN.read_text(encoding="utf-8")
        gateway = GATEWAY_API.read_text(encoding="utf-8")
        backend = BACKEND_HTTP.read_text(encoding="utf-8")
        life = LIFE_RUNTIME.read_text(encoding="utf-8")

        # Model provider, endpoint, model id and secret: renderer -> trusted
        # Electron vault -> backend configuration reader.
        self.assertIn('id="settingsModelApiKey"', panel)
        self.assertIn('writeModelSettings(secureLlmBody)', frontend)
        self.assertIn('handleTrusted("model:setSettings"', electron)
        self.assertIn('handleTrusted("model:probeProviderApi"', electron)
        self.assertIn('requestProviderProbe(providerProbeEndpoint(baseUrl, "models")', electron)
        self.assertIn('error_code: "plaintext_http_forbidden"', electron)
        self.assertIn('stage: "gateway_credential"', electron)
        self.assertIn('error_code: "gateway_credential_not_injected"', electron)
        self.assertIn('id="settingsProbeProviderApi"', panel)
        self.assertIn('probeProviderApi: () => ipcRenderer.invoke("model:probeProviderApi")', preload)
        gateway_env = electron[
            electron.index("function totalGatewayEnvironment(entry)"):
            electron.index("async function waitForTotalGateway(")
        ]
        self.assertIn("hydrateProviderApiKeys();", gateway_env)
        self.assertIn("const env = { ...process.env };", gateway_env)
        self.assertIn('if path == "/api/v1/llm/settings":', backend)

        # Workspace: renderer -> trusted Electron owner -> child-runtime env.
        self.assertIn('id="settingsWorkspaceRoot"', panel)
        self.assertIn('commitDesktopWorkspace(', frontend)
        self.assertIn('setWorkspaceRoot: (request)', preload)
        self.assertIn('handleTrusted("workspace:setRoot"', electron)

        # Permission and Soul settings: renderer -> 7184 route allowlist ->
        # embedded life service, which returns the authoritative projection.
        self.assertIn('id="settingsPermissionMode"', panel)
        self.assertIn('LIFE_API_ROUTES.settingsUpdate.path', frontend)
        self.assertIn('"/api/v1/v3/life/settings", "life"', gateway)
        self.assertIn('path == "/api/v1/v3/life/settings"', life)
        self.assertIn('LIFE_API_ROUTES.soulUpdate.path', frontend)
        self.assertIn('"/api/v1/v3/life/soul/update", "life"', gateway)
        self.assertIn('path == "/api/v1/v3/life/soul/update"', life)

        # Body/user/voice settings: renderer -> 7184 allowlist -> backend
        # persistence handler.
        self.assertIn('"/api/v1/body/settings"', frontend)
        self.assertIn('"/api/v1/body/settings", "backend"', gateway)
        self.assertIn('if path == "/api/v1/body/settings":', backend)

        # Theme has no backend data model by design, but is not local-only:
        # it is applied through the trusted Electron owner of the native window.
        self.assertIn('id="settingsTheme"', panel)
        self.assertIn('setThemeStyle', preload)
        self.assertIn('handleTrusted("theme:set"', electron)

        # Communication controls: renderer -> 7184 route -> communication
        # service action handler. Credentials remain in that service's vault.
        self.assertIn('id="linkWechatEnabled"', panel)
        self.assertIn('gatewayLinksAction', frontend)
        self.assertIn('"/api/v1/gateway/links/action", "communication"', gateway)
        self.assertIn('"wechat_direct_login_wait"', gateway)


if __name__ == "__main__":
    unittest.main()
