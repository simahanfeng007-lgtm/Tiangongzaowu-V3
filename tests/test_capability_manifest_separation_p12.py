"""P12 R1B shared implementation, compatibility and rejection-path regression.

Definition-byte pins bind the unchanged moved definitions to the exact pre-extraction
0354b24 source blob f185e752b4cb3116b8aca547e36d51ac19c026b0. Only CRLF
normalization is allowed. This avoids Python-version-dependent ast.dump output
without weakening source identity or installing a second reference implementation.
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
from unittest.mock import Mock

import pytest

from contracts import canonical_json_bytes
from total_gateway import capability_manifest as shared
from total_gateway import skill_selection as legacy
from tests.test_tool_manifest_evolution_p8 import compiled

ROOT = Path(__file__).resolve().parents[1]
DEFINITION_PINS = {
    "SkillSelectionError": "1f02fd860ec32590736168d2a914fc439262a18f28c6a28e6389200de596ea52",
    "LoadedModelCapabilityManifest": "056e9e0c891a431ae506e768c686382333343d24bd1beaf729dbadbedd0c1f4f",
    "_strict_json_pairs": "de98bb1e90f2e039afab29b9860e4116f5f22edc378ff4582e7c45ce6ca7a22a",
    "_routing_side_effects": "e81a62f999be0c22e7cf4f23534c0a17cb4a5b1df574f2935481b3bbace22124",
    # The loader now applies dictionary readiness. Its rejection behavior is
    # exercised below; the old extraction-only byte pin no longer describes it.
    "compile_composition_execution_manifest": "245915170843aa409d91c78c519f32c13af411224916cbd962e9232fbb86c5ee"
}


def load(path, loader=shared.load_model_capability_manifest):
    data = path.read_bytes()
    return loader(path, expected_sha256=hashlib.sha256(data).hexdigest(),
                  component_manifest_hash="c" * 64, generated_at_ms=1000)


@pytest.fixture
def manifest_path(tmp_path):
    path = tmp_path / "manifest.json"
    # Non-canonical bytes distinguish release byte identity from the parsed-document hash.
    path.write_text(json.dumps(compiled().to_gateway_dict(source_inputs_sha256="a" * 64),
                               indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("name", DEFINITION_PINS)
def test_legacy_exports_the_same_object_without_a_wrapper(name):
    assert getattr(legacy, name) is getattr(shared, name)


def _definition_bytes(source: bytes, name: str) -> bytes:
    """Pin complete source, including decorators; do not serialize versioned ASTs."""
    source = source.replace(b"\r\n", b"\n")
    tree = ast.parse(source.decode("utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and node.name == name]
    if len(nodes) != 1:
        raise ValueError("Expected exactly one top-level definition: " + name)
    node = nodes[0]
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = source.splitlines(keepends=True)
    # AST columns are UTF-8 byte offsets. Slice bytes, not Unicode characters.
    return b"".join(lines[first - 1:node.end_lineno - 1]) + lines[node.end_lineno - 1][:node.end_col_offset]


@pytest.mark.parametrize("name,digest", DEFINITION_PINS.items())
def test_moved_definition_bytes_are_unchanged(name, digest):
    source = (ROOT / "src/total_gateway/capability_manifest.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(_definition_bytes(source, name)).hexdigest() == digest


@pytest.mark.parametrize("name", DEFINITION_PINS)
def test_definition_pin_rejects_body_changes(name):
    source = (ROOT / "src/total_gateway/capability_manifest.py").read_bytes().replace(b"\r\n", b"\n")
    body = _definition_bytes(source, name)
    # A harmless comment still changes the pinned source, so executable edits cannot hide.
    altered = body.replace(b"\n", b"  # changed definition\n", 1)
    assert altered != body
    mutated_source = source.replace(body, altered, 1)
    assert hashlib.sha256(_definition_bytes(mutated_source, name)).hexdigest() != DEFINITION_PINS[name]


def test_definition_pin_includes_decorators_and_utf8_end_columns():
    source = '@decorator(frozen=True)\nclass Sample:\n    value = "你好"\n'.encode("utf-8")
    pinned = _definition_bytes(source, "Sample")
    assert pinned == source.rstrip(b"\n")
    assert _definition_bytes(b"# moved\n\n" + source + b"\nother = 1\n", "Sample") == pinned
    assert _definition_bytes(source.replace(b"\n", b"\r\n"), "Sample") == pinned
    assert _definition_bytes(source.replace(b"True", b"False"), "Sample") != pinned


@pytest.mark.parametrize("source", [b"", b"from elsewhere import Sample", b"class Sample: pass\nclass Sample: pass\n"])
def test_definition_pin_rejects_missing_imported_or_duplicate_definitions(source):
    with pytest.raises(ValueError, match="exactly one"):
        _definition_bytes(source, "Sample")


def test_legacy_has_no_duplicate_shared_definitions():
    tree = ast.parse((ROOT / "src/total_gateway/skill_selection.py").read_text("utf-8"))
    assert not {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))} & DEFINITION_PINS.keys()


@pytest.mark.parametrize("first", ["capability_manifest", "skill_selection"])
def test_both_import_orders_keep_one_class_and_function_identity(first):
    code = """
import importlib, sys
sys.path.insert(0, sys.argv[1])
importlib.import_module('total_gateway.' + sys.argv[2])
a = importlib.import_module('total_gateway.capability_manifest')
b = importlib.import_module('total_gateway.skill_selection')
for name in ('LoadedModelCapabilityManifest', 'SkillSelectionError',
             'load_model_capability_manifest', 'compile_composition_execution_manifest',
             '_strict_json_pairs', '_routing_side_effects'):
    assert getattr(a, name) is getattr(b, name), name
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(ROOT / "src"), first],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_shared_loader_imports_without_loading_or_reading_static_skill_sources(tmp_path):
    manifest = (ROOT / "dictionaries/registry/capability_manifest.generated.json").resolve()
    code = """
import hashlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def reject_legacy(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\\\', '/')
        assert not any(token in path for token in ('deliverable_skills', 'skill_router_index', '/skill_selection.')), path
sys.addaudithook(reject_legacy)
from total_gateway.capability_manifest import load_model_capability_manifest, compile_composition_execution_manifest
p = Path(sys.argv[2])
loaded = load_model_capability_manifest(p, expected_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    component_manifest_hash='c'*64, generated_at_ms=1000)
result = compile_composition_execution_manifest(loaded.manifest, loaded.action_authority.registry,
    loaded.action_authority.schema_catalog)
assert result.has_valid_sha256()
assert 'total_gateway.skill_selection' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(ROOT / "src"), str(manifest)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_direct_and_legacy_entries_keep_both_hash_domains_and_outputs(manifest_path):
    current = load(manifest_path)
    compatible = load(manifest_path, legacy.load_model_capability_manifest)
    assert current == compatible
    assert current.source_sha256 != current.action_authority.manifest_sha256
    left = shared.compile_composition_execution_manifest(current.manifest, current.action_authority.registry,
                                                         current.action_authority.schema_catalog)
    right = legacy.compile_composition_execution_manifest(compatible.manifest, compatible.action_authority.registry,
                                                          compatible.action_authority.schema_catalog)
    assert left == right and left.has_valid_sha256()
    assert len(left.actions) == current.executable_count


def test_loaded_value_remains_frozen_and_legacy_pickle_names_resolve(manifest_path):
    loaded = load(manifest_path)
    assert tuple(f.name for f in fields(loaded)) == ("manifest", "source_sha256", "executable_count", "action_authority")
    with pytest.raises(FrozenInstanceError):
        loaded.executable_count = 0
    for name in ("LoadedModelCapabilityManifest", "SkillSelectionError"):
        # Trusted constant pickle global, not user-supplied serialized input.
        assert pickle.loads(("ctotal_gateway.skill_selection\n" + name + "\n.").encode()) is getattr(shared, name)
    error = shared.SkillSelectionError("original-message")
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is legacy.SkillSelectionError and restored.args == error.args


def test_legacy_static_json_reader_uses_the_same_exception():
    assert legacy._strict_json_pairs is shared._strict_json_pairs
    with pytest.raises(legacy.SkillSelectionError, match="Skill index contains a duplicate JSON key"):
        json.loads('{"id": 1, "id": 2}', object_pairs_hook=shared._strict_json_pairs)


def test_p8_monkeypatch_targets_the_actual_loader_globals(manifest_path, monkeypatch):
    from tests import test_tool_manifest_loading_p8 as p8
    assert p8.skill_selection is shared
    guard = Mock(side_effect=ValueError("sentinel-rejected"))
    monkeypatch.setattr(shared, "compile_action_authority", guard)
    with pytest.raises(legacy.SkillSelectionError, match="schema authority") as exc:
        load(manifest_path, legacy.load_model_capability_manifest)
    guard.assert_called_once()
    assert str(exc.value.__cause__) == "sentinel-rejected"


def test_unpinned_bytes_are_rejected_before_the_new_defining_module_compiles(manifest_path, monkeypatch):
    guard = Mock(side_effect=AssertionError("must not compile"))
    monkeypatch.setattr(shared, "compile_action_authority", guard)
    with pytest.raises(legacy.SkillSelectionError, match="pinned release"):
        shared.load_model_capability_manifest(manifest_path, expected_sha256="0" * 64,
                                              component_manifest_hash="c" * 64, generated_at_ms=1000)
    guard.assert_not_called()


def test_source_launch_imports_shared_loader_and_orchestration_alias_is_identical():
    from total_gateway import orchestration
    assert orchestration.load_model_capability_manifest is shared.load_model_capability_manifest
    assert orchestration.compile_composition_execution_manifest is shared.compile_composition_execution_manifest
    tree = ast.parse((ROOT / "src/total_gateway/tool_source_launch.py").read_text("utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and any(a.name == "load_model_capability_manifest" for a in n.names)]
    assert len(imports) == 1 and imports[0].module == "capability_manifest" and imports[0].level == 1


def test_new_shared_authority_is_frozen_without_unfreezing_legacy():
    from tests.golden.p19_r2.test_freeze_and_guards import VerificationPlaneFreezeGuardTests
    frozen = json.loads((ROOT / "docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json").read_bytes())
    for name in ("capability_manifest", "skill_selection", "orchestration", "tool_source_launch"):
        path = f"src/total_gateway/{name}.py"
        assert path in VerificationPlaneFreezeGuardTests.AUTHORITY_SURFACE_FILES
        assert frozen["authority_surface_sha256"][path] == hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
