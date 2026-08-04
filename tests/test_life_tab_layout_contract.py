from __future__ import annotations

import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LIFE_PANEL = ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "life-panel.mjs"
LIFE_STYLES = ROOT / "app" / "frontend-v2" / "styles" / "life.css"
MATERIAL_STYLES = ROOT / "app" / "frontend-v2" / "styles" / "materials.css"


class LifeTabLayoutContractTests(unittest.TestCase):
    def test_life_navigation_is_fixed_to_two_rows_of_six(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")
        styles = LIFE_STYLES.read_text(encoding="utf-8")

        tab_block = re.search(r"const LIFE_TABS = \[(.*?)\n\];", panel, re.DOTALL)
        self.assertIsNotNone(tab_block)
        self.assertEqual(tab_block.group(1).count("{ id: "), 12)
        tab_entries = re.findall(r'\{ id: "([^"]+)", label: "([^"]+)" \}', tab_block.group(1))
        self.assertEqual(tab_entries[0], ("overview", "总览"))
        self.assertEqual(tab_entries[1], ("identity", "身份"))
        tabs_block = re.search(r"\.life-tabs\s*\{(.*?)\n\}", styles, re.DOTALL)
        self.assertIsNotNone(tabs_block)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr));", tabs_block.group(1))
        self.assertIn("grid-template-rows: repeat(2, minmax(36px, auto));", tabs_block.group(1))
        self.assertNotIn(".life-tabs {", styles[styles.find("@media (max-width: 1180px)"):])

    def test_soul_context_shortcut_card_is_removed(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")

        self.assertNotIn('data-life-open-soul', panel)
        self.assertNotIn('function renderSoulContextLink', panel)
        self.assertNotIn("灵魂 · 生命上下文配置", panel)
        self.assertIn('data-life-soul-save', panel)

    def test_life_submenus_do_not_render_standalone_prompt_boxes(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")
        styles = LIFE_STYLES.read_text(encoding="utf-8")

        self.assertNotIn("life-settings-note", panel)
        self.assertNotIn(".life-settings-note", styles)
        for retired_prompt in (
            "情感与驱动力只能调节注意",
            "记忆原文按条加密",
            "灵魂配置定义生命的表达方式",
            "当前仅提供部分权威数据",
            "当前客户端尚未绑定生命",
        ):
            self.assertNotIn(retired_prompt, panel)

    def test_memory_categories_fill_the_row_without_status_card(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")
        styles = LIFE_STYLES.read_text(encoding="utf-8")

        self.assertIn('class="life-card life-memory-types"', panel)
        self.assertIn('class="life-memory-types-grid"', panel)
        self.assertIn(".life-memory-types-grid .life-kv-list", styles)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", styles)
        self.assertNotIn('sectionTitle("记忆状态"', panel)
        self.assertNotIn("const byStatus =", panel)

    def test_context_hides_internal_compiler_diagnostics(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")
        render_context = re.search(
            r"function renderContext\(payload\) \{(.*?)\n\}\n\nfunction capabilityArtifactRows",
            panel,
            re.DOTALL,
        )
        self.assertIsNotNone(render_context)
        context_source = render_context.group(1)
        self.assertNotIn('sectionTitle("编译原因"', context_source)
        self.assertNotIn('sectionTitle("未纳入内容"', context_source)
        self.assertNotIn("context.compile_reasons", context_source)
        self.assertNotIn("context.omitted_blocks", context_source)

    def test_settings_are_grouped_value_bound_and_do_not_overlap(self) -> None:
        panel = LIFE_PANEL.read_text(encoding="utf-8")
        styles = LIFE_STYLES.read_text(encoding="utf-8")
        materials = MATERIAL_STYLES.read_text(encoding="utf-8")

        for group in ("autonomy", "runtime", "sharing", "privacy"):
            self.assertIn(f'id: "{group}"', panel)
            self.assertIn('data-life-settings-group="${esc(group.id)}"', panel)
        self.assertIn("const rawValue = getPathValue(settings, field.key);", panel)
        self.assertIn("当前：${hasValue", panel)
        self.assertIn('placeholder="${hasValue ? "" : "后端未提供"}"', panel)
        self.assertIn("if (!input || input.disabled) continue;", panel)
        self.assertIn(".life-settings-card {", styles)
        self.assertIn("height: auto;", styles)
        self.assertIn(".life-settings-groups {", styles)
        self.assertIn(".life-select-shell::after", styles)
        self.assertNotIn(
            '.life-tab-content[data-life-active-tab="settings"] .life-tab-view > .life-card:first-child',
            styles,
        )
        settings_actions = re.search(
            r"\.life-settings-actions\s*\{(.*?)\n\}",
            styles,
            re.DOTALL,
        )
        self.assertIsNotNone(settings_actions)
        self.assertNotIn("position: sticky", settings_actions.group(1))
        self.assertNotIn("灵魂 · 生命上下文配置", panel)

        for theme in ("ink_teal", "bronze_gear", "jade_light"):
            self.assertIn(
                f':root[data-theme="{theme}"] .life-select-shell select',
                materials,
            )
            self.assertIn(
                f':root[data-theme="{theme}"] .life-select-shell option',
                materials,
            )

    def test_schedule_plan_grows_with_tasks_without_inner_scrollbar(self) -> None:
        styles = LIFE_STYLES.read_text(encoding="utf-8")
        selector = (
            '.life-tab-content[data-life-active-tab="schedule"] '
            ".life-schedule-plan"
        )
        start = styles.index(selector)
        block = styles[start:styles.index("}", start)]

        self.assertIn("height: auto !important;", block)
        self.assertIn("min-height: 0 !important;", block)
        self.assertIn("overflow: visible;", block)
        self.assertIn("scrollbar-gutter: auto;", block)


if __name__ == "__main__":
    unittest.main()
