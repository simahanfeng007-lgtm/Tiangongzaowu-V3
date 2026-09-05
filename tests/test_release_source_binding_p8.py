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
        "src/world_understanding/world.py", "backend/fact_kernel/compiler.py",
        "backend/runtime.py", "frozen/compat.py", "pyproject.toml", "requirements-source.lock",
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
