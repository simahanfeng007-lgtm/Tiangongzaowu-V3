from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from v3.novel_system import NovelSystemEngine, NovelSystemError  # noqa: E402


def _stage_minimal_blueprint(engine: NovelSystemEngine) -> int:
    sections = [
        ("story", {"premise": "选择必须产生不可逆后果", "protected_anchors": ["anchor-ending"]}),
        ("characters", [{"id": "c1", "name": "林舟", "birth_tick": -20, "initial": {"alive": True, "location": "l1", "realm": "凡人", "injuries": [], "inventory": [], "knowledge": []}}]),
        ("world", {"rules": ["事实先于叙事"]}),
        ("calendar", {"start_tick": 0, "ticks_per_year": 365}),
        ("locations", [{"id": "l1", "name": "旧站台"}]),
        ("plot_events", [{"id": "e1", "chapter": 1, "participants": ["c1"], "location": "l1", "start_tick": 0, "duration_ticks": 1, "requires_events": [], "deadline_chapter": 1, "closure_required": True}]),
        ("chapters", [{"number": 1, "title": "终点之前", "event_ids": ["e1"], "participants": ["c1"], "locations": ["l1"], "required_outcomes": ["resolved"], "theme_tags": ["成长"]}]),
        ("settings", {"min_chapter_chars": 200, "emotional_trigger_threshold": 70, "emotional_payoff_window": 3}),
    ]
    revision = 0
    for section, data in sections:
        result = engine.update_blueprint(
            {"section": section, "data": data, "expected_revision": revision}
        )
        revision = int(result["revision"])
    return revision


def _accepted_actual() -> dict[str, object]:
    return {
        "events": [
            {
                "id": "e1",
                "status": "closed",
                "result": "抵达终点并作出选择",
                "participants": ["c1"],
                "location": "l1",
                "start_tick": 0,
                "duration_ticks": 1,
                "outcome_tags": ["resolved"],
                "evidence_terms": ["终点"],
            }
        ],
        "theme_tags": ["成长"],
        "state_changes": [],
        "relationship_changes": [],
        "foreshadow_ops": [],
        "emotional_transactions": [],
        "summary": "林舟抵达终点并完成不可逆选择。",
    }


class NovelSystemEngineTests(unittest.TestCase):
    def test_full_transaction_rejects_short_prose_then_commits_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-novel-") as temporary:
            engine = NovelSystemEngine(Path(temporary) / "project")
            created = engine.create_project(
                {
                    "title": "终点之前",
                    "genre": "科幻",
                    "planned_chapters": 1,
                    "target_words": 1000,
                }
            )
            self.assertEqual(created["status"], "NOVEL_PROJECT_CREATED")
            revision = _stage_minimal_blueprint(engine)
            assisted = engine.assist_blueprint({})
            self.assertEqual(assisted["energy"], 0)
            compiled = engine.compile_blueprint({"expected_revision": revision})
            self.assertEqual(compiled["next_chapter"], 1)
            lease = engine.checkout_chapter({"chapter_number": 1})

            with self.assertRaises(NovelSystemError) as rejected:
                engine.submit_chapter(
                    {
                        "lease_id": lease["lease_id"],
                        "chapter_number": 1,
                        "title": "终点之前",
                        "content": "林舟抵达终点。",
                        "actual": _accepted_actual(),
                    }
                )
            self.assertEqual(rejected.exception.code, "CHAPTER_SUBMISSION_REJECTED")
            self.assertTrue(rejected.exception.details["lease_reusable"])

            content = "终点的风穿过旧站台。" + "林舟确认眼前的选择不可撤回，他仍然向前。" * 24
            accepted = engine.submit_chapter(
                {
                    "lease_id": lease["lease_id"],
                    "chapter_number": 1,
                    "title": "终点之前",
                    "content": content,
                    "actual": _accepted_actual(),
                }
            )
            self.assertTrue(accepted["accepted"])
            self.assertTrue(accepted["complete"])
            audit = engine.audit({})
            self.assertTrue(audit["complete"])
            self.assertEqual(audit["energy"], 0)
            chapter = Path(accepted["chapter_path"])
            self.assertTrue(chapter.is_file())
            self.assertTrue(chapter.read_text(encoding="utf-8").endswith("\n"))


if __name__ == "__main__":
    unittest.main()
