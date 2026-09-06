"""Prepare an immutable Source publication proposal without granting approval.

Exit 2 retains a valid proposal with outstanding review/evidence obligations;
exit 1 rejects inconsistent input. Neither outcome publishes or executes code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from total_gateway.tool_source_publication import prepare_tool_source_publication  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--action", action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error("report must be a new file")
    try:
        result = prepare_tool_source_publication(
            args.repository.absolute(), bundle_path=args.bundle.absolute(),
            expected_sha256=args.sha256, base_commit=args.base,
            candidate_commit=args.candidate, requested_action_ids=tuple(sorted(args.action)),
        )
        code = 2
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        result = {"status": "SOURCE_PUBLICATION_REJECTED", "error": str(exc),
                  "may_publish": False, "may_authorize": False, "may_execute": False}
        code = 1
    with args.report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "report": str(args.report.absolute()),
                      "may_publish": False}, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
