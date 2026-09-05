"""Read-only P8 source/manifest differential at exact Git commit identities.

Run the installed/trusted reviewer against a candidate repository. Candidate
Python, scripts, hooks and imports never participate in this review. Exit 0
means the report was generated in scope, NOT that Source can be published.
Exit 2 reports collateral Action changes; exit 1 rejects invalid input.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from world_understanding.tool_capability_world.source_candidate import (  # noqa: E402
    inspect_tool_source_candidate,
    read_tool_source_manifests,
)
from total_gateway.tool_manifest_evolution import (  # noqa: E402
    review_manifest_evolution,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--base", required=True, help="full immutable base commit ID")
    parser.add_argument("--candidate", required=True, help="full immutable descendant commit ID")
    parser.add_argument("--action", required=True, action="append", help="intended semantic Action ID; repeat as needed")
    args = parser.parse_args(argv)
    try:
        candidate = inspect_tool_source_candidate(
            args.repository.absolute(), base_commit=args.base, candidate_commit=args.candidate,
            requested_action_ids=tuple(sorted(args.action)),
        )
        before, after = read_tool_source_manifests(args.repository.absolute(), candidate)
        review = review_manifest_evolution(before, after, requested_action_ids=candidate.requested_action_ids)
    except (ValueError, OSError) as exc:
        print(json.dumps({"report_created": False, "error": str(exc), "may_publish": False},
                         ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps({
        "schema": "tiangong.tool-source-differential-report.v1",
        "report_created": True,
        "source_candidate": asdict(candidate),
        "manifest_review": asdict(review),
        "evidence_scope": "IMMUTABLE_SOURCE_AND_COMMITTED_MANIFEST_DIFFERENTIAL_ONLY",
        "source_compilation_verified": False,
        "static_checks_verified": False,
        "sandbox_verified": False,
        "evidence_contract_tests_verified": False,
        "review_approval_verified": False,
        "may_publish": False,
        "may_authorize": False,
        "may_execute": False,
    }, ensure_ascii=True, sort_keys=True, indent=2))
    return 2 if review.unexpected_action_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
