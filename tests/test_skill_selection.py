from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from contracts import CapabilityAction, CapabilityManifest
from total_gateway.skill_selection import (
    SkillCatalog,
    SkillDefinition,
    SkillSelectionError,
    SkillSelectionService,
    load_filesystem_skill_catalog,
    load_model_capability_manifest,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64


def action(action_id: str, *, available: bool = True, model_visible: bool = True) -> CapabilityAction:
    return CapabilityAction(
        action_id=action_id,
        version="1.0.0",
        provider_component_id="tiangong-backend",
        argument_schema_sha256=HASH_A,
        result_schema_sha256=HASH_B,
        risk_class="A2",
        allowed_side_effects=("local_write",),
        idempotency_mode="effect_id_required",
        max_runtime_ms=60_000,
        max_output_bytes=10_000_000,
        max_tool_calls=1,
        available=available,
        unavailable_reason=None if available else "action.unavailable",
        model_visible=model_visible,
    )


def manifest(*action_ids: str) -> CapabilityManifest:
    actions = tuple(sorted((action(item) for item in action_ids), key=lambda item: (item.action_id, item.version)))
    return CapabilityManifest(
        manifest_id="capability_manifest_skill_test",
        revision=1,
        generated_at_ms=100,
        component_manifest_hash=HASH_C,
        actions=actions,
        sha256="0" * 64,
    ).with_computed_sha256()


def definition(
    skill_id: str,
    content: str,
    *,
    title: str,
    keywords: tuple[str, ...],
    intents: tuple[str, ...],
    required_actions: tuple[str, ...],
) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        version="1.0.0",
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_ref="source." + skill_id,
        title=title,
        summary=title,
        category="document",
        keywords=tuple(sorted(set(keywords))),
        task_intents=tuple(sorted(set(intents))),
        required_actions=tuple(sorted(set(required_actions))),
        content=content,
    )


def catalog() -> SkillCatalog:
    items = (
        definition(
            "skill.ppt",
            "# PPT Skill\n先生成，再做 QC。",
            title="演示文稿交付",
            keywords=("ppt", "演示文稿"),
            intents=("制作演示",),
            required_actions=("pptx.create", "qc.ppt.delivery_check"),
        ),
        definition(
            "skill.word",
            "# Word Skill\n生成 DOCX 后必须执行 DOCX QC。",
            title="Word 文档交付",
            keywords=("docx", "word", "方案", "文档"),
            intents=("word文档", "商业方案"),
            required_actions=("docx.create", "qc.docx.delivery_check"),
        ),
    )
    return SkillCatalog(tuple(sorted(items, key=lambda item: item.skill_id)))


class SkillSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SkillSelectionService(catalog())
        self.capabilities = manifest(
            "docx.create",
            "pptx.create",
            "qc.docx.delivery_check",
            "qc.ppt.delivery_check",
        )
        self.base = {
            "request_id": REQUEST_ID,
            "run_id": RUN_ID,
            "generation": 1,
            "capability_manifest": self.capabilities,
            "decided_at_ms": 1000,
        }

    def test_system_recommendation_is_candidate_not_activation(self) -> None:
        recommendation = self.service.system_recommend("请写一份商业 Word 方案", **self.base)
        self.assertEqual(recommendation.origin, "system_recommendation")
        self.assertEqual(recommendation.operation, "system.recommend")
        self.assertEqual(recommendation.decision, "defer")
        self.assertEqual(recommendation.activation_state, "candidate")
        self.assertEqual(recommendation.selected_skill_id, "skill.word")
        self.assertIsNone(recommendation.resolved_via)

        model_route = self.service.model_request(
            "skill.route",
            query="我要制作 PPT 演示文稿",
            **self.base,
        ).record
        self.assertEqual(model_route.origin, "model_request")
        self.assertEqual(model_route.operation, "skill.route")
        self.assertEqual(model_route.candidates[0].skill_id, "skill.ppt")
        self.assertEqual(model_route.activation_state, "candidate")

    def test_model_can_list_route_get_read_and_explicitly_decline(self) -> None:
        listed = self.service.model_request("skill.list", **self.base).record
        self.assertEqual([item.skill_id for item in listed.candidates], ["skill.ppt", "skill.word"])
        self.assertTrue(all(item.score_millis == 0 for item in listed.candidates))

        routed = self.service.model_request("skill.route", query="生成 DOCX 文档", **self.base).record
        self.assertEqual(routed.candidates[0].skill_id, "skill.word")
        self.assertNotEqual(routed.decision, "activate")

        for operation in ("skill.get", "skill.read"):
            resolved = self.service.model_request(operation, skill_id="skill.word", **self.base)
            self.assertEqual(resolved.record.decision, "activate")
            self.assertEqual(resolved.record.activation_state, "active")
            self.assertEqual(resolved.record.resolved_via, operation)
            self.assertTrue(resolved.content.startswith("# Word Skill"))
            self.assertEqual(
                hashlib.sha256(resolved.content.encode("utf-8")).hexdigest(),
                resolved.record.selected_skill_sha256,
            )

        declined = self.service.model_request(
            "skill.route",
            query="普通聊天不需要技能",
            decline=True,
            **self.base,
        ).record
        self.assertEqual(declined.decision, "no_skill")
        self.assertEqual(declined.reason_code, "skill.model_declined")
        self.assertEqual(declined.activation_state, "none")

    def test_missing_action_is_visible_and_blocks_activation(self) -> None:
        capabilities = manifest("docx.create")
        routed = self.service.model_request(
            "skill.route",
            query="生成 Word 文档",
            **{**self.base, "capability_manifest": capabilities},
        ).record
        candidate = routed.candidates[0]
        self.assertFalse(candidate.compatible)
        self.assertEqual(candidate.missing_actions, ("qc.docx.delivery_check",))
        self.assertEqual(candidate.incompatible_reasons, ("skill.required_action_unavailable",))

        resolved = self.service.model_request(
            "skill.get",
            skill_id="skill.word",
            **{**self.base, "capability_manifest": capabilities},
        )
        self.assertEqual(resolved.record.decision, "reject")
        self.assertEqual(resolved.record.activation_state, "rejected")
        self.assertIsNone(resolved.content)

    def test_no_match_never_falls_back_to_default_skill(self) -> None:
        system = self.service.system_recommend("今天天气不错", **self.base)
        self.assertEqual(system.decision, "no_skill")
        self.assertEqual(system.candidates, ())
        self.assertIsNone(system.selected_skill_id)

        model = self.service.model_request("skill.route", query="今天天气不错", **self.base).record
        self.assertEqual(model.decision, "no_skill")
        self.assertEqual(model.candidates, ())

    def test_empty_and_embedded_ascii_fragments_do_not_create_candidates(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty or malformed"):
            self.service.system_recommend("", **self.base)
        query = "What is the capital of France?"
        system = self.service.system_recommend(query, **self.base)
        self.assertEqual(system.decision, "no_skill")
        self.assertEqual(system.candidates, ())
        model = self.service.model_request("skill.route", query=query, **self.base).record
        self.assertEqual(model.decision, "no_skill")
        self.assertEqual(model.candidates, ())

    def test_decisions_are_deterministic_and_bind_manifest_and_query(self) -> None:
        first = self.service.model_request("skill.route", query=" 生成   Word 文档 ", **self.base).record
        second = self.service.model_request("skill.route", query="生成 Word 文档", **self.base).record
        self.assertEqual(first, second)
        self.assertEqual(first.capability_manifest_hash, self.capabilities.sha256)

        next_generation = self.service.model_request(
            "skill.route",
            query="生成 Word 文档",
            **{**self.base, "generation": 2},
        ).record
        self.assertNotEqual(first.selection_id, next_generation.selection_id)

    def test_tampered_manifest_or_skill_source_fails_closed(self) -> None:
        tampered = self.capabilities.model_copy(update={"sha256": HASH_A})
        with self.assertRaisesRegex(SkillSelectionError, "manifest digest"):
            self.service.model_request(
                "skill.route",
                query="生成 Word 文档",
                **{**self.base, "capability_manifest": tampered},
            )
        with self.assertRaises(ValidationError):
            SkillDefinition(
                skill_id="skill.bad",
                version="1",
                sha256=HASH_A,
                source_ref="source.bad",
                title="Bad",
                category="test",
                content="different content",
            )


class FilesystemSkillCatalogTests(unittest.TestCase):
    INDEX_SHA256 = "181c065471265728f7a55cdce28c2043ff0bf7d12ffa9c9dc00d577b24f1bc45"
    CATALOG_SHA256 = "fec4b0709945b614edce5b80aa1a69381ba66b0df85f4bf8f253eb47127d5b35"
    CAPABILITY_SHA256 = "e9e3a381bd27269eb85d53b78d0bb4e089f56014ce867de9572128bf7ec2d0fd"

    @classmethod
    def source_root(cls) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "app"
            / "backend"
            / "tiangong-backend"
            / "_internal"
            / "omni_body_skill"
        )

    def test_loads_actual_34_skill_sources_and_pins_content_and_actions(self) -> None:
        loaded = load_filesystem_skill_catalog(
            self.source_root(),
            expected_index_sha256=self.INDEX_SHA256,
            expected_catalog_sha256=self.CATALOG_SHA256,
        )
        self.assertEqual(loaded.source_file_count, 34)
        self.assertEqual(loaded.catalog.sha256, self.CATALOG_SHA256)
        word = loaded.catalog.get("skill_word_business_proposal_worldclass_v1")
        self.assertIsNotNone(word)
        self.assertEqual(word.version, "v1")
        self.assertEqual(
            word.sha256,
            "f42c07e0234df77cfc10a7fc45b077d4b0344ad89fef3477b2ebfe63181f13a6",
        )
        self.assertIn("docx.create", word.required_actions)
        self.assertIn("qc.docx.delivery_check", word.required_actions)
        self.assertIn("deliverable.package", word.required_actions)
        ppt = loaded.catalog.get("skill_ppt_executive_report_worldclass_v1")
        self.assertIsNotNone(ppt)
        self.assertIn("pptx.read", ppt.required_actions)
        mindmap = loaded.catalog.get("skill_mindmap_knowledge_architecture_worldclass_v1")
        self.assertIsNotNone(mindmap)
        self.assertIn("mindmap.create", mindmap.required_actions)
        self.assertIn("file.read", mindmap.required_actions)
        long_document = loaded.catalog.get("skill_managed_long_document_worldclass_v1")
        self.assertIsNotNone(long_document)
        self.assertEqual(
            long_document.sha256,
            "bcdd56d27cbe81c59584f776b5174b3014527b0b0990099fcbfacdb55d591398",
        )
        self.assertIn("docx.create", long_document.required_actions)
        self.assertIn("qc.docx.delivery_check", long_document.required_actions)

    def test_system_matching_uses_the_pinned_model_action_surface(self) -> None:
        root = self.source_root()
        loaded = load_filesystem_skill_catalog(
            root,
            expected_index_sha256=self.INDEX_SHA256,
            expected_catalog_sha256=self.CATALOG_SHA256,
        )
        model_capabilities = load_model_capability_manifest(
            root / "registry" / "capability_manifest.generated.json",
            expected_sha256=self.CAPABILITY_SHA256,
            component_manifest_hash=HASH_C,
            generated_at_ms=100,
        )
        self.assertEqual(model_capabilities.executable_count, 289)
        recommendation = SkillSelectionService(loaded.catalog).system_recommend(
            "\u8bf7\u5236\u4f5c\u5546\u4e1a\u65b9\u6848 Word\u6587\u6863",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        )
        self.assertEqual(
            recommendation.selected_skill_id,
            "skill_word_business_proposal_worldclass_v1",
        )
        self.assertEqual(recommendation.decision, "defer")
        self.assertTrue(recommendation.candidates[0].compatible)
        self.assertEqual(recommendation.candidates[0].missing_actions, ())

        managed = SkillSelectionService(loaded.catalog).system_recommend(
            "继续受管长篇小说工程，检查完整规划并按大纲断点续写",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        )
        self.assertEqual(
            managed.selected_skill_id,
            "skill_managed_longform_novel_worldclass_v1",
        )
        self.assertTrue(managed.candidates[0].compatible)
        self.assertEqual(managed.candidates[0].missing_actions, ())
        self.assertIn("novel.project.status", managed.candidates[0].required_actions)

        long_document = SkillSelectionService(loaded.catalog).system_recommend(
            "创建一个二十万字超长文档工程，按章节断点续写并最终交付 DOCX",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        )
        self.assertEqual(
            long_document.selected_skill_id,
            "skill_managed_long_document_worldclass_v1",
        )
        self.assertTrue(long_document.candidates[0].compatible)
        self.assertEqual(long_document.candidates[0].missing_actions, ())
        self.assertNotIn(
            "skill_core_actions_reference_v1",
            {item.skill_id for item in long_document.candidates},
        )
        model_long_document = SkillSelectionService(loaded.catalog).model_request(
            "skill.route",
            query="超长文档设计：创建多章文档工程并断点续写",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        ).record
        self.assertEqual(
            model_long_document.candidates[0].skill_id,
            "skill_managed_long_document_worldclass_v1",
        )
        self.assertNotIn(
            "skill_core_actions_reference_v1",
            {item.skill_id for item in model_long_document.candidates},
        )

        mindmap = SkillSelectionService(loaded.catalog).system_recommend(
            "设计一份城市韧性知识图谱脑图，同时交付 Mermaid mindmap 和 OPML",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        )
        self.assertEqual(
            mindmap.selected_skill_id,
            "skill_mindmap_knowledge_architecture_worldclass_v1",
        )
        self.assertTrue(mindmap.candidates[0].compatible)
        self.assertEqual(mindmap.candidates[0].missing_actions, ())

        miniapp = SkillSelectionService(loaded.catalog).system_recommend(
            "code_engineering:wechat_miniapp_offline 微信小程序 WXML WXSS 离线工程",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            capability_manifest=model_capabilities.manifest,
            decided_at_ms=1000,
        )
        self.assertEqual(
            miniapp.selected_skill_id,
            "skill_code_project_delivery_worldclass_v1",
        )
        self.assertTrue(miniapp.candidates[0].compatible)
        self.assertEqual(miniapp.candidates[0].missing_actions, ())

    def test_index_or_any_markdown_drift_fails_pinned_load(self) -> None:
        with self.assertRaisesRegex(SkillSelectionError, "index digest"):
            load_filesystem_skill_catalog(
                self.source_root(),
                expected_index_sha256=HASH_A,
            )

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "skills"
            shutil.copytree(self.source_root(), copied)
            word_path = copied / "deliverable_skills" / "29_skill_word_business_proposal_worldclass.md"
            word_path.write_bytes(word_path.read_bytes() + b"\nsource drift\n")
            with self.assertRaisesRegex(SkillSelectionError, "catalog digest"):
                load_filesystem_skill_catalog(
                    copied,
                    expected_index_sha256=self.INDEX_SHA256,
                    expected_catalog_sha256=self.CATALOG_SHA256,
                )


if __name__ == "__main__":
    unittest.main()
