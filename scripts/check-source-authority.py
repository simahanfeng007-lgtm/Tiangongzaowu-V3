#!/usr/bin/env python3
"""Validate Tiangong source-authority topology and closed-world source boundaries.

CLI wrapper: the implementation lives in src/source_authority/validator.py
(P19-R2 M3.1) so verifiers can reuse it without executing any code from
the repository under inspection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from source_authority.validator import (  # noqa: E402,F401
    ALLOWED_ROLES,
    BOUNDARY_MODE,
    EXPECTED_SCHEMA,
    INDEPENDENT_ROLES,
    REEXPORT_CONTRACT,
    _validate_reexport_tree,
    load_config,
    validate_source_authority,
)

__all__ = [
    "ALLOWED_ROLES",
    "BOUNDARY_MODE",
    "EXPECTED_SCHEMA",
    "INDEPENDENT_ROLES",
    "REEXPORT_CONTRACT",
    "_validate_reexport_tree",
    "load_config",
    "main",
    "validate_source_authority",
]


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=default_root, help="repository root used for source existence checks")
    parser.add_argument("--config", type=Path, default=None, help="ownership config (default: <repo-root>/source-ownership.json)")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    config_path = args.config.resolve() if args.config is not None else repo_root / "source-ownership.json"
    try:
        config = load_config(config_path)
        errors = validate_source_authority(config, repo_root=repo_root, require_sources=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"[source-authority] FAIL: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"[source-authority] FAIL: {len(errors)} ownership invariant(s) violated", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    mappings = config.get("mappings", [])
    independent = sum(1 for row in mappings if isinstance(row, dict) and row.get("source_role") in INDEPENDENT_ROLES)
    aliases = sum(1 for row in mappings if isinstance(row, dict) and row.get("source_role") == "authoritative_alias")
    target_count = sum(len(row.get("targets", [])) for row in mappings if isinstance(row, dict) and isinstance(row.get("targets", []), list))
    closed_world = sum(1 for row in mappings if isinstance(row, dict) and isinstance(row.get("boundary_policy"), dict) and row["boundary_policy"].get("mode") == BOUNDARY_MODE)
    print(f"[source-authority] PASS: {independent} independent authorities, {aliases} aliases, {target_count} generated targets, {closed_world} closed-world boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
