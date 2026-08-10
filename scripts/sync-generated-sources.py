from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "source-ownership.json"
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
MARKER = ".tiangong-generated-source.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def included(path: Path) -> bool:
    return not any(part in IGNORED_PARTS for part in path.parts) and path.suffix.lower() not in IGNORED_SUFFIXES and path.name != MARKER


def files_under(source: Path) -> Iterable[tuple[Path, Path]]:
    if source.is_file():
        yield Path(source.name), source
        return
    for item in sorted(source.rglob("*")):
        if item.is_file() and included(item.relative_to(source)):
            yield item.relative_to(source), item


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def write_marker(target_root: Path, mapping_id: str, source: str, count: int, tree_hash: str) -> None:
    if target_root.suffix:
        return
    payload = {
        "schema": "tiangong.generated-source-marker.v1",
        "mapping_id": mapping_id,
        "source": source,
        "file_count": count,
        "tree_sha256": tree_hash,
        "warning": "Generated mirror. Edit the authoritative source and run scripts/sync-generated-sources.py --write."
    }
    target_root.mkdir(parents=True, exist_ok=True)
    path = target_root / MARKER
    temp = path.with_name(f".{MARKER}.{os.getpid()}.tmp")
    temp.write_bytes((json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    os.replace(temp, path)


def mapping_files(source: Path) -> list[tuple[Path, Path]]:
    return list(files_under(source))


def tree_hash(rows: list[tuple[Path, Path]]) -> str:
    h = hashlib.sha256()
    for rel, path in rows:
        h.update(rel.as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(bytes.fromhex(sha256(path)))
    return h.hexdigest()


def target_is_build_only_in_source_checkout(target: Path) -> bool:
    """Return True when an absent generated target is intentionally git-ignored.

    Source closeout must verify every committed mirror without requiring build-time
    embedded runtimes (for example app/runtime/) to exist in a fresh checkout.
    Tracked targets are never skipped, even when a parent has an ignore rule.
    """
    if target.exists():
        return False
    try:
        relative = target.relative_to(ROOT).as_posix()
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False
    return completed.returncode == 0


def process(write: bool, *, committed_only: bool = False) -> list[str]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    failures: list[str] = []
    for mapping in config["mappings"]:
        source_rel = str(mapping["source"])
        source = ROOT / source_rel
        if not source.exists():
            failures.append(f"{mapping['id']}:source_missing:{source_rel}")
            continue
        rows = mapping_files(source)
        digest = tree_hash(rows)
        for target_rel in mapping["targets"]:
            target = ROOT / str(target_rel)
            if committed_only and target_is_build_only_in_source_checkout(target):
                continue
            target_is_file = source.is_file()
            if target_is_file:
                rel_rows = [(Path(target.name), rows[0][1])]
            else:
                rel_rows = rows
                target_files = dict(files_under(target)) if target.is_dir() else {}
                source_rels = {rel for rel, _ in rows}
                for extra_rel in sorted(set(target_files) - source_rels):
                    extra_path = target / extra_rel
                    if write:
                        extra_path.unlink(missing_ok=True)
                        _prune_empty_parents(extra_path.parent, target)
                    else:
                        failures.append(
                            f"{mapping['id']}:extra:{extra_path.relative_to(ROOT).as_posix()}"
                        )
            for rel, source_file in rel_rows:
                target_file = target if target_is_file else target / rel
                if not target_file.is_file() or sha256(target_file) != sha256(source_file):
                    if write:
                        atomic_copy(source_file, target_file)
                    else:
                        state = "missing" if not target_file.exists() else "drift"
                        failures.append(f"{mapping['id']}:{state}:{target_file.relative_to(ROOT).as_posix()}")
            if not target_is_file:
                marker = target / MARKER
                if write:
                    write_marker(target, str(mapping["id"]), source_rel, len(rows), digest)
                elif not marker.is_file():
                    failures.append(f"{mapping['id']}:marker_missing:{target.relative_to(ROOT).as_posix()}")
                else:
                    try:
                        payload = json.loads(marker.read_text(encoding="utf-8"))
                        if (
                            payload.get("mapping_id") != mapping["id"]
                            or payload.get("tree_sha256") != digest
                        ):
                            failures.append(
                                f"{mapping['id']}:marker_drift:{target.relative_to(ROOT).as_posix()}"
                            )
                    except Exception:
                        failures.append(
                            f"{mapping['id']}:marker_invalid:{target.relative_to(ROOT).as_posix()}"
                        )
    return failures


def _prune_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument(
        "--check-committed",
        action="store_true",
        help="check committed/source-tree mirrors while excluding absent git-ignored build-runtime targets",
    )
    args = parser.parse_args()
    committed_only = bool(args.check_committed)
    failures = process(write=args.write, committed_only=committed_only)
    if failures:
        for item in failures:
            print(item, file=sys.stderr)
        return 1
    mode_name = "write" if args.write else ("check-committed" if committed_only else "check")
    print(json.dumps({"ok": True, "mode": mode_name, "config": str(CONFIG.relative_to(ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
