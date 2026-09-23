import hashlib
import json

from contracts import artifacts
from total_gateway import release_manifest


def test_artifact_build_generates_bundle_once_and_binds_same_bytes(monkeypatch):
    calls = []
    original = artifacts.contract_schema_bundle
    def counted():
        calls.append(1)
        return original()
    monkeypatch.setattr(artifacts, "contract_schema_bundle", counted)
    docs = artifacts.generate_contract_artifact_documents()
    assert len(calls) == 1
    manifest = json.loads(docs["contract-artifacts.manifest.json"])
    api = json.loads(docs["openapi.json"])
    digest = hashlib.sha256(docs["schema-bundle.json"][:-1]).hexdigest()
    assert manifest["schema_bundle_sha256"] == digest
    assert api["x-tiangong-contract-catalog"]["schema_bundle_sha256"] == digest


def test_overlapping_roots_enumerate_once_but_changed_bytes_are_reobserved(tmp_path, monkeypatch):
    from pathlib import Path
    root = tmp_path / "source"
    child = root / "nested"
    child.mkdir(parents=True)
    file = child / "code.py"
    file.write_text("old", encoding="utf-8")
    traversals = []
    original = Path.rglob
    def counted(path, pattern, *args, **kwargs):
        traversals.append(path)
        return original(path, pattern, *args, **kwargs)
    monkeypatch.setattr(Path, "rglob", counted)
    paths = release_manifest._tree_files(tmp_path, ("source/nested", "source", "source/nested/code.py"))
    assert paths == (file,)
    assert traversals == [root]
    before = release_manifest._source_tree(tmp_path, "test-source", ("source", "source/nested"))
    file.write_text("new", encoding="utf-8")
    after = release_manifest._source_tree(tmp_path, "test-source", ("source", "source/nested"))
    assert before != after
