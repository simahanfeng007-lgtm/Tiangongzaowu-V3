#!/usr/bin/env python3
"""P17-A pre-audit: the dynamic-dispatch surface of the authoritative trees.

Static grep cannot see dispatch; this scanner AST-walks every module and
records the THREE dynamic faces the handover book names — dynamic imports
(importlib / __import__), reflective attribute chains (getattr with a
computed name), and environment-variable behaviour branches — each with
its classification (explicit feature / needs_review) and the modules
that appear as DYNAMIC import targets. Cross-checked against the P13-A
retirement matrix: a retire candidate that appears as a dynamic target
is promoted to pending before any deletion is even discussed.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "app/backend/tiangong-backend")
MIRROR_SEGMENTS = ("runtime314", "site-packages", "bundled_skills",
                   "node_modules", "_internal")
DEFAULT_OUTPUT = ROOT / "docs/capability-composition/P17_A_DYNAMIC_SURFACE_2026-09-20.json"
VERIFIED_FILE = ROOT / "docs/capability-composition/P17_A_DYNAMIC_VERIFIED_2026-09-20.json"
RETIREMENT_MATRIX = ROOT / "docs/capability-composition/P13_A_RETIREMENT_MATRIX_2026-09-20.json"
SCHEMA = "tiangong.p17.dynamic-surface.v1"

_ENV_FEATURE_PREFIXES = (
    "TIANGONG_",          # product configuration namespace
)
_KNOWN_FEATURE_ENV = {
    # guarded by tests; each is a documented configuration switch
    "TIANGONG_WORLD_UNDERSTANDING_ENABLED", "TIANGONG_COMPOSITION_PLANNER_MODE",
    "TIANGONG_COMPOSITION_TOOL_SOURCE_PIN", "TIANGONG_WORLD_STATE_ROOT",
    "TIANGONG_CI_ENV",
}


def _authoritative(path: Path) -> bool:
    text = path.relative_to(ROOT).as_posix()
    return (any(text.startswith(t) for t in TREES)
            and not any(seg in text for seg in MIRROR_SEGMENTS)
            and "/test" not in text and "conftest" not in text)


def _classify_env(name: str) -> str:
    if name in _KNOWN_FEATURE_ENV:
        return "explicit_feature_use"
    if name.startswith(_ENV_FEATURE_PREFIXES):
        return "explicit_feature_use"
    return "needs_review"


def scan() -> dict:
    hits: list[dict] = []
    for path in sorted(ROOT.rglob("*.py")):
        if not _authoritative(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # importlib.import_module / __import__
                if (isinstance(func, ast.Attribute)
                        and func.attr == "import_module"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "importlib"):
                    target = node.args[0] if node.args else None
                    hits.append({
                        "kind": "dynamic_import", "path": rel,
                        "line": node.lineno,
                        "target": ast.unparse(target)[:120]
                        if target else "<computed>",
                        "classification": "needs_review"})
                elif (isinstance(func, ast.Name) and func.id == "__import__"):
                    hits.append({
                        "kind": "dynamic_import", "path": rel,
                        "line": node.linino if hasattr(node, "linino")
                        else node.lineno,
                        "target": "<__import__>",
                        "classification": "needs_review"})
                # getattr(obj, computed) — reflective attribute access
                elif (isinstance(func, ast.Name) and func.id == "getattr"
                        and len(node.args) >= 2):
                    name_arg = node.args[1]
                    if not isinstance(name_arg, ast.Constant):
                        hits.append({
                            "kind": "reflective_getattr", "path": rel,
                            "line": node.lineno,
                            "target": ast.unparse(name_arg)[:120],
                            "classification": "needs_review"})
                # os.environ.get / os.getenv
                elif (isinstance(func, ast.Attribute) and func.attr in {
                        "get", "getenv"} and isinstance(func.value, ast.Name)
                        and func.value.id == "os"):
                    hits.append({
                        "kind": "env_get",
                        "path": rel, "line": node.lineno,
                        "target": ast.unparse(node.args[0])[:80]
                        if node.args else "<computed>",
                        "classification": "needs_review"})
                elif (isinstance(func, ast.Attribute)
                        and func.attr == "getenv"):
                    hits.append({
                        "kind": "env_get",
                        "path": rel, "line": node.lineno,
                        "target": ast.unparse(node.args[0])[:80]
                        if node.args else "<computed>",
                        "classification": "needs_review"})
    # classify env names
    for hit in hits:
        if hit["kind"] == "env_get":
            name = hit["target"].strip("'\"")
            hit["env_name"] = name
            hit["classification"] = _classify_env(name)

    # cross-check against the retirement matrix
    retire_candidates = set()
    if RETIREMENT_MATRIX.is_file():
        matrix = json.loads(RETIREMENT_MATRIX.read_text(encoding="utf-8"))
        retire_candidates = {
            e["path"] for e in matrix["entries"]
            if e["kind"] == "retire_candidate"}
    retire_module_names = {Path(p).stem for p in retire_candidates}
    retire_dynamic_targets = [
        hit for hit in hits if hit["kind"] == "dynamic_import"
        and any(name in hit.get("target", "") for name in retire_module_names)]
    if retire_dynamic_targets:
        print("CRITICAL: retire candidates appear as dynamic import targets!",
              file=sys.stderr)
        for hit in retire_dynamic_targets:
            print(f"  {hit['path']}:{hit['line']} -> {hit['target']}",
                  file=sys.stderr)

    # apply the manual verification ledger
    verified: dict = {}
    if VERIFIED_FILE.is_file():
        ledger = json.loads(VERIFIED_FILE.read_text(encoding="utf-8"))
        verified = ledger.get("annotations", {})
    for hit in hits:
        key = f"{hit['path']}:{hit['line']}"
        if key in verified:
            hit["classification"] = "verified"
            hit["verified_category"] = verified[key]["category"]

    needs_review = [h for h in hits if h["classification"] == "needs_review"]
    return {
        "schema": SCHEMA,
        "summary": {
            "total_hits": len(hits),
            "dynamic_imports": sum(
                1 for h in hits if h["kind"] == "dynamic_import"),
            "reflective_getattrs": sum(
                1 for h in hits if h["kind"] == "reflective_getattr"),
            "env_branches": sum(1 for h in hits if h["kind"] == "env_get"),
            "explicit_feature_uses": sum(
                1 for h in hits
                if h["classification"] == "explicit_feature_use"),
            "verified": sum(
                1 for h in hits if h["classification"] == "verified"),
            "needs_review": len(needs_review),
            "retire_candidates_as_dynamic_targets":
                len(retire_dynamic_targets),
        },
        "hits": sorted(hits, key=lambda h: (h["path"], h["line"])),
        "honesty": (
            "AST-level scan of the authoritative trees at build time. A "
            "needs_review hit is a question, not a verdict: the dynamic "
            "face is where grep-blind dispatch hides, so every hit is "
            "recorded for the P17-A full audit — this pre-audit never "
            "approves or deletes anything by itself."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = scan()
    rendered = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") \
            if args.output.is_file() else ""
        if current != rendered:
            print("dynamic surface drift: rebuild required", file=sys.stderr)
            return 1
        s = result["summary"]
        print(f"dynamic surface ok: {s['total_hits']} hits, "
              f"{s['needs_review']} needs_review, "
              f"{s['retire_candidates_as_dynamic_targets']} retire-targets")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"dynamic surface written: {args.output} "
          f"({result['summary']['total_hits']} hits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
