from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from contracts import ActionIntent, ResourceEnvelope, SourceRef, canonical_json_bytes
from life_service.action_intents import ActionIntentReceipt, LifeActionIntentEmitter
from total_gateway.life_action_intake import LifeActionIntentApi


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "app" / "life-service" / "runtime314"
EMBEDDED_PYTHON = RUNTIME / "python.exe"
FROZEN_RUNTIME_AVAILABLE = EMBEDDED_PYTHON.is_file()
PYTHON = EMBEDDED_PYTHON if EMBEDDED_PYTHON.is_file() else Path(sys.executable)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def life_intent():
    return ActionIntent(
        intent_id="intent_life_scheduler_p6",
        source="life_scheduler",
        life_id="life_main",
        principal_scope_hash=HASH_A,
        conversation_scope_hash=HASH_B,
        request_id="req_" + "1" * 64,
        run_id="run_" + "2" * 64,
        generation=1,
        action_id="file.read",
        action_version="omni-registry-v1",
        arguments_sha256=HASH_C,
        workspace_id="workspace_main",
        workspace_scope_hash=HASH_D,
        input_object_refs=(),
        requested_side_effects=("read",),
        requested_resources=ResourceEnvelope(
            max_runtime_ms=10_000,
            max_output_bytes=1_000_000,
            max_tool_calls=1,
        ),
        source_refs=(
            SourceRef(
                source_type="PREAUTHORIZED_USER_FACT",
                object_id="lev_" + "3" * 64,
                object_revision=1,
                sha256="3" * 64,
            ),
        ),
        life_snapshot_revision=1,
        life_snapshot_sha256=HASH_D,
        created_at_ms=10_000,
        expires_at_ms=60_000,
        intent_sha256="0" * 64,
    ).with_computed_sha256()


class LifeActionIntentBoundaryTests(unittest.TestCase):
    def test_source_owned_emitter_only_accepts_exact_gateway_receipts(self) -> None:
        intent = life_intent()

        class Transport:
            def __init__(self) -> None:
                self.seen = None

            def submit(self, value):
                self.seen = value
                receipt = ActionIntentReceipt(
                    intent_id=value.intent_id,
                    intent_sha256=value.intent_sha256,
                    status="REJECTED",
                    policy_decision_id=None,
                    receipt_sha256="",
                )
                return ActionIntentReceipt(
                    intent_id=receipt.intent_id,
                    intent_sha256=receipt.intent_sha256,
                    status=receipt.status,
                    policy_decision_id=receipt.policy_decision_id,
                    receipt_sha256=receipt.computed_sha256(),
                )

        transport = Transport()
        receipt = LifeActionIntentEmitter(transport).submit(intent)
        self.assertIs(transport.seen, intent)
        self.assertEqual(receipt.status, "REJECTED")

    def test_gateway_intake_never_invents_impact_or_starts_an_effect(self) -> None:
        api = LifeActionIntentApi("x" * 48)
        intent = life_intent()
        response = api.submit(
            canonical_json_bytes(
                {
                    "schema": "tiangong.life.action-intent.v2",
                    "intent": intent.model_dump(mode="json"),
                }
            ),
            now_ms=20_000,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "REJECTED")
        self.assertFalse(response.payload["effects_started"])
        self.assertFalse(response.payload["execution_ticket_issued"])

        frozen = api.submit(
            canonical_json_bytes(
                {
                    "schema": "tiangong.life.action-intent-candidate.v1",
                    "candidate": {"risk": "A0", "instruction": "pretend this is safe"},
                }
            ),
            now_ms=20_000,
        )
        self.assertEqual(frozen.status_code, 422)
        self.assertFalse(frozen.payload["effects_started"])
        self.assertFalse(frozen.payload["execution_ticket_issued"])

    @unittest.skipUnless(
        FROZEN_RUNTIME_AVAILABLE,
        "legacy frozen Life scheduler is not part of the redistributable source release",
    )
    def test_frozen_scheduler_bridge_can_only_submit_to_gateway(self) -> None:
        script = textwrap.dedent(
            f"""
            import sys
            from types import SimpleNamespace
            sys.path.insert(0, {str(RUNTIME)!r})
            import life_core
            import life_scheduler
            import life_server
            from tiangong_life_runtime_fixes import install_runtime_fixes, install_scoped_execution_credentials

            install_runtime_fixes(life_core, life_scheduler)
            install_scoped_execution_credentials(life_server, life_scheduler)

            class System:
                def __init__(self): self.records = []
                def record_autonomous_action(self, life_id, decision, result):
                    self.records.append((life_id, decision, result))

            class Gateway:
                def __init__(self): self.calls = []
                def submit(self, payload, *, timeout):
                    self.calls.append((payload, timeout))
                    return 202, {{"status": "REJECTED", "policy_decision_id": ""}}

            fake = SimpleNamespace(system=System(), gateway_action_intent_client=Gateway())
            fake._invoke_lifecycle = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("7174 bypass called"))
            execute = life_scheduler.LifeAutonomyScheduler._execute_decision
            missing = execute(fake, {{"life_id": "life_main"}}, {{"risk": "A0"}}, settings={{}}, timeout=5)
            assert missing["blocked"] is True
            decision = {{
                "action_id": "file.read",
                "action_version": "omni-registry-v1",
                "arguments_sha256": "{'a' * 64}",
                "workspace_id": "workspace_main",
                "workspace_scope_hash": "{'b' * 64}",
                "principal_scope_hash": "{'c' * 64}",
                "request_id": "req_{'1' * 64}",
                "run_id": "run_{'2' * 64}",
                "risk": "A0",
            }}
            submitted = execute(fake, {{"life_id": "life_main"}}, decision, settings={{}}, timeout=5)
            assert submitted["submitted"] is True
            assert submitted["blocked"] is True
            assert len(fake.gateway_action_intent_client.calls) == 1
            payload, _ = fake.gateway_action_intent_client.calls[0]
            assert payload["schema"] == "tiangong.life.action-intent-candidate.v1"
            assert payload["model_risk_is_untrusted"] is True
            """
        )
        completed = subprocess.run(
            [str(PYTHON), "-"],
            input=script,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_life_process_environment_scrubs_every_7174_authority_token(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = main.index("async function startLifeService")
        end = main.index("function stopLifeServiceSync", start)
        life_start = main[start:end]
        self.assertIn("TIANGONG_GATEWAY_LIFE_INTENT_TOKEN: LIFE_ACTION_INTENT_TOKEN", life_start)
        self.assertIn("delete env.TIANGONG_BACKEND_EXECUTION_TOKEN", life_start)
        self.assertIn("delete env.TIANGONG_BACKEND_INTERNAL_TOKEN", life_start)


if __name__ == "__main__":
    unittest.main()
