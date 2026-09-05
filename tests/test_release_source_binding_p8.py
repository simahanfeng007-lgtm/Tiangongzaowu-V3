"""Release byte-binding contracts, not production running-lock evidence."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from contracts import canonical_sha256
from total_gateway import release_manifest as release


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    root = tmp_path / "source"
    path = root / "src" / "helper.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"VALUE = 1\n")
    # Exercise the actual generation entry point and its digest lifecycle,
    # isolating it from unrelated component/contract/skill-catalog generation.
    monkeypatch.setattr(
        release, "_generate_release_manifest",
        lambda workspace: release._source_tree(workspace, "gateway-source", ("src",)),
    )
    return root, path


def test_same_size_same_mtime_source_change_cannot_reuse_old_digest(source_tree):
    root, path = source_tree
    original = release.generate_release_manifest(root)
    before = path.stat()
    path.write_bytes(b"VALUE = 2\n")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_size == before.st_size
    changed = release.generate_release_manifest(root)
    assert changed.tree_sha256 != original.tree_sha256


def test_forged_persistent_hash_cache_is_not_release_evidence(source_tree):
    root, path = source_tree
    stat = path.stat()
    cache = root / ".tiangong-release-hash-cache.json"
    forged = json.dumps({"version": 1, "files": {"src/helper.py": {
        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": "a" * 64,
    }}}).encode()
    cache.write_bytes(forged)
    observed = release.generate_release_manifest(root)
    assert observed.tree_sha256 == canonical_sha256({
        "domain": "tiangong.release-source-tree.v1", "tree_id": "gateway-source",
        "entries": ({"path": "src/helper.py", "size_bytes": stat.st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},),
    })
    # Verification neither trusts nor rewrites legacy cache files.
    assert cache.read_bytes() == forged


def test_release_generation_is_read_only_and_deterministic(source_tree):
    root, _ = source_tree
    before = {path.relative_to(root) for path in root.rglob("*")}
    first = release.generate_release_manifest(root)
    assert release.generate_release_manifest(root) == first
    assert {path.relative_to(root) for path in root.rglob("*")} == before


@pytest.mark.skipif(os.name != "nt", reason="Windows native path identity contract")
def test_release_checks_native_identity_without_dos_volume_lookup(source_tree, monkeypatch):
    root, path = source_tree
    original_resolve = Path.resolve

    def denied_strict(path, strict=False):
        if strict:
            raise PermissionError("DOS volume lookup denied in AppContainer")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", denied_strict)
    assert release._safe_workspace(root) == root
    assert release._safe_file(root, "src/helper.py") == path
    assert release.generate_release_manifest(root).file_count == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native path identity contract")
def test_release_native_identity_failure_never_falls_back(source_tree, monkeypatch):
    from runtime_security import path_identity

    root, _ = source_tree
    def denied(path):
        raise PermissionError("native volume identity unavailable")
    monkeypatch.setattr(path_identity, "_windows_final_path", denied)
    with pytest.raises(release.ReleaseManifestError, match="unsafe"):
        release.generate_release_manifest(root)


def test_existing_gateway_release_tree_covers_all_declared_source_roots():
    manifest = release.generate_release_manifest(ROOT)
    policy = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
    authority = policy["authority_policy"]
    required = {*authority["editable_roots"], *authority["frozen_roots"],
                "source-ownership.json", "pyproject.toml", "requirements-source.lock"}
    gateway = next(item for item in manifest.source_trees if item.tree_id == "gateway-source")
    assert required.issubset(gateway.roots)
    assert manifest.has_valid_release_manifest_sha256()
    assert not manifest.production_claim


@pytest.fixture
def owned_source(tmp_path):
    root = tmp_path / "owned-source"
    policy = {
        "schema": "tiangong.source-ownership.v2",
        "authority_policy": {"editable_roots": ["src", "backend"], "frozen_roots": ["frozen"]},
        "mappings": [{"id": name, "source": name, "source_role": role, "targets": []}
                     for name, role in (("src", "authoritative"), ("backend", "authoritative"),
                                        ("frozen", "frozen_authoritative"))],
    }
    for relative in (
        "src/total_gateway/entry.py", "src/contracts/data.py", "src/runtime_security/trust.py",
        "src/omni_body_skill/tools/handler.py", "src/omni_body_skill/tools/lazy_helper.py",
        "src/world_understanding/world.py", "backend/fact_kernel/compiler.py", "backend/runtime.py",
        "frozen/compat.py", "pyproject.toml", "requirements-source.lock",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"VALUE = 1\n")
    (root / "source-ownership.json").write_text(json.dumps(policy), encoding="utf-8")
    return root


@pytest.mark.parametrize("relative", [
    "src/omni_body_skill/tools/handler.py", "src/omni_body_skill/tools/lazy_helper.py",
    "src/world_understanding/world.py", "backend/fact_kernel/compiler.py", "backend/runtime.py",
    "frozen/compat.py", "requirements-source.lock",
])
def test_implementation_and_dependency_edits_change_the_existing_release_tree(owned_source, relative):
    root = owned_source
    roots = release._gateway_source_roots(root)
    before = release._source_tree(root, "gateway-source", roots)
    path = root / relative
    stat = path.stat()
    path.write_bytes(b"VALUE = 2\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = release._source_tree(root, "gateway-source", roots)
    assert after.tree_sha256 != before.tree_sha256
    assert before.file_count == after.file_count
    assert before.size_bytes == after.size_bytes


def test_release_output_and_bytecode_do_not_feed_back_into_source_identity(owned_source):
    root = owned_source
    roots = release._gateway_source_roots(root)
    before = release._source_tree(root, "gateway-source", roots)
    for relative in ("app/release/release-manifest.json", "output/review.json",
                     "src/__pycache__/helper.cpython-312.pyc"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-source")
    assert release._source_tree(root, "gateway-source", roots) == before


@pytest.mark.parametrize("invalid", [None, [], "src", [True], ["../outside"], ["C:/outside"]])
def test_invalid_ownership_roots_fail_closed(owned_source, invalid):
    path = owned_source / "source-ownership.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["authority_policy"]["editable_roots"] = invalid
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(release.ReleaseManifestError, match="ownership"):
        release._gateway_source_roots(owned_source)


def test_invalid_ownership_topology_is_not_a_release_input(owned_source):
    path = owned_source / "source-ownership.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["mappings"].append({"id": "parallel", "source": "src/contracts",
                             "source_role": "authoritative", "targets": []})
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(release.ReleaseManifestError, match="topology"):
        release._gateway_source_roots(owned_source)


@pytest.fixture
def packaged_runtime(tmp_path, monkeypatch):
    # Real temporary artifact bytes; reuse source metadata to isolate package
    # path binding from unrelated repository/contract generation.
    source = release.generate_release_manifest(ROOT)
    root = tmp_path / "long-package-runtime-directory"
    executable = root / "total-gateway/tiangong-total-gateway.exe"
    archive = root / "electron/resources/app.asar"
    for path, data in ((executable, b"packaged-runtime-fixture"), (archive, b"desktop-fixture")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(release, "generate_release_manifest", lambda _: source)
    def generate(runtime=root, desktop=archive):
        return release.generate_production_release_manifest(
            ROOT, runtime, platform_name="win32", architecture="x64",
            desktop_archive_path=desktop, generated_at_ms=source.generated_at_ms + 1000,
        )
    return root, executable, archive, generate


def test_production_archive_cannot_escape_runtime(packaged_runtime, tmp_path):
    root, _, _, generate = packaged_runtime
    external = tmp_path / "external/app.asar"
    external.parent.mkdir(); external.write_bytes(b"outside")
    with pytest.raises(release.ReleaseManifestError, match="unsafe"):
        generate(root, external)


@pytest.mark.parametrize("failed_call", [1, 2])
def test_production_native_identity_denial_cannot_use_pathlib_fallback(packaged_runtime, monkeypatch, failed_call):
    from types import SimpleNamespace
    from unittest.mock import Mock
    root, _, archive, generate = packaged_runtime
    monkeypatch.setattr(release, "os", SimpleNamespace(name="nt", path=os.path))
    values = [root, archive]
    values[failed_call - 1] = PermissionError("native identity denied")
    observer = Mock(side_effect=values)
    monkeypatch.setattr(release, "resolve_existing_path", observer, raising=False)
    with pytest.raises(release.ReleaseManifestError, match="unsafe|missing"):
        generate()
    assert observer.call_count == failed_call


@pytest.mark.skipif(os.name != "nt", reason="actual Windows package 8.3 identity")
def test_production_archive_short_and_long_spellings_bind_identical_bytes(packaged_runtime):
    import ctypes
    from ctypes import wintypes
    root, executable, archive, generate = packaged_runtime
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    api.GetShortPathNameW.restype = wintypes.DWORD
    short = ctypes.create_unicode_buffer(32768)
    length = api.GetShortPathNameW(str(root), short, len(short))
    assert 0 < length < len(short), "real 8.3 fixture unavailable, not a pass"
    alias = Path(short.value)
    canonical = root.resolve(strict=True)
    assert alias != canonical, "test must exercise a real alias"
    first = generate(alias, alias / archive.relative_to(root))
    second = generate(canonical, canonical / archive.relative_to(root))
    assert release.release_manifest_bytes(first) == release.release_manifest_bytes(second)
    descriptors = {item.component_id: item for item in first.component_manifest.components}
    assert descriptors["tiangong-desktop"].sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert descriptors["tiangong-total-gateway"].sha256 == hashlib.sha256(executable.read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows package native path check")
def test_production_archive_binding_survives_dos_resolution_denial(packaged_runtime, monkeypatch):
    _, _, _, generate = packaged_runtime
    expected = generate()
    def denied(*args, **kwargs):
        raise PermissionError("controlled DOS resolution denial")
    monkeypatch.setattr(Path, "resolve", denied)
    assert generate() == expected


@pytest.mark.skipif(os.name != "nt", reason="actual Windows redirected package directory")
@pytest.mark.parametrize("redirect", ["electron", "total-gateway"])
def test_production_package_rejects_redirected_artifact_ancestor(packaged_runtime, tmp_path, redirect):
    import shutil
    import subprocess
    root, _, _, generate = packaged_runtime
    original = root / redirect
    outside = tmp_path / ("outside-" + redirect)
    shutil.move(str(original), str(outside))
    linked = subprocess.run(["cmd", "/c", "mklink", "/J", str(original), str(outside)],
                            capture_output=True, text=True, check=False)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    try:
        with pytest.raises(release.ReleaseManifestError, match="unsafe|missing"):
            generate()
    finally:
        original.rmdir()
