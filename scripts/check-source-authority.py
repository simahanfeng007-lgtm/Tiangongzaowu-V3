#!/usr/bin/env python3
"""Validate Tiangong source-authority topology and closed-world source boundaries."""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

EXPECTED_SCHEMA = "tiangong.source-ownership.v2"
ALLOWED_ROLES = frozenset({"authoritative", "frozen_authoritative", "authoritative_alias"})
INDEPENDENT_ROLES = frozenset({"authoritative", "frozen_authoritative"})
BOUNDARY_MODE = "closed_world"
REEXPORT_CONTRACT = "python_reexport_only"


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


def _normalize_relative_root(value: Any, *, label: str) -> tuple[PurePosixPath | None, str | None]:
    path, error = _normalize_repo_path(value, label=label)
    if error:
        return None, error
    assert path is not None
    if len(path.parts) != 1:
        return None, f"{label}: closed-world boundary entries must be immediate children, got {str(path)!r}"
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
        for right in rows[index + 1:]:
            yield left, right


def _parse_boundary_paths(values: Any, *, mapping_id: str, field: str, errors: list[str]) -> list[PurePosixPath]:
    if not isinstance(values, list):
        errors.append(f"{mapping_id}.boundary_policy.{field}: expected a list")
        return []
    parsed: list[PurePosixPath] = []
    seen: set[PurePosixPath] = set()
    for index, value in enumerate(values):
        path, error = _normalize_relative_root(value, label=f"{mapping_id}.boundary_policy.{field}[{index}]")
        if error:
            errors.append(error)
            continue
        assert path is not None
        if path in seen:
            errors.append(f"{mapping_id}.boundary_policy.{field}[{index}]: duplicate boundary path {str(path)!r}")
            continue
        seen.add(path)
        parsed.append(path)
    return parsed


def _validate_reexport_tree(root: Path, *, mapping_id: str, relative: PurePosixPath | Path, import_prefix: str) -> list[str]:
    errors: list[str] = []
    if not root.exists():
        return [f"{mapping_id}.boundary_policy: compatibility adapter is missing: {relative}"]
    python_files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    if not python_files:
        return [f"{mapping_id}.boundary_policy: compatibility adapter {str(relative)!r} contains no Python modules"]
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{mapping_id}.boundary_policy: cannot parse compatibility adapter {path.name!r}: {exc}")
            continue
        for index, node in enumerate(tree.body):
            if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module == "__future__" or module == import_prefix or module.startswith(import_prefix + "."):
                    continue
                errors.append(f"{mapping_id}.boundary_policy: compatibility adapter {path.name!r} imports implementation outside {import_prefix!r}: {module!r}")
                continue
            if isinstance(node, ast.Import):
                bad = [alias.name for alias in node.names if not (alias.name == import_prefix or alias.name.startswith(import_prefix + "."))]
                if not bad:
                    continue
                errors.append(f"{mapping_id}.boundary_policy: compatibility adapter {path.name!r} imports implementation outside {import_prefix!r}: {bad!r}")
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                if names and all(name == "__all__" for name in names):
                    continue
            errors.append(f"{mapping_id}.boundary_policy: compatibility adapter {path.name!r} contains executable/owned implementation node {type(node).__name__}; contract={REEXPORT_CONTRACT}")
    return errors


def _validate_boundary_policy(row: dict[str, Any], *, by_id: dict[str, dict[str, Any]], repo_root: Path, require_sources: bool) -> list[str]:
    errors: list[str] = []
    raw = row["raw"].get("boundary_policy")
    if raw is None:
        return errors
    mapping_id = row["id"]
    source = row["source"]
    if row["role"] not in INDEPENDENT_ROLES or source is None:
        return [f"{mapping_id}.boundary_policy: only independent authority may define it"]
    if not isinstance(raw, dict):
        return [f"{mapping_id}.boundary_policy: expected an object"]
    if raw.get("mode") != BOUNDARY_MODE:
        errors.append(f"{mapping_id}.boundary_policy.mode: expected {BOUNDARY_MODE!r}, got {raw.get('mode')!r}")
    implementation = _parse_boundary_paths(raw.get("implementation_roots", []), mapping_id=mapping_id, field="implementation_roots", errors=errors)
    non_runtime = _parse_boundary_paths(raw.get("non_runtime_artifacts", []), mapping_id=mapping_id, field="non_runtime_artifacts", errors=errors)
    raw_adapters = raw.get("compatibility_adapters", [])
    if not isinstance(raw_adapters, list):
        errors.append(f"{mapping_id}.boundary_policy.compatibility_adapters: expected a list")
        raw_adapters = []
    adapters: list[dict[str, Any]] = []
    adapter_paths: list[PurePosixPath] = []
    for index, adapter in enumerate(raw_adapters):
        prefix = f"{mapping_id}.boundary_policy.compatibility_adapters[{index}]"
        if not isinstance(adapter, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        path, error = _normalize_relative_root(adapter.get("path"), label=f"{prefix}.path")
        if error:
            errors.append(error)
            continue
        assert path is not None
        authority = str(adapter.get("implementation_authority") or "").strip()
        contract = str(adapter.get("contract") or "").strip()
        import_prefix = str(adapter.get("import_prefix") or "").strip()
        if authority not in by_id or by_id[authority]["role"] not in INDEPENDENT_ROLES:
            errors.append(f"{prefix}.implementation_authority: {authority!r} is not an independent authority mapping")
        if authority == mapping_id:
            errors.append(f"{prefix}.implementation_authority: adapter cannot point to its own authority")
        if contract != REEXPORT_CONTRACT:
            errors.append(f"{prefix}.contract: expected {REEXPORT_CONTRACT!r}, got {contract!r}")
        if not import_prefix:
            errors.append(f"{prefix}.import_prefix: required for re-export validation")
        adapters.append({"path": path, "authority": authority, "contract": contract, "import_prefix": import_prefix})
        adapter_paths.append(path)
    generated = row["exclusions"]
    for index, path in enumerate(generated):
        if len(path.parts) != 1:
            errors.append(f"{mapping_id}.generated_exclusions[{index}]: closed-world generated root must be an immediate child, got {str(path)!r}")
    categories: list[tuple[str, PurePosixPath]] = []
    categories.extend(("implementation", path) for path in implementation)
    categories.extend(("generated", path) for path in generated)
    categories.extend(("compatibility", path) for path in adapter_paths)
    categories.extend(("non_runtime", path) for path in non_runtime)
    seen: dict[PurePosixPath, str] = {}
    for category, path in categories:
        previous = seen.get(path)
        if previous:
            errors.append(f"{mapping_id}.boundary_policy: {str(path)!r} is classified as both {previous} and {category}")
        else:
            seen[path] = category
    if require_sources:
        source_fs = _fs_path(repo_root, source)
        if not source_fs.is_dir():
            errors.append(f"{mapping_id}.boundary_policy: closed-world authority must be a directory")
        else:
            # Bytecode/test caches are runner artifacts, never authority
            # entries: full-suite pytest creates __pycache__ and .pytest_cache
            # under the V3 backend and would otherwise flip closed-world
            # validation from green to red purely through test ordering.
            cache_children = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
            actual = {
                PurePosixPath(child.name)
                for child in source_fs.iterdir()
                if child.name not in cache_children
            }
            classified = set(seen)
            missing = sorted(str(path) for path in actual - classified)
            stale = sorted(str(path) for path in classified - actual)
            if missing:
                errors.append(f"{mapping_id}.boundary_policy: unclassified immediate children: {missing!r}")
            if stale:
                errors.append(f"{mapping_id}.boundary_policy: classified paths do not exist: {stale!r}")
            for relative in non_runtime:
                path = source_fs / str(relative)
                if path.is_file() and path.suffix.casefold() == ".py":
                    errors.append(f"{mapping_id}.boundary_policy: non-runtime artifact {str(relative)!r} cannot be Python source")
                elif path.is_dir() and any(path.rglob("*.py")):
                    errors.append(f"{mapping_id}.boundary_policy: non-runtime artifact {str(relative)!r} contains Python source")
            for adapter in adapters:
                if adapter["contract"] == REEXPORT_CONTRACT and adapter["import_prefix"]:
                    errors.extend(_validate_reexport_tree(source_fs / str(adapter["path"]), mapping_id=mapping_id, relative=adapter["path"], import_prefix=adapter["import_prefix"]))
    return errors


def validate_source_authority(config: dict[str, Any], *, repo_root: Path, require_sources: bool = True) -> list[str]:
    errors: list[str] = []
    if config.get("schema") != EXPECTED_SCHEMA:
        errors.append(f"schema: expected {EXPECTED_SCHEMA!r}, got {config.get('schema')!r}")
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
            errors.append(f"{mapping_id}.source_role: expected one of {sorted(ALLOWED_ROLES)!r}, got {role!r}")
        source, source_error = _normalize_repo_path(raw_mapping.get("source"), label=f"{mapping_id}.source")
        if source_error:
            errors.append(source_error)
        raw_targets = raw_mapping.get("targets", [])
        if not isinstance(raw_targets, list):
            errors.append(f"{mapping_id}.targets: expected a list")
            raw_targets = []
        targets: list[PurePosixPath] = []
        for target_index, raw_target in enumerate(raw_targets):
            target, target_error = _normalize_repo_path(raw_target, label=f"{mapping_id}.targets[{target_index}]")
            if target_error:
                errors.append(target_error)
                continue
            assert target is not None
            previous = target_owner_ids.get(target)
            if previous:
                errors.append(f"{mapping_id}.targets[{target_index}]: generated target {str(target)!r} is already owned by {previous!r}")
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
            exclusion, exclusion_error = _normalize_repo_path(raw_exclusion, label=f"{mapping_id}.generated_exclusions[{exclusion_index}]")
            if exclusion_error:
                errors.append(exclusion_error)
                continue
            assert exclusion is not None
            if exclusion in seen_exclusions:
                errors.append(f"{mapping_id}.generated_exclusions[{exclusion_index}]: duplicate exclusion {str(exclusion)!r}")
                continue
            seen_exclusions.add(exclusion)
            exclusions.append(exclusion)
        parent_id = str(raw_mapping.get("authority_parent") or "").strip()
        if source is not None:
            previous = source_owner_ids.get(source)
            if previous:
                errors.append(f"{mapping_id}.source: source {str(source)!r} is already declared by {previous!r}")
            else:
                source_owner_ids[source] = mapping_id
            if require_sources and not _fs_path(repo_root, source).exists():
                errors.append(f"{mapping_id}.source: repository source does not exist: {str(source)!r}")
        parsed.append({"id": mapping_id, "role": role, "source": source, "targets": targets, "exclusions": exclusions, "parent_id": parent_id, "raw": raw_mapping})
    by_id = {row["id"]: row for row in parsed}
    for row in parsed:
        mapping_id, role, source = row["id"], row["role"], row["source"]
        targets, exclusions, parent_id = row["targets"], row["exclusions"], row["parent_id"]
        if source is None:
            continue
        if role == "authoritative_alias":
            if targets:
                errors.append(f"{mapping_id}: authoritative_alias must not own generated targets")
            if exclusions:
                errors.append(f"{mapping_id}: authoritative_alias must not define generated_exclusions")
            if row["raw"].get("boundary_policy") is not None:
                errors.append(f"{mapping_id}: authoritative_alias must not define boundary_policy")
            if not parent_id:
                errors.append(f"{mapping_id}: authoritative_alias requires authority_parent")
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                errors.append(f"{mapping_id}: authority_parent {parent_id!r} does not exist")
                continue
            if parent["role"] not in INDEPENDENT_ROLES or parent["source"] is None:
                errors.append(f"{mapping_id}: authority_parent {parent_id!r} is not an independent authority")
                continue
            if not _is_at_or_below(source, parent["source"]):
                errors.append(f"{mapping_id}: alias source {str(source)!r} must be inside parent source {str(parent['source'])!r}")
        elif parent_id:
            errors.append(f"{mapping_id}: only authoritative_alias may set authority_parent")
    independent_sources = [(row["id"], row["source"]) for row in parsed if row["role"] in INDEPENDENT_ROLES and row["source"] is not None]
    for (left_id, left), (right_id, right) in _pairs(independent_sources):
        if _is_at_or_below(left, right) or _is_at_or_below(right, left):
            errors.append(f"independent authority overlap: {left_id!r}={str(left)!r}, {right_id!r}={str(right)!r}; declare the narrower entry as authoritative_alias instead")
    generated_targets = list(target_owner_ids.items())
    generated_diag = [(f"{owner}:{str(path)}", path) for path, owner in generated_targets]
    for (left, left_path), (right, right_path) in _pairs(generated_diag):
        if _is_at_or_below(left_path, right_path) or _is_at_or_below(right_path, left_path):
            errors.append(f"generated target overlap: {left!r} conflicts with {right!r}; generated mirrors must not nest")
    for row in parsed:
        source = row["source"]
        if source is None:
            continue
        for target, target_owner in generated_targets:
            if _is_at_or_below(source, target):
                errors.append(f"{row['id']}.source: {str(source)!r} is inside generated target {str(target)!r} owned by {target_owner!r}")
    for row in parsed:
        if row["role"] not in INDEPENDENT_ROLES or row["source"] is None:
            continue
        source = row["source"]
        exclusion_roots = [_join_source(source, relative) for relative in row["exclusions"]]
        nested_targets: list[PurePosixPath] = []
        for target, target_owner in generated_targets:
            if _is_at_or_below(target, source):
                nested_targets.append(target)
                if not any(_is_at_or_below(target, exclusion) for exclusion in exclusion_roots):
                    errors.append(f"{row['id']}: generated target {str(target)!r} owned by {target_owner!r} sits inside editable source {str(source)!r} without generated_exclusions coverage")
        for relative, exclusion in zip(row["exclusions"], exclusion_roots):
            if not any(_is_at_or_below(target, exclusion) for target in nested_targets):
                errors.append(f"{row['id']}.generated_exclusions: {str(relative)!r} is stale; it does not cover any generated target inside this source")
    for row in parsed:
        errors.extend(_validate_boundary_policy(row, by_id=by_id, repo_root=repo_root, require_sources=require_sources))
    return sorted(set(errors))


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("source ownership config root must be an object")
    return data


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
