"""Source-root selection without starting a scheduler, model or tool."""

from pathlib import Path
from types import ModuleType
import sys

import pytest

from total_gateway import embedded_backend as backend


class ImportBoundaryReached(Exception):
    pass


@pytest.fixture
def explicit_source(tmp_path, monkeypatch):
    root = tmp_path / "release-x"
    (root / "app/backend/tiangong-backend/v3").mkdir(parents=True)
    (root / "src").mkdir()
    # A fresh launcher has only its own backend path; an unrelated namespace
    # directory is tested separately as a rejected mixed-installation input.
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path
                                     if not (Path(entry) / "v3").is_dir()
                                     and not (Path(entry) / "tiangong_kernel").is_dir()])
    # Isolate this loader contract from modules imported by earlier tests.
    for name in tuple(sys.modules):
        if name in {"v3", "tiangong_kernel"} or name.startswith(("v3.", "tiangong_kernel.")):
            monkeypatch.delitem(sys.modules, name)
    original_import = backend.importlib.import_module

    def stop_at_import(name, package=None):
        if name.startswith("v3."):
            raise ImportBoundaryReached(name)
        return original_import(name, package)

    monkeypatch.setattr(backend.importlib, "import_module", stop_at_import)
    return root


def test_explicit_release_root_wins_over_checkout_and_environment(explicit_source, monkeypatch, tmp_path):
    other = tmp_path / "environment-backend"
    (other / "v3").mkdir(parents=True)
    monkeypatch.setenv("TIANGONG_BACKEND_DIR", str(other))
    runtime = object.__new__(backend.EmbeddedBackendRuntime)
    with pytest.raises(ImportBoundaryReached, match="v3.peizhi"):
        runtime._initialize(release_source_root=explicit_source)
    assert runtime._backend_root == explicit_source / "app/backend/tiangong-backend"
    assert runtime._source_roots == [explicit_source]


@pytest.mark.parametrize("relative", ["missing", "src-only", "backend-only"])
def test_incomplete_explicit_root_never_falls_back(explicit_source, tmp_path, relative):
    root = tmp_path / relative
    if relative == "src-only":
        (root / "src").mkdir(parents=True)
    if relative == "backend-only":
        (root / "app/backend/tiangong-backend/v3").mkdir(parents=True)
    before = list(sys.path)
    with pytest.raises(backend.EmbeddedBackendError, match="source_root"):
        backend.EmbeddedBackendRuntime.start(release_source_root=root)
    assert sys.path == before
    assert backend._PROCESS_OWNER is None


@pytest.mark.parametrize("module_name", ["v3.peizhi", "v3.lazy_helper", "tiangong_kernel.helper"])
def test_foreign_cached_backend_module_is_rejected_without_hot_reload(
    explicit_source, monkeypatch, tmp_path, module_name,
):
    module = ModuleType(module_name)
    module.__file__ = str(tmp_path / "release-y/backend/helper.py")
    monkeypatch.setitem(sys.modules, module_name, module)
    before = list(sys.path)
    with pytest.raises(backend.EmbeddedBackendError, match="source_module_mismatch"):
        backend.EmbeddedBackendRuntime.start(release_source_root=explicit_source)
    assert sys.modules[module_name] is module
    assert sys.path == before
    assert backend._PROCESS_OWNER is None


def test_foreign_namespace_search_path_is_rejected(explicit_source, monkeypatch, tmp_path):
    namespace = ModuleType("v3")
    namespace.__path__ = [str(explicit_source / "app/backend/tiangong-backend/v3"),
                          str(tmp_path / "release-y/v3")]
    monkeypatch.setitem(sys.modules, "v3", namespace)
    with pytest.raises(backend.EmbeddedBackendError, match="source_module_mismatch"):
        backend.EmbeddedBackendRuntime.start(release_source_root=explicit_source)


def test_modules_from_the_selected_release_can_be_reused(explicit_source, monkeypatch):
    package_root = explicit_source / "app/backend/tiangong-backend/v3"
    namespace = ModuleType("v3")
    namespace.__path__ = [str(package_root)]
    module = ModuleType("v3.peizhi")
    module.__file__ = str(package_root / "peizhi.py")
    monkeypatch.setitem(sys.modules, "v3", namespace)
    monkeypatch.setitem(sys.modules, "v3.peizhi", module)
    with pytest.raises(ImportBoundaryReached, match="v3.peizhi"):
        backend.EmbeddedBackendRuntime.start(release_source_root=explicit_source)
    assert sys.modules["v3.peizhi"] is module
    assert backend._PROCESS_OWNER is None


def test_uncached_namespace_cannot_aggregate_a_foreign_checkout(explicit_source, monkeypatch, tmp_path):
    other = tmp_path / "foreign-backend"
    (other / "v3").mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [*sys.path, str(other)])
    before = list(sys.path)
    with pytest.raises(backend.EmbeddedBackendError, match="source_module_mismatch"):
        backend.EmbeddedBackendRuntime.start(release_source_root=explicit_source)
    assert sys.path == before
    assert "v3" not in sys.modules
