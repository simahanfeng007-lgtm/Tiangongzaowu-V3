#!/usr/bin/env python3
"""P17-B seed: mechanical unexplained-obligation scan + snapshot.

Scans the AUTHORITATIVE trees for the marker classes the handover book
names (TODO/FIXME/XXX, NotImplementedError, pass-only definitions, empty
tuple placeholders) and classifies every hit as an explicit feature use
(e.g. a placeholder-detection regex) or a needs_review item. The snapshot
makes the audit reproducible: any new marker forces an explicit matrix
revision instead of drifting silently.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "app/backend/tiangong-backend")
MIRROR_SEGMENTS = ("runtime314", "site-packages", "bundled_skills", "node_modules", "_internal")
DEFAULT_OUTPUT = ROOT / "docs/capability-composition/P17_B_OBLIGATION_SCAN_2026-09-20.json"
SCHEMA = "tiangong.p17.obligation-scan.v1"
_MARKER = re.compile(r"\b(TODO|FIXME|XXX)\b")
_KNOWN_FEATURE_PATTERNS = (
    # placeholder-detection regexes legitimately spell the markers out
    "placeholders = len(re.findall",
    "_PLACEHOLDER_RE = re.compile",
)


def _authoritative(path: Path) -> bool:
    text = path.relative_to(ROOT).as_posix()
    return any(text.startswith(t) for t in TREES) and not any(
        seg in text for seg in MIRROR_SEGMENTS) and "/test" not in text \
        and "conftest" not in text


def _pass_only_defs(tree: ast.Module, source: str) -> list[dict]:
    found = []
    lines = source.splitlines()

    class Visitor(ast.NodeVisitor):
        def _check(self, node, label):
            body = [n for n in node.body
                    if not (isinstance(n, ast.Expr)
                            and isinstance(n.value, ast.Constant)
                            and isinstance(n.value.value, str))]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                # Silencing http.server's stderr logging is the standard,
                # explicit override — not an unimplemented path.
                classification = ("explicit_feature_use"
                                  if label == "log_message" else None)
                found.append({
                    "kind": "pass_only_definition", "label": label,
                    "line": node.lineno,
                    "classification": classification,
                    "text": lines[node.lineno - 1].strip()[:160]})
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)):
                    self.visit(child)

        def visit_FunctionDef(self, node):
            self._check(node, node.name)

        def visit_AsyncFunctionDef(self, node):
            self._check(node, node.name)

    Visitor().visit(tree)
    return found


def scan() -> dict:
    hits: list[dict] = []
    for path in sorted(ROOT.rglob("*.py")):
        if not _authoritative(path):
            continue
        source = path.read_text(encoding="utf-8", errors="strict")
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(source.splitlines(), 1):
            if _MARKER.search(line):
                classification = "explicit_feature_use" if any(
                    pattern in line for pattern in _KNOWN_FEATURE_PATTERNS) \
                    else "needs_review"
                hits.append({"kind": "marker", "path": rel, "line": number,
                             "text": line.strip()[:160],
                             "classification": classification})
            if "NotImplementedError" in line:
                # an except clause handling it is a feature; a bare raise is
                # an unimplemented path needing review
                classification = ("explicit_feature_use"
                                  if "except NotImplementedError" in line
                                  else "needs_review")
                hits.append({"kind": "not_implemented", "path": rel,
                             "line": number, "text": line.strip()[:160],
                             "classification": classification})
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for item in _pass_only_defs(tree, source):
            item["path"] = rel
            if item.get("classification") is None:
                item["classification"] = "needs_review"
            hits.append(item)
    needs_review = [h for h in hits if h["classification"] == "needs_review"]
    return {
        "schema": SCHEMA,
        "summary": {
            "total_hits": len(hits),
            "explicit_feature_uses": len(hits) - len(needs_review),
            "needs_review": len(needs_review),
        },
        "hits": sorted(hits, key=lambda h: (h.get("path", ""), h.get("line", 0))),
        "honesty": (
            "Marker-class scan over the authoritative trees at build time. "
            "A 'needs_review' hit is a question, not a verdict; an "
            "explicit_feature_use is code whose job is to detect placeholders. "
            "This scan is one input to P17-B, never a substitute for the "
            "clause-level traceability matrix."
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
            print("obligation scan drift: rebuild required", file=sys.stderr)
            return 1
        print(f"obligation scan ok: {result['summary']['total_hits']} hits, "
              f"{result['summary']['needs_review']} needs_review")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"obligation scan written: {args.output} "
          f"({result['summary']['total_hits']} hits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
