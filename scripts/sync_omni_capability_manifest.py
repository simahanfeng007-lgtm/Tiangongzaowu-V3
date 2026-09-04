"""Synchronize schemas and managed-novel actions into the src capability view.

Only ``src/omni_body_skill/registry/capability_manifest.generated.json`` is
written here.  Compatibility/runtime copies remain generated mirrors owned by
``scripts/sync-generated-sources.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
from typing import Any


READ_ACTIONS = {
    "novel.project.status",
    "novel.blueprint.assist",
    "novel.reference.resolve",
    "novel.timeline.calculate",
    "novel.context.query",
    "novel.project.audit",
}


def _literal_assignment(path: Path, name: str) -> dict[str, dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"), filename=str(path))
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name:
            value = ast.literal_eval(node.value)
            if isinstance(value, dict) and all(isinstance(key, str) and isinstance(meta, dict) for key, meta in value.items()):
                return value
    raise ValueError(f"{name} is not a literal mapping in {path}")


def _load_tool_contracts(workspace: Path):
    path = workspace / "src" / "omni_body_skill" / "tool_contracts.py"
    source_root = str((workspace / "src").resolve())
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from omni_body_skill import tool_contracts

    if Path(tool_contracts.__file__).resolve() != path.resolve():
        raise ValueError(
            "loaded tool contracts do not come from the src authority"
        )
    return tool_contracts


def _canonical_sha256(value: Any) -> str:
    import hashlib

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _synchronized_document(
    path: Path,
    novel_actions: dict[str, dict[str, Any]],
    *,
    tool_contracts: Any,
) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    expected_root = {"capabilities", "executable", "schema", "source_hash", "total", "unavailable", "validation"}
    if set(document) != expected_root or document.get("schema") != "tiangong.v3.capability_manifest.v1":
        raise ValueError(f"unsupported capability manifest: {path}")
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError(f"capabilities must be an object: {path}")

    for action_id, metadata in novel_actions.items():
        if metadata.get("implemented") is not True:
            raise ValueError(f"managed novel action is not executable: {action_id}")
        capabilities[action_id] = {
            "alias_to": "",
            "declared_status": "core_executable",
            "effect": "read" if action_id in READ_ACTIONS else ("create" if action_id == "novel.project.create" else "update"),
            "executable": True,
            "handler": "delivery_kernel",
            "id": action_id,
            "reason": "",
            "risk": str(metadata.get("risk") or "A2"),
            "summary": str(metadata.get("summary") or f"Managed novel action: {action_id}"),
        }

    descriptors = tool_contracts.build_action_schema_catalog(capabilities)
    if set(descriptors) != set(capabilities):
        raise ValueError("action schema catalog does not close over the manifest")
    for action_id, descriptor in descriptors.items():
        capabilities[action_id].update(descriptor)

    ordered = {key: capabilities[key] for key in sorted(capabilities)}
    executable = sum(1 for item in ordered.values() if isinstance(item, dict) and item.get("executable") is True)
    source_hash = _canonical_sha256(ordered)
    document.update(
        capabilities=ordered,
        total=len(ordered),
        executable=executable,
        unavailable=len(ordered) - executable,
        source_hash=source_hash,
        validation={"executable_without_route": [], "ok": True, "source_hash": source_hash},
    )
    return document


def _sync_manifest(
    path: Path,
    novel_actions: dict[str, dict[str, Any]],
    *,
    tool_contracts: Any,
) -> tuple[int, int, str]:
    document = _synchronized_document(
        path,
        novel_actions,
        tool_contracts=tool_contracts,
    )
    # Write only the src/ authority. Mirrors are exclusively produced by the
    # repository-wide generated-source sync.
    path.write_bytes(
        (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    )
    return (
        int(document["total"]),
        int(document["executable"]),
        str(document["source_hash"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    root = workspace / "src" / "omni_body_skill"
    path = root / "registry" / "capability_manifest.generated.json"
    novel_actions = _literal_assignment(
        root / "tools" / "novel_system.py", "NOVEL_SYSTEM_ACTIONS"
    )
    tool_contracts = _load_tool_contracts(workspace)
    if args.check:
        current = json.loads(path.read_text(encoding="utf-8", errors="strict"))
        expected = _synchronized_document(
            path,
            novel_actions,
            tool_contracts=tool_contracts,
        )
        if current != expected:
            raise ValueError(f"authoritative capability manifest is stale: {path}")
        total, executable, source_hash = (
            int(current["total"]),
            int(current["executable"]),
            str(current["source_hash"]),
        )
    else:
        total, executable, source_hash = _sync_manifest(
            path,
            novel_actions,
            tool_contracts=tool_contracts,
        )
    print(json.dumps({"ok": True, "managed_novel_actions": len(novel_actions), "total": total, "executable": executable, "source_hash": source_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
