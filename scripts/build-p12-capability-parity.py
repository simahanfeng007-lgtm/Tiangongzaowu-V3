#!/usr/bin/env python3
"""Build the P12-R1E legacy-skill capability parity table.

Re-enumerates the CURRENT static skill index and deliverable documents (no
hard-coded count), joins every required-action field against the CURRENT
generated capability manifest, and emits a versioned parity table.

Honesty contract: ``action_surface_covered`` is a mechanical set comparison
only. It does NOT prove behaviour parity. Every item stays
``REQUIRES_TASK_PARITY`` until the formal P11 task matrix (R1G) proves the
composition chain completes the legacy task; this script never upgrades that
status and never fabricates evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "src/omni_body_skill/registry/skill_router_index.json"
MANIFEST_PATH = ROOT / "src/omni_body_skill/registry/capability_manifest.generated.json"
SKILLS_ROOT = ROOT / "src/omni_body_skill"
DEFAULT_OUTPUT = ROOT / "docs/capability-composition/P12_R1E_CAPABILITY_PARITY_2026-09-20.json"

ACTION_FIELDS = (
    "starter_actions", "production_actions", "quality_gates",
    "repair_actions", "final_actions",
)
RETAINED_SHARED_ACTIONS = ("skill.step.check", "skill.progress.report")
_WRITE_EFFECTS = {"write", "create", "update", "execute", "send"}
SCHEMA = "tiangong.p12.capability-parity.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob_id(path: Path) -> str:
    """Return the committed blob SHA-1 for this path from the HEAD tree.

    Working-tree bytes drift across platforms (a Windows runner's git may
    even smudge ``git show`` output through core.autocrlf) and a runner may
    refresh the index, but the HEAD tree blob is the repository's own
    content address and is stable by construction everywhere.
    """

    relative = path.resolve().relative_to(ROOT).as_posix()
    blob = subprocess.run(
        ["git", "rev-parse", f"HEAD:{relative}"], cwd=ROOT,
        capture_output=True, check=True, text=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", blob):
        raise SystemExit(f"parity: no committed blob for {relative}")
    return blob


def _strict(payload_text: str, label: str):
    def _pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SystemExit(f"{label}: duplicate JSON key {key}")
            result[key] = value
        return result

    return json.loads(payload_text, object_pairs_hook=_pairs)


def build(index_path: Path = INDEX_PATH, manifest_path: Path = MANIFEST_PATH,
          skills_root: Path = SKILLS_ROOT) -> dict:
    index_payload = _strict(index_path.read_text(encoding="utf-8"), "index")
    items = index_payload.get("skills")
    if not isinstance(items, list) or not items:
        raise SystemExit("index: skills list is empty or invalid")
    manifest_payload = _strict(manifest_path.read_text(encoding="utf-8"), "manifest")
    capabilities = manifest_payload.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise SystemExit("manifest: capabilities map is empty or invalid")

    seen_ids: set[str] = set()
    parity_items = []
    uncovered_actions: set[str] = set()
    for item in items:
        skill_id = item.get("id")
        if not isinstance(skill_id, str) or not skill_id or skill_id in seen_ids:
            raise SystemExit(f"index: duplicate or invalid skill id {skill_id!r}")
        seen_ids.add(skill_id)
        doc = skills_root / str(item.get("file") or "")
        if not doc.is_file():
            raise SystemExit(f"index: missing document for {skill_id}: {doc}")
        actions: list[str] = []
        for field in ACTION_FIELDS:
            values = item.get(field)
            if values is None:
                values = []
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values):
                raise SystemExit(f"index: invalid {field} for {skill_id}")
            actions.extend(values)
        action_set = sorted(set(actions))
        missing = [a for a in action_set if a not in capabilities]
        uncovered_actions.update(missing)
        effects = sorted({
            str((capabilities[a] or {}).get("effect") or "unknown")
            for a in action_set if a in capabilities
        })
        risks = sorted({
            str((capabilities[a] or {}).get("risk") or "unknown")
            for a in action_set if a in capabilities
        })
        acceptance = item.get("acceptance") or {}
        must_pass = acceptance.get("must_pass") or []
        if not isinstance(must_pass, list):
            must_pass = []
        parity_items.append({
            "id": skill_id,
            "mingcheng": item.get("mingcheng"),
            "category": item.get("category"),
            "file": item.get("file"),
            "file_blob": _git_blob_id(doc),
            "task_intents": list(item.get("taskIntents") or []),
            "deliverables": list(item.get("deliverables") or []),
            "required_actions": action_set,
            "action_surface_covered": not missing,
            "missing_actions": missing,
            "action_effects": effects,
            "action_risk_levels": risks,
            "high_risk_effects": sorted(
                set(effects) & _WRITE_EFFECTS) != [],
            "acceptance_must_pass_count": len(must_pass),
            "task_parity": "REQUIRES_TASK_PARITY",
        })

    retained = []
    for action in RETAINED_SHARED_ACTIONS:
        retained.append({
            "action": action,
            "present_in_current_manifest": action in capabilities,
            "reason": "machine-fact progress / shared security surface; "
                      "retained regardless of planner retirement (R1A)",
        })

    total = len(parity_items)
    covered = sum(1 for entry in parity_items if entry["action_surface_covered"])
    return {
        "schema": SCHEMA,
        "generated_from": {
            "skill_router_index": {
                "path": str(index_path.relative_to(ROOT)),
                "index_blob": _git_blob_id(index_path),
                "item_count": total,
            },
            "capability_manifest": {
                "path": str(manifest_path.relative_to(ROOT)),
                "manifest_blob": _git_blob_id(manifest_path),
                "action_count": len(capabilities),
            },
        },
        "summary": {
            "items_total": total,
            "action_surface_covered_items": covered,
            "action_surface_uncovered_items": total - covered,
            "uncovered_actions": sorted(uncovered_actions),
            "task_parity_open_items": total,
            "high_risk_effect_items": sum(
                1 for entry in parity_items if entry["high_risk_effects"]),
        },
        "retained_shared_actions": retained,
        "items": parity_items,
        "honesty": (
            "action_surface_covered is a mechanical set comparison against the "
            "current generated manifest. It is NOT behavioural parity: every "
            "item remains REQUIRES_TASK_PARITY until the formal P11 matrix "
            "(R1G) proves the composition chain completes the legacy task. "
            "This table never upgrades that status by itself."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed table matches a rebuild")
    args = parser.parse_args()
    table = build()
    rendered = json.dumps(table, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        # Compare parsed structure, not byte layout: the table is generated
        # deterministically, so any semantic drift is caught here while
        # platform text-mode reading cannot fabricate one.
        try:
            committed = json.loads(
                args.output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            committed = None
        if committed != table:
            print("parity table drift: rebuild required", file=sys.stderr)
            if isinstance(committed, dict) and isinstance(table, dict):
                for key in sorted(set(committed) | set(table)):
                    if committed.get(key) != table.get(key):
                        print(f"  differs: {key}", file=sys.stderr)
            return 1
        print(f"parity table ok: {table['summary']['items_total']} items, "
              f"{table['summary']['action_surface_covered_items']} covered")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"parity table written: {args.output} "
          f"({table['summary']['items_total']} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
