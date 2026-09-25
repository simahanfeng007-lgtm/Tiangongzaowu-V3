"""Validate dictionary bindings and generate Gateway views from one release."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def build(root: Path, *, check: bool = False) -> dict:
    sys.path[:0] = [str(root / "src"), str(root / "app/backend/tiangong-backend")]
    from capability_dictionary import load_dictionary
    from omni_body_skill.tools.omni_body_tool import ACTIONS, BodyRuntime, DELIVERY_ACTIONS
    from omni_body_skill.tool_contracts import build_action_schema_catalog
    from v3.fact_kernel import compile_manifest

    release = load_dictionary(root / "dictionaries")
    compiled = compile_manifest(ACTIONS, BodyRuntime, dynamic_actions=set(DELIVERY_ACTIONS),
                                action_schema_catalog=build_action_schema_catalog(ACTIONS))
    manifest = compiled.to_gateway_dict()
    for action, row in release.tools.items():
        binding = row["binding"]
        if binding["kind"] == "method" and row["runtime"].get("implemented"):
            if not callable(getattr(BodyRuntime, binding["target"], None)):
                raise ValueError("dictionary_binding_unresolved:" + action)
        if binding["kind"] == "delivery" and row["runtime"].get("implemented") and action not in DELIVERY_ACTIONS:
            raise ValueError("dictionary_delivery_binding_unresolved:" + action)
    documents = {
        "capability_manifest.generated.json": manifest,
        "actions.json": {"schema": "tiangong.dictionary.actions.v1", "actions": release.action_metadata()},
        "release.json": {"schema": "tiangong.dictionary.release.v1", "version": release.version,
                         "dictionary_sha256": release.sha256, "source_hash": manifest["source_hash"],
                         "tool_count": len(release.tools), "skill_count": len(release.skills["skills"])},
    }
    target = root / "dictionaries/registry"
    def encode(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    documents["release.json"]["views"] = {name: hashlib.sha256(encode(value)).hexdigest()
        for name, value in documents.items() if name != "release.json"}
    if not check:
        target.mkdir(parents=True, exist_ok=True)
    for name, value in documents.items():
        raw = encode(value)
        path = target / name
        if check:
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError("dictionary_generated_view_stale:" + name)
        else:
            staged = path.with_suffix(path.suffix + ".tmp")
            staged.write_bytes(raw)
            staged.replace(path)
    return documents["release.json"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.workspace.resolve(), check=args.check), ensure_ascii=False))
