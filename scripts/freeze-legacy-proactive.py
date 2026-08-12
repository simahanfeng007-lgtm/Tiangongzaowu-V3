from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "life_service" / "embedded_runtime.py"


def replace_method(text: str, name: str, replacement: str) -> str:
    start = text.index(f"    def {name}(")
    end = text.find("\n    def ", start + 1)
    if end < 0:
        raise RuntimeError(f"next method not found after {name}")
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    greeting = '''    def _schedule_greeting(self, *, life_id: str) -> None:
        """Legacy random-greeting producer is frozen after the P15 cutover.

        Delivery infrastructure remains available for the future native Life
        initiative path; this producer intentionally performs no queue write,
        model generation, journal publication, or scheduler retry mutation.
        """
        freeze_reason = "life.proactive.legacy_producer_frozen"
        _ = (life_id, freeze_reason)
        return
'''
    text = replace_method(text, "_schedule_greeting", greeting)

    learning = '''    def _learning_report(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Return a durable publication report without legacy proactive delivery.

        Learning publication still records deterministic metadata in the
        journal, but the pre-P15 producer no longer writes a chat message.
        """
        target = str(record.get("target") or "knowledge")
        title = str(record.get("title") or "learning")
        detail = f"学习完成：{title}。已写入{'知识库' if target == 'knowledge' else target}。"
        return {
            "message_id": "learnmsg_" + canonical_sha256(
                {
                    "learning_id": record.get("learning_id"),
                    "status": record.get("status"),
                }
            )[:40],
            "kind": "learning_report",
            "learning_id": record.get("learning_id"),
            "text": detail,
            "created_at": utc_now(),
            "read": True,
            "delivery": "legacy_proactive_frozen",
            "suppressed": True,
            "reason_code": "life.proactive.legacy_producer_frozen",
        }
'''
    text = replace_method(text, "_learning_report", learning)

    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print({"ok": True, "target": str(TARGET.relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
