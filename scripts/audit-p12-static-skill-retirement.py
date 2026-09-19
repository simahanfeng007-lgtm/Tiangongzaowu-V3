"""Read-only P12 R0 inventory; never authorizes cutover or executes product code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ALLOWED_ADDITIONS = frozenset({
    "scripts/audit-p12-static-skill-retirement.py",
    "tests/test_p12_retirement_preflight.py",
    ".github/workflows/p12-retirement-preflight.yml",
    "docs/capability-composition/P12_R0_PREFLIGHT_2026-09-17.md",
})
ROOTS = ("src/", "readable-python-source/", "app/", "tests/", "scripts/")
TEXT_SUFFIXES = {".py", ".json", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                 ".yaml", ".yml", ".toml", ".md", ".txt"}
PATTERNS = {
    "deliverable_planning": r"deliverable_skills|SkillSelectionRecord|SkillCatalog|load_filesystem_skill_catalog",
    "router_index": r"skill_router_index",
    "static_query": r"(?<![\w.])skill\.(?:route|list|get|read)(?![\w.])",
    "context_injection": r"_simple_chain_explicit_skill_context|_learned_skill_context|TIANGONG_ENABLE_LEARNED_SKILL_CONTEXT|\bskill_context\b",
    "full_skill_publication": r"life\.learning\.legacy_publication_frozen|learning\.publication_frozen|publish_draft|compile_learning_artifact|SKILL\.md",
}
REQUIRED_ROOTS = ("src/omni_body_skill/", "src/total_gateway/")
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_MATCHES = 100


class AuditError(ValueError):
    """Invalid/incomplete snapshot, not a production failure or acceptance."""


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("Git inspection unavailable or timed out") from exc
    if result.returncode:
        raise AuditError(f"Git inspection failed ({args[0]}, exit {result.returncode})")
    return result.stdout


def validate_sha(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise AuditError("Expected an exact lowercase 40-character Git SHA")
    return value


def snapshot(repo: Path, head: str) -> list[dict]:
    raw = git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", head)
    rows = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path_bytes = record.split(b"\t", 1)
        mode, kind, oid, size = header.decode("ascii").split()
        path = path_bytes.decode("utf-8", errors="strict")
        rows.append({"path": path, "mode": mode, "kind": kind, "oid": oid,
                     "size": None if size == "-" else int(size)})
    if not rows:
        raise AuditError("Empty Git snapshot is not a retirement inventory")
    for root in REQUIRED_ROOTS:
        if not any(row["path"].startswith(root) and row["mode"] in {"100644", "100755"}
                   for row in rows):
            raise AuditError(f"Required preserved component is absent: {root}")
    return rows


def read_blobs(repo: Path, rows: list[dict]) -> dict[str, bytes]:
    sizes = {row["oid"]: row["size"] for row in rows}
    if sum(sizes.values()) > MAX_TOTAL:
        raise AuditError("Snapshot exceeds bounded text-input budget")
    if not sizes:
        raise AuditError("No scannable text objects; absence is not zero usage")
    ids = sorted(sizes)
    raw = git(repo, "cat-file", "--batch", data="".join(oid + "\n" for oid in ids).encode("ascii"))
    offset = 0
    blobs = {}
    for expected in ids:
        end = raw.find(b"\n", offset)
        if end < 0:
            raise AuditError("Truncated native Git object header")
        parts = raw[offset:end].decode("ascii").split()
        if parts != [expected, "blob", str(sizes[expected])]:
            raise AuditError("Native Git object identity/type/size mismatch")
        start = end + 1
        end = start + sizes[expected]
        content = raw[start:end]
        if len(content) != sizes[expected] or raw[end:end + 1] != b"\n":
            raise AuditError("Truncated native Git object payload")
        if hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() != expected:
            raise AuditError("Native Git blob digest mismatch")
        blobs[expected] = content
        offset = end + 1
    if offset != len(raw):
        raise AuditError("Unexpected bytes after native Git objects")
    return blobs


def changes(repo: Path, baseline: str, head: str) -> list[dict]:
    fields = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                 "--name-status", "-z", baseline, head, "--").split(b"\0")
    if fields[-1] != b"" or (len(fields) - 1) % 2:
        raise AuditError("Malformed Git change inventory")
    return [{"status": fields[i].decode("ascii"), "path": fields[i + 1].decode("utf-8")}
            for i in range(0, len(fields) - 1, 2)]


def audit(repo: Path, head: str, baseline: str) -> dict:
    validate_sha(head)
    validate_sha(baseline)
    actual = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if actual != head:
        raise AuditError("HEAD differs from independently supplied expected commit")
    git(repo, "merge-base", "--is-ancestor", baseline, head)
    # Do not claim that a dirty observer or tracked worktree is the frozen candidate.
    git(repo, "diff", "--no-ext-diff", "--no-textconv", "--quiet", "HEAD", "--")
    rows = snapshot(repo, head)
    inputs, unscanned = [], []
    for row in rows:
        if not row["path"].startswith(ROOTS):
            continue
        reason = None
        if row["mode"] not in {"100644", "100755"} or row["kind"] != "blob":
            reason = "non_regular_git_entry_not_followed"
        elif Path(row["path"]).suffix.lower() not in TEXT_SUFFIXES:
            reason = "non_text_extension"
        elif row["size"] > MAX_FILE:
            reason = "file_size_limit"
        if reason:
            unscanned.append({**row, "reason": reason})
        else:
            inputs.append(row)
    blobs = read_blobs(repo, inputs)
    own_path = "scripts/audit-p12-static-skill-retirement.py"
    own = next((row for row in inputs if row["path"] == own_path), None)
    observer = Path(__file__).read_bytes()
    if own is None or blobs[own["oid"]] != observer:
        raise AuditError("Observer bytes are not the committed candidate's audit script")
    findings = {name: [] for name in PATTERNS}
    scanned = []
    for row in inputs:
        content = blobs[row["oid"]]
        digest = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            unscanned.append({**row, "sha256": digest, "reason": "not_utf8"})
            continue
        scanned.append({**row, "sha256": digest})
        role = "test_reference" if row["path"].startswith("tests/") else (
            "development_script" if row["path"].startswith("scripts/") else "product_or_mirror_reference")
        for name, pattern in PATTERNS.items():
            matches = []
            if re.search(pattern, row["path"]):
                matches.append({"line": 0, "token": "path_match"})
            for number, line in enumerate(text.splitlines(), 1):
                matches.extend({"line": number, "token": found.group(0)}
                               for found in re.finditer(pattern, line))
            if matches:
                findings[name].append({"path": row["path"], "role": role, "sha256": digest,
                                       "match_count": len(matches), "matches": matches[:MAX_MATCHES],
                                       "matches_truncated": len(matches) > MAX_MATCHES})
    delta = changes(repo, baseline, head)
    prohibited = [row for row in delta if row["status"] != "A" or row["path"] not in ALLOWED_ADDITIONS]
    result = {
        "schema": "tiangong.p12.retirement-preflight.v1", "head": head, "baseline_head": baseline,
        "tree": git(repo, "rev-parse", head + "^{tree}").decode().strip(),
        "observer_sha256": hashlib.sha256(observer).hexdigest(),
        "evidence_mode": "STATIC_GIT_INVENTORY", "r0_scope_passed": not prohibited,
        "p11_exit_proven": False, "p12_authorized": False, "retirement_authorized": False,
        "production_zero_usage_proven": False, "production_behavior_changed_by_audit": False,
        "scanned_inputs": scanned, "unscanned_inputs": unscanned,
        "findings": findings, "changes": delta, "prohibited_changes": prohibited,
        "limitations": ["Lexical references are not execution telemetry or a complete call graph.",
                        "Mirrors and authoritative sources are not reclassified by this audit.",
                        "Reflection, external plugins, binary content and live state remain unverified.",
                        "Zero textual matches never proves zero production use.",
                        "P11 live evidence, independent review and merge remain separate prerequisites."],
    }
    result["report_sha256"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                                          separators=(",", ":")).encode("utf-8")).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--baseline-head", required=True)
    args = parser.parse_args()
    try:
        report = audit(args.repo, args.expected_head, args.baseline_head)
    except (AuditError, UnicodeError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "retirement_authorized": False}), file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return 0 if report["r0_scope_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
