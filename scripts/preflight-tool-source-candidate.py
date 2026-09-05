"""Read-only P8 core-policy/candidate preflight; never build, publish or authorize.

An explicit core-maintenance revision may register the existing path observer.
It must change only that one policy row and retain every original source blob.
The subsequent candidate must inherit that policy without changing it. Existing
Git candidate inspection remains the classifier/ancestry authority; this CLI
adds rejection checks for this specific offline maintenance work order only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from total_gateway.tool_source_candidate import (  # noqa: E402
    SourceCandidateError, _commit, _git, _source_policy, _tree,
    inspect_tool_source_candidate,
)

PATH_SOURCE = "src/runtime_security/path_identity.py"
PATH_MAPPING = {
    "id": "existing-path-security", "source": PATH_SOURCE,
    "source_role": "authoritative", "targets": [],
    "note": "P8-R3 core-maintenance declaration of the existing shared path observer. "
            "Exact file only; no new runtime, registry, editable root or generated target. "
            "Tool Source candidates cannot amend this policy.",
}


def preflight(repository: Path, *, original_base: str, policy_base: str, candidate: str) -> dict:
    """Check native immutable Git objects, not the working tree or a new Registry."""
    if not repository.is_absolute() or not repository.is_dir() or repository.is_symlink():
        raise SourceCandidateError("preflight repository is missing or unsafe")
    for oid in (original_base, policy_base, candidate):
        _commit(repository, oid)
    if original_base == policy_base or _git(
        repository, "merge-base", original_base, policy_base,
    ).decode("ascii").strip() != original_base:
        raise SourceCandidateError("core policy baseline must descend from its distinct original")
    original_tree, policy_tree = _tree(repository, original_base), _tree(repository, policy_base)
    changed = sorted(path for path in original_tree.keys() | policy_tree.keys()
                     if original_tree.get(path) != policy_tree.get(path))
    if changed != ["source-ownership.json"]:
        raise SourceCandidateError("core policy baseline must change only source-ownership.json")
    if PATH_SOURCE not in original_tree:
        raise SourceCandidateError("core policy cannot claim a previously absent path observer")
    before, before_hash = _source_policy(repository, original_tree)
    after, after_hash = _source_policy(repository, policy_tree)
    rows = after["mappings"]
    registered = [row for row in rows if row["id"] == PATH_MAPPING["id"]]
    if registered != [PATH_MAPPING]:
        raise SourceCandidateError("core policy must register exactly the existing path observer")
    restored = {**after, "mappings": [row for row in rows if row["id"] != PATH_MAPPING["id"]]}
    if restored != before:
        raise SourceCandidateError("core policy changed another authority, root or boundary")
    observed = inspect_tool_source_candidate(
        repository, base_commit=policy_base, candidate_commit=candidate,
        requested_action_ids=("file.read",),
    )
    helper = [row for row in observed.changes if row.path == PATH_SOURCE]
    if (len(helper) != 1 or helper[0].role != "SOURCE"
            or helper[0].authority_id != PATH_MAPPING["id"]
            or helper[0].before is None or helper[0].after is None
            or helper[0].before.git_oid != original_tree[PATH_SOURCE][1]
            or helper[0].before.git_oid == helper[0].after.git_oid):
        raise SourceCandidateError("candidate must expose the original-to-repaired observer change")
    return {
        "schema": "tiangong.p8-core-policy-preflight.v1",
        "status": "SOURCE_CANDIDATE_OBSERVED", "original_base": original_base,
        "policy_base": policy_base, "candidate_commit": candidate,
        "original_ownership_sha256": before_hash, "ownership_sha256": after_hash,
        "core_policy_changed_paths": changed, "candidate": asdict(observed),
        "may_publish": False, "may_authorize": False, "may_execute": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--original-base", required=True)
    parser.add_argument("--policy-base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error("report must be a new file")
    try:
        report = preflight(args.repository.absolute(), original_base=args.original_base,
                           policy_base=args.policy_base, candidate=args.candidate)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        report = {"schema": "tiangong.p8-core-policy-preflight.v1", "status": "PREFLIGHT_REJECTED",
                  "original_base": args.original_base, "policy_base": args.policy_base,
                  "candidate_commit": args.candidate, "error_type": type(exc).__name__,
                  "error": str(exc), "may_publish": False, "may_authorize": False, "may_execute": False}
    with args.report.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.report.absolute()),
                      "report_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
                      "may_publish": False}))
    return 0 if report["status"] == "SOURCE_CANDIDATE_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
