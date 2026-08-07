from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend-v2"


def test_interaction_contract_is_last_style_layer():
    imports = [
        line.strip()
        for line in (FRONTEND / "styles.css").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("@import")
    ]
    assert imports[-1] == '@import url("./styles/interaction-contracts.css");'


def test_theme_material_does_not_turn_settings_subgroups_into_cards():
    material = (FRONTEND / "styles" / "materials.css").read_text(encoding="utf-8")
    contract = (FRONTEND / "styles" / "interaction-contracts.css").read_text(encoding="utf-8")
    assert ".settings-composite-item," not in material
    assert ".settings-composite-item:first-child" in contract
    assert "padding-top: 10px" in contract
    assert "background: transparent" in contract
    assert "box-shadow: none" in contract


def test_all_primary_scroll_regions_share_wheel_contract():
    contract = (FRONTEND / "styles" / "interaction-contracts.css").read_text(encoding="utf-8")
    required = (
        ".page-body",
        ".messages",
        ".dashboard-panel",
        ".history-list",
        ".skills-list",
        ".knowledge-list",
        ".knowledge-result",
        ".vrm-scroll-area",
        ".life-tab-content",
        ".life-card",
        ".life-boundary-card",
    )
    for selector in required:
        assert selector in contract
    assert "overscroll-behavior-y: auto" in contract
    assert "scrollbar-gutter: stable" in contract
    assert "touch-action: pan-y" in contract


def test_body_page_hides_default_history_scroll_region():
    source = (FRONTEND / "renderer" / "plugins" / "history-block.mjs").read_text(
        encoding="utf-8"
    )
    assert 'page !== "chat"' in source
    assert 'activePage !== "chat"' in source
