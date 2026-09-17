"""P12 R1A: frozen-source consumer map; no runtime imports or retirement authority."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import types

R0_PATH = "scripts/audit-p12-static-skill-retirement.py"
R0_SHA256 = "8364de2974eddeec7f31a6804dcd37494f7caf86f602ef207fc7d76f7b6b0465"
SELF_PATH = "scripts/audit-p12-retirement-map.py"
WORKFLOW = ".github/workflows/p12-retirement-preflight.yml"
ADDITIONS = frozenset({SELF_PATH, "tests/test_p12_retirement_map.py",
                      "docs/capability-composition/P12_R1A_CONSUMER_MAP_2026-09-17.md"})
PREFIXES = ("app/backend/tiangong-backend/", "app/life-service/runtime314/",
            "readable-python-source/", "src/")
SKILL_ROOT = "src/omni_body_skill/"
INDEX_PATH = SKILL_ROOT + "registry/skill_router_index.json"
MAX_REFERENCES = 250

# These are review anchors, not a second action registry or a deletion allowlist.
# (path, top-level definition or None for a whole file, disposition)
ANCHORS = {
    "static.catalog": ("src/total_gateway/skill_selection.py", "SkillCatalog", "RETIRE_CANDIDATE"),
    "static.selector": ("src/total_gateway/skill_selection.py", "SkillSelectionService", "RETIRE_CANDIDATE"),
    "static.loader": ("src/total_gateway/skill_selection.py", "load_filesystem_skill_catalog", "RETIRE_CANDIDATE"),
    "shared.model_manifest": ("src/total_gateway/skill_selection.py", "load_model_capability_manifest", "KEEP_DYNAMIC_DEPENDENCY"),
    "shared.execution_manifest": ("src/total_gateway/skill_selection.py", "compile_composition_execution_manifest", "KEEP_DYNAMIC_DEPENDENCY"),
    "context.explicit_hint": ("app/backend/tiangong-backend/v3/simple_chain/kernel.py", "_simple_chain_explicit_skill_context", "REVIEW_NOT_BLIND_DELETE"),
    "context.learned": ("app/backend/tiangong-backend/v3/jineng/http_kehuduan.py", "_learned_skill_context", "RETIRE_CANDIDATE"),
    "dynamic.proposal": ("src/world_understanding/capability_composition/parser.py", "parse_with_single_repair", "REPLACEMENT_CANDIDATE"),
    "dynamic.plan": ("src/world_understanding/capability_composition/compiler.py", "compile_capability_composition_plan", "REPLACEMENT_CANDIDATE"),
    "dynamic.validation": ("src/world_understanding/capability_composition/validator.py", "validate_capability_composition_plan", "REPLACEMENT_CANDIDATE"),
    "dynamic.candidates": ("src/world_understanding/capability_composition/models.py", "build_candidate_snapshot", "REPLACEMENT_CANDIDATE"),
    "dynamic.context_packet": ("src/world_understanding/context_output/capability_context.py", "build_capability_context_packet", "REPLACEMENT_CANDIDATE"),
    "dynamic.context_slot": ("src/world_understanding/context_output/capability_context.py", "build_capability_world_context_slot", "REPLACEMENT_CANDIDATE"),
    "publication.freeze": ("src/life_service/learning_workflow.py", "legacy_publication_blocked", "KEEP_EXISTING_FREEZE"),
    "experience.intent": ("src/world_understanding/capability_composition/capability_experience_api.py", "build_capability_experience_memory_intent", "NON_AUTHORIZING_OUTPUT"),
    "router.module": ("src/omni_body_skill/tools/skill_router.py", None, "MIXED_MODULE_KEEP"),
    "router.index": (INDEX_PATH, None, "CORPUS_KEEP_UNTIL_PARITY"),
}
SURFACES = {
    "deliverable_planning": {"legacy": ["static.catalog", "static.selector", "static.loader"],
        "replacement": ["dynamic.candidates", "dynamic.proposal", "dynamic.plan", "dynamic.validation"],
        "preserve": ["shared.model_manifest", "shared.execution_manifest"],
        "exit_check": "Real task parity, exact Source/Plan identity and P11 independent acceptance."},
    "router_index": {"legacy": ["router.index"], "replacement": ["dynamic.candidates"],
        "preserve": ["router.module"],
        "exit_check": "Map every corpus item and required action to Method/Tool World; retain unresolved items."},
    "static_query": {"legacy": ["static.selector"], "replacement": ["dynamic.candidates", "dynamic.context_packet"],
        "preserve": ["router.module", "shared.execution_manifest"],
        "exit_check": "Review dispatch and direct/reflective callers; no new authority or silent fallback."},
    "context_injection": {"legacy": ["context.explicit_hint", "context.learned"],
        "replacement": ["dynamic.context_packet", "dynamic.context_slot"], "preserve": [],
        "exit_check": "One WORLD_CONTEXT_SLOT, explicit user intent retained, no stale experience execution."},
    "full_skill_publication": {"legacy": ["publication.freeze"], "replacement": ["experience.intent"],
        "preserve": ["publication.freeze"],
        "exit_check": "Retain P10 freeze; separately review Knowledge and P8/P9 Source publication; intent never authorizes."},
}


class MapError(ValueError):
    """Invalid/incomplete development evidence, not a product acceptance result."""


def load_r0():
    path = Path(__file__).with_name(Path(R0_PATH).name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != R0_SHA256:
        raise MapError("The original R0 observer changed; do not inherit its evidence")
    module = types.ModuleType("p12_r0_frozen_observer")
    module.__file__ = str(path)
    # Execute only the hash-pinned developer observer, never a product module.
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MapError("Duplicate JSON key in Skill index")
        result[key] = value
    return result


def module_name(path: str) -> str | None:
    for prefix in PREFIXES:
        if path.startswith(prefix) and path.endswith(".py"):
            parts = list(PurePosixPath(path[len(prefix):-3]).parts)
            if parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts)
    return None


def import_origin(path: str, node: ast.ImportFrom) -> str | None:
    if not node.level:
        return node.module or ""
    name = module_name(path)
    if name is None:
        return None
    package = name.split(".")
    if not path.endswith("/__init__.py"):
        package.pop()
    if node.level > len(package):
        return None
    prefix = package[:len(package) - node.level + 1]
    return ".".join(prefix + ((node.module or "").split(".") if node.module else []))


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else None
    return None


def syntax_references(path: str, tree: ast.Module) -> dict:
    """Conservative syntactic candidates; scope shadowing/re-exports stay unresolved."""
    targets = {(module_name(file), symbol): key for key, (file, symbol, _) in ANCHORS.items() if symbol}
    bindings: dict[str, set[str]] = {}
    module_bindings: dict[str, str] = {}
    refs = []
    dynamic = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            origin = import_origin(path, node)
            for alias in node.names:
                key = targets.get((origin, alias.name))
                if key:
                    bindings.setdefault(alias.asname or alias.name, set()).add(key)
                    refs.append({"line": node.lineno, "anchor": key, "kind": "IMPORT_BINDING"})
                joined = (origin + "." + alias.name) if origin else None
                if joined in {item[0] for item in targets}:
                    module_bindings[alias.asname or alias.name] = joined
                if alias.name == "*":
                    dynamic.append({"line": node.lineno, "kind": "STAR_IMPORT"})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_bindings[alias.asname or alias.name] = alias.name
    own_module = module_name(path)
    for (origin, symbol), key in targets.items():
        if origin == own_module:
            bindings.setdefault(symbol, set()).add(key)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        keys = set(bindings.get(name, ()))
        if name:
            for local, origin in module_bindings.items():
                if name.startswith(local + "."):
                    key = targets.get((origin, name[len(local) + 1:]))
                    if key:
                        keys.add(key)
            if name.split(".")[-1] in {"getattr", "__import__", "import_module", "eval", "exec"}:
                dynamic.append({"line": node.lineno, "kind": "DYNAMIC_SITE_UNRESOLVED"})
        for key in sorted(keys):
            refs.append({"line": node.lineno, "anchor": key, "kind": "CALL_CANDIDATE_SCOPE_UNRESOLVED"})
    refs.sort(key=lambda item: (item["line"], item["anchor"], item["kind"]))
    dynamic.sort(key=lambda item: (item["line"], item["kind"]))
    return {"references": refs[:MAX_REFERENCES], "reference_count": len(refs),
            "references_truncated": len(refs) > MAX_REFERENCES,
            "dynamic_sites": dynamic[:MAX_REFERENCES], "dynamic_site_count": len(dynamic),
            "dynamic_sites_truncated": len(dynamic) > MAX_REFERENCES}


def corpus_rows(texts: dict[str, str], rows: dict[str, dict], baseline: dict[str, dict], strict_pairs) -> list[dict]:
    try:
        data = json.loads(texts[INDEX_PATH], object_pairs_hook=strict_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(MapError("Non-finite index value")))
    except (KeyError, ValueError) as exc:
        raise MapError("Missing or non-strict Skill index") from exc
    skills = data.get("skills") if isinstance(data, dict) else None
    if (not isinstance(skills, list) or not skills or len(skills) > 10_000
        or type(data.get("skill_count")) is not int or data["skill_count"] != len(skills)
        or data.get("schema") != "tiangong.v3.omni_body.skill_router_index.v1"):
        raise MapError("Skill index schema/count is invalid")
    result, seen_ids, seen_paths = [], set(), set()
    for item in skills:
        if not isinstance(item, dict):
            raise MapError("Skill index item is not an object")
        ident, relative = item.get("id"), item.get("file")
        if not isinstance(ident, str) or not ident or ident in seen_ids or not isinstance(relative, str):
            raise MapError("Skill identity is invalid or duplicated")
        path = PurePosixPath(relative)
        if (path.is_absolute() or len(path.parts) != 2 or path.parts[0] != "deliverable_skills"
            or path.suffix != ".md" or ".." in path.parts or "\\" in relative
            or ":" in relative or "\0" in relative or relative != path.as_posix() or relative in seen_paths):
            raise MapError("Skill source path is unsafe or duplicated")
        file = SKILL_ROOT + relative
        current, previous = rows.get(file), baseline.get(file)
        if (current is None or previous is None or current["mode"] not in {"100644", "100755"}
            or previous["mode"] != current["mode"] or current["oid"] != previous["oid"] or file not in texts):
            raise MapError("Skill source is absent, unscanned or changed from the R0 corpus")
        actions = set()
        for field in ("starter_actions", "production_actions", "inspection_actions", "quality_gates", "repair_actions", "final_actions"):
            value = item.get(field, [])
            if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
                raise MapError("Skill required-action field is invalid")
            actions.update(value)
        result.append({"skill_id": ident, "source_path": file,
                       "source_sha256": hashlib.sha256(texts[file].encode("utf-8")).hexdigest(),
                       "required_actions": sorted(actions), "source_preserved": True,
                       "replacement_behavior_proven": False, "migration_status": "REQUIRES_TASK_PARITY"})
        seen_ids.add(ident)
        seen_paths.add(relative)
    return sorted(result, key=lambda item: item["skill_id"])


def build_map(texts: dict[str, str], rows: dict[str, dict], baseline: dict[str, dict], strict_pairs) -> dict:
    definitions, failures, files = {}, [], []
    parsed = 0
    anchor_paths = {row[0] for row in ANCHORS.values()}
    for path, text in sorted(texts.items()):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError, RecursionError):
            failures.append({"path": path, "reason": "AST_PARSE_UNAVAILABLE"})
            continue
        parsed += 1
        if path in anchor_paths:
            definitions[path] = [(n.name, n.lineno, n.end_lineno) for n in tree.body
                                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        detail = syntax_references(path, tree)
        if detail["reference_count"] or detail["dynamic_site_count"]:
            role = "test" if path.startswith("tests/") else "development" if path.startswith("scripts/") else "product_or_mirror"
            files.append({"path": path, "role": role, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), **detail})
        # Only small definition/reference records survive; do not retain every AST.
        del tree
    anchors = {}
    for key, (path, symbol, disposition) in ANCHORS.items():
        if path not in texts or path not in baseline or rows[path]["oid"] != baseline[path]["oid"]:
            raise MapError("Required review anchor missing or changed: " + key)
        line, end = 0, 0
        if symbol:
            nodes = [n for n in definitions.get(path, ()) if n[0] == symbol]
            if len(nodes) != 1:
                raise MapError("Review anchor must resolve to one top-level definition: " + key)
            line, end = nodes[0][1], nodes[0][2]
        anchors[key] = {"path": path, "symbol": symbol, "line": line, "end_line": end,
                        "disposition": disposition, "source_blob": rows[path]["oid"],
                        "source_sha256": hashlib.sha256(texts[path].encode("utf-8")).hexdigest(),
                        "retirement_authorized": False}
    surfaces = {key: {**value, "replacement_wiring_proven": False, "capability_parity_proven": False}
                for key, value in SURFACES.items()}
    for value in surfaces.values():
        if any(key not in anchors for field in ("legacy", "replacement", "preserve") for key in value[field]):
            raise MapError("Surface references an unresolved anchor")
    return {"anchors": anchors, "surfaces": surfaces, "consumer_candidates": files,
            "parsed_python_files": parsed, "unparsed_python_files": failures,
            "skill_migration_matrix": corpus_rows(texts, rows, baseline, strict_pairs),
            "complete_call_graph": False, "full_consumer_review_completed": False}


def audit(repo: Path, head: str, baseline_head: str) -> dict:
    r0 = load_r0()
    inventory = r0.audit(repo, head, baseline_head)
    rows = {row["path"]: row for row in r0.snapshot(repo, head)}
    baseline = {row["path"]: row for row in r0.snapshot(repo, baseline_head)}
    if rows.get(R0_PATH, {}).get("oid") != baseline.get(R0_PATH, {}).get("oid"):
        raise MapError("R0 observer must remain unchanged")
    blobs = r0.read_blobs(repo, inventory["scanned_inputs"])
    if SELF_PATH not in rows or blobs.get(rows[SELF_PATH]["oid"]) != Path(__file__).read_bytes():
        raise MapError("R1 observer is not the committed candidate's bytes")
    texts = {row["path"]: blobs[row["oid"]].decode("utf-8", errors="strict") for row in inventory["scanned_inputs"]}
    prohibited = [item for item in inventory["changes"]
                  if not ((item["path"] in ADDITIONS and item["status"] == "A")
                          or (item["path"] == WORKFLOW and item["status"] == "M"))]
    mapped = build_map(texts, rows, baseline, strict_pairs)
    report = {"schema": "tiangong.p12.retirement-map.v1", "head": head,
              "baseline_head": baseline_head, "tree": inventory["tree"],
              "evidence_mode": "STATIC_CONSUMER_MAP", "mapping_gate_passed": not prohibited,
              "r1_scope_passed": not prohibited, "changes": inventory["changes"], "prohibited_changes": prohibited,
              "source_preservation_proven": not prohibited, "r0_inventory_report_sha256": inventory["report_sha256"],
              "r0_scope_passed_on_r1_delta": inventory["r0_scope_passed"],
              "observer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "p11_exit_proven": False, "p12_authorized": False, "retirement_authorized": False,
              "production_zero_usage_proven": False, "capability_parity_proven": False,
              "unscanned_inputs": inventory["unscanned_inputs"], "lexical_findings": inventory["findings"],
              **mapped,
              "limitations": ["Import/call syntax is not proof of execution; shadowing and re-exports need review.",
                              "Lexical matches, AST failures, reflection, binaries and external plugins remain coverage gaps.",
                              "Source preservation is not behavioral parity of a future replacement.",
                              "This diagnostic neither classifies source ownership nor changes any runtime authority."]}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--baseline-head", required=True)
    args = parser.parse_args()
    try:
        report = audit(args.repo, args.expected_head, args.baseline_head)
    except (ValueError, OSError, UnicodeError, RuntimeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc), "retirement_authorized": False}), file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return 0 if report["mapping_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
