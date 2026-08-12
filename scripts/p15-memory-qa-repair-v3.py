from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = runpy.run_path(str(ROOT / "scripts" / "p15-memory-qa-repair-v2.py"))
replace_slice = BASE["replace_slice"]
rewrite = BASE["rewrite"]
patch_coordinator = BASE["patch_coordinator"]
patch_invalidation = BASE["patch_invalidation"]


def patch_gateway(text: str) -> str:
    if "from datetime import datetime, timezone\n" not in text:
        anchor = "from dataclasses import dataclass\n"
        if anchor not in text:
            raise RuntimeError("gateway import anchor changed")
        text = text.replace(
            anchor,
            anchor + "from datetime import datetime, timezone\n",
            1,
        )
    if "Return bounded, non-expired long-term memory for chat context (P15)." in text:
        return text
    replacement = r'''def _gateway_p15_memory_recall(runtime: object, user_text: object) -> str:
    """Return bounded, non-expired long-term memory for chat context (P15)."""

    try:
        from life_service.explicit_memory import detect_explicit_intent, expiry_deadline_ms

        text = str(user_text or "").strip()
        life_service = getattr(runtime, "life_service", None)
        if life_service is None:
            return ""
        active = life_service._active() if hasattr(life_service, "_active") else {}
        life_id = str(active.get("life_id") or "")
        if not life_id:
            return ""
        memory_markers = (
            "名字", "我叫", "我是谁", "称呼", "记住", "记得", "之前", "上次",
            "偏好", "习惯", "忘了", "长期", "以后",
        )
        lines: list[str] = []
        seen: set[str] = set()
        now_ms = time.time_ns() // 1_000_000

        def created_at_ms(row: Mapping[str, object]) -> int | None:
            raw = str(row.get("created_at") or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1000)
            except (TypeError, ValueError, OverflowError):
                return None

        def recallable(row: Mapping[str, object], snippet: str) -> bool:
            try:
                detection = detect_explicit_intent(snippet)
            except ValueError:
                return True
            if not detection.triggered or detection.expiry_kind is None:
                return True
            created_ms = created_at_ms(row)
            if created_ms is None:
                return False
            deadline = expiry_deadline_ms(detection.expiry_kind, created_ms)
            return deadline is None or now_ms < deadline

        def collect(query: str, limit: int = 10) -> None:
            status, payload, _ = life_service.request(
                "POST",
                "/api/v1/v3/life/memory/search",
                {"life_id": life_id, "query": query, "limit": limit},
            )
            if status >= 400 or not isinstance(payload, Mapping) or payload.get("ok") is not True:
                return
            for row in (payload.get("results") or [])[:limit]:
                if not isinstance(row, Mapping):
                    continue
                content = row.get("content")
                if isinstance(content, str):
                    snippet = content
                elif isinstance(content, Mapping):
                    snippet = str(content.get("text") or content.get("content") or "")
                else:
                    snippet = ""
                snippet = str(snippet).strip()
                if snippet and snippet not in seen and recallable(row, snippet):
                    seen.add(snippet)
                    lines.append(snippet[:1200])

        collect(text)
        if not lines and any(marker in text for marker in memory_markers):
            collect("")
        return "\n".join(lines[:10])
    except Exception:
        return ""


'''
    return replace_slice(
        text,
        "def _gateway_p15_memory_recall(",
        "def _gateway_body_state_query(",
        replacement,
    )


def main() -> int:
    changed: list[str] = []
    for relative, transform in (
        ("src/life_service/memory_coordinator.py", patch_coordinator),
        ("src/life_service/memory_invalidation.py", patch_invalidation),
        ("src/total_gateway/runtime.py", patch_gateway),
    ):
        if rewrite(relative, transform):
            changed.append(relative)
    print({"ok": True, "changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
