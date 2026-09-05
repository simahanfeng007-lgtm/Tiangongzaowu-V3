"""Offline wrapper fixtures, not live-Run or publication acceptance evidence."""

import hashlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest


WRAPPER = Path(__file__).resolve().parents[1] / "src/omni_body_skill/api/v1/v3/tools/omni_body.py"


@pytest.fixture
def versions(tmp_path, monkeypatch):
    roots = []
    for revision in ("X", "Y"):
        root = tmp_path / revision
        (root / "tools").mkdir(parents=True)
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "tools/__init__.py").write_text("", encoding="utf-8")
        (root / "tools/omni_body_tool.py").write_text(
            f"class BodyRuntime: revision = {revision!r}\nclass BodyRuntimeConfig: pass\n",
            encoding="utf-8",
        )
        (root / "tools/omni_capability.py").write_text(
            f"def verify_capability_grant(*a, **kw): return {{'fixture_revision': {revision!r}}}\n",
            encoding="utf-8",
        )
        wrapper = root / "api/v1/v3/tools/omni_body.py"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(WRAPPER.read_bytes())
        roots.append(root)
    monkeypatch.delenv("TIANGONG_OMNI_BODY_ALLOW_USER_ROOT", raising=False)
    yield roots
    for root in roots:
        suffix = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
        for name in list(sys.modules):
            if name == "_tiangong_omni_capability_" + suffix or name.startswith("_tiangong_omni_" + suffix):
                sys.modules.pop(name, None)


def load(path):
    spec = importlib.util.spec_from_file_location("p8_fixture_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(wrapper):
    return wrapper._verify_capability({}, action="fixture.read", target="", payload={}, workspace="", runtime_meta={})


def test_wrapper_uses_its_own_revision_even_if_default_changes_before_first_import(versions, monkeypatch):
    old, new = versions
    wrapper = load(old / "api/v1/v3/tools/omni_body.py")
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(new))
    runtime, _, error = wrapper._import_runtime()
    assert error is None
    assert runtime.revision == "X"
    assert verify(wrapper) == {"fixture_revision": "X"}


def test_existing_wrapper_and_verifier_remain_on_x_after_y_is_selected(versions, monkeypatch):
    old, new = versions
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(old))
    wrapper = load(old / "api/v1/v3/tools/omni_body.py")
    first, _, error = wrapper._import_runtime()
    assert error is None
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(new))
    second, _, error = wrapper._import_runtime()
    assert error is None
    assert second is first
    assert verify(wrapper) == {"fixture_revision": "X"}
    new_wrapper = load(new / "api/v1/v3/tools/omni_body.py")
    assert new_wrapper._import_runtime()[0].revision == "Y"
    assert verify(new_wrapper) == {"fixture_revision": "Y"}


def test_standalone_wrapper_pins_first_host_root_and_does_not_hot_reload(versions, tmp_path, monkeypatch):
    old, new = versions
    standalone = tmp_path / "standalone.py"
    standalone.write_bytes(WRAPPER.read_bytes())
    wrapper = load(standalone)
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(old))
    assert wrapper._import_runtime()[0].revision == "X"
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(new))
    assert wrapper._import_runtime()[0].revision == "X"
    assert verify(wrapper) == {"fixture_revision": "X"}


def test_missing_pinned_source_does_not_fall_back_to_new_default(versions, monkeypatch):
    old, new = versions
    wrapper = load(old / "api/v1/v3/tools/omni_body.py")
    assert wrapper._import_runtime()[0].revision == "X"
    (old / "tools/omni_body_tool.py").unlink()
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(new))
    runtime, config, error = wrapper._import_runtime()
    assert runtime is config is None
    assert "root not found" in error
    with pytest.raises(ValueError, match="root not found"):
        verify(wrapper)


def test_cached_verifier_from_another_source_is_rejected(versions, monkeypatch):
    old, new = versions
    wrapper = load(old / "api/v1/v3/tools/omni_body.py")
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(old))
    suffix = hashlib.sha256(str(old.resolve()).encode("utf-8")).hexdigest()[:16]
    foreign = types.ModuleType("foreign_verifier")
    foreign.__file__ = str(new / "tools/omni_capability.py")
    foreign.verify_capability_grant = lambda *a, **kw: {"fixture_revision": "Y"}
    monkeypatch.setitem(sys.modules, "_tiangong_omni_capability_" + suffix, foreign)
    with pytest.raises(ImportError, match="verifier source mismatch"):
        verify(wrapper)
