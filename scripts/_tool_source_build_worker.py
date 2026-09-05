"""Internal P8 build worker; launch only through build-tool-source.py.

The parent enforces OS containment BEFORE this interpreter starts. Environment
markers here are accidental-invocation guards, never proof of isolation.
"""

from __future__ import annotations

import ast
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys


ARTIFACT_NAME = ".tiangong-candidate-compiled-manifest.json"


def main() -> int:
    if os.environ.get("TIANGONG_SANDBOX") != "1":
        raise RuntimeError("source build worker requires its isolated parent runner")
    root = Path(__file__).resolve().parent
    if (root / ARTIFACT_NAME).exists():
        raise RuntimeError("source build artifact already exists")
    parsed = 0
    for path in sorted(root.rglob("*.py")):
        ast.parse(path.read_bytes(), filename=path.relative_to(root).as_posix())
        parsed += 1
    sys.path[:0] = [str(root / "src"), str(root / "app/backend/tiangong-backend")]
    from source_authority.validator import load_config, validate_source_authority
    failures = validate_source_authority(load_config(root / "source-ownership.json"), repo_root=root)
    if failures:
        raise RuntimeError("candidate source topology failed: " + "; ".join(failures))

    from omni_body_skill import tool_contracts
    from omni_body_skill.tools import omni_body_tool
    from v3 import fact_kernel
    from world_understanding.tool_capability_world.source_inputs import compile_tool_source_inputs
    source_inputs = compile_tool_source_inputs(root)
    bindings = {}
    for name, module, relative in (
        ("compiler", fact_kernel, "app/backend/tiangong-backend/v3/fact_kernel/__init__.py"),
        ("actions", omni_body_tool, "src/omni_body_skill/tools/omni_body_tool.py"),
        ("schemas", tool_contracts, "src/omni_body_skill/tool_contracts.py"),
    ):
        expected = root / relative
        if Path(module.__file__).resolve() != expected.resolve():
            raise RuntimeError("source build imported a foreign authority: " + name)
        bindings[name] = {"path": relative, "sha256": hashlib.sha256(expected.read_bytes()).hexdigest()}
    # No BodyRuntime instance, Action dispatch or source publication. Reuse
    # the existing runtime compiler and its explicit Gateway projection.
    compiled = fact_kernel.compile_manifest(
        omni_body_tool.ACTIONS, omni_body_tool.BodyRuntime,
        dynamic_actions=omni_body_tool.DELIVERY_ACTIONS,
        action_schema_catalog=tool_contracts.build_action_schema_catalog(omni_body_tool.ACTIONS),
    )
    result = {
        "schema": "tiangong.tool-source-build-artifact.v1",
        "compiler": "v3.fact_kernel.compile_manifest",
        "runtime_source_hash": compiled.source_hash,
        "authority_bindings": bindings,
        "python_version": sys.version,
        "python_ast_files": parsed,
        "source_topology_valid": True,
        "source_inputs": asdict(source_inputs),
        "gateway_manifest": compiled.to_gateway_dict(source_inputs_sha256=source_inputs.source_inputs_sha256),
    }
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with (root / ARTIFACT_NAME).open("xb") as output:
        output.write(raw)
    print(json.dumps({"artifact_sha256": hashlib.sha256(raw).hexdigest(), "python_ast_files": parsed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
