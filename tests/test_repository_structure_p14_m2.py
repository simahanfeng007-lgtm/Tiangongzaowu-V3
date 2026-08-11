from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BACKEND = ROOT / "app" / "backend" / "tiangong-backend" / "v3"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contracts.world_understanding.repository import (
    RepositoryIdentity,
    RepositoryObservation,
    RepositoryPathChange,
    RepositoryPathRename,
    RepositoryRevision,
    RepositoryWorkingTreeState,
)


def _load_structure_module():
    path = BACKEND / "repository_structure.py"
    spec = importlib.util.spec_from_file_location("tiangong_p14_repository_structure", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STRUCTURE = _load_structure_module()


def _identity(root: Path) -> RepositoryIdentity:
    return RepositoryIdentity(
        provider_kind="test-local",
        repository_id="repo.test",
        repository_root_ref=str(root.resolve()),
        worktree_id="worktree.test",
        worktree_root_ref=str(root.resolve()),
        remote_identity_hash=None,
        default_branch_hint="main",
    )


def _observation(
    root: Path,
    *,
    head: str = "a" * 40,
    changes: tuple[RepositoryPathChange, ...] = (),
    modified: tuple[str, ...] = (),
    deleted: tuple[str, ...] = (),
    renamed: tuple[tuple[str, str], ...] = (),
    untracked: tuple[str, ...] = (),
    observed_at_ms: int = 1000,
) -> RepositoryObservation:
    state = RepositoryWorkingTreeState.build(
        modified_paths=tuple(sorted(modified)),
        deleted_paths=tuple(sorted(deleted)),
        renamed_paths=tuple(
            RepositoryPathRename(old_path=old, new_path=new)
            for old, new in sorted(renamed)
        ),
        untracked_paths=tuple(sorted(untracked)),
    )
    revision = RepositoryRevision(
        branch="main",
        head_commit=head,
        parent_commit=None,
        detached_head=False,
        observed_at_ms=observed_at_ms,
    )
    return RepositoryObservation.build(
        identity=_identity(root),
        revision=revision,
        working_tree_state=state,
        changes=tuple(sorted(changes, key=lambda item: item.sort_key())),
        files=(),
        provider_version="test-v1",
    )


def test_m2_initial_attach_builds_bounded_coherent_snapshot(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import os\n\nclass Alpha:\n    def run(self):\n        return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "b.ts").write_text(
        "export function beta(): number { return 2; }\n",
        encoding="utf-8",
    )
    index = STRUCTURE.RepositoryStructureIndex()
    delta = index.update(_observation(tmp_path))
    snapshot = index.current("repo.test", "worktree.test")
    assert snapshot is not None
    assert delta.status == "APPLIED"
    assert delta.full_rescan is True
    assert delta.new_view_sha256 == snapshot.view_sha256
    assert len(snapshot.files) == 2
    assert {file.language for file in snapshot.files} == {"python", "typescript"}
    assert all(file.content_sha256 for file in snapshot.files)
    assert not any(hasattr(file, "content") for file in snapshot.files)


def test_m2_one_file_change_parses_only_affected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(f"def {name[0]}():\n    return 1\n", encoding="utf-8")
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    calls: list[str] = []
    original = STRUCTURE._materialize_file

    def counted(**kwargs):
        calls.append(kwargs["path"])
        return original(**kwargs)

    monkeypatch.setattr(STRUCTURE, "_materialize_file", counted)
    (tmp_path / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    change = RepositoryPathChange(
        change_kind="MODIFY", old_path="b.py", new_path="b.py"
    )
    delta = index.update(
        _observation(
            tmp_path,
            changes=(change,),
            modified=("b.py",),
            observed_at_ms=2000,
        )
    )
    assert delta.full_rescan is False
    assert delta.parsed_file_count == 1
    assert calls == ["b.py"]
    assert delta.changed_paths == ("b.py",)


def test_m2_rename_preserves_file_and_symbol_identity(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    old_path = pkg / "alpha.py"
    old_path.write_text(
        "class Worker:\n    def run(self):\n        return 1\n",
        encoding="utf-8",
    )
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    before = index.current("repo.test", "worktree.test")
    assert before is not None
    old_file = next(file for file in before.files if file.path == "pkg/alpha.py")
    old_anchors = tuple(node.stable_anchor for node in old_file.nodes)
    new_path = pkg / "beta.py"
    old_path.rename(new_path)
    change = RepositoryPathChange(
        change_kind="RENAME",
        old_path="pkg/alpha.py",
        new_path="pkg/beta.py",
    )
    delta = index.update(
        _observation(
            tmp_path,
            changes=(change,),
            renamed=(("pkg/alpha.py", "pkg/beta.py"),),
            observed_at_ms=2000,
        )
    )
    after = index.current("repo.test", "worktree.test")
    assert after is not None
    new_file = next(file for file in after.files if file.path == "pkg/beta.py")
    assert delta.full_rescan is False
    assert new_file.file_key == old_file.file_key
    assert new_file.module_anchor == old_file.module_anchor
    assert tuple(node.stable_anchor for node in new_file.nodes) == old_anchors
    assert new_file.module_name != old_file.module_name


def test_m2_delete_retires_module_and_symbols(tmp_path: Path) -> None:
    path = tmp_path / "gone.py"
    path.write_text("def gone():\n    return 1\n", encoding="utf-8")
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    before = index.current("repo.test", "worktree.test")
    assert before is not None
    old = before.files[0]
    path.unlink()
    change = RepositoryPathChange(change_kind="DELETE", old_path="gone.py")
    delta = index.update(
        _observation(
            tmp_path,
            changes=(change,),
            deleted=("gone.py",),
            observed_at_ms=2000,
        )
    )
    after = index.current("repo.test", "worktree.test")
    assert after is not None
    assert not after.files
    assert old.file_key in delta.retired_file_keys
    retired = {(item.entity_type, item.stable_anchor) for item in delta.retirements}
    assert ("Module", old.module_anchor) in retired
    for node in old.nodes:
        assert (node.kind, node.stable_anchor) in retired


def test_m2_parser_failure_keeps_previous_coherent_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "stable.py"
    path.write_text("def stable():\n    return 1\n", encoding="utf-8")
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    before = index.current("repo.test", "worktree.test")
    assert before is not None

    def fail(**kwargs):
        raise STRUCTURE.RepositoryStructureError("injected parser failure")

    monkeypatch.setattr(STRUCTURE, "_materialize_file", fail)
    path.write_text("def stable():\n    return 2\n", encoding="utf-8")
    change = RepositoryPathChange(
        change_kind="MODIFY", old_path="stable.py", new_path="stable.py"
    )
    delta = index.update(
        _observation(
            tmp_path,
            changes=(change,),
            modified=("stable.py",),
            observed_at_ms=2000,
        )
    )
    after = index.current("repo.test", "worktree.test")
    assert delta.status == "FAILED_OPEN"
    assert delta.base_view_sha256 == before.view_sha256
    assert delta.new_view_sha256 == before.view_sha256
    assert after is before
    assert not delta.upsert_files
    assert not delta.retirements


def test_m2_cache_discard_rebuild_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "确定性.py").write_text(
        "def 计算():\n    return 42\n", encoding="utf-8"
    )
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path, observed_at_ms=1000))
    first = index.current("repo.test", "worktree.test")
    assert first is not None
    index.discard("repo.test", "worktree.test")
    index.update(_observation(tmp_path, observed_at_ms=9999))
    second = index.current("repo.test", "worktree.test")
    assert second is not None
    assert first.view_sha256 == second.view_sha256
    assert tuple(file.source_fingerprint for file in first.files) == tuple(
        file.source_fingerprint for file in second.files
    )


def test_m2_large_repository_baseline_is_bounded(tmp_path: Path) -> None:
    for index in range(270):
        (tmp_path / f"m{index:03d}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )
    structure_index = STRUCTURE.RepositoryStructureIndex()
    delta = structure_index.update(_observation(tmp_path))
    snapshot = structure_index.current("repo.test", "worktree.test")
    assert snapshot is not None
    assert snapshot.candidate_path_count == 270
    assert snapshot.truncated is True
    assert len(snapshot.files) <= STRUCTURE._MAX_BASELINE_FILES
    assert len(delta.upsert_files) <= STRUCTURE._MAX_BASELINE_FILES
    continuation = structure_index.update(
        _observation(tmp_path, observed_at_ms=2000)
    )
    completed = structure_index.current("repo.test", "worktree.test")
    assert completed is not None
    assert continuation.full_rescan is False
    assert len(completed.files) == 270
    assert completed.truncated is False


def test_m2_python_ast_emits_only_uniquely_resolved_semantic_relations(
    tmp_path: Path,
) -> None:
    (tmp_path / "semantic.py").write_text(
        "class Base:\n    pass\n\n"
        "def target():\n    return 1\n\n"
        "class Child(Base):\n"
        "    def run(self):\n        return target()\n",
        encoding="utf-8",
    )
    observation = _observation(tmp_path)
    delta = STRUCTURE.RepositoryStructureIndex().update(observation)
    file = delta.upsert_files[0]
    assert file.parser_kind == "python-ast"
    facts = {
        (item.predicate, item.target_token, item.resolution)
        for item in file.semantic_relations
        if item.resolution != "UNRESOLVED"
    }
    assert ("INHERITS", "Base", "UNIQUE_SYMBOL") in facts
    assert ("DIRECT_CALLS", "target", "UNIQUE_SYMBOL") in facts
    assert all(
        item.resolved_target_name and item.resolved_target_anchor
        for item in file.semantic_relations
        if item.resolution != "UNRESOLVED"
    )

    from contracts.world_understanding.scope import (
        ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash,
    )
    from contracts.world_understanding.time import WorldTime
    from world_understanding.source_adapters import build_post_commit_source_envelope
    from world_understanding.source_compilers.p3 import build_p3_compilers
    from world_understanding.software_world.frame import SoftwareWorldFrame
    from world_understanding.software_world.updater import SoftwareWorldUpdater

    bindings = (ScopeBinding(key="repository", value="repo.test"),)
    world_id = derive_world_id(life_id="life.semantic", namespace_anchor="repo.test")
    scope = WorldScope(
        life_id="life.semantic",
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id="life.semantic", world_id=world_id, domain_id="software",
            scope_bindings=bindings,
        ),
        principal_scope_hash="a" * 64,
        privacy_scope="system",
    )
    world_time = WorldTime(valid_from_ms=1, observed_at_ms=1, recorded_at_ms=1)
    envelope = build_post_commit_source_envelope(
        source_kind="GIT_CODE",
        source_native_id="repoobs.semantic",
        producer_ref="test.repository",
        payload={
            "repository_observation": observation.model_dump(mode="json"),
            "structure_delta": delta.model_dump(mode="json"),
        },
        source_time=world_time,
        scope=scope,
        correlation_id="corr.semantic",
    )
    rows = build_p3_compilers()["GIT_CODE"](envelope)
    assert {"DIRECT_CALLS", "INHERITS"}.issubset(
        {row.proposition_type for row in rows}
    )
    frame = SoftwareWorldFrame.build(
        scope=scope,
        workspace="workspace.semantic",
        repository="repo.test",
        worktree="worktree.test",
        branch="main",
        commit="a" * 40,
        environment="test",
        time=world_time,
    )
    result = SoftwareWorldUpdater().update(frame=frame, known_delta=rows)
    predicates = {relation.predicate for relation in result.graph.relations()}
    assert {"DIRECT_CALLS", "INHERITS"}.issubset(predicates), result.diagnostics


@pytest.mark.parametrize(
    ("name", "source", "language"),
    (
        ("main.go", "package main\nfunc Run() {}\n", "go"),
        ("lib.rs", "pub struct Engine {}\npub fn run() {}\n", "rust"),
        ("Main.java", "public class Main {}\n", "java"),
        ("Worker.kt", "class Worker\nfun run() = 1\n", "kotlin"),
        ("Core.cs", "public class Core {}\n", "csharp"),
        ("core.cpp", "class Core {};\nint run() { return 1; }\n", "cpp"),
        ("worker.rb", "class Worker\nend\ndef run\nend\n", "ruby"),
        ("worker.php", "<?php\nclass Worker {}\nfunction run() {}\n", "php"),
        ("main.swift", "struct Engine {}\nfunc run() {}\n", "swift"),
        ("Main.scala", "class Engine\ndef run() = 1\n", "scala"),
        ("build.sh", "run() { echo ok; }\n", "shell"),
    ),
)
def test_m2_bounded_cross_language_structure_fallback(
    tmp_path: Path, name: str, source: str, language: str,
) -> None:
    (tmp_path / name).write_text(source, encoding="utf-8")
    delta = STRUCTURE.RepositoryStructureIndex().update(_observation(tmp_path))
    file = delta.upsert_files[0]
    assert file.language == language
    assert file.parse_status == "PARSED"
    assert file.parser_kind in {"tree-sitter", "bounded-lexical"}
    assert file.nodes


def test_m2_nested_source_path_compiles_to_opaque_known_subject(tmp_path: Path) -> None:
    nested = tmp_path / "package" / "feature.py"
    nested.parent.mkdir()
    nested.write_text("import os\nimport os\n\ndef run():\n    return True\n", encoding="utf-8")
    observation = _observation(tmp_path)
    delta = STRUCTURE.RepositoryStructureIndex().update(observation)

    from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
    from contracts.world_understanding.time import WorldTime
    from world_understanding.source_adapters import build_post_commit_source_envelope
    from world_understanding.source_compilers.p3 import build_p3_compilers

    bindings = (ScopeBinding(key="repository", value="repo.test"),)
    world_id = derive_world_id(life_id="life.test", namespace_anchor="repo.test")
    scope = WorldScope(
        life_id="life.test",
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id="life.test", world_id=world_id, domain_id="software", scope_bindings=bindings
        ),
        principal_scope_hash="a" * 64,
        privacy_scope="system",
    )
    envelope = build_post_commit_source_envelope(
        source_kind="GIT_CODE",
        source_native_id="repoobs.nested",
        producer_ref="test.repository",
        payload={
            "repository_observation": observation.model_dump(mode="json"),
            "structure_delta": delta.model_dump(mode="json"),
        },
        source_time=WorldTime(valid_from_ms=1, observed_at_ms=1, recorded_at_ms=1),
        scope=scope,
        correlation_id="corr.nested",
    )
    rows = build_p3_compilers()["GIT_CODE"](envelope)
    defines = [row for row in rows if row.predicate == "parser.defines"]
    assert defines
    assert all("/" not in row.subject_ref and "\\" not in row.subject_ref for row in defines)
    assert len({row.known_id for row in rows}) == len(rows)


def test_m2_secret_and_binary_source_are_not_structurally_parsed(
    tmp_path: Path,
) -> None:
    (tmp_path / "secrets.py").write_text(
        'TOKEN = "do-not-project"\n', encoding="utf-8"
    )
    (tmp_path / "binary.py").write_bytes(b"\xff\xfe\x00\x01")
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    snapshot = index.current("repo.test", "worktree.test")
    assert snapshot is not None
    by_path = {file.path: file for file in snapshot.files}
    assert by_path["secrets.py"].parse_status == "SKIPPED_SECRET"
    assert not by_path["secrets.py"].nodes
    assert by_path["binary.py"].parse_status == "SKIPPED_BINARY"
    assert not by_path["binary.py"].nodes


def test_m2_local_import_resolution_updates_reverse_neighborhood(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import b\n\ndef a():\n    return b.value()\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    index = STRUCTURE.RepositoryStructureIndex()
    index.update(_observation(tmp_path))
    baseline = index.current("repo.test", "worktree.test")
    assert baseline is not None
    a_before = next(file for file in baseline.files if file.path == "a.py")
    assert a_before.imports[0].resolved_path == "b.py"
    (tmp_path / "b.py").rename(tmp_path / "c.py")
    change = RepositoryPathChange(
        change_kind="RENAME", old_path="b.py", new_path="c.py"
    )
    delta = index.update(
        _observation(
            tmp_path,
            changes=(change,),
            renamed=(("b.py", "c.py"),),
            observed_at_ms=2000,
        )
    )
    by_path = {file.path: file for file in delta.upsert_files}
    assert "c.py" in by_path
    assert "a.py" in by_path
    assert by_path["a.py"].imports[0].resolved_path is None
    assert delta.parsed_file_count == 1


def test_m2_world_updater_keeps_io_boundary_frozen() -> None:
    updater_path = ROOT / "src" / "world_understanding" / "software_world" / "updater.py"
    tree = ast.parse(updater_path.read_text(encoding="utf-8"))
    forbidden = {
        "os", "pathlib", "subprocess", "socket", "requests", "httpx",
        "urllib", "git",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    assert not (imports & forbidden)


def test_m2_repository_structure_has_no_runtime_or_learning_owner() -> None:
    path = BACKEND / "repository_structure.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_roots = {
        "life_service", "total_gateway", "runtime_security",
        "world_understanding.world_state",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not any(
        imported == root or imported.startswith(root + ".")
        for imported in imports
        for root in forbidden_roots
    )
