from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HTTP_CLIENT = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "jineng" / "http_kehuduan.py"
OMNI_TOOL = ROOT / "readable-python-source" / "omni_body_skill" / "api" / "v1" / "v3" / "tools" / "omni_body.py"
OMNI_TOOL_JSON = ROOT / "readable-python-source" / "omni_body_skill" / "api" / "v1" / "v3" / "tools" / "omni_body.tool.json"
ADAPTER_CORE = ROOT / "readable-python-source" / "omni_body_skill" / "model_adapters" / "core.py"


def load_canonicalizer():
    tree = ast.parse(HTTP_CLIENT.read_text(encoding="utf-8-sig"), filename=str(HTTP_CLIENT))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_canonical_to_omni_arguments"
    )
    namespace = {"Any": Any}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(HTTP_CLIENT), "exec"), namespace)
    return namespace["_canonical_to_omni_arguments"]


class ModelToolBoundaryTests(unittest.TestCase):
    def test_empty_args_and_target_are_canonical_not_dropped(self) -> None:
        canonicalize = load_canonicalizer()
        result = canonicalize(
            {"action": "system.health"},
            {"action": "system.health", "args": {}},
        )
        self.assertEqual(
            result,
            {"action": "system.health", "target": "", "args": {}},
        )

    def test_model_authority_fields_never_leave_parser_boundary(self) -> None:
        canonicalize = load_canonicalizer()
        result = canonicalize(
            {"action": "file.read", "confirm": True},
            {
                "action": "file.read",
                "target": "a.txt",
                "args": {"encoding": "utf-8"},
                "confirmed": True,
                "allow_shell": True,
                "allow_python": True,
                "allow_absolute_paths": True,
                "workspace": "C:/escape",
            },
        )
        self.assertEqual(
            result,
            {"action": "file.read", "target": "a.txt", "args": {"encoding": "utf-8"}},
        )
        self.assertFalse(
            {"confirm", "confirmed", "allow_shell", "allow_python", "allow_absolute_paths", "workspace"}
            & set(result)
        )

    def test_authoritative_model_schemas_preserve_dictionary_composition(self) -> None:
        from capability_dictionary import load_dictionary
        from omni_body_skill.model_adapters.core import render_tool_schema
        from omni_body_skill.api.v1.v3.tools.omni_body import TOOL_DESCRIPTION
        expected = load_dictionary().host_protocol["parameters"]
        contract = TOOL_DESCRIPTION
        self.assertEqual(contract["parameters"], expected)
        self.assertEqual(render_tool_schema(provider="mimo")["tool_schema"][0]["function"]["parameters"], expected)
        self.assertIn("composition", expected["properties"])
        self.assertNotIn("action", expected.get("required", []))


if __name__ == "__main__":
    unittest.main()
