#!/usr/bin/env python3
"""Validate Tiangong source-authority topology.

This guard complements ``sync-generated-sources.py``:
- sync-generated-sources verifies mirror bytes/tree hashes;
- this file verifies that the ownership graph itself cannot describe two
  independent authorities or silently place generated code inside an editable
  source tree.

It is intentionally dependency-free so it can run before package installation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

EXPECTED_SCHEMA = "tiangong.source-ownership.v2"
ALLOWED_ROLES = frozenset(
    {"authoritative", "frozen_authoritative", "authoritative_alias"}
)
INDEPENDENT_ROLES = frozenset({"authoritative", "frozen_authoritative"})


def _normalize_repo_path(value: Any, *, label: str) -> tuple[PurePosixPath | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, f"{label}: path is empty"
    if "\\" in raw:
        return None, f"{label}: use repository '/' separators, not backslashes: {raw!r}"
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None, f"{label}: path must stay inside the repository: {raw!r}"
    normalized = str(path)
    if normalized in {"", "."}:
        return None, f"{label}: path must name a repository entry"
    if normalized != raw:
        return None, f"{label}: path is not canonical: {raw!r} -> {normalized!r}"
    return path, None


def _join_source(source: PurePosixPath, relative: PurePosixPath) -> PurePosixPath:
    return PurePosixPath(*(source.parts + relative.parts))


def _is_at_or_below(child: PurePosixPath, parent: PurePosixPath) -> bool:
    return child == parent or parent in child.parents


def _fs_path(repo_root: Path, path: PurePosixPath) -> Path:
    return repo_root.joinpath(*path.parts)


def _pairs(items: Iterable[tuple[str, PurePosixPath]]):
    rows = list(items)
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            yield left, right


def validate_source_authority(
    config: dict[str, Any],
    *,
    repo_root: Path,
    require_sources: bool = True,
) -> list[str]:
    errors: list[str] = []

    if config.get("schema") != EXPECTED_SCHEMA:
        errors.append(
            f"schema: expected {EXPECTED_SCHEMA!r}, got {config.get('schema')!r}"
        )

    raw_mappings = config.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        return errors + ["mappings: expected a non-empty list"]

    parsed: list[dict[str, Any]] = []
    ids: set[str] = set()
    source_owner_ids: dict[PurePosixPath, str] = {}
    target_owner_ids: dict[PurePosixPath, str] = {}

    for index, raw_mapping in enumerate(raw_mappings):
        prefix = f"mappings[{index}]"
        if not isinstance(raw_mapping, dict):
            errors.append(f"{prefix}: mapping must be an object")
            continue

        mapping_id = str(raw_mapping.get("id") or "").strip()
        if not mapping_id:
            errors.append(f"{prefix}.id: id is empty")
            continue
        if mapping_id in ids:
            errors.append(f"{prefix}.id: duplicate mapping id {mapping_id!r}")
            continue
        ids.add(mapping_id)

        role = str(raw_mapping.get("source_role") or "").strip()
        if role not in ALLOWED_ROLES:
            errors.append(
                f"{mapping_id}.source_role: expected one of "
                f"{sorted(ALLOWED_ROLES)!r}, got {role!r}"
            )

        source, source_error = _normalize_repo_path(
            raw_mapping.get("source"), label=f"{mapping_id}.source"
        )
        if source_error:
            errors.append(source_error)

        raw_targets = raw_mapping.get("targets", [])
        if not isinstance(raw_targets, list):
            errors.append(f"{mapping_id}.targets: expected a list")
            raw_targets = []

        targets: list[PurePosixPath] = []
        for target_index, raw_target in enumerate(raw_targets):
            target, target_error = _normalize_repo_path(
                raw_target, label=f"{mapping_id}.targets[{target_index}]"
            )
            if target_error:
                errors.append(target_error)
                continue
            assert target is not None
            previous = target_owner_ids.get(target)
            if previous:
                errors.append(
                    f"{mapping_id}.targets[{target_index}]: generated target "
                    f"{str(target)!r} is already owned by {previous!r}"
                )
            else:
                target_owner_ids[target] = mapping_id
            targets.append(target)

        exclusions: list[PurePosixPath] = []
        raw_exclusions = raw_mapping.get("generated_exclusions", [])
        if not isinstance(raw_exclusions, list):
            errors.append(f"{mapping_id}.generated_exclusions: expected a list")
            raw_exclusions = []
        seen_exclusions: set[PurePosixPath] = set()
        for exclusion_index, raw_exclusion in enumerate(raw_exclusions):
            exclusion, exclusion_error = _normalize_repo_path(
                raw_exclusion,
                label=f"{mapping_id}.generated_exclusions[{exclusion_index}]",
            )
            if exclusion_error:
                errors.append(exclusion_error)
                continue
            assert exclusion is not None
            if exclusion in seen_exclusions:
                errors.append(
                    f"{mapping_id}.generated_exclusions[{exclusion_index}]: "
                    f"duplicate exclusion {str(exclusion)!r}"
                )
                continue
            seen_exclusions.add(exclusion)
            exclusions.append(exclusion)

        parent_id = str(raw_mapping.get("authority_parent") or "").strip()

        if source is not None:
            previous = source_owner_ids.get(source)
            if previous:
                errors.append(
                    f"{mapping_id}.source: source {str(source)!r} is already "
                    f"declared by {previous!r}"
                )
            else:
                source_owner_ids[source] = mapping_id
            if require_sources and not _fs_path(repo_root, source).exists():
                errors.append(
                    f"{mapping_id}.source: repository source does not exist: "
                    f"{str(source)!r}"
                )

        parsed.append(
            {
                "id": mapping_id,
                "role": role,
                "source": source,
                "targets": targets,
                "exclusions": exclusions,
                "parent_id": parent_id,
            }
        )

    by_id = {row["id"]: row for row in parsed}

    for row in parsed:
        mapping_id = row["id"]
        role = row["role"]
        source = row["source"]
        targets = row["targets"]
        exclusions = row["exclusions"]
        parent_id = row["parent_id"]

        if source is None:
            continue

        if role == "authoritative_alias":
            if targets:
                errors.append(
                    f"{mapping_id}: authoritative_alias must not own generated targets"
                )
            if exclusions:
                errors.append(
                    f"{mapping_id}: authoritative_alias must not define generated_exclusions"
                )
            if not parent_id:
                errors.append(
                    f"{mapping_id}: authoritative_alias requires authority_parent"
                )
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                errors.append(
                    f"{mapping_id}: authority_parent {parent_id!r} does not exist"
                )
                continue
            if parent["role"] not in INDEPENDENT_ROLES or parent["source"] is None:
                errors.append(
                    f"{mapping_id}: authority_parent {parent_id!r} is not an "
                    "independent authority"
                )
                continue
            if not _is_at_or_below(source, parent["source"]):
                errors.append(
                    f"{mapping_id}: alias source {str(source)!r} must be inside "
                    f"parent source {str(parent['source'])!r}"
                )
        elif parent_id:
            errors.append(
                f"{mapping_id}: only authoritative_alias may set authority_parent"
            )

    independent_sources = [
        (row["id"], row["source"])
        for row in parsed
        if row["role"] in INDEPENDENT_ROLES and row["source"] is not None
    ]
    for (left_id, left), (right_id, right) in _pairs(independent_sources):
        if _is_at_or_below(left, right) or _is_at_or_below(right, left):
            errors.append(
                "independent authority overlap: "
                f"{left_id!r}={str(left)!r}, {right_id!r}={str(right)!r}; "
                "declare the narrower entry as authoritative_alias instead"
            )

    generated_targets = list(target_owner_ids.items())
    for (left, left_id), (right, right_id) in _pairs(
        [(f"{owner}:{str(path)}", path) for path, owner in generated_targets]
    ):
        # The first tuple field is diagnostic text; the second is the path.
        if _is_at_or_below(left_id, right_id) or _is_at_or_below(right_id, left_id):
            errors.append(
                "generated target overlap: "
                f"{left!r} conflicts with {right!r}; generated mirrors must not nest"
            )

    # No human-editable source may itself live inside a generated mirror.
    for row in parsed:
        source = row["source"]
        if source is None:
            continue
        for target, target_owner in generated_targets:
            if _is_at_or_below(source, target):
                errors.append(
                    f"{row['id']}.source: {str(source)!r} is inside generated target "
                    f"{str(target)!r} owned by {target_owner!r}"
                )

    # Generated subtrees inside an otherwise editable source tree must be
    # explicitly carved out. This is the critical v3 backend exception.
    for row in parsed:
        if row["role"] not in INDEPENDENT_ROLES or row["source"] is None:
            continue
        source = row["source"]
        exclusion_roots = [
            _join_source(source, relative) for relative in row["exclusions"]
        ]
        nested_targets: list[PurePosixPath] = []
        for target, target_owner in generated_targets:
            if _is_at_or_below(target, source):
                nested_targets.append(target)
                if not any(
                    _is_at_or_below(target, exclusion)
                    for exclusion in exclusion_roots
                ):
                    errors.append(
                        f"{row['id']}: generated target {str(target)!r} owned by "
                        f"{target_owner!r} sits inside editable source "
                        f"{str(source)!r} without generated_exclusions coverage"
                    )

        for relative, exclusion in zip(row["exclusions"], exclusion_roots):
            if not any(_is_at_or_below(target, exclusion) for target in nested_targets):
                errors.append(
                    f"{row['id']}.generated_exclusions: {str(relative)!r} is stale; "
                    "it does not cover any generated target inside this source"
                )

    return sorted(set(errors))


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("source ownership config root must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_root,
        help="repository root used for source existence checks",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="ownership config (default: <repo-root>/source-ownership.json)",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    config_path = (
        args.config.resolve()
        if args.config is not None
        else repo_root / "source-ownership.json"
    )
    try:
        config = load_config(config_path)
        errors = validate_source_authority(
            config, repo_root=repo_root, require_sources=True
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"[source-authority] FAIL: {exc}", file=sys.stderr)
        return 1

    if errors:
        print(
            f"[source-authority] FAIL: {len(errors)} ownership invariant(s) violated",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    mappings = config.get("mappings", [])
    independent = sum(
        1
        for row in mappings
        if isinstance(row, dict) and row.get("source_role") in INDEPENDENT_ROLES
    )
    aliases = sum(
        1
        for row in mappings
        if isinstance(row, dict) and row.get("source_role") == "authoritative_alias"
    )
    target_count = sum(
        len(row.get("targets", []))
        for row in mappings
        if isinstance(row, dict) and isinstance(row.get("targets", []), list)
    )
    print(
        "[source-authority] PASS: "
        f"{independent} independent authorities, {aliases} aliases, "
        f"{target_count} generated targets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
