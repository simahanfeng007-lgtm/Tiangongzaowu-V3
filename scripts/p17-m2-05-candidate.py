from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "241f4cdd6b208896fe83573d17b5adc81b22d14b"
RUNTIME = ROOT / "src" / "total_gateway" / "runtime.py"
WIRING = ROOT / "src" / "life_service" / "embedded_runtime_wiring.py"
TEST = ROOT / "tests" / "test_embedded_life_p17_m2_05.py"
GATE = ROOT / ".github" / "workflows" / "architecture-gate.yml"

WIRING_TEXT = '''"""Typed Gateway-to-Life callback wiring boundary.

This module owns only the dependency-installation contract between Total Gateway
and the existing EmbeddedLifeRuntime public setter surface.  It creates no
runtime, scheduler, store, writer, policy, or callback implementation.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Protocol, TypeAlias

GatewayCallback: TypeAlias = Callable[..., object]
OptionalGatewayCallback: TypeAlias = GatewayCallback | None


class EmbeddedLifeGatewayBinding(str, Enum):
    COGNITION_DECIDER = "cognition_decider"
    AUTONOMY_DECIDER = "autonomy_decider"
    LEARNING_DECIDER = "learning_decider"
    LEARNING_SHARE_WRITER = "learning_share_writer"
    PROACTIVE_DECIDER = "proactive_decider"
    PROACTIVE_EXPRESSION_WRITER = "proactive_expression_writer"
    PROACTIVE_WORLD_PROVIDER = "proactive_world_provider"
    SELF_ITERATION_DECIDER = "self_iteration_decider"
    UPGRADE_EXECUTOR = "upgrade_executor"
    GREETING_WRITER = "greeting_writer"
    ARTIFACT_ACTION_CATALOG_PROVIDER = "artifact_action_catalog_provider"
    ARTIFACT_PUBLISHER = "artifact_publisher"
    WORLD_IDENTITY_PROVIDER = "world_identity_provider"
    CAPABILITY_WORKSPACE_MAPPER = "capability_workspace_mapper"
    CAPABILITY_WORKSPACE_REMOVER = "capability_workspace_remover"
    CAPABILITY_WORKSPACE_MARKER = "capability_workspace_marker"
    CAPABILITY_PATCH_DECIDER = "capability_patch_decider"
    ARTIFACT_INVOKER = "artifact_invoker"


class EmbeddedLifeGatewayWiringPort(Protocol):
    def set_cognition_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_autonomy_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_learning_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_learning_share_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_proactive_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_proactive_expression_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_proactive_world_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_self_iteration_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_upgrade_executor(self, executor: OptionalGatewayCallback) -> None: ...
    def set_greeting_writer(self, writer: OptionalGatewayCallback) -> None: ...
    def set_artifact_action_catalog_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_artifact_publisher(self, publisher: OptionalGatewayCallback) -> None: ...
    def set_world_identity_provider(self, provider: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_mapper(self, mapper: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_remover(self, remover: OptionalGatewayCallback) -> None: ...
    def set_capability_workspace_marker(self, marker: OptionalGatewayCallback) -> None: ...
    def set_capability_patch_decider(self, decider: OptionalGatewayCallback) -> None: ...
    def set_artifact_invoker(self, invoker: OptionalGatewayCallback) -> None: ...
    def set_learning_materializers(
        self,
        *,
        researcher: OptionalGatewayCallback = None,
        synthesizer: OptionalGatewayCallback = None,
    ) -> None: ...


def bind_embedded_life_gateway_callback(
    target: EmbeddedLifeGatewayWiringPort,
    binding: EmbeddedLifeGatewayBinding,
    callback: OptionalGatewayCallback,
) -> None:
    """Install one callback through the Runtime's existing public setter.

    Validation, locking, side effects, and exception semantics remain owned by
    the existing setter.  This function only removes setter-name knowledge from
    Total Gateway and keeps installation order at the caller.
    """
    if binding is EmbeddedLifeGatewayBinding.COGNITION_DECIDER:
        target.set_cognition_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.AUTONOMY_DECIDER:
        target.set_autonomy_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.LEARNING_DECIDER:
        target.set_learning_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.LEARNING_SHARE_WRITER:
        target.set_learning_share_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_DECIDER:
        target.set_proactive_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_EXPRESSION_WRITER:
        target.set_proactive_expression_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.PROACTIVE_WORLD_PROVIDER:
        target.set_proactive_world_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.SELF_ITERATION_DECIDER:
        target.set_self_iteration_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.UPGRADE_EXECUTOR:
        target.set_upgrade_executor(callback)
    elif binding is EmbeddedLifeGatewayBinding.GREETING_WRITER:
        target.set_greeting_writer(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_ACTION_CATALOG_PROVIDER:
        target.set_artifact_action_catalog_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_PUBLISHER:
        target.set_artifact_publisher(callback)
    elif binding is EmbeddedLifeGatewayBinding.WORLD_IDENTITY_PROVIDER:
        target.set_world_identity_provider(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_MAPPER:
        target.set_capability_workspace_mapper(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_REMOVER:
        target.set_capability_workspace_remover(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_WORKSPACE_MARKER:
        target.set_capability_workspace_marker(callback)
    elif binding is EmbeddedLifeGatewayBinding.CAPABILITY_PATCH_DECIDER:
        target.set_capability_patch_decider(callback)
    elif binding is EmbeddedLifeGatewayBinding.ARTIFACT_INVOKER:
        target.set_artifact_invoker(callback)
    else:
        raise ValueError(f"unsupported embedded life gateway binding: {binding!r}")


def bind_embedded_life_learning_materializers(
    target: EmbeddedLifeGatewayWiringPort,
    *,
    researcher: OptionalGatewayCallback = None,
    synthesizer: OptionalGatewayCallback = None,
) -> None:
    """Install the paired learning materializers without changing setter semantics."""
    target.set_learning_materializers(
        researcher=researcher,
        synthesizer=synthesizer,
    )


__all__ = [
    "EmbeddedLifeGatewayBinding",
    "EmbeddedLifeGatewayWiringPort",
    "GatewayCallback",
    "OptionalGatewayCallback",
    "bind_embedded_life_gateway_callback",
    "bind_embedded_life_learning_materializers",
]
'''

TEST_TEXT = '''from __future__ import annotations

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
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


WIRING.write_text(WIRING_TEXT, encoding="utf-8", newline="\n")
TEST.write_text(TEST_TEXT, encoding="utf-8", newline="\n")

text = RUNTIME.read_text(encoding="utf-8")
first_anchor = "                runtime.life_service.set_autonomy_decider("
import_block = '''                from life_service.embedded_runtime_wiring import (
                    EmbeddedLifeGatewayBinding,
                    bind_embedded_life_gateway_callback,
                    bind_embedded_life_learning_materializers,
                )

'''
text = replace_once(text, first_anchor, import_block + first_anchor, "local wiring import")

bindings = [
    ("set_autonomy_decider", "AUTONOMY_DECIDER"),
    ("set_learning_decider", "LEARNING_DECIDER"),
    ("set_capability_patch_decider", "CAPABILITY_PATCH_DECIDER"),
    ("set_learning_share_writer", "LEARNING_SHARE_WRITER"),
    ("set_greeting_writer", "GREETING_WRITER"),
    ("set_proactive_decider", "PROACTIVE_DECIDER"),
    ("set_proactive_expression_writer", "PROACTIVE_EXPRESSION_WRITER"),
    ("set_self_iteration_decider", "SELF_ITERATION_DECIDER"),
    ("set_upgrade_executor", "UPGRADE_EXECUTOR"),
    ("set_artifact_action_catalog_provider", "ARTIFACT_ACTION_CATALOG_PROVIDER"),
    ("set_artifact_publisher", "ARTIFACT_PUBLISHER"),
    ("set_world_identity_provider", "WORLD_IDENTITY_PROVIDER"),
    ("set_proactive_world_provider", "PROACTIVE_WORLD_PROVIDER"),
    ("set_capability_workspace_mapper", "CAPABILITY_WORKSPACE_MAPPER"),
    ("set_capability_workspace_remover", "CAPABILITY_WORKSPACE_REMOVER"),
    ("set_capability_workspace_marker", "CAPABILITY_WORKSPACE_MARKER"),
    ("set_artifact_invoker", "ARTIFACT_INVOKER"),
]
for setter, enum_name in bindings:
    old = f"runtime.life_service.{setter}("
    new = (
        "bind_embedded_life_gateway_callback(\n"
        "                    runtime.life_service,\n"
        f"                    EmbeddedLifeGatewayBinding.{enum_name},\n"
        "                    "
    )
    text = replace_once(text, old, new, setter)

materializer_old = "runtime.life_service.set_learning_materializers("
materializer_new = (
    "bind_embedded_life_learning_materializers(\n"
    "                    runtime.life_service,\n"
    "                    "
)
text = replace_once(text, materializer_old, materializer_new, "learning materializers")

remaining = sorted(set(re.findall(r"runtime\.life_service\.(set_[A-Za-z0-9_]+)\(", text)))
if remaining:
    raise SystemExit(f"unmigrated direct life setters: {remaining}")
compile(text, str(RUNTIME), "exec")
RUNTIME.write_text(text, encoding="utf-8", newline="\n")

gate = GATE.read_text(encoding="utf-8")
gate_anchor = '''      - name: Compile P17 M2 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py src/life_service/embedded_runtime.py src/life_service/embedded_runtime_lifecycle.py
'''
gate_new = '''      - name: Run P17 M2-05 embedded life gateway wiring regression
        run: python tests/test_embedded_life_p17_m2_05.py -v

      - name: Compile P17 M2 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py src/life_service/embedded_runtime.py src/life_service/embedded_runtime_lifecycle.py src/life_service/embedded_runtime_wiring.py src/total_gateway/runtime.py
'''
gate = replace_once(gate, gate_anchor, gate_new, "architecture gate")
GATE.write_text(gate, encoding="utf-8", newline="\n")

print("P17-M2-05 candidate patched")
