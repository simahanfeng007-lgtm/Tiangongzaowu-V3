from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIRING = ROOT / "src" / "life_service" / "embedded_runtime_wiring.py"
RUNTIME = ROOT / "src" / "total_gateway" / "runtime.py"
LIFE_RUNTIME = ROOT / "src" / "life_service" / "embedded_runtime.py"
HOTFIX = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "hotfix_20260727.py"

EXPECTED = [
    "AUTONOMY_DECIDER",
    "LEARNING_DECIDER",
    "CAPABILITY_PATCH_DECIDER",
    "LEARNING_SHARE_WRITER",
    "GREETING_WRITER",
    "PROACTIVE_DECIDER",
    "PROACTIVE_EXPRESSION_WRITER",
    "SELF_ITERATION_DECIDER",
    "UPGRADE_EXECUTOR",
    "ARTIFACT_ACTION_CATALOG_PROVIDER",
    "ARTIFACT_PUBLISHER",
    "WORLD_IDENTITY_PROVIDER",
    "PROACTIVE_WORLD_PROVIDER",
    "CAPABILITY_WORKSPACE_MAPPER",
    "CAPABILITY_WORKSPACE_REMOVER",
    "CAPABILITY_WORKSPACE_MARKER",
    "ARTIFACT_INVOKER",
]


class EmbeddedLifeM205Tests(unittest.TestCase):
    def test_wiring_boundary_is_typed_coordination_only(self):
        text = WIRING.read_text(encoding="utf-8")
        tree = ast.parse(text)
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("EmbeddedLifeGatewayBinding", classes)
        self.assertIn("EmbeddedLifeGatewayWiringPort", classes)
        self.assertIn("bind_embedded_life_gateway_callback", functions)
        self.assertIn("bind_embedded_life_learning_materializers", functions)
        self.assertNotIn("EmbeddedLifeRuntime", classes)
        self.assertNotIn("Any", text)
        self.assertNotIn("LifeShadowStore", text)
        self.assertNotIn("EmbeddedLifeScheduler", text)
        self.assertNotIn("CompleteLifeSystem", text)
        self.assertNotIn("total_gateway", text)

    def test_boundary_dispatches_through_existing_public_setters(self):
        text = WIRING.read_text(encoding="utf-8")
        setters = (
            "set_cognition_decider", "set_autonomy_decider", "set_learning_decider",
            "set_learning_share_writer", "set_proactive_decider",
            "set_proactive_expression_writer", "set_proactive_world_provider",
            "set_self_iteration_decider", "set_upgrade_executor", "set_greeting_writer",
            "set_artifact_action_catalog_provider", "set_artifact_publisher",
            "set_world_identity_provider", "set_capability_workspace_mapper",
            "set_capability_workspace_remover", "set_capability_workspace_marker",
            "set_capability_patch_decider", "set_artifact_invoker",
            "set_learning_materializers",
        )
        for setter in setters:
            self.assertIn(f"target.{setter}(", text)

    def test_total_gateway_no_longer_knows_life_setter_names(self):
        text = RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn("runtime.life_service.set_", text)
        self.assertIn("from life_service.embedded_runtime_wiring import (", text)
        self.assertIn("bind_embedded_life_gateway_callback(", text)
        self.assertIn("bind_embedded_life_learning_materializers(", text)

    def test_installation_order_is_preserved(self):
        text = RUNTIME.read_text(encoding="utf-8")
        positions = [text.index(f"EmbeddedLifeGatewayBinding.{name}") for name in EXPECTED]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(
            text.index("bind_embedded_life_learning_materializers("),
            text.index("EmbeddedLifeGatewayBinding.ARTIFACT_INVOKER"),
        )

    def test_runtime_storage_and_hotfix_compatibility_remain(self):
        runtime = LIFE_RUNTIME.read_text(encoding="utf-8")
        hotfix = HOTFIX.read_text(encoding="utf-8")
        self.assertIn("self._autonomy_decider: Any = None", runtime)
        self.assertIn("def set_autonomy_decider", runtime)
        self.assertIn('getattr(self, "_autonomy_decider", None)', hotfix)
        self.assertNotIn("embedded_runtime_wiring", runtime)

    def test_materializers_remain_paired_public_setter(self):
        wiring = WIRING.read_text(encoding="utf-8")
        self.assertIn("target.set_learning_materializers(", wiring)
        self.assertIn("researcher=researcher", wiring)
        self.assertIn("synthesizer=synthesizer", wiring)


if __name__ == "__main__":
    unittest.main()
