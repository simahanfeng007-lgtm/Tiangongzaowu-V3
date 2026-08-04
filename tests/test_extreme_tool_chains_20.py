from __future__ import annotations

import importlib.util
import os
import tempfile
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from contracts import ActionIntent, ResourceEnvelope, SourceRef, canonical_sha256, derive_run_identity
from runtime_security import EphemeralTestProtector
from total_gateway.impact_evaluator import compute_action_impact
from total_gateway.omni_grant_authority import OmniGrantAuthorityError
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.policy_engine import PolicyEngine
from total_gateway.store import GatewayStateStore
from tests.test_execution_contracts import execution_ticket

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_SOURCE = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "omni_capability.py"
BODY_SOURCE = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "omni_body_tool.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExtremeToolChains20(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temporary.name).resolve()
        (cls.workspace / "sample.txt").write_text("trusted", encoding="utf-8")
        cls.now_ms = int(time.time() * 1000)
        config = SimpleNamespace(
            release_manifest_path=None,
            release_source_root=ROOT,
            environment="development",
            state_root=cls.workspace,
            workspace_root=cls.workspace,
            backend_internal_token="b" * 48,
            life_internal_token="l" * 48,
            communication_api_token="c" * 48,
            runtime_key_protector=EphemeralTestProtector(),
        )
        # D-06 统一 admission：authority 必须接真实 effect 台账（机械适配：
        # SimpleNamespace → 真 store；合成年 run_id → 派生 run_id）。
        cls.store = GatewayStateStore.open(cls.workspace / "gateway-state" / "gateway.sqlite3", now_ms=cls.now_ms)
        cls.worker = GatewayOrchestrationWorker.from_runtime_config(
            config=config,
            activator=SimpleNamespace(), store=cls.store, objects=SimpleNamespace(), facts=SimpleNamespace(),
            gateway_epoch=71, gateway_instance_id="gateway-extreme-tools", now_ms=cls.now_ms,
        )
        cls.authority = cls.worker.omni_grant_authority
        cls.outer = cls._register_outer("main", generation=3)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.close()
        cls.store.close()
        cls.temporary.cleanup()

    @classmethod
    def _register_outer(cls, suffix: str, *, generation: int = 1):
        now = cls.now_ms
        request_id = "req_" + canonical_sha256({"suffix": suffix})
        outer = execution_ticket(
            ticket_id=f"ticket_extreme_{suffix}", nonce=f"nonce_extreme_{suffix}",
            issued_at_ms=now, not_before_ms=now, expires_at_ms=now + 60_000,
            gateway_epoch=71, request_id=request_id,
            run_id=derive_run_identity(request_id, 1).run_id, generation=generation,
            principal_scope_hash=canonical_sha256({"principal": suffix}),
            workspace_id=cls.authority.workspace_id, max_runtime_ms=3_600_000,
            max_tool_calls=10_000,
        )
        cls.authority.register(
            outer, life_id=f"life_{suffix}", life_evidence_ref="lev_" + canonical_sha256({"life": suffix}),
            session_id=f"session_{suffix}", registered_at_ms=now,
            authority_expires_at_ms=now + 60_000,
        )
        return outer

    def _payload(self, index: int, *, action: str = "system.health", target: str = "", args=None, outer=None):
        outer = outer or self.outer
        return {
            "ticket_id": outer.payload.ticket_id,
            "call_id": "toolcall_" + canonical_sha256({"case": index, "ticket": outer.payload.ticket_id}),
            "request_id": outer.payload.request_id,
            "run_id": outer.payload.run_id,
            "generation": outer.payload.generation,
            "principal_scope_hash": outer.payload.principal_scope_hash,
            "action": action,
            "target": target,
            "args": {} if args is None else args,
            "workspace": str(self.workspace),
        }

    def test_t01_short_chain_empty_args_grant_verify_and_execute(self):
        issued = self.authority.issue(self._payload(1), now_ms=self.now_ms + 10)
        capability = _load(CAPABILITY_SOURCE, "extreme_capability_t01")
        with mock.patch.dict(os.environ, {"TIANGONG_OMNI_BODY_STATE_ROOT": str(self.workspace / "state-t01")}, clear=True):
            verified = capability.verify_capability_grant(
                issued["grant"], action="system.health", target="", args={},
                workspace=str(self.workspace), runtime_meta=issued["runtime"],
            )
        self.assertRegex(verified["grant_sha256"], r"^[0-9a-f]{64}$")
        readable_root = str(ROOT / "readable-python-source")
        if readable_root not in sys.path:
            sys.path.insert(0, readable_root)
        from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
        runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(self.workspace), fact_kernel_enabled=False))
        result = runtime.run("system.health", "", {})
        self.assertIn("healthy", result)

    def test_t02_unknown_action_is_denied(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "action.not_registered"):
            self.authority.issue(self._payload(2, action="unknown.root.execute"), now_ms=self.now_ms + 20)

    def test_t03_a5_core_impact_is_hard_rejected(self):
        permission = next(item for item in self.authority.registry.permissions if item.effective_risk == "A2")
        intent = ActionIntent(
            intent_id="intent_extreme_a5", source="chat", life_id="life_main",
            principal_scope_hash="a" * 64, conversation_scope_hash="b" * 64,
            request_id="req_" + "1" * 64, run_id="run_" + "2" * 64, generation=1,
            action_id=permission.action_id, action_version=permission.action_version,
            arguments_sha256="c" * 64, workspace_id="workspace_main", workspace_scope_hash="d" * 64,
            input_object_refs=(), requested_side_effects=permission.allowed_side_effects,
            requested_resources=ResourceEnvelope(max_runtime_ms=1000, max_output_bytes=1000, max_tool_calls=1),
            source_refs=(
                SourceRef(
                    source_type="PREAUTHORIZED_USER_FACT",
                    object_id="lev_" + "3" * 64,
                    object_revision=1,
                    sha256="3" * 64,
                ),
            ),
            created_at_ms=10_000, expires_at_ms=60_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        impact = compute_action_impact(intent, permission, affected_internal_nodes=("core_code",), created_at_ms=20_000)
        decision = PolicyEngine(
            self.authority.registry, policy_snapshot_sha256="b" * 64,
            skill_catalog_hash="b" * 64, capability_manifest_hash="c" * 64,
            component_manifest_hash="d" * 64,
        ).evaluate(intent, impact, decided_at_ms=20_000)
        self.assertEqual((decision.computed_risk, decision.outcome), ("A5", "REJECT"))

    def test_t04_nested_authority_field_is_denied(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "model_authority_field"):
            self.authority.issue(self._payload(4, args={"options": [{"confirmed": True}]}), now_ms=self.now_ms + 40)

    def test_t05_unicode_fullwidth_authority_field_is_denied(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "model_authority_field"):
            self.authority.issue(self._payload(5, args={"ｃｏｎｆｉｒｍｅｄ": True}), now_ms=self.now_ms + 50)

    def test_t06_parent_traversal_in_nested_path_uses_signed_path_freedom(self):
        issued = self.authority.issue(
            self._payload(6, action="file.read", target="sample.txt", args={"output_path": "../../outside"}),
            now_ms=self.now_ms + 60,
        )
        self.assertEqual(issued["status"], "OK")
        self.assertTrue(issued["grant"]["payload"]["allow_absolute_paths"])

    def test_t07_posix_absolute_path_uses_signed_path_freedom(self):
        issued = self.authority.issue(
            self._payload(7, action="file.read", target=str(self.workspace / "sample.txt"), args={}),
            now_ms=self.now_ms + 70,
        )
        self.assertEqual(issued["status"], "OK")

    def test_t08_symlink_to_normal_external_file_is_allowed(self):
        outside = self.workspace.parent / f"outside-{time.time_ns()}.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.workspace / "escape-link"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        try:
            issued = self.authority.issue(
                self._payload(8, action="file.read", target="escape-link", args={}),
                now_ms=self.now_ms + 80,
            )
            self.assertEqual(issued["status"], "OK")
        finally:
            link.unlink(missing_ok=True); outside.unlink(missing_ok=True)

    def test_t09_url_in_output_path_is_denied(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "path.url_forbidden"):
            self.authority.issue(self._payload(9, action="file.read", target="sample.txt", args={"output_path": "data:text/plain,escape"}), now_ms=self.now_ms + 90)

    def test_t10_windows_system_path_remains_a5_denied_cross_platform(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "workspace_escape"):
            self.authority.issue(self._payload(10, action="file.read", target=r"C:\\Windows\\system.ini", args={}), now_ms=self.now_ms + 100)

    def test_t11_unc_absolute_path_uses_signed_path_freedom(self):
        issued = self.authority.issue(
            self._payload(11, action="file.read", target=r"\\\\server\\share\\secret.txt", args={}),
            now_ms=self.now_ms + 110,
        )
        self.assertEqual(issued["status"], "OK")

    def test_t12_destructive_overwrite_without_permission_is_denied(self):
        with self.assertRaisesRegex(OmniGrantAuthorityError, "overwrite.not_authorized"):
            self.authority.issue(self._payload(12, action="file.read", target="sample.txt", args={"overwrite": True}), now_ms=self.now_ms + 120)

    def test_t13_generation_drift_is_denied(self):
        payload = self._payload(13); payload["generation"] += 1
        with self.assertRaisesRegex(OmniGrantAuthorityError, "binding_invalid"):
            self.authority.issue(payload, now_ms=self.now_ms + 130)

    def test_t14_expired_active_authority_is_denied(self):
        suffix = "expired"
        outer = execution_ticket(
            ticket_id="ticket_extreme_expired", nonce="nonce_extreme_expired",
            issued_at_ms=self.now_ms - 2000, not_before_ms=self.now_ms - 2000,
            expires_at_ms=self.now_ms + 10_000, gateway_epoch=71,
            request_id="req_" + "e" * 64, run_id="run_" + "f" * 64, generation=1,
            principal_scope_hash="1" * 64, workspace_id=self.authority.workspace_id,
        )
        self.authority.register(
            outer, life_id=f"life_{suffix}", life_evidence_ref="lev_" + "2" * 64,
            session_id="session_expired", registered_at_ms=self.now_ms - 2000,
            authority_expires_at_ms=self.now_ms - 1000,
        )
        with self.assertRaisesRegex(OmniGrantAuthorityError, "binding_invalid"):
            self.authority.issue(self._payload(14, outer=outer), now_ms=self.now_ms)

    def test_t15_unregistered_outer_ticket_is_denied(self):
        outer = self._register_outer("unregistered")
        self.authority.unregister(outer.payload.ticket_id)
        with self.assertRaisesRegex(OmniGrantAuthorityError, "active_ticket.missing"):
            self.authority.issue(self._payload(15, outer=outer), now_ms=self.now_ms + 150)

    def test_t16_signed_grant_nonce_replay_is_denied(self):
        issued = self.authority.issue(self._payload(16), now_ms=self.now_ms + 160)
        capability = _load(CAPABILITY_SOURCE, "extreme_capability_t16")
        state_root = self.workspace / "state-t16"
        with mock.patch.dict(os.environ, {"TIANGONG_OMNI_BODY_STATE_ROOT": str(state_root)}, clear=True):
            capability.verify_capability_grant(issued["grant"], action="system.health", target="", args={}, workspace=str(self.workspace), runtime_meta=issued["runtime"])
            with self.assertRaises(Exception):
                capability.verify_capability_grant(issued["grant"], action="system.health", target="", args={}, workspace=str(self.workspace), runtime_meta=issued["runtime"])

    def test_t17_lost_response_retry_returns_same_grant(self):
        payload = self._payload(17)
        first = self.authority.issue(payload, now_ms=self.now_ms + 170)
        second = self.authority.issue(payload, now_ms=self.now_ms + 171)
        self.assertEqual(first["grant"], second["grant"])
        self.assertEqual(first["runtime"], second["runtime"])

    def test_t18_same_call_id_changed_invocation_is_conflict(self):
        payload = self._payload(18, args={"probe": 1})
        self.authority.issue(payload, now_ms=self.now_ms + 180)
        changed = dict(payload); changed["args"] = {"probe": 2}
        with self.assertRaisesRegex(OmniGrantAuthorityError, "call_id.conflict"):
            self.authority.issue(changed, now_ms=self.now_ms + 181)

    def test_t19_long_chain_150_unique_grants_has_no_collision(self):
        nonces = set(); effects = set()
        for index in range(150):
            payload = self._payload(1900 + index, args={"chain_step": index})
            issued = self.authority.issue(payload, now_ms=self.now_ms + 200 + index)
            nonces.add(issued["grant"]["payload"]["nonce"])
            effects.add(issued["runtime"]["execution_ticket_id"])
        self.assertEqual(len(nonces), 150)
        self.assertEqual(len(effects), 150)

    def test_t20_concurrent_two_agent_grants_remain_scope_isolated(self):
        second = self._register_outer("agent_b", generation=7)
        barrier = threading.Barrier(32)
        def issue(index: int):
            outer = self.outer if index % 2 == 0 else second
            barrier.wait(timeout=5)
            return self.authority.issue(self._payload(3000 + index, args={"index": index}, outer=outer), now_ms=self.now_ms + 1000 + index)
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(issue, range(32)))
        self.assertEqual(len({row["grant"]["payload"]["nonce"] for row in results}), 32)
        for index, row in enumerate(results):
            expected = self.outer if index % 2 == 0 else second
            self.assertEqual(row["runtime"]["request_id"], expected.payload.request_id)
            self.assertEqual(row["runtime"]["run_id"], expected.payload.run_id)
            self.assertEqual(row["runtime"]["generation"], expected.payload.generation)

    def test_t21_concurrent_lost_response_retries_mint_once(self):
        payload = self._payload(21, args={"same_occurrence": True})
        barrier = threading.Barrier(16)

        def retry_same_call(_: int):
            barrier.wait(timeout=5)
            return self.authority.issue(payload, now_ms=self.now_ms + 2_100)

        with (
            mock.patch.object(
                self.authority.signer,
                "sign_execution",
                wraps=self.authority.signer.sign_execution,
            ) as sign_execution,
            mock.patch.object(
                self.authority.signer,
                "sign_omni_capability",
                wraps=self.authority.signer.sign_omni_capability,
            ) as sign_capability,
            mock.patch.object(
                self.authority.evidence,
                "record_evaluation",
                wraps=self.authority.evidence.record_evaluation,
            ) as record_evaluation,
            ThreadPoolExecutor(max_workers=16) as pool,
        ):
            results = list(pool.map(retry_same_call, range(16)))

        self.assertTrue(all(row == results[0] for row in results))
        self.assertEqual(sign_execution.call_count, 1)
        self.assertEqual(sign_capability.call_count, 1)
        self.assertEqual(record_evaluation.call_count, 1)


if __name__ == "__main__":
    unittest.main()
