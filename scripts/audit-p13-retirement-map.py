#!/usr/bin/env python3
"""Build the P13-A legacy authority retirement matrix.

Re-enumerates the CURRENT source tree (no hard-coded counts): for every
legacy learning/registry/publication surface it records the REAL import
callers found in the authoritative trees (src/ and app/backend — mirrors
and tests excluded), classifies the surface, and snapshots the caller set
so any drift requires an explicit matrix revision. Honest by construction:
zero-caller surfaces are marked retire candidates, untouched modern
authorities are listed as retained, and nothing is deleted here — this is
an inventory, not a retirement.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_ROOTS = ("src", "app/backend/tiangong-backend")
MIRROR_SEGMENTS = ("runtime314", "site-packages", "node_modules")
DEFAULT_OUTPUT = ROOT / "docs/capability-composition/P13_A_RETIREMENT_MATRIX_2026-09-20.json"
SCHEMA = "tiangong.p13.retirement-matrix.v1"

# kind: legacy_write_authority | execution_entry | compatibility_shim |
#       read_projection | historical_read | retained_modern | retire_candidate
SURFACES = (
    # ── legacy life-service learning chain (owner: P10 write-back chain) ──
    dict(path="src/life_service/learning_workflow.py", kind="legacy_write_authority",
         storage="Life shadow store (journal)", migration_target="MemoryCoordinator promotions",
         rollback_cost="medium", note="21+ live callers incl. runtime + learning_output chain"),
    dict(path="src/life_service/learning_executor.py", kind="execution_entry",
         storage="Life journal", migration_target="P19-verified write-back only",
         rollback_cost="medium", note="embedded_runtime executor"),
    dict(path="src/life_service/artifact_executor.py", kind="execution_entry",
         storage="artifact store", migration_target="P8 source publication",
         rollback_cost="medium", note="embedded_runtime + learning_output_preparation"),
    dict(path="src/life_service/journal_replay.py", kind="historical_read",
         storage="Life journal", migration_target="retained for restart replay",
         rollback_cost="low", note="embedded_runtime restart path"),
    dict(path="src/life_service/legacy_learning_migration.py", kind="compatibility_shim",
         storage="signed Life journal", migration_target="P13-C field migration",
         rollback_cost="high", note="owns EXTERNAL_COMPATIBILITY_ENTRYPOINTS + telemetry"),
    dict(path="src/life_service/capability_lifecycle.py", kind="retire_candidate",
         storage="n/a (no callers found)", migration_target="none observed",
         rollback_cost="low", note="zero authoritative callers at audit time"),
    dict(path="src/life_service/life_learning_memory.py", kind="legacy_write_authority",
         storage="in-memory policy constants", migration_target="fold into memory_coordinator",
         rollback_cost="low",
         note="P13-D deletion drill exposed the hidden consumer: "
              "memory_coordinator does `from . import life_learning_memory` "
              "(relative import the original grep missed). NOT a retire candidate."),
    dict(path="src/life_service/capability_learning.py", kind="legacy_write_authority",
         storage="Life shadow store", migration_target="capability experience (P5)",
         rollback_cost="medium", note="12+ live callers incl. store/episode_builder"),
    # ── gateway learning write-back chain (owner: P10) ──
    dict(path="src/total_gateway/learning_output_preparation.py", kind="compatibility_shim",
         storage="gateway store", migration_target="composition experience bridge (R1F)",
         rollback_cost="medium", note="bridges legacy learning into runtime"),
    dict(path="src/total_gateway/learning_output_binding.py", kind="compatibility_shim",
         storage="gateway store", migration_target="composition experience bridge (R1F)",
         rollback_cost="medium", note="runtime-scoped binding"),
    dict(path="src/total_gateway/learning_experience_writeback.py", kind="legacy_write_authority",
         storage="MemoryCoordinator (L3)", migration_target="already the modern owner",
         rollback_cost="high", note="P10 verified write-back; retained"),
    # ── v3 backend compatibility surfaces (owner: P10 18-entry telemetry) ──
    dict(path="app/backend/tiangong-backend/v3/legacy_learning_telemetry.py", kind="compatibility_shim",
         storage="append-only usage journal", migration_target="P13-B zero-use window",
         rollback_cost="low", note="the ONLY production observation of legacy entries"),
    dict(path="app/backend/tiangong-backend/v3/l0_ability_projection.py", kind="read_projection",
         storage="read-only projection", migration_target="world-understanding candidates",
         rollback_cost="low", note="display only; must never gain a write path"),
    dict(path="app/backend/tiangong-backend/v3/zhili/nengli_zhuche.py", kind="compatibility_shim",
         storage="~/.tiangong/v3/nengli_zhuche.json (legacy data protocol)",
         migration_target="P8/P9 source publication",
         rollback_cost="high",
         data_consumers=["app/backend/tiangong-backend/v3/duihua_qiaojie.py",
                         "app/backend/tiangong-backend/v3/jinhua/yuanyu_yingshe.py"],
         note="zero import callers, BUT duihua_qiaojie reads/writes its JSON "
              "path directly (data-protocol consumer, not an import)"),
    # ── retained modern authorities (explicitly NOT retirement targets) ──
    dict(path="src/life_service/memory_coordinator.py", kind="retained_modern",
         storage="Life shadow store", migration_target="n/a", rollback_cost="n/a",
         note="the sole memory write authority"),
    dict(path="src/life_service/memory_invalidation.py", kind="retained_modern",
         storage="append-only invalidations", migration_target="n/a", rollback_cost="n/a",
         note="P15-B cascade"),
    dict(path="src/life_service/experience_invalidation_bridge.py", kind="retained_modern",
         storage="n/a (bridge)", migration_target="n/a", rollback_cost="n/a",
         note="P15-B bridge"),
    dict(path="src/world_understanding/capability_composition/capability_experience_api.py",
         kind="retained_modern", storage="aggregate states", migration_target="n/a",
         rollback_cost="n/a", note="P5 policy API"),
    # ── HTTP mutation entries (definition anchored in legacy_learning_migration) ──
    dict(path="src/life_service/legacy_learning_migration.py::R3A_LEGACY_MUTATION_ENTRYPOINTS",
         kind="execution_entry", storage="signed Life journal",
         migration_target="P13-C/D staged removal", rollback_cost="high",
         note="14 HTTP mutation routes"),
    dict(path="src/life_service/legacy_learning_migration.py::EXTERNAL_COMPATIBILITY_ENTRYPOINTS",
         kind="compatibility_shim", storage="usage journal",
         migration_target="P13-B zero-use window", rollback_cost="medium",
         note="4 in-process backend surfaces"),
)


def _authoritative(path: str) -> bool:
    return any(path.startswith(root) for root in AUTHORITATIVE_ROOTS) and not any(
        segment in path for segment in MIRROR_SEGMENTS)


def _module_of(path: str) -> str | None:
    rel = Path(path)
    if not rel.is_file():
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if "src" in parts:
        parts = parts[parts.index("src") + 1:]
    if not parts:
        return None
    return ".".join(parts)


def _callers(module: str) -> list[str]:
    short = module.split('.')[-1]
    # P13-D deletion-drill fix: the original pattern missed same-package
    # relative imports (`from . import X`), which hid a real consumer of
    # life_learning_memory inside memory_coordinator.
    pattern = rf"(from {re.escape(module)} import|import {re.escape(module)}\b|from \. import .*\b{re.escape(short)}\b|from \.{re.escape(short)} import|from \.\. import .*\b{re.escape(short)}\b)"
    result = subprocess.run(
        ["grep", "-rEl", pattern, "src", "app/backend/tiangong-backend", "--include=*.py"],
        cwd=ROOT, capture_output=True, text=True)
    found = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "/test" in line or "conftest" in line or not _authoritative(line):
            continue
        found.append(str(Path(line).as_posix()))
    return sorted(set(found))


def build() -> dict:
    entries = []
    for surface in SURFACES:
        row = dict(surface)
        path = surface["path"]
        module = _module_of(path) if "::" not in path else _module_of(path.split("::")[0])
        row["exists"] = Path(path.split("::")[0]).is_file()
        if module:
            row["callers"] = _callers(module)
            row["data_consumers"] = surface.get("data_consumers", [])
            row["caller_count"] = len(row["callers"])
            if row["kind"] == "retire_candidate" and row["callers"]:
                raise SystemExit(
                    f"matrix drift: {path} marked retire_candidate but has callers: {row['callers']}")
            if (row["kind"] not in {"retire_candidate", "retained_modern"}
                    and not row["callers"] and not row.get("data_consumers")
                    and "::" not in path):
                print(f"warning: {path} ({row['kind']}) has no authoritative callers",
                      file=sys.stderr)
        else:
            row["callers"] = []
            row["caller_count"] = 0
        entries.append(row)
    return {
        "schema": SCHEMA,
        "summary": {
            "surfaces_total": len(entries),
            "retire_candidates_now": sum(
                1 for e in entries if e["kind"] == "retire_candidate"),
            "retained_modern": sum(1 for e in entries if e["kind"] == "retained_modern"),
            "zero_production_use_proven": False,
            "note": "Inventory only: zero callers observed ≠ production zero use; "
                    "P13-B's telemetry window remains the removal gate.",
        },
        "entries": entries,
        "honesty": (
            "Callers are import-level facts from the authoritative trees at build "
            "time, not a sound call graph (dynamic dispatch and external consumers "
            "still need review). Deletion authority stays with P13-B zero-use "
            "proof and operator approval; this matrix never deletes anything."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    matrix = build()
    rendered = json.dumps(matrix, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if json.loads(current) != matrix if current else True:
            print("retirement matrix drift: rebuild required", file=sys.stderr)
            return 1
        print(f"retirement matrix ok: {matrix['summary']['surfaces_total']} surfaces, "
              f"{matrix['summary']['retire_candidates_now']} retire candidates")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"retirement matrix written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
