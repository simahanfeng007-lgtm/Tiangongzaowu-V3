from __future__ import annotations

import json
import unittest
from pathlib import Path

from total_gateway.regenerative_execution import ExecutionFrontier, derive_logical_effect_id


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend" / "v3"


class P18M2RuntimeIntegrationTests(unittest.TestCase):
    def test_backend_logical_effect_id_matches_gateway_contract(self) -> None:
        import sys
        backend_root = str(BACKEND.parent)
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)
        from v3.runtime_regenerative_boundary import derive_logical_effect_id as backend_id

        kwargs = {
            "request_id": "req_" + "1" * 64,
            "run_id": "run_" + "2" * 64,
            "generation": 7,
            "obligation_key": "deliver-result",
            "effect_namespace": "omni_body:file.write",
            "normalized_target": "path:C:/tmp/result.txt",
            "desired_postcondition_sha256": "3" * 64,
        }
        self.assertEqual(backend_id(**kwargs), derive_logical_effect_id(**kwargs))

    def test_backend_frontier_payload_is_gateway_valid_and_bounded(self) -> None:
        import sys
        backend_root = str(BACKEND.parent)
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)
        from v3.runtime_regenerative_boundary import build_frontier_payload

        payload = build_frontier_payload(
            request_id="req_" + "1" * 64,
            run_id="run_" + "2" * 64,
            generation=1,
            life_id="life_m2",
            root_goal_hash="3" * 64,
            task_contract_hash="4" * 64,
            authority_hash="5" * 64,
            global_step=300,
            epoch_index=5,
            epoch_step=50,
            frontier_version=8,
            completed_obligation_ids=[f"done-{i}" for i in range(900)],
            pending_obligation_ids=[f"pending-{i}" for i in range(900)],
            pending_effect_ids=[f"eff_{i}" for i in range(900)],
            ambiguous_effect_ids=[],
            active_blockers=[f"block-{i}" for i in range(300)],
            failed_strategy_ids=[f"strategy-{i}" for i in range(500)],
        )
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        frontier = ExecutionFrontier.model_validate_json(raw, strict=True)
        self.assertTrue(frontier.has_valid_hash())
        self.assertLess(len(raw.encode("utf-8")), 128 * 1024)
        self.assertLessEqual(len(frontier.completed_obligation_ids), 512)
        self.assertLessEqual(len(frontier.active_blockers), 128)

    def test_production_source_wires_single_parallel_checkpoint_resume_and_completion(self) -> None:
        source = (BACKEND / "zongdiaodu.py").read_text(encoding="utf-8")
        self.assertIn("set_simple_chain_regenerative_execution_provider", source)
        self.assertIn("_simple_chain_regenerative_initialize(run_state, xiaoxi)", source)
        self.assertIn("_simple_chain_regenerative_restore_turn_loop(run_state, turn_loop)", source)
        self.assertGreaterEqual(source.count("_simple_chain_regenerative_execute_tool("), 3)
        self.assertNotIn(
            "lambda: self._jineng_zhixing(tool_name, tool_args, xiaoxi, call_id=tool_call_id)",
            source,
        )
        self.assertNotIn("raw = self._jineng_zhixing(tn, ta, xiaoxi, call_id=call_id)", source)
        self.assertIn("update_frontier=False", source)
        self.assertIn("_simple_chain_regenerative_verify_completion(", source)
        self.assertGreaterEqual(source.count("_simple_chain_regenerative_verify_completion("), 3)
        checkpoint_index = source.index("if not _simple_chain_regenerative_checkpoint(run_state, turn_loop, source=source):")
        rollover_index = source.index("turn_loop.begin_next_epoch()", checkpoint_index)
        self.assertLess(checkpoint_index, rollover_index)
        self.assertIn("_simple_chain_bound_history(quality_history, limit=24)", source)

    def test_gateway_runtime_injects_provider_over_existing_store_only(self) -> None:
        runtime_source = (ROOT / "src" / "total_gateway" / "runtime.py").read_text(encoding="utf-8")
        embedded_source = (ROOT / "src" / "total_gateway" / "embedded_backend.py").read_text(encoding="utf-8")
        provider_source = (ROOT / "src" / "total_gateway" / "regenerative_provider.py").read_text(encoding="utf-8")
        self.assertIn("RegenerativeExecutionAuthority(runtime.store)", runtime_source)
        self.assertIn("set_regenerative_execution_provider", embedded_source)
        self.assertIn("set_simple_chain_regenerative_execution_provider", embedded_source)
        self.assertNotIn("GatewayStateStore.open", provider_source)
        self.assertNotIn("GatewayStateStore.open", (BACKEND / "runtime_regenerative_boundary.py").read_text(encoding="utf-8"))

    def test_source_authority_registers_regenerative_boundary(self) -> None:
        ownership = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
        v3 = next(row for row in ownership["mappings"] if row.get("id") == "v3-backend-main")
        roots = set(v3["boundary_policy"]["implementation_roots"])
        self.assertIn("runtime_regenerative_boundary.py", roots)

    def test_provider_has_live_frontier_cas_and_completion_terminal_event(self) -> None:
        provider_source = (ROOT / "src" / "total_gateway" / "regenerative_provider.py").read_text(encoding="utf-8")
        self.assertIn('"update_frontier": self._update_frontier', provider_source)
        self.assertIn("def _update_frontier", provider_source)
        self.assertIn("frontier revision is not the next authoritative CAS revision", provider_source)
        self.assertIn('event_type="chain.completed"', provider_source)


if __name__ == "__main__":
    unittest.main()
