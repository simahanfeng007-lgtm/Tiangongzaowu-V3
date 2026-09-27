"""Derive the complete capability/connection worklist from existing authorities.

No implementation, connection, task acceptance or semantic review is inferred
from an action name. Pending requirements remain visible after consolidation.
This file generates documentation only; it is never a runtime authority.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/ontology/capability-coverage.json"


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build():
    catalog = read("dictionaries/tools/catalog.json")
    schemas = read("dictionaries/tools/schemas.json")
    apps = read("dictionaries/tools/apps.json")["apps"]
    tools = catalog["tools"]
    source_roots = [ROOT / "src/omni_body_skill", ROOT / "app/backend/tiangong-backend/v3"]
    refs = {name: set() for name in tools}
    methods = {}
    for base in source_roots:
        for path in sorted(base.rglob("*.py")):
            if "bundled_skills" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            relative = path.relative_to(ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in refs:
                    refs[node.value].add(f"{relative}:{node.lineno}")
                if relative.endswith("/tools/omni_body_tool.py") and isinstance(node, ast.FunctionDef):
                    methods[node.name] = f"{relative}:{node.lineno}"
    rows = []
    for name, row in sorted(tools.items()):
        runtime, binding = row["runtime"], row["binding"]
        canonical = name
        lineage = []
        while tools[canonical]["binding"]["kind"] == "alias":
            lineage.append(canonical)
            canonical = tools[canonical]["binding"]["target"]
            if canonical in lineage:
                raise ValueError("alias_cycle:" + name)
        associations = [a["app_id"] for a in apps if name in a["actions"]]
        state = runtime.get("status", "active")
        if state == "retired_fixed_skill":
            disposition, reason = "retire", "Fixed business Skill entry is retired; preserve historical identity."
        elif state == "disabled":
            disposition, reason = "retain_disabled", "Explicit consent/backend conditions are not fulfilled by registration."
        elif binding["kind"] == "alias":
            disposition, reason = "merge_explicit_alias", "Delegate to the declared canonical action with visible defaults; verify equivalence in the supported scope."
        elif not runtime.get("implemented"):
            disposition, reason = "retain_pending_connection", "Requirement retained. Native binding, exact MCP operation and target readback must be verified before claiming execution."
        else:
            disposition, reason = "retain_with_contract", "Existing execution binding retained; task acceptance and optimal atomic granularity require evidence beyond source inspection."
        if name == "browser.chrome.click":
            reason = "One real click and subsequent observation in an isolated page; no persistent-session or rollback claim."
        elif name in {"browser.chrome.goto", "browser.chrome.open", "browser.open", "browser.chrome.extract_dom", "browser.chrome.extract_text"}:
            reason = "Retained static fetch/parse operation; explicitly excludes JavaScript and live browser state."
        elif name == "qc.docx.delivery_check":
            reason = "Actual content observation or explicit managed-document structural checks; no generic quality score."
        rows.append({
            "action": name, "definition_sha256": digest(row), "disposition": disposition, "reason": reason,
            "implementation_declared": bool(runtime.get("implemented")), "lifecycle": state,
            "canonical_action": canonical, "default_args": runtime.get("default_args", {}),
            "binding": binding, "binding_source": methods.get(binding["target"]),
            "runtime_literal_references": sorted(refs[name]), "applications": associations,
            "contract_entries": {kind: name in schemas[kind] for kind in ("arguments", "results", "values")},
            "effect": row["effect"], "retry": row["retry"], "summary": row["summary"],
            "required_dependencies": row["required_dependencies"], "adapter_label": runtime.get("adapter"),
            "connection_discovery": [{"action": "mcp.servers.list", "args": {"app_id": app}} for app in associations],
            "review_scope": "source_definition_and_binding_review",
            "semantic_atomicity_review": "explicit_change_review" if row.get("version") == "2.0.0" else "not_claimed_complete",
            "live_evidence": "separate_candidate_bound_evidence_required",
        })
    return {"schema": "tiangong.ontology.coverage.v1", "dictionary_version": catalog["version"],
            "catalog_sha256": digest(catalog), "schemas_sha256": digest(schemas), "applications_sha256": digest(apps),
            "registered_actions": len(rows), "application_count": len(apps),
            "counts": dict(sorted(Counter(r["disposition"] for r in rows).items())),
            "interpretation": "Complete inventory is not complete semantic audit, real execution coverage or application connection proof.",
            "actions": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = build()
    if args.check:
        if not OUTPUT.is_file() or json.loads(OUTPUT.read_text(encoding="utf-8")) != value:
            raise SystemExit("ontology coverage is stale; run scripts/build-ontology-coverage.py")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: value[k] for k in ("dictionary_version", "registered_actions", "application_count", "counts")}, ensure_ascii=False))
