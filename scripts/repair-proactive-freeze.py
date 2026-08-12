from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "life_service" / "embedded_runtime.py"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    marker = "    def _normalize_upgrade_changes(self, value: Any) -> list[dict[str, Any]]:\n"
    if "    _UPGRADE_PATH_FORBIDDEN_PARTS = frozenset(" not in text:
        constants = '''    _UPGRADE_OPEN_STATUSES = frozenset({"awaiting_user", "confirmed", "executing"})
    _UPGRADE_PATH_SUFFIXES = frozenset(
        {".py", ".mjs", ".cjs", ".js", ".html", ".css", ".json", ".md", ".yaml", ".yml"}
    )
    _UPGRADE_PATH_FORBIDDEN_PARTS = frozenset(
        {"__pycache__", ".git", "_internal", "node_modules", "site-packages"}
    )

'''
        if marker not in text:
            raise RuntimeError("upgrade normalization anchor changed")
        text = text.replace(marker, constants + marker, 1)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print({"ok": True, "target": str(TARGET.relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
