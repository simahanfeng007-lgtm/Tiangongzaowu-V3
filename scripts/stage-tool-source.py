"""Stage or verify a pinned Tool Source package without publication/activation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from total_gateway.tool_source_bundle import stage_tool_source_bundle, verify_staged_tool_source_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="full ZIP digest from the isolated-build report")
    parser.add_argument("--destination", type=Path, required=True, help="new version directory; no active pointer is changed")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    operation = verify_staged_tool_source_bundle if args.verify_only else stage_tool_source_bundle
    try:
        result = operation(args.bundle.absolute(), expected_sha256=args.sha256,
                           staging_root=args.destination.absolute())
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        print(json.dumps({"status": "SOURCE_STAGE_REJECTED", "error": str(exc),
                          "may_publish": False, "may_authorize": False, "may_execute": False}))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
