from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill" / "tools" / "novel_system.py"


def _load_module():
    package = types.ModuleType("v3")
    package.__path__ = []  # type: ignore[attr-defined]
    engine = types.ModuleType("v3.novel_system")

    class NovelSystemError(RuntimeError):
        def payload(self):
            return {"success": False, "message": str(self)}

    class NovelSystemEngine:
        pass

    engine.NovelSystemEngine = NovelSystemEngine
    engine.NovelSystemError = NovelSystemError
    old_v3 = sys.modules.get("v3")
    old_engine = sys.modules.get("v3.novel_system")
    sys.modules["v3"] = package
    sys.modules["v3.novel_system"] = engine
    try:
        spec = importlib.util.spec_from_file_location("test_novel_workspace_module", SOURCE)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old_v3 is None:
            sys.modules.pop("v3", None)
        else:
            sys.modules["v3"] = old_v3
        if old_engine is None:
            sys.modules.pop("v3.novel_system", None)
        else:
            sys.modules["v3.novel_system"] = old_engine


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class NovelWorkspaceProjectionTests(unittest.TestCase):
    def test_chapter_files_sort_by_chapter_number_not_filename_text(self) -> None:
        module = _load_module()
        chapters = [Path("第9章_旧章.md"), Path("第10章_新章.md"), Path("第2章_早章.md")]
        ordered = sorted(chapters, key=module._chapter_file_sort_key)
        self.assertEqual([item.name for item in ordered], ["第2章_早章.md", "第9章_旧章.md", "第10章_新章.md"])

    def test_complete_canonical_project_exports_portable_workspace(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "潮汐盲区"
            system = root / ".novel-system"
            blueprint = {
                "project": {"title": "潮汐盲区", "genre": "近未来悬疑", "planned_chapters": 2, "target_words": 8000},
                "story": {"soul": "记忆与责任", "core_conflict": "公共记忆被篡改"},
                "characters": [{"id": "char.zhoulan", "name": "周岚"}],
                "world": {"rules": ["异常必须留下物理痕迹"]},
                "calendar": {"tick_unit": "hour"},
                "locations": [{"id": "loc.station", "name": "潮汐电站"}],
                "routes": [],
                "schedules": [],
                "progression_rules": [],
                "plot_events": [{"id": "evt.001", "chapter": 1}],
                "chapters": [{"number": 1, "title": "潮线之下"}, {"number": 2, "title": "缺口回声"}],
                "relationships": [],
                "foreshadows": [{"id": "f.001", "name": "黄铜钥匙"}],
                "emotional_accounts": [],
                "settings": {"min_chapter_chars": 2500},
            }
            _write_json(system / "manifest.json", blueprint["project"])
            _write_json(system / "blueprints" / "original.json", blueprint)
            _write_json(system / "blueprints" / "rolling.json", blueprint)
            _write_json(system / "state" / "current.json", {"next_chapter": 2, "state_hash": "abc"})
            chapter = root / "正文" / "第0001章_潮线之下.md"
            chapter.parent.mkdir(parents=True, exist_ok=True)
            chapter.write_text("第一章正文", encoding="utf-8")

            result = module._sync_managed_workspace(root)

            self.assertTrue(result["planning_complete"])
            self.assertEqual(result["resume_action"], "novel.chapter.checkout")
            self.assertEqual(Path(result["latest_chapter"]), chapter.resolve())
            self.assertTrue((root / "大纲" / "全书大纲.md").is_file())
            self.assertIn("公共记忆被篡改", (root / "大纲" / "全书大纲.md").read_text(encoding="utf-8"))
            pipeline = json.loads((root / "pipeline_state.json").read_text(encoding="utf-8"))
            self.assertEqual(pipeline["mode"], "resume")
            self.assertEqual(pipeline["next_chapter"], 2)

    def test_partial_project_reports_missing_plan_instead_of_claiming_resume(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "未完成工程"
            system = root / ".novel-system"
            _write_json(system / "manifest.json", {"title": "未完成", "planned_chapters": 10, "target_words": 40000})
            _write_json(system / "blueprints" / "staged.json", {"story": {"soul": "test"}, "characters": []})

            result = module._sync_managed_workspace(root)

            self.assertFalse(result["planning_complete"])
            self.assertEqual(result["resume_action"], "novel.blueprint.update")
            self.assertIn("chapters", result["missing_planning_sections"])
            self.assertTrue((root / "工程说明.md").is_file())

    def test_duplicate_chapter_numbers_cannot_claim_complete_planning(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "损坏规划"
            system = root / ".novel-system"
            blueprint = {
                "project": {"title": "损坏规划", "planned_chapters": 2, "target_words": 8000},
                "story": {"soul": "test"},
                "characters": [{"id": "c1", "name": "一"}],
                "world": {"rules": ["r"]},
                "calendar": {"tick_unit": "day"},
                "locations": [{"id": "l1", "name": "地"}],
                "plot_events": [{"id": "e1", "chapter": 1}],
                "chapters": [{"number": 1, "title": "一"}, {"number": 1, "title": "重复"}],
            }
            _write_json(system / "manifest.json", blueprint["project"])
            _write_json(system / "blueprints" / "original.json", blueprint)
            _write_json(system / "blueprints" / "rolling.json", blueprint)
            _write_json(system / "state" / "current.json", {"next_chapter": 1, "state_hash": "abc"})
            result = module._sync_managed_workspace(root)
            self.assertFalse(result["planning_complete"])
            self.assertIn("chapters.numbering", result["missing_planning_sections"])
            self.assertEqual(result["resume_action"], "novel.blueprint.update")

    def test_corrupt_checkpoint_requires_recovery_not_continuation(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "损坏断点"
            system = root / ".novel-system"
            blueprint = {
                "project": {"title": "损坏断点", "planned_chapters": 1, "target_words": 4000},
                "story": {"soul": "test"},
                "characters": [{"id": "c1", "name": "一"}],
                "world": {"rules": ["r"]},
                "calendar": {"tick_unit": "day"},
                "locations": [{"id": "l1", "name": "地"}],
                "plot_events": [{"id": "e1", "chapter": 1}],
                "chapters": [{"number": 1, "title": "一"}],
            }
            _write_json(system / "manifest.json", blueprint["project"])
            _write_json(system / "blueprints" / "original.json", blueprint)
            _write_json(system / "blueprints" / "rolling.json", blueprint)
            _write_json(system / "state" / "current.json", {"next_chapter": 99, "state_hash": "abc"})
            result = module._sync_managed_workspace(root)
            self.assertFalse(result["planning_complete"])
            self.assertTrue(result["recovery_required"])
            self.assertIn("state.next_chapter", result["state_issues"])
            self.assertEqual(result["resume_action"], "novel.project.recover")

    def test_manifest_and_blueprint_target_mismatch_cannot_claim_complete_planning(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "字数冲突"
            system = root / ".novel-system"
            blueprint = {
                "project": {"title": "字数冲突", "planned_chapters": 1, "target_words": 4000},
                "story": {"soul": "test"},
                "characters": [{"id": "c1", "name": "一"}],
                "world": {"rules": ["r"]},
                "calendar": {"tick_unit": "day"},
                "locations": [{"id": "l1", "name": "地"}],
                "plot_events": [{"id": "e1", "chapter": 1}],
                "chapters": [{"number": 1, "title": "一"}],
            }
            _write_json(system / "manifest.json", {**blueprint["project"], "target_words": 8000})
            _write_json(system / "blueprints" / "original.json", blueprint)
            _write_json(system / "blueprints" / "rolling.json", blueprint)
            _write_json(system / "state" / "current.json", {"next_chapter": 1, "state_hash": "abc"})

            result = module._sync_managed_workspace(root)

            self.assertFalse(result["planning_complete"])
            self.assertIn("project.target_words_mismatch", result["missing_planning_sections"])
            self.assertEqual(result["resume_action"], "novel.blueprint.update")


if __name__ == "__main__":
    unittest.main()
