"""P10 R0 developer inventory tests, not runtime publication/cutover evidence."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-learning-publication.py"
spec = importlib.util.spec_from_file_location("p10_publication_inventory", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(inventory)


def _file(root: Path, relative: str, source: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source)
    return path


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.STDOUT, text=True,
    ).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Inventory fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _file(root, "src/sample.py", b"def publish():\n    return 'proposal'\n")
    _git(root, "add", "src/sample.py")
    _git(root, "commit", "-qm", "source fixture")
    return root, _git(root, "rev-parse", "HEAD")


def test_audit_parses_source_without_executing_it(tmp_path):
    marker = tmp_path / "MUST_NOT_EXIST"
    raw = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        "def publish():\n    return sink()\n"
    ).encode("utf-8")
    _file(tmp_path, "src/source.py", raw)
    result = inventory.audit_file(tmp_path, "src/source.py", ("publish",))
    assert not marker.exists()
    assert result["symbols"][0]["syntactic_calls"] == ["sink"]
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["git_blob_sha1"] == hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


def test_decorated_async_and_nested_symbols_have_exact_spans(tmp_path):
    raw = (
        b"def deco(f):\n    return f\n\nclass A:\n"
        b"    @deco\n    async def publish(self):\n"
        b"        def inner():\n            return target()\n"
        b"        return inner()\n"
    )
    _file(tmp_path, "src/source.py", raw)
    result = inventory.audit_file(tmp_path, "src/source.py", ("A.publish", "A.publish.inner"))
    outer, inner = result["symbols"]
    assert outer["symbol"] == "A.publish"
    assert (outer["start_line"], outer["end_line"]) == (5, 9)
    assert outer["source_span_sha256"] == hashlib.sha256(
        b"".join(raw.splitlines(keepends=True)[4:9])
    ).hexdigest()
    assert outer["syntactic_calls"] == ["inner", "target"]
    assert inner["symbol"] == "A.publish.inner"
    assert (inner["start_line"], inner["end_line"]) == (7, 8)


@pytest.mark.parametrize("selectors", [("missing",), ("publish", "publish"), ("same",)])
def test_missing_duplicate_and_ambiguous_selectors_fail(tmp_path, selectors):
    raw = b"def publish():\n    pass\nclass A:\n    def same(self):\n        pass\nclass B:\n    def same(self):\n        pass\n"
    _file(tmp_path, "src/source.py", raw)
    with pytest.raises(inventory.InventoryError):
        inventory.audit_file(tmp_path, "src/source.py", selectors)


@pytest.mark.parametrize("path", ["../source.py", "/absolute.py", "C:/source.py", "C:source.py", "src\\source.py", "src//source.py", "src/./source.py", "src/data.json"])
def test_unsafe_source_paths_are_rejected(tmp_path, path):
    with pytest.raises(inventory.InventoryError, match="path"):
        inventory.audit_file(tmp_path, path, ())


@pytest.mark.parametrize("case", ["missing", "oversized"])
def test_missing_or_oversized_source_is_rejected(tmp_path, case):
    if case == "oversized":
        _file(tmp_path, "src/source.py", b"#" * (2 * 1024 * 1024 + 1))
    with pytest.raises(inventory.InventoryError, match="missing or oversized"):
        inventory.audit_file(tmp_path, "src/source.py", ())


def test_link_observer_rejects_root_and_source(tmp_path, monkeypatch):
    # Portable fault injection of the path observer; not native symlink evidence.
    target = _file(tmp_path, "src/source.py", b"def publish():\n    pass\n")
    real = Path.is_symlink
    for linked in (tmp_path, target, target.parent):
        with monkeypatch.context() as m:
            m.setattr(Path, "is_symlink", lambda path: path == linked or real(path))
            with pytest.raises(inventory.InventoryError, match="linked"):
                inventory.audit_file(tmp_path, "src/source.py", ("publish",))


def test_route_literals_are_sorted_deduplicated_syntax_not_live_telemetry(tmp_path):
    _file(tmp_path, "src/source.py", b'''if False:
    route = "/api/v1/learning/publish"
other = "/api/v1/capability/activate"
copy = "/api/v1/learning/publish"
ignore = "/api/v1/chat"
''')
    result = inventory.audit_file(tmp_path, "src/source.py", ())
    assert result["route_literals"] == ["/api/v1/capability/activate", "/api/v1/learning/publish"]
    assert result["symbols"] == []


def test_canonical_inventory_is_repeatable_and_explicitly_non_authorizing(repository, monkeypatch):
    root, commit = repository
    monkeypatch.setattr(inventory, "SURFACES", {"src/sample.py": ("publish",)})
    first = inventory.audit_repository(root, commit)
    second = inventory.audit_repository(root, commit)
    assert first == second
    assert first["baseline_commit"] == commit
    assert first["file_count"] == first["symbol_count"] == 1
    assert first["status"] == "STATIC_INVENTORY_ONLY"
    for field in ("production_usage_verified", "publication_freeze_applied", "may_authorize", "may_execute"):
        assert first[field] is False
    assert not _git(root, "status", "--porcelain")


def test_dirty_product_source_cannot_be_reported_as_baseline(repository, monkeypatch):
    root, commit = repository
    monkeypatch.setattr(inventory, "SURFACES", {"src/sample.py": ("publish",)})
    (root / "src/sample.py").write_bytes(b"def publish():\n    return 'drifted'\n")
    with pytest.raises(inventory.InventoryError, match="differs from baseline"):
        inventory.audit_repository(root, commit)


def test_unrelated_descendant_document_does_not_invalidate_unchanged_sources(repository, monkeypatch):
    root, commit = repository
    monkeypatch.setattr(inventory, "SURFACES", {"src/sample.py": ("publish",)})
    before = inventory.audit_repository(root, commit)
    _file(root, "note.txt", b"later documentation\n")
    _git(root, "add", "note.txt")
    _git(root, "commit", "-qm", "unrelated documentation")
    assert inventory.audit_repository(root, commit) == before


@pytest.mark.parametrize("value", ["main", "abc", "F" * 40, "--help"])
def test_baseline_requires_literal_full_sha(repository, value):
    root, _ = repository
    with pytest.raises(inventory.InventoryError, match="full Git commit"):
        inventory.audit_repository(root, value)


def test_unknown_commit_cannot_be_a_baseline(repository):
    root, _ = repository
    with pytest.raises(subprocess.CalledProcessError):
        inventory.audit_repository(root, "f" * 40)


def test_invalid_cli_exits_without_product_execution_or_output_file(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repository", str(tmp_path), "--baseline", "main"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "inventory failed" in result.stderr
    assert not result.stdout
    assert list(tmp_path.iterdir()) == []


def test_non_ancestor_commit_cannot_be_used_as_source_baseline(repository, monkeypatch):
    root, first = repository
    monkeypatch.setattr(inventory, "SURFACES", {"src/sample.py": ("publish",)})
    _file(root, "future.txt", b"not in the checked-out lineage\n")
    _git(root, "add", "future.txt")
    _git(root, "commit", "-qm", "future commit")
    future = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--detach", first)
    with pytest.raises(subprocess.CalledProcessError):
        inventory.audit_repository(root, future)
