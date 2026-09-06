"""Model schema rendering must preserve the pinned backend import namespace."""

import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from v3.jineng import http_kehuduan as client


@pytest.fixture
def adapter_source(tmp_path, monkeypatch):
    root = tmp_path / "skill"
    (root / "model_adapters").mkdir(parents=True)
    (root / "v3/registry").mkdir(parents=True)
    (root / "v3/registry/data.json").write_text("{}", encoding="utf-8")
    path = root / "model_adapters/core.py"
    path.write_text("SOURCE = 'selected'\n", encoding="utf-8")
    monkeypatch.setattr(client, "_MODEL_ADAPTER_CORE", None)
    monkeypatch.setattr(client, "_omni_body_skill_root_for_model_adapter", lambda: root)
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(root))
    old_modules = set(sys.modules)
    old_path = list(sys.path)
    yield root, path
    sys.path[:] = old_path
    for name in set(sys.modules) - old_modules:
        if name.startswith("_tiangong_omni_model_adapter_"):
            sys.modules.pop(name, None)


def test_adapter_does_not_add_skill_data_to_backend_namespace(adapter_source):
    import v3

    old_path = list(sys.path)
    old_backend_path = list(v3.__path__)
    loaded = client._model_adapter_core()
    assert loaded.SOURCE == "selected"
    assert Path(loaded.__file__) == adapter_source[1]
    assert sys.path == old_path
    assert list(v3.__path__) == old_backend_path


def test_global_adapter_from_another_source_is_not_selected(adapter_source, monkeypatch):
    monkeypatch.setitem(sys.modules, "model_adapters", SimpleNamespace(core=SimpleNamespace(SOURCE="foreign")))
    assert client._model_adapter_core().SOURCE == "selected"


def test_cached_adapter_cannot_cross_source_revision(adapter_source, monkeypatch, tmp_path):
    first = client._model_adapter_core()
    assert client._model_adapter_core() is first
    other = tmp_path / "other"
    (other / "model_adapters").mkdir(parents=True)
    (other / "model_adapters/core.py").write_text("SOURCE = 'other'", encoding="utf-8")
    monkeypatch.setattr(client, "_omni_body_skill_root_for_model_adapter", lambda: other)
    with pytest.raises(RuntimeError, match="source_changed"):
        client._model_adapter_core()


def test_exact_module_survives_client_cache_reset(adapter_source, monkeypatch):
    first = client._model_adapter_core()
    monkeypatch.setattr(client, "_MODEL_ADAPTER_CORE", None)
    assert client._model_adapter_core() is first


def test_adapter_alias_cannot_hide_another_module_origin(adapter_source, monkeypatch):
    root, _ = adapter_source
    name = "_tiangong_omni_model_adapter_" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    monkeypatch.setitem(sys.modules, name, SimpleNamespace(__file__="foreign.py"))
    with pytest.raises(RuntimeError, match="origin_mismatch"):
        client._model_adapter_core()


def test_failed_adapter_load_does_not_leave_a_partial_module(adapter_source):
    root, path = adapter_source
    path.write_text("raise RuntimeError('failed load')\n", encoding="utf-8")
    assert client._model_adapter_core() is None
    name = "_tiangong_omni_model_adapter_" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    assert name not in sys.modules
    assert client._MODEL_ADAPTER_CORE is None


def test_real_schema_adapter_is_visible_to_source_origin_checks(adapter_source, monkeypatch):
    root = Path(__file__).resolve().parents[1] / "src/omni_body_skill"
    monkeypatch.setattr(client, "_omni_body_skill_root_for_model_adapter", lambda: root)
    loaded = client._model_adapter_core()
    rendered = loaded.render_tool_schema(provider="minimax_m3", model="MiniMax-M3")
    assert rendered["tool_schema"]
    assert sys.modules[loaded.__name__] is loaded
    assert loaded.__name__.startswith("_tiangong_omni_")
    assert Path(loaded.__spec__.origin) == root / "model_adapters/core.py"
