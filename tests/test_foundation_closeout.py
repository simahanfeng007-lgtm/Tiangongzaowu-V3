from __future__ import annotations

import pytest

from contextlib import closing
from email.message import Message
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
OMNI_SOURCE = ROOT / "readable-python-source"
for item in (SRC, BACKEND, OMNI_SOURCE):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from runtime_security import model_endpoint
from total_gateway.life_log import LifeLog, LifeLogError
from total_gateway.soul_backup import SoulBackupError, SoulBackupManager, SoulSource
from omni_body_skill.tools.sandbox_runtime import SandboxLimits, SandboxRunner, _rewrite_workspace_paths


class BackendGatewayBoundaryTests(unittest.TestCase):
    def test_backend_business_routes_require_internal_token_and_reject_browser_origin(self) -> None:
        from v3.duihua_qiaojie import _ChuliQi

        handler = object.__new__(_ChuliQi)
        handler.headers = Message()
        observed: list[tuple[dict, int]] = []
        handler._write_json = lambda body, status=200: observed.append((body, status))
        with mock.patch.dict(os.environ, {"TIANGONG_DESKTOP_TOKEN": "T" * 48}, clear=False):
            self.assertTrue(handler._authorize_business_route("/health"))
            self.assertFalse(handler._authorize_business_route("/api/v1/llm/settings"))
            self.assertEqual(observed[-1][1], 401)
            handler.headers["X-Tiangong-Token"] = "T" * 48
            self.assertTrue(handler._authorize_business_route("/api/v1/llm/settings"))
            handler.headers["Origin"] = "https://evil.example"
            self.assertFalse(handler._authorize_business_route("/api/v1/llm/settings"))
            self.assertEqual(observed[-1][1], 403)

    def test_backend_never_accepts_plaintext_key_in_settings_json(self) -> None:
        from v3.duihua_qiaojie import _save_llm_settings

        result = _save_llm_settings({
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "modelApiKey": "must-not-be-written",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "credential_plaintext_forbidden")


class EndpointCredentialBindingTests(unittest.TestCase):
    def test_official_and_custom_endpoints_have_separate_credential_scopes(self) -> None:
        with mock.patch.object(model_endpoint, "_resolve", return_value=("104.18.0.1",)):
            official = model_endpoint.validate_model_endpoint("openai", "https://api.openai.com/v1")
            custom = model_endpoint.validate_model_endpoint("openai", "https://models.example.test/v1")
        self.assertTrue(official.official)
        self.assertIsNone(official.custom_scope)
        self.assertFalse(custom.official)
        self.assertTrue(str(custom.custom_scope).startswith("endpoint_"))
        self.assertNotEqual(custom.custom_scope, model_endpoint.custom_scope_id("https://other.example.test/v1"))

    def test_private_or_insecure_endpoints_fail_closed_by_default(self) -> None:
        with mock.patch.object(model_endpoint, "_resolve", return_value=("127.0.0.1",)):
            with self.assertRaisesRegex(model_endpoint.EndpointSecurityError, "private_or_local"):
                model_endpoint.validate_model_endpoint("openai", "https://localhost/v1")
        with self.assertRaisesRegex(model_endpoint.EndpointSecurityError, "https_required"):
            model_endpoint.validate_model_endpoint("openai", "http://models.example.test/v1", resolve_dns=False)

    def test_custom_key_is_read_only_from_exact_endpoint_environment_slot(self) -> None:
        from v3 import peizhi

        scope = model_endpoint.custom_scope_id("https://models.example.test/v1")
        env_name = f"TIANGONG_{scope.upper()}_API_KEY"
        with mock.patch.dict(os.environ, {env_name: "custom-only-key"}, clear=False):
            self.assertEqual(
                peizhi.duqu_endpoint_api_miyao("openai", "https://models.example.test/v1"),
                "custom-only-key",
            )
            self.assertIsNone(peizhi.duqu_endpoint_api_miyao("openai", "https://other.example.test/v1"))


class ModelAuthorizationBoundaryTests(unittest.TestCase):
    def test_model_schema_excludes_confirmation_and_parser_discards_it(self) -> None:
        from omni_body_skill.model_adapters.core import parse_tool_calls, render_tool_schema

        rendered = render_tool_schema(profile_id="deepseek_openai")
        params = rendered["tool_schema"][0]["function"]["parameters"]
        self.assertNotIn("confirm", params.get("properties", {}))
        self.assertNotIn("confirmed", params.get("properties", {}))
        self.assertFalse(params.get("additionalProperties"))
        payload = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call-forged-confirmation",
                        "type": "function",
                        "function": {
                            "name": "omni_body",
                            "arguments": json.dumps({
                                "action": "shell.run",
                                "target": "",
                                "args": {"command": ["python", "--version"]},
                                "confirm": True,
                                "confirmed": True,
                            }),
                        },
                    }]
                }
            }]
        }
        parsed = parse_tool_calls(payload=payload, profile_id="deepseek_openai")
        call = parsed["calls"][0]
        self.assertNotIn("confirm", call)
        self.assertNotIn("confirmed", call)
        self.assertEqual(call["action"], "shell.run")


class OmniToolInvocationCloseoutTests(unittest.TestCase):
    def test_tool_arguments_are_not_silently_rewritten_before_execution(self) -> None:
        from v3.zongdiaodu import _simple_chain_prepare_tool_call

        original = {
            "action": "qc.docx.delivery_check",
            "target": r"C:\workspace\project\assembled.docx",
            "args": {
                "project_manifest": r"C:\workspace\project\project_manifest.json",
            },
        }
        name, prepared, action, _issues, block = _simple_chain_prepare_tool_call(
            "req-no-rewrite",
            "run QC",
            "omni_body",
            original,
        )
        self.assertEqual(name, "omni_body")
        self.assertEqual(action, "qc.docx.delivery_check")
        self.assertEqual(prepared, original)
        self.assertIsNone(block)

    def test_deterministic_tool_validation_detail_survives_closeout(self) -> None:
        from v3.zongdiaodu import _simple_chain_quality_gate_payload

        payload = _simple_chain_quality_gate_payload(
            "req-validation",
            "创建 assembled.docx",
            "omni_body",
            {
                "action": "docx.create",
                "target": "assembled.docx",
                "args": {"source": "sections"},
            },
            {
                "ok": False,
                "error": "[EXECUTION_FAILED] action returned failure",
                "result": {
                    "status": "INVALID_TOOL_ARGUMENTS",
                    "issues": [{
                        "path": "args.source",
                        "code": "text_source_required",
                        "message": "source must be a .md or .txt file",
                    }],
                },
            },
            1,
        )
        self.assertFalse(payload["ok"])
        self.assertIn(
            "INVALID_TOOL_ARGUMENTS: args.source text_source_required source must be a .md or .txt file",
            payload["failures"],
        )

    def test_qc_execution_success_does_not_hide_failed_acceptance(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_evidence_check,
            _simple_chain_quality_gate_payload,
        )

        prompt = (
            "创建 proposal.docx，并严格按顺序实际执行 qc.docx.delivery_check 与 file.hash。"
        )
        tool_result = {
            "success": True,
            "result": {
                "success": True,
                "result": {
                    "score": 59,
                    "acceptance": False,
                    "issues": [{"code": "content_too_thin"}],
                },
            },
        }
        qc_payload = _simple_chain_quality_gate_payload(
            "req-qc",
            prompt,
            "omni_body",
            {
                "action": "qc.docx.delivery_check",
                "target": "proposal.docx",
                "args": {},
            },
            tool_result,
            1,
        )
        self.assertTrue(qc_payload["ok"], "the QC action itself executed successfully")
        self.assertIn("quality acceptance failed: score=59", qc_payload["final_requirement_gaps"])
        self.assertIn(
            "quality acceptance detail: content_too_thin",
            qc_payload["final_requirement_gaps"],
        )

        hash_payload = {
            "ok": True,
            "tool_action": "file.hash",
            "tool_result": {"success": True, "sha256": "a" * 64},
            "tool_result_contract": {
                "ok": True,
                "paths": ["proposal.docx"],
                "generated_attachments": [{"path": "proposal.docx"}],
            },
            "failures": [],
            "final_requirement_gaps": [],
        }
        allowed, status, reasons = _simple_chain_evidence_check(
            prompt,
            [qc_payload, hash_payload],
            [{"path": "proposal.docx"}],
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertIn(
            "qc.docx.delivery_check did not meet its acceptance gate (score=59)",
            reasons,
        )
        self.assertIn(
            "qc.docx.delivery_check repair evidence: content_too_thin",
            reasons,
        )

    def test_failed_later_qc_attempt_cannot_replace_failed_acceptance(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_evidence_check,
            _simple_chain_new_run_state,
            _simple_chain_record_observation,
        )

        prompt = "创建 video.mp4，并严格按顺序执行 qc.video.delivery_check 与 file.hash。"
        failed_acceptance = {
            "ok": True,
            "tool_action": "qc.video.delivery_check",
            "tool_result": {"result": {"acceptance": False, "score": 59}},
            "tool_result_contract": {"ok": True, "paths": ["video.mp4"]},
            "failures": [],
            "final_requirement_gaps": ["quality acceptance failed: score=59"],
        }
        transport_failure = {
            "ok": False,
            "tool_action": "qc.video.delivery_check",
            "tool_result": {"error": "omni_grant_client.gateway_unavailable"},
            "tool_result_contract": {"ok": False, "paths": ["video.mp4"]},
            "failures": ["omni_grant_client.gateway_unavailable"],
            "final_requirement_gaps": [],
        }
        hash_payload = {
            "ok": True,
            "tool_action": "file.hash",
            "tool_result_contract": {"ok": True, "paths": ["video.mp4"]},
            "failures": [],
            "final_requirement_gaps": [],
        }
        run_state = _simple_chain_new_run_state("req-qc-failed", "session-qc")
        with mock.patch("v3.zongdiaodu._simple_chain_save_run_state"):
            _simple_chain_record_observation(run_state, failed_acceptance)
            _simple_chain_record_observation(run_state, transport_failure)
            _simple_chain_record_observation(run_state, hash_payload)
        self.assertNotIn("qc.video.delivery_check", run_state["completed_actions"])

        allowed, status, reasons = _simple_chain_evidence_check(
            prompt,
            [failed_acceptance, transport_failure, hash_payload],
            [{"path": "video.mp4"}],
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertIn(
            "qc.video.delivery_check execution failed and has no passing acceptance evidence",
            reasons,
        )

    def test_run_state_tracks_loaded_skill_target_and_active_delivery_gaps(self) -> None:
        from v3.zongdiaodu import _simple_chain_new_run_state, _simple_chain_record_observation

        state = _simple_chain_new_run_state("req-state", "session-state")
        with mock.patch("v3.zongdiaodu._simple_chain_save_run_state"):
            _simple_chain_record_observation(state, {
                "ok": True,
                "tool_name": "omni_body",
                "tool_action": "skill.get",
                "tool_args": {"action": "skill.get", "target": "skill_example_v1", "args": {}},
                "failures": [],
                "final_requirement_gaps": [],
            })
            _simple_chain_record_observation(state, {
                "ok": True,
                "tool_name": "omni_body",
                "tool_action": "file.mkdir",
                "tool_args": {"action": "file.mkdir", "target": "output"},
                "failures": [],
                "final_requirement_gaps": ["artifact suffix is not final"],
            })
            _simple_chain_record_observation(state, {
                "ok": True,
                "tool_name": "omni_body",
                "tool_action": "file.write",
                "tool_args": {"action": "file.write", "target": "output/result.txt"},
                "failures": [],
                "final_requirement_gaps": [],
            })
        self.assertEqual(state["loaded_skill_ids"], ["skill_example_v1"])
        self.assertEqual(state["delivery"]["active_gaps"], [])
        self.assertEqual(state["delivery"]["phase"], "producing")
        self.assertIn("artifact suffix is not final", state["gaps"])
        self.assertEqual(
            state["completed_actions"],
            ["skill.get", "file.mkdir", "file.write"],
        )

    def test_learning_only_request_closes_from_authoritative_receipt(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_evidence_check,
            _simple_chain_is_learning_only_request,
            _simple_chain_learning_completion_reply,
            _simple_chain_learning_material_text,
            _simple_chain_learning_receipt,
        )

        prompt = (
            "请调用 learning.ingest，只创建 awaiting_user 学习卡；"
            "成功后立即报告 card_id，绝不激活、注册或发布。"
        )
        payload = {
            "ok": True,
            "tool_action": "learning.ingest",
            "tool_result": {
                "result": {
                    "card_id": "learn_test_receipt",
                    "status": "awaiting_user",
                    "registered": False,
                    "authority": "life_kernel",
                }
            },
            "failures": [],
            "final_requirement_gaps": [],
        }
        self.assertTrue(_simple_chain_is_learning_only_request(prompt))
        self.assertEqual(
            _simple_chain_learning_material_text('请把“只写隔离目录”作为显式内容学习'),
            "只写隔离目录",
        )
        self.assertEqual(_simple_chain_learning_receipt(payload)["card_id"], "learn_test_receipt")
        self.assertIn("learn_test_receipt", _simple_chain_learning_completion_reply(payload))
        self.assertEqual(
            _simple_chain_evidence_check(prompt, [payload], []),
            (True, "complete", []),
        )

    def test_explicit_action_sequence_advances_after_skill_get(self) -> None:
        from v3.zongdiaodu import _simple_chain_explicit_action_sequence

        prompt = (
            "严格按顺序调用 skill.get 读取 skill_core_actions_reference_v1；"
            "然后在 skill-e2e-all/02-core 实际执行 file.write、file.read、file.hash，"
            "文件名 hello.txt，内容严格为 CORE_ACTIONS_REFERENCE_OK。"
        )
        self.assertEqual(
            _simple_chain_explicit_action_sequence(prompt),
            ["skill.get", "file.write", "file.read", "file.hash"],
        )

    def test_explicit_action_sequence_uses_complete_capability_registry(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_declared_action_names,
            _simple_chain_explicit_action_sequence,
        )

        prompt = (
            "创建交付文档并按顺序实际执行 docx.create、word.read、"
            "qc.docx.delivery_check、file.hash。"
        )
        self.assertIn("word.read", _simple_chain_declared_action_names())
        self.assertEqual(
            _simple_chain_explicit_action_sequence(prompt),
            ["docx.create", "word.read", "qc.docx.delivery_check", "file.hash"],
        )

    def test_plain_tool_mentions_are_not_a_strict_sequence(self) -> None:
        from v3.zongdiaodu import _simple_chain_explicit_action_sequence

        self.assertEqual(
            _simple_chain_explicit_action_sequence(
                "可以用 file.read、file.hash 或 qc.docx.delivery_check 来核对。"
            ),
            [],
        )

    def test_skill_context_uses_exact_registered_id_or_name_only(self) -> None:
        from v3 import zongdiaodu as scheduler

        with tempfile.TemporaryDirectory() as td:
            index_path = Path(td) / "skill_router_index.json"
            index_path.write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "id": "skill_exact_demo_v1",
                                "mingcheng": "精确演示技能",
                                "keywords": ["演示", "文档"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(scheduler, "_SKILL_INDEX_PATH", index_path):
                self.assertEqual(
                    scheduler._simple_chain_explicit_named_skill_ids(
                        "请使用 skill_exact_demo_v1 完成任务"
                    ),
                    ["skill_exact_demo_v1"],
                )
                self.assertEqual(
                    scheduler._simple_chain_explicit_named_skill_ids(
                        "请使用精确演示技能完成任务"
                    ),
                    ["skill_exact_demo_v1"],
                )
                self.assertEqual(
                    scheduler._simple_chain_explicit_named_skill_ids(
                        "请做一个演示文档"
                    ),
                    [],
                )
                self.assertEqual(
                    scheduler._simple_chain_explicit_named_skill_ids(
                        "请使用 skill_exact_demo 完成任务"
                    ),
                    [],
                )

    def test_ordinary_task_does_not_preinject_skill_content(self) -> None:
        from v3 import zongdiaodu as scheduler

        self.assertEqual(
            scheduler._simple_chain_explicit_skill_context("帮我生成一份 Word 文档"),
            "",
        )
        self.assertFalse(hasattr(scheduler, "_match_and_inject_skills"))
        self.assertFalse(hasattr(scheduler, "_partial_cjk_match"))

    def test_strict_order_is_checked_only_by_final_gate(self) -> None:
        from v3.zongdiaodu import _simple_chain_evidence_check

        prompt = "请严格按顺序实际执行 file.read、file.hash。"
        reversed_history = [
            {"ok": True, "tool_action": "file.hash", "failures": [], "final_requirement_gaps": []},
            {"ok": True, "tool_action": "file.read", "failures": [], "final_requirement_gaps": []},
        ]
        allowed, status, reasons = _simple_chain_evidence_check(
            prompt,
            reversed_history,
            [],
            final_reply="已经核对。",
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertIn("strict action order", "\n".join(reasons))

    def test_auto_continuation_control_contract_does_not_become_user_actions(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_explicit_action_sequence,
            _simple_chain_user_goal_text,
        )

        compiled = (
            "上一轮只缺验证。禁止调用 file.write、file.append、file.mkdir、file.move、zip.create，"
            "只执行 shell.run 或 python.run。\n\n"
            "【必须继承且仍未完成的原始总目标】\n"
            "创建 assembled.docx，实际执行 docx.create、qc.docx.delivery_check 与 file.hash。\n\n"
            "本轮不得只按“继续”验收；必须检查真实产物。\n\n"
            "【本轮活跃项目根】\nC:\\workspace\\03-longdoc\n\n"
            "【工具批次执行契约】\n每轮最多两个工具调用。"
        )
        self.assertEqual(
            _simple_chain_user_goal_text(compiled),
            "创建 assembled.docx，实际执行 docx.create、qc.docx.delivery_check 与 file.hash。",
        )
        self.assertEqual(
            _simple_chain_explicit_action_sequence(compiled),
            [],
        )

    def test_final_gate_requires_every_explicitly_named_action(self) -> None:
        from v3.zongdiaodu import _simple_chain_evidence_check

        prompt = "Strictly in this order, execute file.hash and qc.docx.delivery_check."
        history = [{
            "ok": True,
            "tool_action": "file.hash",
            "failures": [],
            "final_requirement_gaps": [],
        }]
        allowed, status, reasons = _simple_chain_evidence_check(prompt, history, [])
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertIn("qc.docx.delivery_check", "\n".join(reasons))
        history.append({
            "ok": True,
            "tool_action": "qc.docx.delivery_check",
            "tool_result": {"result": {"acceptance": True, "score": 100}},
            "failures": [],
            "final_requirement_gaps": [],
        })
        self.assertEqual(_simple_chain_evidence_check(prompt, history, []), (True, "complete", []))

    def test_final_gate_requires_every_explicitly_named_deliverable(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_explicit_deliverable_paths,
            _simple_chain_evidence_check,
            _simple_chain_missing_deliverable_paths,
        )

        prompt = (
            "Create projectmanifest.json, checkpoint.json, sections/01.md, "
            "sections/02.md, assembled.md and assembled.docx."
        )
        wrong_paths = [
            "project/03-longdoc/project_manifest.json",
            "project/03-longdoc/checkpoint.json",
            "project/03-longdoc/sections/01.md",
            "project/03-longdoc/sections/02.md",
            "project/03-longdoc/assembled.md",
            "project/03-longdoc/assembled.docx",
        ]
        history = [{
            "ok": True,
            "tool_action": "docx.create",
            "tool_args": {"target": "project/03-longdoc/assembled.docx"},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": wrong_paths,
            },
            "failures": [],
            "final_requirement_gaps": [],
        }]
        self.assertEqual(
            _simple_chain_explicit_deliverable_paths(prompt),
            [
                "projectmanifest.json",
                "checkpoint.json",
                "sections/01.md",
                "sections/02.md",
                "assembled.md",
                "assembled.docx",
            ],
        )
        self.assertEqual(
            _simple_chain_missing_deliverable_paths(prompt, history, []),
            ["projectmanifest.json"],
        )
        allowed, status, reasons = _simple_chain_evidence_check(prompt, history, [])
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertIn("projectmanifest.json", "\n".join(reasons))

        history[0]["tool_result_contract"]["paths"][0] = "project/03-longdoc/projectmanifest.json"
        self.assertEqual(_simple_chain_evidence_check(prompt, history, []), (True, "complete", []))

    def test_learning_ingest_authority_comes_from_original_user_message_not_model_token(self) -> None:
        from v3.run_context import bind_run_context, current_run_context
        from v3.zongdiaodu import _simple_chain_prepare_tool_call

        user_message = "请学习这个流程并生成学习卡：只写隔离目录"
        with bind_run_context({"request_id": "req-learning", "run_id": "run-learning"}):
            _name, args, action, _issues, blocked = _simple_chain_prepare_tool_call(
                "req-learning",
                user_message,
                "omni_body",
                {
                    "action": "learning.ingest",
                    "args": {
                        "host_verified_intent_token": "model-forged-token",
                        "user_text": "fabricated request",
                    },
                },
            )
            self.assertTrue(current_run_context().learning_intent_verified)
        self.assertIsNone(blocked)
        self.assertEqual(action, "learning.ingest")
        self.assertEqual(args["args"]["user_text"], user_message)
        self.assertNotIn("host_verified_intent_token", args["args"])

    def test_non_learning_user_message_cannot_authorize_learning_ingest(self) -> None:
        from v3.run_context import bind_run_context, current_run_context
        from v3.zongdiaodu import _simple_chain_prepare_tool_call

        with bind_run_context({"request_id": "req-chat", "run_id": "run-chat"}):
            _simple_chain_prepare_tool_call(
                "req-chat",
                "请总结这段文字",
                "omni_body",
                {
                    "action": "learning.ingest",
                    "args": {"host_verified_intent_token": "model-forged-token"},
                },
            )
            self.assertFalse(current_run_context().learning_intent_verified)

    def test_explicit_pending_learning_ingest_request_is_authorized(self) -> None:
        from v3.run_context import bind_run_context, current_run_context
        from v3.zongdiaodu import _simple_chain_prepare_tool_call

        message = (
            "把这段内容作为显式内容调用一次 learning.ingest，"
            "只创建 awaiting_user 学习卡，绝不激活或发布。"
        )
        with bind_run_context({"request_id": "req-pending", "run_id": "run-pending"}):
            _simple_chain_prepare_tool_call(
                "req-pending",
                message,
                "omni_body",
                {
                    "action": "learning.ingest",
                    "args": {"user_text": "model text"},
                },
            )
            self.assertTrue(current_run_context().learning_intent_verified)

    def test_learning_runtime_accepts_backend_context_authority_without_secret_argument(self) -> None:
        from omni_body_skill.tools import omni_body_tool

        class FakeLearningEngine:
            def __init__(self, root: Path):
                self.root = root

            def create_learning_card_from_request(self, **_kwargs):
                return {
                    "ok": True,
                    "status": "pending",
                    "card_id": "card_context_verified",
                }

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            omni_body_tool,
            "_load_learning_runtime",
            return_value=(FakeLearningEngine, Path(tmp) / "learning", ""),
        ):
            runtime = omni_body_tool.BodyRuntime(
                omni_body_tool.BodyRuntimeConfig(
                    workspace=str(Path(tmp) / "workspace"),
                    fact_kernel_enabled=False,
                    host_verified_learning_intent=True,
                )
            )
            result = runtime.run(
                "learning.ingest",
                "",
                {"user_text": "请学习这个", "material_text": "测试材料"},
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["card_id"], "card_context_verified")
        self.assertEqual(result["allowed_effect"], "create_pending_learning_card_only")

    def test_learning_runtime_prefers_authoritative_pending_only_provider(self) -> None:
        from omni_body_skill.tools import omni_body_tool

        observed: list[dict] = []

        def provider(payload: dict) -> dict:
            observed.append(payload)
            return {
                "ok": True,
                "learning": {
                    "learning_id": "learn_authoritative_pending",
                    "status": "awaiting_user",
                    "registered": False,
                },
            }

        with tempfile.TemporaryDirectory() as tmp:
            runtime = omni_body_tool.BodyRuntime(
                omni_body_tool.BodyRuntimeConfig(
                    workspace=str(Path(tmp) / "workspace"),
                    fact_kernel_enabled=False,
                    host_verified_learning_intent=True,
                )
            )
            omni_body_tool.set_learning_ingest_provider(provider)
            try:
                result = runtime.run(
                    "learning.ingest",
                    "",
                    {
                        "user_text": "please learn this workflow",
                        "material_text": "isolated output only",
                        "desired_scope": "skill",
                    },
                )
            finally:
                omni_body_tool.set_learning_ingest_provider(None)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["card_id"], "learn_authoritative_pending")
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(result["evidence"]["authority"], "life_kernel")
        self.assertFalse(result["evidence"]["registered"])
        self.assertEqual(observed[0]["desired_scope"], "skill")
        self.assertNotIn("activity_scope", result["result"])
        self.assertEqual(
            result["result"]["learning"]["learning_id"],
            "learn_authoritative_pending",
        )

    def test_learning_observation_names_pending_card_and_forbids_rerouting(self) -> None:
        from v3.zongdiaodu import _simple_chain_quality_gate_payload

        payload = _simple_chain_quality_gate_payload(
            "req-learning-observation",
            "请学习并把卡片ID写入 result.json",
            "omni_body",
            {
                "action": "learning.ingest",
                "target": "",
                "args": {"user_text": "请学习这个流程"},
            },
            {
                "ok": True,
                "result": {
                    "success": True,
                    "status": "awaiting_user",
                    "card_id": "learn_observation_test",
                    "result": {
                        "learning": {
                            "learning_id": "learn_observation_test",
                            "status": "awaiting_user",
                            "registered": False,
                        }
                    },
                },
            },
            1,
        )
        self.assertTrue(payload["ok"], payload)
        self.assertIn("learn_observation_test", payload["instruction"])
        self.assertIn("Decide the next step", payload["instruction"])
        self.assertNotIn("Do not call skill.route", payload["instruction"])

    def test_learning_call_dedup_ignores_model_material_rephrasing(self) -> None:
        from v3.zongdiaodu import _gongju_diaoyong_key

        first = _gongju_diaoyong_key(
            "omni_body",
            {
                "action": "learning.ingest",
                "target": "",
                "args": {
                    "user_text": "请学习这个规则",
                    "material_text": "产物只能写入隔离目录",
                    "desired_scope": "skill",
                },
            },
        )
        restated = _gongju_diaoyong_key(
            "omni_body",
            {
                "action": "learning.ingest",
                "target": "",
                "args": {
                    "user_text": "请学习这个规则",
                    "material_text": "所有测试文件和中间产物必须只进入隔离目录",
                    "desired_scope": "skill",
                    "allow_network": False,
                },
            },
        )
        other_request = _gongju_diaoyong_key(
            "omni_body",
            {
                "action": "learning.ingest",
                "target": "",
                "args": {
                    "user_text": "请学习另一个规则",
                    "material_text": "不同材料",
                    "desired_scope": "skill",
                },
            },
        )
        self.assertEqual(first, restated)
        self.assertNotEqual(first, other_request)

    def test_verification_intent_is_not_misclassified_as_required_mutation(self) -> None:
        from v3.zongdiaodu import _requires_real_mutation, _simple_chain_has_post_mutation_verification

        self.assertFalse(_requires_real_mutation("不要读取或修改文件，只运行 unittest 做验证"))
        self.assertTrue(_requires_real_mutation("不要只检查，请修改 inventory.py"))
        history = [{
            "ok": True,
            "tool_action": "shell.run",
            "tool_args": {"args": {"command": "python -m unittest -q"}},
            "tool_result_contract": {"ok": True, "write_effect": False},
        }]
        self.assertTrue(_simple_chain_has_post_mutation_verification(history))

    def test_verification_byproducts_do_not_create_recursive_verification_debt(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

        history = [
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_args": {"target": "project/app.py", "args": {"content": "print('ok')"}},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": True,
                    "paths": ["project/app.py"],
                },
            },
            {
                "ok": True,
                "tool_action": "shell.run",
                "tool_args": {"args": {"command": "python -m unittest -q"}},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": True,
                    "paths": ["project/__pycache__/app.cpython-312.pyc"],
                },
            },
        ]
        self.assertTrue(_simple_chain_has_post_mutation_verification(history))

    def test_readback_of_the_mutated_path_is_verification(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_evidence_check,
            _simple_chain_has_post_mutation_verification,
        )

        history = [
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_args": {"target": "project/result.json", "args": {"content": "{}"}},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": True,
                    "paths": ["project/result.json"],
                },
            },
            {
                "ok": True,
                "tool_action": "file.read",
                "tool_args": {"target": "project/result.json"},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": False,
                    "paths": ["project/result.json"],
                },
            },
        ]
        self.assertTrue(_simple_chain_has_post_mutation_verification(history))
        allowed, status, reasons = _simple_chain_evidence_check(
            "请修改 project/result.json 并验证",
            history,
            [],
        )
        self.assertTrue(allowed, reasons)
        self.assertEqual(status, "complete")

    def test_hash_and_qc_of_mutated_artifact_are_verification(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

        mutation = {
            "ok": True,
            "tool_action": "docx.create",
            "tool_result_contract": {
                "write_effect": True,
                "paths": ["output/report.docx"],
            },
            "tool_args": {"action": "docx.create", "target": "output/report.docx"},
        }
        hashed = {
            "ok": True,
            "tool_action": "file.hash",
            "tool_result_contract": {
                "write_effect": False,
                "paths": ["output/report.docx"],
            },
            "tool_args": {"action": "file.hash", "target": "output/report.docx"},
        }
        qc = {
            "ok": True,
            "tool_action": "qc.docx.delivery_check",
            "tool_result_contract": {
                "write_effect": False,
                "paths": ["output/report.docx"],
            },
            "tool_args": {
                "action": "qc.docx.delivery_check",
                "target": "output/report.docx",
            },
        }
        self.assertTrue(_simple_chain_has_post_mutation_verification([mutation, hashed]))
        self.assertTrue(_simple_chain_has_post_mutation_verification([mutation, qc]))

    def test_mutating_command_with_latest_in_path_is_not_misread_as_a_test(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

        history = [{
            "ok": True,
            "tool_action": "shell.run",
            "tool_args": {"args": {"command": "python build_latest.py"}},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": ["project/latest.json"],
            },
        }]
        self.assertFalse(_simple_chain_has_post_mutation_verification(history))

    def test_verification_only_read_is_a_valid_observation(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

        history = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"target": "project/result.json"},
            "tool_result_contract": {
                "ok": True,
                "write_effect": False,
                "paths": ["project/result.json"],
            },
        }]
        self.assertTrue(_simple_chain_has_post_mutation_verification(history))

    def test_readback_of_an_unrelated_path_is_not_verification(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

        history = [
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_args": {"target": "project/result.json", "args": {"content": "{}"}},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": True,
                    "paths": ["project/result.json"],
                },
            },
            {
                "ok": True,
                "tool_action": "file.read",
                "tool_args": {"target": "project/README.md"},
                "tool_result_contract": {
                    "ok": True,
                    "write_effect": False,
                    "paths": ["project/README.md"],
                },
            },
        ]
        self.assertFalse(_simple_chain_has_post_mutation_verification(history))

    def test_completion_correction_reports_facts_without_selecting_route(self) -> None:
        from v3.zongdiaodu import _simple_chain_completion_correction_payload

        state = {
            "completion_correction": {
                "attempts_used": 1,
                "attempts_max": 3,
                "last_blockers": [],
                "exhausted": False,
            }
        }
        payload = _simple_chain_completion_correction_payload(
            "req-final-gap",
            ["requested verification/test step is missing after the latest mutation"],
            state,
        )
        self.assertEqual(payload["schema"], "tiangong.v3.simple_chain.completion_correction.v1")
        self.assertEqual(payload["attempts_used"], 1)
        self.assertEqual(payload["attempts_remaining"], 2)
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for forbidden in ("file.write", "file.read", "file.hash", "omni_body", "exactly one", "stop read"):
            self.assertNotIn(forbidden, serialized)

    def test_completion_correction_stops_after_unchanged_blockers(self) -> None:
        from v3.zongdiaodu import _simple_chain_completion_correction_stalled

        correction = {
            "attempts_used": 1,
            "last_blockers": ["execution_obligation:observation:missing_evidence"],
        }
        self.assertTrue(
            _simple_chain_completion_correction_stalled(
                correction,
                ["execution_obligation:observation:missing_evidence"],
            )
        )
        self.assertFalse(
            _simple_chain_completion_correction_stalled(
                correction,
                ["requested verification/test step is missing"],
            )
        )
    def test_simple_chain_checkpoint_path_is_durable_and_sanitized(self) -> None:
        from v3.zongdiaodu import _simple_chain_run_state_path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": tmp},
                clear=False,
            ):
                path = _simple_chain_run_state_path("../request:unsafe")
        self.assertEqual(path.parent, Path(tmp))
        self.assertEqual(path.name, "request_unsafe.json")

    def test_interim_tool_fallback_names_the_real_action_and_target(self) -> None:
        from v3.zongdiaodu import _gongju_jieduan_huifu

        self.assertEqual(
            _gongju_jieduan_huifu(
                "omni_body",
                {"action": "file.write", "target": r"project\log_analyzer.py", "args": {"content": "x"}},
            ),
            "正在写入文件：log_analyzer.py",
        )
        self.assertEqual(
            _gongju_jieduan_huifu("omni_body", {"action": "shell.run", "target": "", "args": {"command": "x"}}),
            "正在运行命令验证",
        )

    def test_empty_omni_args_are_canonicalized_before_grant_request(self) -> None:
        from v3.jineng.guge_ceng import GugeCeng
        from v3.jineng.jirou_ceng import JirouCeng

        mapping = GugeCeng().duiying("omni_body")
        self.assertIsNotNone(mapping)
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"TIANGONG_FORCE_WORKSPACE_ROOT": td},
            clear=False,
        ), mock.patch(
            "v3.jineng.jirou_ceng.issue_omni_grant",
            return_value={"grant": {"grant_id": "test"}, "runtime": {"gateway_epoch": 1}},
        ) as grant, mock.patch(
            "v3.jineng.jirou_ceng._run_omni_body_tool",
            return_value={"ok": True, "zhuangtai": "wancheng", "action": "system.health"},
        ):
            result = JirouCeng().zhixing(mapping, {"action": "system.health"})
        self.assertTrue(result.get("ok"), result)
        invocation = grant.call_args.args[0]
        self.assertEqual(invocation, {"action": "system.health", "target": "", "args": {}})

    def test_provider_protocol_metadata_is_not_split_into_characters(self) -> None:
        from v3.peizhi import l4_provider_presets

        rows = [item for item in l4_provider_presets() if item.get("id") != "openai"]
        self.assertTrue(rows)
        for row in rows:
            protocols = row.get("protocol_family")
            self.assertIsInstance(protocols, list)
            self.assertEqual(protocols, ["openai_compatible"])

    def test_read_only_omni_actions_are_not_misclassified_as_mutations(self) -> None:
        from v3.tool_result_contract import normalize_tool_result

        health = normalize_tool_result(
            "omni_body",
            {"ok": True, "action": "system.health", "effect": "execute", "result": {"healthy": True}},
        )
        read = normalize_tool_result(
            "omni_body",
            {"ok": True, "action": "file.read", "effect": "execute", "result": {"content": "x"}},
        )
        write = normalize_tool_result(
            "omni_body",
            {"ok": True, "action": "file.write", "effect": "execute", "path": "x.txt"},
        )
        self.assertFalse(health["write_effect"])
        self.assertFalse(read["write_effect"])
        self.assertTrue(write["write_effect"])

    def test_execution_write_effect_comes_from_sandbox_merge_evidence(self) -> None:
        from v3.tool_result_contract import normalize_tool_result

        verification = normalize_tool_result(
            "omni_body",
            {
                "ok": True,
                "action": "shell.run",
                "result": {"execution": {"returncode": 0, "changed_files": [], "deleted_files": []}},
            },
        )
        mutation = normalize_tool_result(
            "omni_body",
            {
                "ok": True,
                "action": "shell.run",
                "result": {"execution": {"returncode": 0, "changed_files": ["result.txt"], "deleted_files": []}},
            },
        )
        self.assertFalse(verification["write_effect"])
        self.assertTrue(mutation["write_effect"])


class SandboxCloseoutTests(unittest.TestCase):
    @pytest.mark.ci_fragile
    def test_embedded_workspace_path_in_shell_command_is_rewritten_to_private_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            sandbox = root / "sandbox" / "workspace"
            command = [
                "cmd.exe",
                "/d",
                "/s",
                "/c",
                f'cd /d "{workspace / "project"}" && python -m pytest -q',
            ]
            rewritten = _rewrite_workspace_paths(command, workspace, sandbox)
            self.assertNotIn(str(workspace), rewritten[-1])
            self.assertIn(str(sandbox / "project"), rewritten[-1])

    @unittest.skipUnless(os.name == "nt", "Windows cmd integration")
    @pytest.mark.ci_fragile
    def test_absolute_unicode_workspace_path_in_cmd_runs_inside_private_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "工作 区"
            project = workspace / "project"
            project.mkdir(parents=True)
            runner = SandboxRunner(
                workspace,
                root / "sandbox-state",
                root / "trash",
                SandboxLimits(timeout_seconds=20, max_workspace_bytes=10_000_000, max_changed_bytes=10_000_000),
            )
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            command = f'{subprocess.list2cmdline([comspec])} /d /s /c "cd /d "{project}" && echo sandbox-ok>result.txt"'
            with mock.patch.dict(os.environ, {"TIANGONG_SANDBOX_COMPAT": "1"}, clear=False):
                result = runner.run(command, cwd=workspace, op_id="unicode-embedded-path")
            self.assertTrue(result["ok"], result)
            self.assertIn("project/result.txt", [item.replace("\\", "/") for item in result["changed_files"]])
            self.assertEqual((project / "result.txt").read_text(encoding="utf-8").strip(), "sandbox-ok")

    def test_workspace_coordination_lock_is_not_copied_into_private_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".omni_workspace.lock").write_text("held-by-runtime", encoding="utf-8")
            runner = SandboxRunner(
                workspace,
                root / "sandbox-state",
                root / "trash",
                SandboxLimits(timeout_seconds=20, max_workspace_bytes=10_000_000, max_changed_bytes=10_000_000),
            )
            code = (
                "import pathlib; "
                "assert not pathlib.Path('.omni_workspace.lock').exists(); "
                "pathlib.Path('result.txt').write_text('ok', encoding='utf-8')"
            )
            with mock.patch.dict(os.environ, {"TIANGONG_SANDBOX_COMPAT": "1"}, clear=False):
                result = runner.run([sys.executable, "-c", code], cwd=workspace, op_id="lock-skip-test")
            self.assertTrue(result["ok"], result)
            self.assertEqual((workspace / "result.txt").read_text(encoding="utf-8"), "ok")
            self.assertEqual((workspace / ".omni_workspace.lock").read_text(encoding="utf-8"), "held-by-runtime")

    def test_python_process_has_no_parent_secret_and_outputs_are_brokered_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "original.txt").write_text("before", encoding="utf-8")
            runner = SandboxRunner(
                workspace,
                root / "sandbox-state",
                root / "trash",
                SandboxLimits(timeout_seconds=20, max_workspace_bytes=10_000_000, max_changed_bytes=10_000_000),
            )
            code = (
                "import os, pathlib; "
                "pathlib.Path('result.txt').write_text(str(os.getenv('OPENAI_API_KEY')), encoding='utf-8'); "
                "pathlib.Path('original.txt').write_text('after', encoding='utf-8')"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "leak-me-not",
                    "TIANGONG_SANDBOX_COMPAT": "1",
                },
                clear=False,
            ):
                result = runner.run([sys.executable, "-c", code], cwd=workspace, op_id="secret-test")
            self.assertTrue(result["ok"], result)
            self.assertEqual((workspace / "result.txt").read_text(encoding="utf-8"), "None")
            self.assertEqual((workspace / "original.txt").read_text(encoding="utf-8"), "after")
            self.assertIn("result.txt", result["changed_files"])
            self.assertFalse(Path(result["sandbox_root"]).exists())

    @pytest.mark.ci_fragile
    def test_legacy_terminal_is_sandboxed_and_a5_cannot_be_confirmed_away(self) -> None:
        from v3.jineng.jirou_ceng import JirouCeng

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "workspace"
            workspace.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "TIANGONG_FORCE_WORKSPACE_ROOT": str(workspace),
                    "TIANGONG_SANDBOX_COMPAT": "1",
                },
                clear=False,
            ):
                command = (
                    "Set-Content -NoNewline -LiteralPath legacy.txt -Value sandboxed"
                    if os.name == "nt"
                    else "printf sandboxed > legacy.txt"
                )
                safe = JirouCeng._zhongduan(command, chaoshi=20)
                self.assertFalse(safe.get("cuowu"), safe)
                self.assertEqual(safe.get("fanhui_ma"), 0, safe)
                self.assertEqual((workspace / "legacy.txt").read_text(encoding="utf-8"), "sandboxed")
                self.assertTrue(str(safe.get("sandbox") or ""))
                failed = JirouCeng._zhongduan(
                    "Write-Error 'sandbox failure'; exit 7"
                    if os.name == "nt"
                    else "printf 'sandbox failure' >&2; exit 7",
                    chaoshi=20,
                )
                self.assertEqual(failed.get("fanhui_ma"), 7, failed)
                self.assertTrue(failed.get("cuowu"), failed)
                rejected = JirouCeng._zhongduan("rm -rf .", chaoshi=20, confirm=True)
                self.assertTrue(rejected.get("a5_rejected"), rejected)
                self.assertFalse(rejected.get("requires_confirm"), rejected)
                self.assertTrue(rejected.get("confirm_ignored"), rejected)

    def test_workspace_symlink_is_rejected_before_execution(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlink unsupported")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                (workspace / "link.txt").symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable")
            runner = SandboxRunner(workspace, root / "state", root / "trash")
            with self.assertRaisesRegex(Exception, "link_forbidden"):
                runner.run([sys.executable, "-c", "print('never')"])


class LifeLogCloseoutTests(unittest.TestCase):
    def test_binary_key_preserves_newline_byte_across_restart(self) -> None:
        key = (b"A" * 15) + b"\n" + (b"B" * 16)
        self.assertEqual(len(key), 32)
        with tempfile.TemporaryDirectory() as td, mock.patch(
            "total_gateway.life_log.secrets.token_bytes",
            return_value=key,
        ):
            root = Path(td)
            first = LifeLog(root)
            self.assertEqual(first.key_path.read_bytes(), key)
            self.assertEqual(first.key_path.stat().st_size, 32)
            second = LifeLog(root)
            self.assertEqual(second.verify()["sequence"], 0)

    def test_chain_detects_record_edit_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = LifeLog(Path(td))
            log.append("task.started", {"run_id": "run-1"})
            log.append("task.completed", {"ok": True})
            self.assertEqual(log.verify()["sequence"], 2)
            original = log.events_path.read_bytes()
            rows = original.splitlines()
            record = json.loads(rows[0])
            record["event"]["fields"]["run_id"] = "forged"
            rows[0] = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            log.events_path.write_bytes(b"\n".join(rows) + b"\n")
            with self.assertRaises(LifeLogError):
                log.verify()
            log.events_path.write_bytes(original.splitlines()[0] + b"\n")
            with self.assertRaisesRegex(LifeLogError, "truncation"):
                log.verify()


class SoulBackupCloseoutTests(unittest.TestCase):
    def test_encrypted_backup_verifies_and_restores_text_and_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gateway = root / "gateway"
            life = root / "life"
            gateway.mkdir(); life.mkdir()
            (gateway / "state.json").write_text('{"value":1}', encoding="utf-8")
            db = life / "memory.sqlite"
            with closing(sqlite3.connect(db)) as conn:
                with conn:
                    conn.execute("create table memory(value text)")
                    conn.execute("insert into memory values ('remember')")
            manager = SoulBackupManager(gateway, (SoulSource("gateway", gateway), SoulSource("life", life)))
            backup = root / "soul.tgsoul"
            result = manager.create(backup, passphrase="correct horse battery staple")
            self.assertTrue(result["ok"])
            self.assertTrue(manager.verify(backup, passphrase="correct horse battery staple")["ok"])
            restore_gateway = root / "restore-gateway"
            restore_life = root / "restore-life"
            restored = manager.restore(
                backup,
                passphrase="correct horse battery staple",
                targets={"gateway": restore_gateway, "life": restore_life},
            )
            self.assertTrue(restored["ok"])
            self.assertEqual((restore_gateway / "state.json").read_text(encoding="utf-8"), '{"value":1}')
            with closing(sqlite3.connect(restore_life / "memory.sqlite")) as conn:
                self.assertEqual(conn.execute("select value from memory").fetchone()[0], "remember")
            self.assertFalse(list(root.glob("*.pre-soul-restore-*")))

    def test_wrong_passphrase_and_ciphertext_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state"; state.mkdir()
            (state / "x.txt").write_text("x", encoding="utf-8")
            manager = SoulBackupManager(state, (SoulSource("gateway", state),))
            backup = root / "soul.tgsoul"
            manager.create(backup, passphrase="a sufficiently long passphrase")
            with self.assertRaises(SoulBackupError):
                manager.verify(backup, passphrase="wrong but long enough")
            raw = bytearray(backup.read_bytes()); raw[-1] ^= 1; backup.write_bytes(raw)
            with self.assertRaises(SoulBackupError):
                manager.verify(backup, passphrase="a sufficiently long passphrase")


class SecureUpdateAndSourceAuthorityTests(unittest.TestCase):
    def test_signed_update_manifest_and_replay_rules(self) -> None:
        script = r'''
const crypto = require("crypto");
const { canonicalJson, verifyManifestEnvelope } = require("./app/secure-updater");
const pair = crypto.generateKeyPairSync("ed25519");
const trust = {
  key_id: "root-1",
  public_key_pem: pair.publicKey.export({type:"spki",format:"pem"}),
  allowed_origins: ["https://updates.example.test"],
};
const signed = {
  schema: "tiangong.update.manifest.v1",
  metadata_version: 1,
  expires: "2099-01-01T00:00:00Z",
  release_version: "3.0.4",
  target: {url:"https://updates.example.test/TiangongV3.exe", sha256:"a".repeat(64), size:12345},
};
const sig = crypto.sign(null, Buffer.from(canonicalJson(signed)), pair.privateKey).toString("base64");
const envelope = {signed, signature:{key_id:"root-1", sig}};
const accepted = verifyManifestEnvelope({envelope, trust, state:{highest_metadata_version:0,highest_release_version:"3.0.3"}, currentVersion:"3.0.3"});
let replay = "";
try { verifyManifestEnvelope({envelope, trust, state:{highest_metadata_version:1,highest_release_version:"3.0.3"}, currentVersion:"3.0.3"}); } catch (error) { replay = error.message; }
console.log(JSON.stringify({version: accepted.release_version, replay}));
'''
        completed = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual(data["version"], "3.0.4")
        self.assertIn("rollback_or_replay", data["replay"])

    def test_electron_update_and_security_boundaries_are_present(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "frontend-v2" / "index.html").read_text(encoding="utf-8")
        helper = (ROOT / "app" / "scripts" / "update-transaction.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "build" / "installer.nsh").read_text(encoding="utf-8-sig")
        self.assertIn("webSecurity: true", main)
        self.assertNotIn("webSecurity: false", main)
        self.assertIn('handleTrusted("update:apply"', main)
        self.assertIn("createPreUpdateSoulBackup", main)
        self.assertNotIn("'unsafe-eval'", html)
        self.assertIn("Invoke-Rollback", helper)
        self.assertIn("update-baseline", installer)

    def test_generated_source_mirrors_are_in_sync(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/sync-generated-sources.py", "--check-committed"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class DeliveryFormatContractTests(unittest.TestCase):
    def test_natural_language_format_tokens_are_delivery_suffixes(self) -> None:
        from v3.zongdiaodu import _simple_chain_expected_suffixes

        prompt = "创建一张320x180 PNG海报、1秒MP4短视频、DOCX说明和脚本.md"
        self.assertEqual(
            _simple_chain_expected_suffixes(prompt),
            {".png", ".mp4", ".docx", ".md"},
        )

    def test_word_conversion_keeps_source_file_out_of_output_contract(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_codex_evidence,
            _simple_chain_expected_suffixes,
            _simple_chain_explicit_deliverable_paths,
            _simple_chain_evidence_check,
            _simple_chain_mutation_payload_satisfies_request,
            _simple_chain_preflight_issues,
            _simple_chain_requested_target_paths,
        )

        prompt = "桌面上有个母亲的灯.md，可以帮我转成word格式么"
        self.assertEqual(_simple_chain_expected_suffixes(prompt), {".docx"})
        self.assertEqual(_simple_chain_requested_target_paths(prompt), [])
        self.assertEqual(_simple_chain_explicit_deliverable_paths(prompt), [])

        with tempfile.TemporaryDirectory() as temp_root:
            desktop = Path(temp_root) / "Desktop"
            desktop.mkdir()
            target = desktop / "母亲的灯.docx"
            tool_args = {
                "action": "docx.create",
                "target": str(target),
                "args": {"content": "# 母亲的灯"},
            }
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_DESKTOP_PATH": str(desktop)},
                clear=False,
            ):
                # Preflight validates the future target, not bytes that cannot
                # exist until docx.create has executed.
                self.assertEqual(
                    _simple_chain_preflight_issues(prompt, "docx.create", tool_args),
                    [],
                )

                import zipfile

                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr("word/document.xml", "<w:document/>")
                contract = {
                    "ok": True,
                    "write_effect": True,
                    "paths": [str(target)],
                    "generated_attachments": [
                        {"kind": "document", "path": str(target)},
                    ],
                }
                payload = {
                    "ok": True,
                    "tool_action": "docx.create",
                    "tool_args": tool_args,
                    "tool_result_contract": contract,
                    "failures": [],
                    "final_requirement_gaps": [],
                    "gaps": [],
                }

                evidence = _simple_chain_codex_evidence(
                    prompt,
                    "omni_body",
                    tool_args,
                    {},
                    contract,
                )
                self.assertTrue(evidence["checks"]["path_matches_expected"])
                self.assertTrue(evidence["checks"]["suffix_matches_expected"])
                self.assertTrue(evidence["checks"]["desktop_matches_expected"])

                mutation_ok, mutation_issues = _simple_chain_mutation_payload_satisfies_request(
                    prompt,
                    payload,
                )
                self.assertTrue(mutation_ok, mutation_issues)
                self.assertEqual(mutation_issues, [])

                ready, status, reasons = _simple_chain_evidence_check(
                    prompt,
                    [payload],
                    [{"path": str(target), "suffix": ".docx"}],
                    final_reply="已转换完成。",
                )
                self.assertTrue(ready, reasons)
                self.assertEqual(status, "complete")
                self.assertEqual(reasons, [])

    def test_word_product_names_map_to_docx_without_monkeypatching(self) -> None:
        from v3.zongdiaodu import _simple_chain_expected_suffixes

        for product_name in ("Word", "Word格式", "Word文档"):
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    _simple_chain_expected_suffixes(f"请生成一份{product_name}"),
                    {".docx"},
                )

    def test_verification_compensation_does_not_restart_mutation_sequence(self) -> None:
        from v3.zongdiaodu import _simple_chain_explicit_action_sequence

        prompt = (
            "上一轮的产物修改已经完成，完成门只缺少修改后的验证证据。"
            "本轮是验证补偿，不是重做任务：禁止新建、写入或覆盖任何产物；"
            "只对现有产物执行一个有明确通过/失败结果的验证动作。\n\n"
            "【必须继承且仍未完成的原始总目标】\n"
            "依次执行 skill.get、image.create_canvas、image.add_text、"
            "video.slideshow、video.info、qc.video.delivery_check、file.hash。\n\n"
            "本轮不得只按“继续”验收。"
        )
        self.assertEqual(
            _simple_chain_explicit_action_sequence(prompt),
            ["video.info", "qc.video.delivery_check", "file.hash"],
        )

    def test_video_qc_arguments_are_not_inferred_from_sibling_files(self) -> None:
        from v3.zongdiaodu import _simple_chain_prepare_tool_call

        original = {
            "action": "qc.video.delivery_check",
            "target": "08-video/clip.mp4",
            "args": {},
        }
        _name, prepared, action, _issues, block = _simple_chain_prepare_tool_call(
            "req-video-no-inference",
            "执行 qc.video.delivery_check",
            "omni_body",
            original,
        )
        self.assertEqual(action, "qc.video.delivery_check")
        self.assertEqual(prepared, original)
        self.assertIsNone(block)


if __name__ == "__main__":
    unittest.main()
