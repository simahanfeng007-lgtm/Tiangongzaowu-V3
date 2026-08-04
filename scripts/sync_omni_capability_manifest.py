"""Synchronize managed-novel actions into the release Skill capability view.

The frozen backend loads these actions dynamically from ``novel_system.py``.
The total gateway, however, deliberately matches Skills against a pinned static
manifest.  This script keeps that read-only compatibility view aligned without
importing frozen Python bytecode (which may target another Python version).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
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


def _source_hash(capabilities: dict[str, Any]) -> str:
    payload = json.dumps(capabilities, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sync_manifest(path: Path, novel_actions: dict[str, dict[str, Any]]) -> tuple[int, int, str]:
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

    ordered = {key: capabilities[key] for key in sorted(capabilities)}
    executable = sum(1 for item in ordered.values() if isinstance(item, dict) and item.get("executable") is True)
    source_hash = _source_hash(ordered)
    document.update(
        capabilities=ordered,
        total=len(ordered),
        executable=executable,
        unavailable=len(ordered) - executable,
        source_hash=source_hash,
        validation={"executable_without_route": [], "ok": True, "source_hash": source_hash},
    )
    # Keep generated release bytes stable across Windows and POSIX hosts.
    path.write_bytes((json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return len(ordered), executable, source_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    roots = (
        workspace / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill",
        workspace / "readable-python-source" / "omni_body_skill",
    )
    novel_actions = _literal_assignment(roots[0] / "tools" / "novel_system.py", "NOVEL_SYSTEM_ACTIONS")
    if set(novel_actions) != set(_literal_assignment(roots[1] / "tools" / "novel_system.py", "NOVEL_SYSTEM_ACTIONS")):
        raise ValueError("app and readable managed-novel action sets disagree")

    results: list[tuple[int, int, str]] = []
    for root in roots:
        path = root / "registry" / "capability_manifest.generated.json"
        if args.check:
            document = json.loads(path.read_text(encoding="utf-8", errors="strict"))
            missing = sorted(set(novel_actions) - set(document.get("capabilities") or {}))
            unavailable = sorted(
                action_id
                for action_id in novel_actions
                if not bool((document.get("capabilities") or {}).get(action_id, {}).get("executable"))
            )
            if missing or unavailable:
                raise ValueError(f"managed-novel manifest mismatch at {path}: missing={missing}, unavailable={unavailable}")
            results.append((int(document["total"]), int(document["executable"]), str(document["source_hash"])))
        else:
            results.append(_sync_manifest(path, novel_actions))
    if len(set(results)) != 1:
        raise ValueError(f"app and readable capability manifests disagree: {results}")
    total, executable, source_hash = results[0]
    print(json.dumps({"ok": True, "managed_novel_actions": len(novel_actions), "total": total, "executable": executable, "source_hash": source_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
