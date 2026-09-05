"""Host path contracts and controlled denial fixtures, not OS/product evidence."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime_security import path_identity as identity


def test_existing_file_and_directory_are_observed(tmp_path):
    path = tmp_path / "payload.txt"
    path.write_bytes(b"unchanged")
    assert identity.resolve_existing_path(tmp_path) == tmp_path.resolve(strict=True)
    assert identity.resolve_existing_path(path) == path.resolve(strict=True)
    assert identity.verify_relative_path(tmp_path, path) == "payload.txt"
    assert path.read_bytes() == b"unchanged"


def test_missing_existing_path_fails(tmp_path):
    with pytest.raises(OSError):
        identity.resolve_existing_path(tmp_path / "missing")


def test_outside_scope_path_fails_before_native_query(tmp_path):
    with pytest.raises(identity.PathIdentityError, match="outside_installation"):
        identity.verify_relative_path(tmp_path / "selected", tmp_path / "foreign")


def test_posix_keeps_strict_resolution_without_using_windows_backend(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(Path, "resolve", lambda path, strict=False: seen.append((path, strict)) or path)
    assert identity.resolve_existing_path(tmp_path) == tmp_path
    assert seen == [(tmp_path, True)]


@pytest.mark.skipif(os.name != "nt", reason="actual Windows path API contract")
def test_windows_native_resolution_does_not_call_pathlib_resolution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pathlib resolution is not native identity evidence")
    monkeypatch.setattr(Path, "resolve", forbidden)
    assert identity.resolve_existing_path(tmp_path) == tmp_path


@pytest.mark.skipif(os.name != "nt", reason="actual Windows path API contract")
def test_windows_native_query_failure_has_no_pathlib_fallback(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError("native identity unavailable")
    monkeypatch.setattr(identity, "_windows_final_path", denied)
    with pytest.raises(PermissionError, match="native identity unavailable"):
        identity.resolve_existing_path(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows rejects lexical normalization into acceptance")
def test_windows_dotdot_path_is_not_silently_normalized(tmp_path):
    with pytest.raises(identity.PathIdentityError, match="path_not_canonical"):
        identity.resolve_existing_path(tmp_path / "unused/../file.txt")
