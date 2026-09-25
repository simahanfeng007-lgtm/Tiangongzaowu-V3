from __future__ import annotations

import hashlib
import json
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
    @classmethod
    def source_root(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "dictionaries"

    @classmethod
    def setUpClass(cls):
        root = cls.source_root()
        cls.INDEX_SHA256 = hashlib.sha256((root / "skills/catalog.json").read_bytes()).hexdigest()
        cls.CATALOG_SHA256 = load_filesystem_skill_catalog(root, expected_index_sha256=cls.INDEX_SHA256).catalog.sha256
        cls.CAPABILITY_SHA256 = hashlib.sha256((root / "registry/capability_manifest.generated.json").read_bytes()).hexdigest()

    def test_production_catalog_has_no_injected_fixed_skills(self) -> None:
        loaded = load_filesystem_skill_catalog(
            self.source_root(), expected_index_sha256=self.INDEX_SHA256,
            expected_catalog_sha256=self.CATALOG_SHA256,
        )
        self.assertEqual(loaded.source_file_count, 0)
        self.assertEqual(loaded.catalog.sha256, self.CATALOG_SHA256)
        self.assertEqual(loaded.catalog.definitions, ())
        self.assertIsNone(loaded.catalog.get("skill_word_business_proposal_worldclass_v1"))

    def test_model_surface_remains_executable_without_fixed_skill_recommendations(self) -> None:
        root = self.source_root()
        loaded = load_filesystem_skill_catalog(
            root, expected_index_sha256=self.INDEX_SHA256,
            expected_catalog_sha256=self.CATALOG_SHA256,
        )
        model_capabilities = load_model_capability_manifest(
            root / "registry/capability_manifest.generated.json",
            expected_sha256=self.CAPABILITY_SHA256,
            component_manifest_hash=HASH_C, generated_at_ms=100,
        )
        self.assertEqual(model_capabilities.executable_count, 285)
        for query in ("请制作商业方案 Word 文档", "继续受管长篇小说工程",
                      "创建超长文档并交付 DOCX", "设计 Mermaid 脑图",
                      "微信小程序 WXML WXSS 离线工程"):
            with self.subTest(query=query):
                recommendation = SkillSelectionService(loaded.catalog).system_recommend(
                    query, request_id=REQUEST_ID, run_id=RUN_ID, generation=1,
                    capability_manifest=model_capabilities.manifest, decided_at_ms=1000,
                )
                self.assertEqual(recommendation.decision, "no_skill")
                self.assertIsNone(recommendation.selected_skill_id)
                self.assertEqual(recommendation.candidates, ())

    def test_pins_and_retired_fixed_skill_injection_are_enforced(self) -> None:
        from capability_dictionary import DictionaryError
        with self.assertRaisesRegex(SkillSelectionError, "index digest"):
            load_filesystem_skill_catalog(self.source_root(), expected_index_sha256=HASH_A)
        with self.assertRaisesRegex(SkillSelectionError, "catalog digest"):
            load_filesystem_skill_catalog(self.source_root(),
                expected_index_sha256=self.INDEX_SHA256, expected_catalog_sha256=HASH_A)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "dictionary"
            shutil.copytree(self.source_root(), copied)
            (copied / "skills/fixture.md").write_text("# Fixture", encoding="utf-8")
            loaded = load_filesystem_skill_catalog(copied,
                expected_index_sha256=self.INDEX_SHA256, expected_catalog_sha256=self.CATALOG_SHA256)
            self.assertEqual(loaded.catalog.definitions, ())
            index = copied / "skills/catalog.json"
            raw = json.loads(index.read_text(encoding="utf-8"))
            raw.update(skill_count=1, actions=["skill.route", "skill.list", "skill.get", "skill.read"],
                       skills=[{"id": "skill_fixture_v1", "mingcheng": "Fixture", "category": "test",
                                "file": "skills/fixture.md", "required_actions": ["file.list"]}])
            index.write_text(json.dumps(raw), encoding="utf-8")
            # Rehashing a caller-injected catalog does not restore fixed Skills.
            with self.assertRaisesRegex(DictionaryError, "dictionary_fixed_skills_retired"):
                load_filesystem_skill_catalog(copied,
                    expected_index_sha256=hashlib.sha256(index.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
