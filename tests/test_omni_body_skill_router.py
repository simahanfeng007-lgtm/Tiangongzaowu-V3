from __future__ import annotations

import importlib.util
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, patch


ROOT = Path(__file__).resolve().parents[1]
ROUTER = (
    ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "omni_body_skill"
    / "tools"
    / "skill_router.py"
)
MODEL_ADAPTER = ROUTER.parents[1] / "model_adapters" / "core.py"
MANAGED_NOVEL_SKILL = (
    ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "v3"
    / "bundled_skills"
    / "novel-creation"
    / "SKILL.md"
)
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
PRINCIPAL = "3" * 64


def _load_router():
    spec = importlib.util.spec_from_file_location("tiangong_test_skill_router", ROUTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime(*, activation: str = ""):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            principal_scope_hash=PRINCIPAL,
            skill_activation_sha256=activation,
        )
    )


def _selection(*, decision="defer", candidates=(), selected=None):
    return {
        "selection_id": "selection_test",
        "origin": "model_request",
        "operation": "skill.route",
        "decision": decision,
        "candidates": list(candidates),
        "selected_skill_id": selected,
    }


def _candidate(skill_id: str, *, compatible: bool = True):
    return {
        "skill_id": skill_id,
        "version": "v1",
        "sha256": "a" * 64,
        "source_ref": "skill_source_test",
        "score_millis": 900,
        "required_actions": ["file.read"],
        "missing_actions": [] if compatible else ["file.read"],
        "incompatible_reasons": [] if compatible else ["skill.required_action_unavailable"],
        "compatible": compatible,
    }


class OmniBodySkillRouterTests(unittest.TestCase):
    def test_router_has_no_second_catalog_or_local_matching_logic(self) -> None:
        router = _load_router()
        self.assertFalse(hasattr(router, "SKILL_CATALOG"))
        self.assertFalse(hasattr(router, "_skill_by_id"))
        self.assertFalse(hasattr(router, "_score_skill"))
        self.assertFalse(hasattr(router, "_package_root"))

    def test_route_presents_gateway_candidates_without_local_fallback(self) -> None:
        router = _load_router()
        candidate = _candidate("skill_managed_longform_novel_worldclass_v1")
        response = {
            "status": "OK",
            "catalog_sha256": "b" * 64,
            "selection_record_sha256": "c" * 64,
            "selection": _selection(candidates=(candidate,)),
        }
        with patch.object(router, "_request_gateway", return_value=response) as request:
            result = router._skill_route(_runtime(), None, {"job": "继续受管小说工程"})  # noqa: SLF001
        self.assertTrue(result["success"])
        self.assertEqual(
            result["result"]["recommended_skill"]["id"],
            "skill_managed_longform_novel_worldclass_v1",
        )
        request.assert_called_once_with(
            ANY,
            "skill.route",
            {"query": "继续受管小说工程", "limit": 8, "decline": False},
        )

        no_match = {**response, "selection": _selection(decision="no_skill")}
        with patch.object(router, "_request_gateway", return_value=no_match):
            absent = router._skill_route(_runtime(), None, {"job": "今天天气不错"})  # noqa: SLF001
        self.assertIsNone(absent["result"]["recommended_skill"])
        self.assertEqual(absent["result"]["next_model_action"]["call"], "skill.list")

    def test_get_and_read_return_only_gateway_verified_content_and_activation(self) -> None:
        router = _load_router()
        activation = {
            "activation_id": "activation_test",
            "activation_sha256": "d" * 64,
        }
        response = {
            "status": "OK",
            "catalog_sha256": "b" * 64,
            "selection_record_sha256": "c" * 64,
            "selection": _selection(decision="activate", selected="skill_word"),
            "content": "# Verified Skill",
            "activation": activation,
        }
        for operation in ("skill.get", "skill.read"):
            with patch.object(router, "_request_gateway", return_value=response) as request:
                result = router._skill_get(  # noqa: SLF001
                    _runtime(), "skill_word", {}, operation=operation
                )
            self.assertTrue(result["success"])
            self.assertEqual(result["result"]["markdown"], "# Verified Skill")
            self.assertEqual(result["activation"], activation)
            request.assert_called_once_with(
                ANY,
                operation,
                {"skill_id": "skill_word"},
            )

    def test_step_guard_ignores_model_completed_qc_and_artifact_claims(self) -> None:
        router = _load_router()
        observed = {}

        def gateway(_runtime, operation, payload):
            observed["operation"] = operation
            observed["payload"] = payload
            return {
                "status": "OK",
                "catalog_sha256": "b" * 64,
                "step": {
                    "current_stage": "quality_gate",
                    "complete": False,
                    "completed_actions": ["pptx.create"],
                    "pending_actions": ["qc.ppt.delivery_check"],
                },
            }

        with patch.object(router, "_request_gateway", side_effect=gateway):
            result = router._skill_step_check(  # noqa: SLF001
                _runtime(activation="e" * 64),
                "skill_ppt",
                {
                    "completed_actions": ["pptx.create", "qc.ppt.delivery_check"],
                    "last_qc": {"passed": True},
                    "artifacts": [{"path": "missing.pptx", "exists": True}],
                },
            )
        self.assertFalse(result["result"]["complete"])
        self.assertEqual(observed["operation"], "skill.step.check")
        self.assertEqual(
            observed["payload"],
            {"skill_id": "skill_ppt", "skill_activation_sha256": "e" * 64},
        )

    def test_missing_execution_scope_or_gateway_is_fail_closed(self) -> None:
        router = _load_router()
        result = router._skill_route(None, None, {"job": "做 PPT"})  # noqa: SLF001
        self.assertFalse(result["success"])
        self.assertIn("authority scope", result["message"])
        with patch.object(router, "_request_gateway", side_effect=router.SkillGatewayError("offline")):
            unavailable = router._skill_get(_runtime(), "skill_word", {})  # noqa: SLF001
        self.assertFalse(unavailable["success"])
        self.assertFalse(unavailable["evidence"]["verified"])

    def test_model_contract_and_managed_skill_keep_scope_gate(self) -> None:
        model_contract = MODEL_ADAPTER.read_text(encoding="utf-8")
        managed_skill = MANAGED_NOVEL_SKILL.read_text(encoding="utf-8")
        self.assertIn("技能选择有双通道", model_contract)
        self.assertIn("Never infer `target_words` or `planned_chapters`", managed_skill)
        description_body = model_contract.split("def _tool_description()", 1)[1].split(
            "def render_tool_schema", 1
        )[0]
        self.assertNotIn("novel.project.create", description_body)


if __name__ == "__main__":
    unittest.main()
