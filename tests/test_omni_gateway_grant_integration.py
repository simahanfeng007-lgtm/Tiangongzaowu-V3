from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from runtime_security import EphemeralTestProtector
from contracts import derive_run_identity
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.omni_grant_authority import OmniGrantAuthorityError
from total_gateway.store import GatewayStateStore
from tests.test_execution_contracts import execution_ticket


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_SOURCE = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "omni_capability.py"


def _load_capability_module():
    spec = importlib.util.spec_from_file_location("test_gateway_inline_capability", CAPABILITY_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OmniGatewayGrantIntegrationTests(unittest.TestCase):
    def test_gateway_issued_grant_and_inline_trust_verify_end_to_end(self) -> None:
        now_ms = time.time_ns() // 1_000_000
        source = ROOT
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary).resolve()
            workspace_file = state_root / "sample.txt"
            workspace_file.write_text("trusted", encoding="utf-8")
            config = SimpleNamespace(
                release_manifest_path=None,
                release_source_root=source,
                environment="development",
                state_root=state_root,
                workspace_root=state_root,
                backend_internal_token="b" * 48,
                life_internal_token="l" * 48,
                communication_api_token="c" * 48,
                runtime_key_protector=EphemeralTestProtector(),
            )
            # D-06 统一 admission：authority 必须接真实 effect 台账（机械适配：
            # SimpleNamespace → 真 store；合成 run_id → 派生 run_id）。
            store = GatewayStateStore.open(state_root / "gateway-state" / "gateway.sqlite3", now_ms=now_ms)
            worker = GatewayOrchestrationWorker.from_runtime_config(
                config=config,
                activator=SimpleNamespace(),
                store=store,
                objects=SimpleNamespace(),
                facts=SimpleNamespace(),
                gateway_epoch=7,
                gateway_instance_id="gateway-omni-integration",
                now_ms=now_ms,
            )
            authority = worker.omni_grant_authority
            request_id = "req_" + "7" * 64
            outer = execution_ticket(
                ticket_id="ticket_omni_outer",
                nonce="nonce_omni_outer",
                issued_at_ms=now_ms,
                not_before_ms=now_ms,
                expires_at_ms=now_ms + 60_000,
                gateway_epoch=7,
                request_id=request_id,
                run_id=derive_run_identity(request_id, 1).run_id,
                generation=3,
                principal_scope_hash="9" * 64,
                workspace_id=authority.workspace_id,
                max_runtime_ms=60_000,
            )
            authority.register(
                outer,
                life_id="life_omni_integration",
                life_evidence_ref="lev_" + "a" * 64,
                session_id="session_omni_integration",
                registered_at_ms=now_ms,
                authority_expires_at_ms=now_ms + 60_000,
            )
            invocation = {
                "ticket_id": outer.payload.ticket_id,
                "call_id": "toolcall_" + "1" * 64,
                "request_id": outer.payload.request_id,
                "run_id": outer.payload.run_id,
                "generation": outer.payload.generation,
                "principal_scope_hash": outer.payload.principal_scope_hash,
                "action": "file.read",
                "target": "sample.txt",
                "args": {"encoding": "utf-8"},
                "workspace": str(state_root),
            }
            issued = authority.issue(invocation, now_ms=now_ms + 1)
            self.assertEqual(issued["status"], "OK")
            self.assertEqual(issued["runtime"]["gateway_epoch"], 7)
            self.assertEqual(
                issued["runtime"]["trust_bundle_sha256"],
                issued["runtime"]["trust_bundle"]["bundle_sha256"],
            )

            adversarial = dict(invocation)
            adversarial["args"] = {"options": [{"Confirmed": True}]}
            with self.assertRaisesRegex(OmniGrantAuthorityError, "model_authority_field"):
                authority.issue(adversarial, now_ms=now_ms + 2)

            adversarial["args"] = {"options": {"output-path": "../../outside.txt"}}
            adversarial["call_id"] = "toolcall_" + "2" * 64
            relative_outside = authority.issue(adversarial, now_ms=now_ms + 3)
            self.assertEqual(relative_outside["status"], "OK")

            adversarial["args"] = {"attachments": [str(state_root.parent / "outside.txt")]}
            adversarial["call_id"] = "toolcall_" + "3" * 64
            absolute_outside = authority.issue(adversarial, now_ms=now_ms + 4)
            self.assertEqual(absolute_outside["status"], "OK")

            adversarial["args"] = {"options": {"replace-existing": True}}
            with self.assertRaisesRegex(OmniGrantAuthorityError, "overwrite.not_authorized"):
                authority.issue(adversarial, now_ms=now_ms + 5)

            verifier = _load_capability_module()
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_OMNI_BODY_STATE_ROOT": str(state_root / "omni-state")},
                clear=True,
            ):
                verified = verifier.verify_capability_grant(
                    issued["grant"],
                    action="file.read",
                    target="sample.txt",
                    args={"encoding": "utf-8"},
                    workspace=str(state_root),
                    runtime_meta=issued["runtime"],
                )
            self.assertRegex(verified["grant_sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(verified["allow_shell"])
            authority.unregister(outer.payload.ticket_id)
            worker.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
