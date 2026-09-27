from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from copy import deepcopy
import json
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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tg-novel-")
        self.addCleanup(self.temp.cleanup)
        self.engine = NovelSystemEngine(Path(self.temp.name) / "project")
        self.engine.create_project({"title": "终点之前", "genre": "科幻", "planned_chapters": 1, "target_words": 6000})
        self.revision = _stage_minimal_blueprint(self.engine)

    def compile(self):
        return self.engine.compile_blueprint({"expected_revision": self.revision})

    def submission(self, actual=None):
        lease = self.engine.checkout_chapter({"chapter_number": 1})
        return {"lease_id": lease["lease_id"], "chapter_number": 1, "title": "终点之前",
                "content": "星光熄灭。", "actual": _accepted_actual() if actual is None else actual}

    def test_short_prose_and_missing_literal_evidence_are_observations_not_rejections(self):
        self.compile()
        result = self.engine.submit_chapter(self.submission())
        self.assertTrue(result["committed"])
        self.assertTrue(result["all_planned_chapters_recorded"])
        self.assertNotIn("accepted", result)
        self.assertNotIn("complete", result)
        self.assertEqual(result["content_quality"], "unassessed")
        self.assertEqual(result["observations"]["literal_evidence_matches"], [{"event_id": "e1", "term": "终点", "present": False}])
        self.assertEqual(Path(result["chapter_path"]).read_text(encoding="utf-8"), "星光熄灭。\n")
        self.assertTrue(self.engine.audit({})["all_planned_chapters_recorded"])
        self.assertNotIn("complete", self.engine.audit({}))
        self.assertNotIn("complete", self.engine.status())

    def test_unreported_planned_event_does_not_block_recording(self):
        self.compile()
        result = self.engine.submit_chapter(self.submission({}))
        self.assertEqual(result["observations"]["planned_event_ids_not_reported"], ["e1"])
        self.assertTrue(result["committed"])

    def test_caller_selects_single_scene_without_score_and_invalidates_old_lease(self):
        compiled = self.compile()
        submission = self.submission()
        scene = self.engine.design_scene({"candidates": [{"title": "宁静", "target_chapter": 1, "scores": {"emotion": 0}}],
                "selected_index": 0, "expected_state_hash": compiled["state_hash"]})
        self.assertEqual(scene["selected"]["selection_source"], "caller")
        with self.assertRaises(NovelSystemError) as error:
            self.engine.submit_chapter(submission)
        self.assertEqual(error.exception.code, "STALE_STATE")
        self.assertTrue(self.engine.submit_chapter(self.submission())["committed"])

    def test_bad_delta_types_and_stale_precondition_are_rejected_without_writes(self):
        self.compile()
        submission = self.submission()
        before = self.engine.state_path.read_bytes()
        for actual in ({"events": [{}]}, {"events": [{"id": "e1", "status": []}]},
                       {"state_changes": [{"character_id": "c1", "field": "alive", "op": []}]},
                       {"state_changes": [{"character_id": "c1", "field": "alive", "from": False, "to": True}]}):
            with self.subTest(actual=actual), self.assertRaises(NovelSystemError) as error:
                self.engine.submit_chapter({**submission, "actual": actual})
            self.assertEqual(error.exception.code, "CHAPTER_SUBMISSION_REJECTED")
            self.assertEqual(self.engine.state_path.read_bytes(), before)
            self.assertEqual(self.engine._ledger(), [])
        self.assertTrue(self.engine.submit_chapter(submission)["committed"])

    def test_old_checkout_requires_new_lease_but_preserves_original(self):
        self.compile()
        submission = self.submission()
        path = self.engine.leases_dir / (submission["lease_id"] + ".json")
        lease = json.loads(path.read_text(encoding="utf-8"))
        lease["schema"] = "tiangong.novel.chapter-lease.v1"
        path.write_text(json.dumps(lease))
        with self.assertRaises(NovelSystemError) as error:
            self.engine.submit_chapter(submission)
        self.assertEqual(error.exception.code, "LEASE_VERSION_CHANGED")
        self.assertTrue(path.exists())
        self.assertTrue(self.engine.submit_chapter(self.submission())["committed"])

    def prepared(self):
        self.compile()
        submission = self.submission()
        import v3.novel_system as module
        real_write = module._atomic_json
        def interrupt(path, value):
            if path == self.engine.state_path:
                raise OSError("injected after chapter bytes and ledger, before state")
            return real_write(path, value)
        with mock.patch.object(module, "_atomic_json", side_effect=interrupt), self.assertRaises(OSError):
            self.engine.submit_chapter(submission)
        return next(self.engine.prepared_dir.glob("*.json"))

    def test_partial_commit_recovers_once_in_fresh_engine(self):
        self.prepared()
        restarted = NovelSystemEngine(self.engine.root)
        result = restarted.recover()
        self.assertEqual(result["recovered_count"], 1)
        self.assertEqual(restarted.recover()["recovered_count"], 0)
        self.assertEqual(len(restarted._ledger()), 1)
        self.assertEqual(restarted._state()["next_chapter"], 2)
        self.assertEqual(result["content_quality"], "unassessed")

    def test_legacy_transaction_recovery_is_factual_not_quality_approval(self):
        path = self.prepared()
        transaction = json.loads(path.read_text(encoding="utf-8"))
        transaction["schema"] = "tiangong.novel.chapter-transaction.v1"
        path.write_text(json.dumps(transaction))
        self.assertEqual(self.engine.recover()["recovered_count"], 1)
        self.assertEqual(self.engine.audit({})["content_quality"], "unassessed")

    def test_invalid_recovery_never_writes_canonical_files(self):
        path = self.prepared()
        transaction = json.loads(path.read_text(encoding="utf-8"))
        for mutate, code in ((lambda t: t["next_state"].update(next_chapter=99), "CORRUPT_PREPARED_STATE"),
                             (lambda t: t.update(content="forged"), "CORRUPT_PREPARED_TRANSACTION"),
                             (lambda t: t.update(prose_relative="../outside.md"), "UNSAFE_PREPARED_TRANSACTION")):
            tampered = deepcopy(transaction)
            mutate(tampered)
            path.write_text(json.dumps(tampered))
            before = {p: p.read_bytes() for p in self.engine.root.rglob("*") if p.is_file()}
            with self.assertRaises(NovelSystemError) as error:
                self.engine.recover()
            self.assertEqual(error.exception.code, code)
            self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_recovery_preserves_concurrent_external_edits(self):
        path = self.prepared()
        transaction = json.loads(path.read_text(encoding="utf-8"))
        prose = self.engine.root / transaction["prose_relative"]
        prose.write_text("用户修改", encoding="utf-8")
        before = self.engine.state_path.read_bytes()
        with self.assertRaises(NovelSystemError) as error:
            self.engine.recover()
        self.assertEqual(error.exception.code, "RECOVERY_CONTENT_CONFLICT")
        self.assertEqual(prose.read_text(encoding="utf-8"), "用户修改")
        self.assertEqual(self.engine.state_path.read_bytes(), before)

    def test_last_chapter_repair_preserves_old_bytes_and_invalidates_old_version(self):
        self.compile()
        first = self.engine.submit_chapter(self.submission({}))
        lease = self.engine.checkout_chapter({"chapter_number": 1, "revision_of": first["chapter_sha256"]})
        fixed = self.engine.submit_chapter({"lease_id": lease["lease_id"], "chapter_number": 1,
            "title": "修订", "content": "星光重新亮起。", "actual": {}})
        self.assertEqual(fixed["revision_of"], first["chapter_sha256"])
        self.assertNotEqual(fixed["state_hash"], first["state_hash"])
        self.assertEqual(len(self.engine._ledger()), 1)
        self.assertEqual(Path(fixed["chapter_path"]).read_text(encoding="utf-8"), "星光重新亮起。\n")
        history = [json.loads(p.read_text(encoding="utf-8")) for p in self.engine.committed_dir.glob("*.json")]
        self.assertEqual(len(history), 2)
        self.assertTrue(any(t.get("previous_content") == "星光熄灭。\n" for t in history))
        with self.assertRaises(NovelSystemError) as error:
            self.engine.checkout_chapter({"chapter_number": 1, "revision_of": first["chapter_sha256"]})
        self.assertEqual(error.exception.code, "CHAPTER_REVISION_CONFLICT")

    def test_revision_after_interruption_recovers_without_duplicate_delta(self):
        self.compile()
        first = self.engine.submit_chapter(self.submission())
        lease = self.engine.checkout_chapter({"chapter_number": 1, "revision_of": first["chapter_sha256"]})
        import v3.novel_system as module
        before_events = self.engine._state()["events"]
        real_write = module._atomic_json
        def interrupt(path, value):
            if path == self.engine.state_path:
                raise OSError("interrupted revision")
            return real_write(path, value)
        with mock.patch.object(module, "_atomic_json", side_effect=interrupt), self.assertRaises(OSError):
            self.engine.submit_chapter({"lease_id": lease["lease_id"], "chapter_number": 1,
                "title": "修订", "content": "星光重新亮起。", "actual": {}})
        self.assertEqual(self.engine.recover()["recovered_count"], 1)
        self.assertEqual(self.engine._state()["events"], before_events)
        self.assertEqual(len(self.engine._ledger()), 1)

    def test_timeline_inference_is_advisory(self):
        self.engine.patch_blueprint({"section": "plot_events", "selector": {"id": "e1"},
            "changes": {"expected_ages": {"c1": 999}}})
        self.revision += 1
        assisted = self.engine.assist_blueprint({})
        self.assertGreater(assisted["energy"], 0)
        self.assertTrue(assisted["ready_for_compile"])
        self.assertEqual(assisted["observations"][0]["code"], "AGE_MISMATCH")
        self.compile()


if __name__ == "__main__":
    unittest.main()
