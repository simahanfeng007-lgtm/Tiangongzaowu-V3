from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW_MODEL = ROOT / "app" / "frontend-v2" / "renderer" / "runtime" / "life-view-model.mjs"
HTTP_RUNTIME = ROOT / "app" / "frontend-v2" / "renderer" / "runtime" / "http-runtime.mjs"
LIFE_CSS = ROOT / "app" / "frontend-v2" / "styles" / "life.css"
LIFE_SUMMARY = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "life-summary-block.mjs"
LIFE_PANEL = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "life-panel.mjs"
CONVERSATION = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "conversation-panel.mjs"
EXECUTE_PANEL = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "execute-panel.mjs"
PRELOAD = ROOT / "app" / "preload.js"
LEGACY_WINDOW = ROOT / "app" / "对话窗口.html"


def run_node(source: str) -> object:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


class LifeFrontendP10Tests(unittest.TestCase):
    def test_settings_render_authoritative_backend_values_without_legacy_soul_card(self) -> None:
        payload = {
            "settings": {
                "available": True,
                "editable": True,
                "readonly": False,
                "source": "embedded_life_runtime",
                "permission_mode": "confirm_high_risk",
                "autonomous_risk_max": "A4",
                "autonomy_enabled": True,
                "autonomy_task_generation_enabled": True,
                "autonomy_activity_types": ["daily_plan"],
                "autonomy_activity_catalog": [
                    {
                        "activity_id": "daily_plan",
                        "label": "今日规划",
                        "description": "整理当天计划",
                    }
                ],
                "heartbeat_enabled": True,
                "llm_daily_budget": 20,
                "llm_daily_attempt_budget": 30,
                "share_enabled": True,
                "share_quiet_if_user_active": True,
                "share_min_interval_seconds": 2700,
                "share_hourly_limit": 1,
                "share_daily_limit": 5,
                "share_dnd_start": "23:00",
                "share_dnd_end": "08:00",
                "privacy": {"redact_llm": True, "redact_share": True},
            }
        }
        script = f"""
          import {{ renderSettings }} from {json.dumps(LIFE_PANEL.as_uri())};
          const html = renderSettings({json.dumps(payload, ensure_ascii=False)});
          process.stdout.write(JSON.stringify({{ html }}));
        """
        html = run_node(script)["html"]
        for expected in (
            'value="20"',
            'value="30"',
            'value="2700"',
            'value="1"',
            'value="5"',
            'value="23:00"',
            'value="08:00"',
            "当前：20",
            "当前：30",
            "权限与自主行动",
            "心跳与模型预算",
            "主动分享",
            "隐私保护",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("灵魂 · 生命上下文配置", html)
        self.assertNotIn("打开灵魂配置", html)

    def test_life_inbox_opens_full_message_in_main_chat_only(self) -> None:
        script = f"""
          import {{ deliverInboxMessageToChat }} from {json.dumps(LIFE_SUMMARY.as_uri())};
          const calls = [];
          const state = {{
            snapshot: () => ({{ activeSessionId: "session_test" }}),
            addMessage: (...args) => {{
              calls.push(args);
              return {{ id: args[3].id, sessionId: "session_test" }};
            }}
          }};
          const result = deliverInboxMessageToChat({{
            message_id: "daily-summary:2026-07-23",
            title: "今日生命总结",
            message: "今天完成了全部生命计划，并形成整体反思。",
            created_at: "2026-07-23T20:00:00+08:00"
          }}, state);
          process.stdout.write(JSON.stringify({{ calls, result }}));
        """
        value = run_node(script)
        self.assertEqual(len(value["calls"]), 1)
        role, content, error, options = value["calls"][0]
        self.assertEqual(role, "assistant")
        self.assertEqual(content, "【生命信箱 · 今日生命总结】\n\n今天完成了全部生命计划，并形成整体反思。")
        self.assertFalse(error)
        self.assertEqual(options["id"], "life-inbox:session_test:daily-summary:2026-07-23")
        self.assertEqual(options["kind"], "life_inbox")

        source = LIFE_SUMMARY.read_text(encoding="utf-8")
        css = LIFE_CSS.read_text(encoding="utf-8")
        self.assertNotIn("openInboxId", source)
        self.assertNotIn("aria-expanded", source)
        self.assertNotIn("life-summary-inbox-message", source)
        self.assertNotIn(".life-summary-inbox-message", css)

        panel_source = LIFE_PANEL.read_text(encoding="utf-8")
        self.assertIn('data-life-open-inbox', panel_source)
        self.assertIn('actions.setActivePage("chat")', panel_source)

    def test_frontend_connects_only_to_the_total_gateway(self) -> None:
        frontend_files = (HTTP_RUNTIME, PRELOAD, LEGACY_WINDOW)
        retired_origins = ("127.0.0.1:7174", "127.0.0.1:7175", "127.0.0.1:7176")
        for file_path in frontend_files:
            with self.subTest(file=file_path.name):
                content = file_path.read_text(encoding="utf-8")
                self.assertNotIn("http://127.0.0.1:7174", content)
                self.assertNotIn("http://127.0.0.1:7175", content)
                self.assertNotIn("http://127.0.0.1:7176", content)
                self.assertFalse(any(origin in content for origin in retired_origins))
        preload = PRELOAD.read_text(encoding="utf-8")
        self.assertIn('const gatewayUrl = String(gatewayBootstrap.gatewayUrl || "http://127.0.0.1:7184");', preload)
        self.assertIn("const backendUrl = gatewayUrl;", preload)
        self.assertIn("const lifeUrl = gatewayUrl;", preload)
        self.assertIn("const communicationUrl = gatewayUrl;", preload)

    def test_unsourced_projection_is_empty_and_user_identity_is_stable(self) -> None:
        script = f"""
          import {{ buildLifeViewModel, normalizeUserIdentity, relationshipDisplayName }} from {json.dumps(VIEW_MODEL.as_uri())};
          const settings = {{ userCallsign: "夏平", userAvatarDataUrl: "data:image/png;base64,AA==" }};
          const view = buildLifeViewModel({{ ok: true, summary: {{ status: "ALIVE" }}, free_will: {{ skip_reason: "invented" }} }}, settings);
          const identity = normalizeUserIdentity(settings);
          process.stdout.write(JSON.stringify({{
            status: view.projection_status,
            summaryKeys: Object.keys(view.summary),
            reason: view.free_will.skip_reason || "",
            identity,
            relationship: relationshipDisplayName("user:primary", identity)
          }}));
        """
        value = run_node(script)
        self.assertEqual(value["status"], "awaiting_authority_projection")
        self.assertEqual(value["summaryKeys"], [])
        self.assertEqual(value["reason"], "")
        self.assertEqual(value["identity"]["fallbackGlyph"], "夏")
        self.assertEqual(value["relationship"], "夏平 · 主要用户关系")

    def test_authoritative_domains_require_sources_and_preserve_exact_long_text(self) -> None:
        long_text = "因果解释" * 2_000
        payload = {
            "projection_authority": {
                "schema": "tiangong.gateway.life-view-authority.v1",
                "revisions": {
                    "writer_epoch": 3,
                    "identity_revision": 3,
                    "soul_revision": 2,
                    "memory_revision": 4,
                    "affect_revision": 5,
                    "causal_revision": 6,
                    "viability_revision": 7,
                    "policy_revision": 8,
                    "reflection_revision": 9,
                    "capability_revision": 10,
                    "vector_sha256": "a" * 64,
                },
                "source_refs": {
                    "causal": "fact:causal:6",
                    "viability": "fact:viability:7",
                    "policy": "fact:policy:8",
                },
            },
            "context": {"explanation": long_text},
            "summary": {"status": "ALIVE"},
            "free_will": {"ready_for_action": False},
            "reflections": [{"summary": "must-not-leak-without-reflection-source"}],
        }
        script = f"""
          import {{ buildLifeViewModel }} from {json.dumps(VIEW_MODEL.as_uri())};
          const view = buildLifeViewModel({json.dumps(payload, ensure_ascii=False)}, {{ userCallsign: "夏平" }});
          process.stdout.write(JSON.stringify({{
            status: view.projection_status,
            explanation: view.context.explanation,
            alive: view.summary.status,
            reason: view.free_will.skip_reason || "",
            reflectionCount: view.reflections.length
          }}));
        """
        value = run_node(script)
        self.assertEqual(value["status"], "authoritative")
        self.assertEqual(value["explanation"], long_text)
        self.assertEqual(value["alive"], "ALIVE")
        self.assertEqual(value["reason"], "")
        self.assertEqual(value["reflectionCount"], 0)

    def test_authoritative_identity_domain_preserves_bound_identity_collection(self) -> None:
        payload = {
            "projection_authority": {
                "schema": "tiangong.gateway.life-view-authority.v1",
                "revisions": {
                    "writer_epoch": 2,
                    "identity_revision": 1,
                    "soul_revision": 1,
                    "memory_revision": 0,
                    "affect_revision": 0,
                    "causal_revision": 0,
                    "viability_revision": 0,
                    "policy_revision": 0,
                    "reflection_revision": 0,
                    "capability_revision": 0,
                    "vector_sha256": "b" * 64,
                },
                "source_refs": {
                    "identity": "life:org_current:identity:1",
                    "temperament": "life:org_current:temperament:1",
                },
            },
            "identity": {
                "life_id": "org_current",
                "name": "苏凌霜",
                "root": r"C:\life\org_current",
            },
            "identities": [
                {
                    "life_id": "org_current",
                    "name": "苏凌霜",
                    "root": r"C:\life\org_current",
                    "active": True,
                    "integrity": "valid",
                },
                {
                    "life_id": "org_other",
                    "name": "起源",
                    "root": r"D:\life\org_other",
                    "active": False,
                    "integrity": "valid",
                },
            ],
            "temperament": {
                "schema": "tiangong.life.temperament-projection.v1",
                "soul_influence": "none",
                "current_traits": {"openness": 0.51},
            },
        }
        script = f"""
          import {{ buildLifeViewModel }} from {json.dumps(VIEW_MODEL.as_uri())};
          const view = buildLifeViewModel({json.dumps(payload, ensure_ascii=False)}, {{}});
          process.stdout.write(JSON.stringify({{
            currentRoot: view.identity.root,
            count: view.identities.length,
            activeId: view.identities.find((item) => item.active)?.life_id || "",
            roots: view.identities.map((item) => item.root),
            openness: view.temperament.current_traits?.openness,
            soulInfluence: view.temperament.soul_influence
          }}));
        """
        value = run_node(script)
        self.assertEqual(value["currentRoot"], r"C:\life\org_current")
        self.assertEqual(value["count"], 2)
        self.assertEqual(value["activeId"], "org_current")
        self.assertEqual(value["roots"], [r"C:\life\org_current", r"D:\life\org_other"])
        self.assertEqual(value["openness"], 0.51)
        self.assertEqual(value["soulInfluence"], "none")

        panel = (ROOT / "app/frontend-v2/renderer/plugins/life-panel.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-life-bind-root value="${esc(current.root || "")}"', panel)
        self.assertIn('item.active ? "当前生命"', panel)
        self.assertIn('item.integrity === "valid" ? "封印中" : "封印异常"', panel)
        self.assertIn('item.integrity === "valid" ? "激活" : "不可激活"', panel)
        self.assertIn('<span class="life-identity-active-state">激活中</span>', panel)
        self.assertIn('item.soul_intro || "这个生命还没有写下自己的简介。"', panel)
        self.assertIn('safeObject(item.temperament_traits)', panel)
        self.assertIn('const auditEvents = safeArray(payload.identity_audit);', panel)
        self.assertIn('身份操作记录', panel)
        self.assertIn('style="--life-tone: ${identityTone(item)}"', panel)
        self.assertNotIn('title="${esc(item.root || "")}"', panel)
        self.assertNotIn(">解除绑定</button>", panel)

        css = LIFE_CSS.read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn(
            "grid-template-columns: minmax(72px, auto) minmax(0, 1fr) auto;",
            css,
        )
        self.assertIn("backdrop-filter: blur(14px) saturate(1.35);", css)
        self.assertIn("hsl(var(--life-tone, 168)", css)

    def test_free_will_normalizer_does_not_invent_skip_reason(self) -> None:
        script = f"""
          import {{ normalizeFreeWillState }} from {json.dumps(HTTP_RUNTIME.as_uri())};
          const value = normalizeFreeWillState({{ free_will: {{ curiosity: 0, curiosity_threshold: 0.5 }} }});
          process.stdout.write(JSON.stringify({{ reason: value.skip_reason, detail: value.skip_detail }}));
        """
        self.assertEqual(run_node(script), {"reason": "", "detail": ""})

    def test_life_layout_keeps_stable_primary_grids_with_bounded_reflection_reflow(self) -> None:
        css = LIFE_CSS.read_text(encoding="utf-8")
        self.assertNotIn("@media", css)
        self.assertNotIn("@container", css)
        self.assertNotIn("container-type:", css)
        self.assertEqual(css.count("repeat(auto-fit"), 1)
        self.assertIn(
            ".life-reflection-cards .life-reflection-list {\n"
            "  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));",
            css,
        )
        self.assertIn(
            ".life-overview-grid-auto {\n  grid-template-columns: repeat(4, minmax(0, 1fr));",
            css,
        )
        self.assertIn(
            ".life-identity-list {\n  display: grid;\n  grid-template-columns: repeat(2, minmax(0, 1fr));",
            css,
        )
        for marker in (
            ".life-will-layout",
            ".life-reflection-layout",
            ".life-capability-layout",
            ".life-schedule-layout",
        ):
            self.assertIn(marker, css)
        responsive = (ROOT / "app/frontend-v2/styles/responsive.css").read_text(
            encoding="utf-8"
        )
        pages = (ROOT / "app/frontend-v2/styles/pages.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('.app-shell:not([data-page="lifecycle"])', responsive)
        self.assertIn('.app-shell[data-page="lifecycle"]', responsive)
        self.assertIn(
            '.app-shell[data-page="lifecycle"] .inspector {\n    display: none;',
            responsive,
        )
        self.assertIn(
            '.app-shell[data-page="lifecycle"] .context-pane {\n    display: none;',
            responsive,
        )
        self.assertNotIn(".lifecycle-overview-grid", responsive)
        self.assertNotIn(".lifecycle-vitals-grid", responsive)
        self.assertIn(
            ".lifecycle-freewill-list {\n  grid-template-columns: repeat(4, minmax(0, 1fr));",
            pages,
        )
        source = CONVERSATION.read_text(encoding="utf-8")
        self.assertNotIn('container.textContent = "你"', source)
        self.assertIn("normalizeUserIdentity(settings).fallbackGlyph", source)

    def test_life_metric_values_fit_to_one_line_without_reflowing_cards(self) -> None:
        panel = (
            ROOT / "app/frontend-v2/renderer/plugins/life-panel.mjs"
        ).read_text(encoding="utf-8")
        css = LIFE_CSS.read_text(encoding="utf-8")

        self.assertIn('class="life-fit-single-line" data-life-fit-text', panel)
        self.assertIn("function fitLifeCardValues(root)", panel)
        self.assertIn("new ResizeObserver(scheduleLifeCardFit)", panel)
        self.assertIn("element.scrollWidth <= availableWidth", panel)
        self.assertIn(".life-card-copy strong.life-fit-single-line {", css)
        fit_start = css.index(".life-card-copy strong.life-fit-single-line {")
        fit_block = css[fit_start:css.index("}", fit_start)]
        self.assertIn("white-space: nowrap", fit_block)
        self.assertIn("overflow-wrap: normal", fit_block)
        self.assertIn("overflow: hidden", fit_block)

    def test_life_panel_contract_uses_embedded_fields_without_legacy_placeholders(self) -> None:
        panel = (
            ROOT / "app/frontend-v2/renderer/plugins/life-panel.mjs"
        ).read_text(encoding="utf-8")
        inspector = (
            ROOT / "app/frontend-v2/renderer/plugins/inspector-panel.mjs"
        ).read_text(encoding="utf-8")

        for required in (
            'key: "permission_mode"',
            'key: "autonomous_risk_max"',
            'key: "autonomy_enabled"',
            'key: "autonomy_task_generation_enabled"',
            'key: "heartbeat_enabled"',
            'key: "privacy.redact_llm"',
            'key: "privacy.redact_share"',
            "item.objective",
            "item.task_kind",
            "item.proposed_action",
            "payload.temperament",
            'sectionTitle("天生人格"',
            "boundaries.declared_rules",
            'sectionTitle("最近自主行动总结"',
            'interest: "兴趣"',
            'hope: "期待"',
            'frustration: "生气与受挫"',
            'disappointment: "失望"',
            'vigilance: "警觉"',
            'fatigue: "疲惫"',
            'sectionTitle("当前情感", zhTerm(',
        ):
            self.assertIn(required, panel)
        for emotion_key in (
            "joy", "interest", "hope", "gratitude", "warmth", "calm",
            "concern", "sadness", "frustration", "disappointment",
            "vigilance", "fatigue",
        ):
            self.assertEqual(
                panel.count(f"  {emotion_key}: "),
                1,
                f"{emotion_key} must have exactly one Chinese mapping",
            )
        self.assertNotIn('sectionTitle("最近真实执行"', panel)
        self.assertNotIn('sectionTitle("今日自主计划"', panel)

        for retired in (
            'key: "share_probability"',
            'key: "daily_plan_enabled"',
            'key: "dream_enabled"',
            'key: "self_clean_delete"',
        ):
            self.assertNotIn(retired, panel)

        self.assertIn('label: "接线健康"', inspector)
        self.assertIn('const contextMetricLabel =', inspector)
        self.assertIn('const latestAutonomousAction =', inspector)

    def test_execute_dashboard_uses_authoritative_operational_projection(self) -> None:
        panel = EXECUTE_PANEL.read_text(encoding="utf-8")
        runtime = HTTP_RUNTIME.read_text(encoding="utf-8")
        inspector = (
            ROOT / "app/frontend-v2/renderer/plugins/inspector-panel.mjs"
        ).read_text(encoding="utf-8")

        for required in (
            "runtime.operational",
            "operational.active_task_count",
            "operational.completed_execution_count",
            "operational.latest_execution",
            'metric("记忆记录"',
            'metric("待处理任务"',
            'metric("完成执行"',
            'metric("调度心跳"',
            "<span>最近执行终态</span>",
            "<span>执行证据</span>",
        ):
            self.assertIn(required, panel)

        for retired in (
            'row("生命力"',
            'row("唤醒次数"',
            'metric("成长"',
            'metric("生命力"',
        ):
            self.assertNotIn(retired, panel)

        self.assertIn("const uiOperational =", runtime)
        self.assertIn("operational: normalized.operational", runtime)
        self.assertIn("const operational = payload.runtime?.operational || {};", inspector)
        self.assertIn('label: "执行账本"', inspector)


if __name__ == "__main__":
    unittest.main()
