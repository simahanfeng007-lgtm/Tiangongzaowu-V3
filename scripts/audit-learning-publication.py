"""Read-only P10 inventory of reviewed learning/publication source surfaces.

Parses source as data, never imports product modules or modifies catalogs. This
is a developer inventory, NOT a runtime freeze, permission or zero-use proof.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


SURFACES = {
    "src/life_service/learning_workflow.py": ("build_draft", "confirm_draft", "discard_draft", "publish_draft"),
    "src/life_service/learning_executor.py": ("execute_learning_preview",),
    "src/life_service/artifact_executor.py": (
        "compile_artifact", "publish_artifact", "persist_artifact_bundle",
        "persist_current_pointer", "rollback_pointer"),
    "src/life_service/embedded_runtime.py": (
        "_learning_draft", "_learning_confirm", "_learning_publish",
        "_recover_approved_learning_cards", "_build_learning_artifact",
        "_set_capability_pointer", "_capability_activate", "_capability_reactivate",
        "_capability_rollback", "_capability_patch_propose", "_capability_patch_settle",
        "_capability_invoke", "_sync_life_capability_workspace_zone",
        "_scheduler_tick", "EmbeddedLifeRuntime.request", "_capability_verify_patch",
        "_capability_outcome_report", "_capability_discard", "_learning_discard"),
    "src/life_service/embedded_runtime_wiring.py": (
        "bind_embedded_life_gateway_callback", "bind_embedded_life_learning_materializers"),
    "src/life_service/store.py": (
        "put_capability_candidate", "advance_capability_candidate", "put_capability_pointer",
        "commit_capability_learning", "apply_capability_rollback"),
    "src/life_service/capability_learning.py": ("learn_capability", "rollback_capability"),
    "src/life_service/life_learning_memory.py": ("build_learning_scope", "derive_learning_result_ids"),
    "src/total_gateway/runtime.py": (
        "publish_learning_artifact", "invoke_learning_artifact_action",
        "life_capability_workspace_mapper", "life_capability_workspace_marker",
        "life_capability_workspace_remover"),
    "src/total_gateway/desktop_api.py": ("DesktopApiRouter._dispatch_native", "DesktopApiRouter._forward"),
    "src/world_understanding/capability_composition/capability_experience_api.py": (
        "build_capability_experience_memory_intent",),
    "src/world_understanding/capability_composition/capability_experience_memory.py": (
        "commit_capability_experience_via_memory_coordinator",),
    "app/backend/tiangong-backend/v3/duihua_qiaojie.py": (
        "run_learning_pipeline", "process_approved_learning_card", "activate_learning_card",
        "release_learning_card", "_write_registry_rows", "_delete_learned_skill_from_registry",
        "create_learning_card_from_request", "confirm_learning_card",
        "request_learning_activation", "discard_learning_card"),
    "app/backend/tiangong-backend/v3/zongdiaodu.py": ("_ensure_xuexi_lian",),
    "app/backend/tiangong-backend/v3/jineng/jirou_ceng.py": ("_xuexi_liucheng",),
    "app/backend/tiangong-backend/v3/zhili/nengli_zhuche.py": (
        "_baocun", "zhuce_nengli", "jihuo_nengli"),
    "app/backend/tiangong-backend/v3/l0_ability_projection.py": (),
}


class InventoryError(ValueError):
    pass


class _Definitions(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.rows = []

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.rows.append((".".join(self.stack), node))
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def audit_file(root: Path, relative: str, selectors: tuple[str, ...]) -> dict:
    """Extract exact spans and syntactic calls, not a resolved runtime callgraph."""
    posix = PurePosixPath(relative)
    if (posix.is_absolute() or str(posix) != relative or ".." in posix.parts
            or "\\" in relative or ":" in relative or posix.suffix != ".py"):
        raise InventoryError("inventory source path is invalid")
    if len(set(selectors)) != len(selectors):
        raise InventoryError("inventory selectors are duplicated")
    if root.is_symlink():
        raise InventoryError("inventory root is linked")
    root = root.resolve(strict=True)
    path = root.joinpath(*posix.parts)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise InventoryError("inventory source path is linked")
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise InventoryError("inventory source is missing or oversized")
    raw = path.read_bytes()
    tree = ast.parse(raw.decode("utf-8-sig"), filename=relative)
    definitions = _Definitions()
    definitions.visit(tree)
    lines = raw.splitlines(keepends=True)
    symbols = []
    for selected in sorted(selectors):
        matches = [(name, node) for name, node in definitions.rows
                   if node.name == selected or name == selected]
        if len(matches) != 1:
            raise InventoryError(f"inventory symbol absent or ambiguous: {relative}:{selected}")
        name, node = matches[0]
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        text = b"".join(lines[start - 1:node.end_lineno])
        calls = sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)})
        symbols.append({"symbol": name, "start_line": start, "end_line": node.end_lineno,
                        "source_span_sha256": hashlib.sha256(text).hexdigest(),
                        "syntactic_calls": calls})
    routes = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str) and n.value.startswith("/api/")
                     and any(s in n.value for s in ("/learning/", "/capability/", "/capabilities/"))})
    return {"path": relative, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
            "symbols": symbols, "route_literals": routes}


def audit_repository(root: Path, baseline: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", baseline) is None:
        raise InventoryError("inventory baseline must be a full Git commit SHA")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE).decode().strip()
    if git("rev-parse", "--verify", baseline + "^{commit}") != baseline:
        raise InventoryError("inventory baseline commit is unavailable")
    git("merge-base", "--is-ancestor", baseline, "HEAD")
    files = []
    for path, selectors in sorted(SURFACES.items()):
        row = audit_file(root, path, selectors)
        expected = git("ls-tree", baseline, "--", path)
        if expected != f"100644 blob {row['git_blob_sha1']}\t{path}":
            raise InventoryError(f"inventory source differs from baseline: {path}")
        files.append(row)
    return {"schema": "tiangong.p10.learning-publication-inventory.v1",
            "baseline_commit": baseline, "status": "STATIC_INVENTORY_ONLY",
            "coverage": "reviewed selected authorities; not an exhaustive runtime reachability proof",
            "call_reference_semantics": "syntactic only, includes nested call expressions; not resolved dispatch",
            "production_usage_verified": False, "publication_freeze_applied": False,
            "may_authorize": False, "may_execute": False,
            "file_count": len(files), "symbol_count": sum(len(f["symbols"]) for f in files),
            "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    try:
        report = audit_repository(args.repository, args.baseline)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"inventory failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
