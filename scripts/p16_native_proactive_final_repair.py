from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def patch_runtime_test_clock() -> None:
    rel = "tests/test_p16_native_proactive_runtime.py"
    text = read(rel)
    if "import time\n" not in text:
        text = replace_once(text, "import tempfile\n", "import tempfile\nimport time\n", "runtime test time import")
    text = replace_once(
        text,
        "NOW = 1_800_000_000_000\n",
        "NOW = int(time.time() * 1000)\n",
        "runtime test real clock",
    )
    write(rel, text)


def patch_freeze_test() -> None:
    rel = "tests/test_legacy_proactive_freeze.py"
    text = read(rel)
    old = '''    greeting = _def_block(text, "_schedule_greeting")
    learning = _def_block(text, "_learning_report")
    assert "proactive_chats" not in greeting
    assert 'proactive_chats"].append' not in learning
    assert text.count('proactive_chats"].append') == 1
    assert 'scope["proactive_chats"].append(row)' in text
'''
    new = '''    greeting = text.split("def _schedule_greeting", 1)[1].split("\\n    def ", 1)[0]
    learning = text.split("def _learning_report", 1)[1].split("\\n    def ", 1)[0]
    assert "proactive_chats" not in greeting
    assert 'proactive_chats"].append' not in learning
    assert text.count('proactive_chats"].append') == 1
    assert 'scope["proactive_chats"].append(row)' in text
'''
    text = replace_once(text, old, new, "freeze test inline block extraction")
    write(rel, text)


def patch_budget_accounting_and_setting_ranges() -> None:
    rel = "src/life_service/embedded_runtime.py"
    text = read(rel)
    old = '''            scheduler = scope.setdefault("scheduler", {})
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            now_ms = time.time_ns() // 1_000_000
'''
    new = '''            scheduler = scope.setdefault("scheduler", {})
            self._reset_proactive_model_budget_if_needed(scheduler)
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            now_ms = time.time_ns() // 1_000_000
'''
    text = replace_once(text, old, new, "decision success budget date")

    old_limits = '''                        "proactive_dnd_start_hour": (0, 23),
                        "proactive_dnd_end_hour": (0, 23),
                        "proactive_user_active_window_seconds": (0, 3600),
'''
    new_limits = '''                        "proactive_dnd_start_hour": (0, 23),
                        "proactive_dnd_end_hour": (0, 23),
                        "proactive_timezone_offset_minutes": (-840, 840),
                        "proactive_max_future_skew_seconds": (0, 3600),
                        "proactive_user_active_window_seconds": (0, 3600),
'''
    text = replace_once(text, old_limits, new_limits, "temporal settings validation ranges")
    write(rel, text)


if __name__ == "__main__":
    patch_runtime_test_clock()
    patch_freeze_test()
    patch_budget_accounting_and_setting_ranges()
    print("P16 final focused-test repair applied")
