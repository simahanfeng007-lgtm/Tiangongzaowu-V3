from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from contracts import StateSnapshot
from total_gateway.store import GatewayStateStore
from total_gateway.ui_projection import build_gateway_ui_projection


ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = "req_" + "1" * 64
OTHER_REQUEST_ID = "req_" + "2" * 64
RUN_ID = "run_" + "3" * 64
OTHER_RUN_ID = "run_" + "4" * 64


def snapshot(
    machine: str,
    state: str,
    *,
    entity_id: str | None = None,
    request_id: str = REQUEST_ID,
    run_id: str = RUN_ID,
    generation: int = 2,
    revision: int = 1,
) -> StateSnapshot:
    return StateSnapshot(
        machine=machine,
        entity_id=entity_id or f"{machine}_{state.lower()}_{generation}",
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        revision=revision,
        state=state,
        created_at_ms=1_000,
        updated_at_ms=1_000 + revision,
        last_event_id=f"event_{machine}_{state.lower()}" if revision else None,
    )


def projection(*states: StateSnapshot, legacy_status: dict[str, object] | None = None):
    return build_gateway_ui_projection(
        gateway_request_id=REQUEST_ID,
        presentation_request_id="frontend-request-1",
        journal_state="COMPLETED",
        snapshots=states,
        legacy_status=legacy_status or {"run": {"status": "RUNNING"}},
        observed_at_ms=5_000,
    )


class GatewayUiProjectionTests(unittest.TestCase):
    def test_three_lanes_preserve_channel_accepted_as_lower_delivery_guarantee(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED", entity_id="execution_1"),
            snapshot("artifact", "QC_PASSED", entity_id="artifact_1"),
            snapshot("delivery", "DELIVERED", entity_id="delivery_1"),
            snapshot("delivery", "CHANNEL_ACCEPTED", entity_id="delivery_2"),
        )
        self.assertEqual(result.execution.state, "SUCCEEDED")
        self.assertEqual(result.artifact.state, "QC_PASSED")
        self.assertEqual(result.delivery.state, "CHANNEL_ACCEPTED")
        self.assertEqual(result.overall_phase, "channel_accepted")
        self.assertTrue(result.execution.evidence_verified)
        self.assertTrue(result.artifact.evidence_verified)
        self.assertTrue(result.delivery.evidence_verified)
        self.assertEqual(result.projection_sha256, result.computed_sha256())

    def test_all_delivered_is_distinct_from_channel_accepted(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_PASSED"),
            snapshot("delivery", "DELIVERED", entity_id="delivery_1"),
            snapshot("delivery", "DELIVERED", entity_id="delivery_2"),
        )
        self.assertEqual(result.delivery.state, "DELIVERED")
        self.assertEqual(result.overall_phase, "delivered")

    def test_one_accepted_and_one_failed_delivery_is_partial_not_complete(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_PASSED"),
            snapshot("delivery", "CHANNEL_ACCEPTED", entity_id="delivery_1"),
            snapshot("delivery", "FAILED_FINAL", entity_id="delivery_2"),
        )
        self.assertEqual(result.delivery.state, "FAILED_FINAL")
        self.assertEqual(result.delivery.tone, "failed")
        self.assertEqual(result.overall_phase, "partial")

    def test_ambiguity_and_qc_failure_are_not_collapsed_into_execution_success(self) -> None:
        ambiguous = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_PASSED"),
            snapshot("delivery", "AMBIGUOUS"),
        )
        self.assertEqual(ambiguous.execution.state, "SUCCEEDED")
        self.assertEqual(ambiguous.delivery.tone, "blocked")
        self.assertEqual(ambiguous.overall_phase, "reconcile_required")
        self.assertTrue(ambiguous.needs_reconciliation)

        failed = projection(
            snapshot("request", "VALIDATING_ARTIFACTS", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_FAILED"),
        )
        self.assertEqual(failed.artifact.tone, "failed")
        self.assertEqual(failed.overall_phase, "failed")

    def test_old_generation_ambiguity_is_filtered_by_current_request_authority(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority", generation=2),
            snapshot("execution", "SUCCEEDED", generation=2),
            snapshot("artifact", "QC_PASSED", generation=2),
            snapshot("delivery", "DELIVERED", entity_id="delivery_current", generation=2),
            snapshot("delivery", "AMBIGUOUS", entity_id="delivery_old", generation=1),
        )
        self.assertEqual(result.delivery.state, "DELIVERED")
        self.assertEqual(result.delivery.entity_count, 1)
        self.assertFalse(result.needs_reconciliation)

    def test_legacy_execution_and_absent_artifact_never_claim_verified_success(self) -> None:
        result = projection(legacy_status={"run": {"status": "SUCCEEDED"}})
        self.assertEqual(result.execution.state, "SUCCEEDED")
        self.assertEqual(result.execution.source, "LEGACY_OBSERVATION")
        self.assertFalse(result.execution.evidence_verified)
        self.assertEqual(result.artifact.state, "PENDING")
        self.assertEqual(result.artifact.source, "ABSENT")
        self.assertFalse(result.artifact.evidence_verified)
        self.assertEqual(result.delivery.state, "NOT_PLANNED")
        self.assertEqual(result.overall_phase, "qc")

    def test_store_lists_only_requested_snapshots_in_machine_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = GatewayStateStore.open(Path(temporary) / "gateway.sqlite3", now_ms=1_000)
            try:
                store.initialize_snapshot(
                    snapshot("request", "RECEIVED", entity_id="request_authority", revision=0)
                )
                store.initialize_snapshot(
                    snapshot("execution", "NOT_STARTED", entity_id="execution_1", revision=0)
                )
                store.initialize_snapshot(
                    snapshot("artifact", "PENDING", entity_id="artifact_1", revision=0)
                )
                store.initialize_snapshot(
                    snapshot(
                        "request",
                        "RECEIVED",
                        entity_id="other_request_authority",
                        request_id=OTHER_REQUEST_ID,
                        run_id=OTHER_RUN_ID,
                        revision=0,
                    )
                )
                listed = store.list_request_snapshots(REQUEST_ID)
                self.assertEqual([item.machine for item in listed], ["request", "execution", "artifact"])
                self.assertTrue(all(item.request_id == REQUEST_ID for item in listed))
            finally:
                store.close()

    def test_frontend_maps_projection_to_three_distinct_steps_and_blocks_ambiguity(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_PASSED"),
            snapshot("delivery", "AMBIGUOUS"),
        )
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/gateway-ui-projection.mjs").as_uri()
        script = (
            f'import {{ projectionToProgressSteps }} from {json.dumps(module_url)};'
            "const projection = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify(projectionToProgressSteps(projection, 'frontend-request-1')));"
        )
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                json.dumps(result.model_dump(mode="json"), separators=(",", ":")),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        steps = json.loads(completed.stdout)
        self.assertEqual(
            [item["id"] for item in steps],
            ["gateway-lane-execution", "gateway-lane-artifact", "gateway-lane-delivery"],
        )
        self.assertTrue(steps[0]["title"].startswith("执行 ·"))
        self.assertTrue(steps[1]["title"].startswith("产物 QC ·"))
        self.assertTrue(steps[2]["title"].startswith("投递 ·"))
        self.assertEqual(steps[2]["status"], "blocked")
        self.assertIn("禁止重发", steps[2]["summary"])

    def test_frontend_rejects_oversized_or_non_integral_projection_fields(self) -> None:
        result = projection(
            snapshot("request", "DELIVERING", entity_id="request_authority"),
            snapshot("execution", "SUCCEEDED"),
            snapshot("artifact", "QC_PASSED"),
            snapshot("delivery", "DELIVERED"),
        ).model_dump(mode="json")
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/gateway-ui-projection.mjs").as_uri()
        script = f"""
import {{ projectionToProgressSteps }} from {json.dumps(module_url)};
const source = JSON.parse(process.argv[1]);
const oversized = structuredClone(source); oversized.execution.label = "x".repeat(501);
const fractional = structuredClone(source); fractional.delivery.entity_count = 1.5;
const badTime = structuredClone(source); badTime.observed_at_ms = -1;
console.log(JSON.stringify([
  projectionToProgressSteps(oversized, "frontend-request-1"),
  projectionToProgressSteps(fractional, "frontend-request-1"),
  projectionToProgressSteps(badTime, "frontend-request-1")
]));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script, json.dumps(result)],
            cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(json.loads(completed.stdout), [[], [], []])

    def test_conversation_ui_renders_a_dedicated_three_lane_fact_card(self) -> None:
        renderer = (
            ROOT / "app/frontend-v2/renderer/plugins/conversation-panel.mjs"
        ).read_text(encoding="utf-8")
        state_source = (ROOT / "app/frontend-v2/renderer/core/state.mjs").read_text(
            encoding="utf-8"
        )
        styles = (ROOT / "app/frontend-v2/styles/conversation.css").read_text(encoding="utf-8")
        self.assertIn('title.textContent = "网关事实状态"', renderer)
        self.assertIn('execution: "执行", artifact: "产物 QC", delivery: "投递"', renderer)
        self.assertIn("appendGatewayStateCard(container, steps)", renderer)
        self.assertIn("(!finishedOk || hasGatewayFacts)", renderer)
        self.assertIn("gateway-state-card", styles)
        self.assertIn("gateway-state-failed", styles)
        self.assertIn('["failed", "blocked", "timeout"].includes(step.status)', state_source)

    def test_blocked_gateway_lane_survives_frontend_finish_as_failure(self) -> None:
        module_url = (ROOT / "app/frontend-v2/renderer/core/state.mjs").as_uri()
        script = f"""
            import {{ createState }} from {json.dumps(module_url)};
            const values = new Map();
            globalThis.localStorage = {{
              getItem: (key) => values.has(key) ? values.get(key) : null,
              setItem: (key, value) => values.set(key, String(value)),
              removeItem: (key) => values.delete(key)
            }};
            globalThis.window = globalThis;
            const state = createState();
            const sessionId = state.snapshot().activeSessionId;
            state.startRunProgress(sessionId, "frontend-request-1");
            state.applyRunProgress(sessionId, {{
              id: "gateway-lane-delivery",
              title: "投递 · 结果不明，等待对账",
              status: "blocked",
              summary: "禁止重发，等待网关对账",
              requestId: "frontend-request-1",
              sessionId,
              meta: {{ type: "GATEWAY_STATE_PROJECTION", machine: "delivery" }}
            }});
            state.finishRunProgress(sessionId, "frontend-request-1", true);
            console.log(JSON.stringify(state.snapshot().runProgress));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        progress = json.loads(completed.stdout)
        self.assertEqual(progress["phase"], "finished")
        self.assertFalse(progress["ok"])
        self.assertTrue(any(step["status"] == "blocked" for step in progress["steps"]))


if __name__ == "__main__":
    unittest.main()
