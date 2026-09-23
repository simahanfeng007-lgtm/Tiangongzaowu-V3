"""Independent Body consumers retain the issuer's fixed wire boundaries."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from contracts import canonical_sha256
from contracts import composition_profile as issuer
from omni_body_skill.tools import omni_capability as consumer
from omni_body_skill.tools.composition_path import probe_composition_write_target as body_probe
from runtime_security.composition_path import probe_composition_write_target as issuer_probe


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src" / "omni_body_skill"
PROFILE_FUNCTIONS = {
    "composition_profile_valid", "composition_profile_fields",
    "composition_authority_allowed", "validate_composition_arguments",
}


def _profile_definitions(path):
    definitions = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.FunctionDef) and node.name in PROFILE_FUNCTIONS:
            definitions[node.name] = ast.dump(node, include_attributes=False)
    return definitions


def test_fixed_profile_consumer_definitions_match_issuer():
    expected = _profile_definitions(ROOT / "src" / "contracts" / "composition_profile.py")
    actual = _profile_definitions(SKILL / "tools" / "omni_capability.py")
    assert {name: actual[name] for name in expected} == expected
    assert consumer.WORKSPACE_WRITE_PROFILE_SHA256 == issuer.WORKSPACE_WRITE_PROFILE_SHA256
    assert consumer.WORKSPACE_PYTHON_PROFILE_SHA256 == issuer.WORKSPACE_PYTHON_PROFILE_SHA256


@pytest.mark.parametrize("name", ["path_identity.py", "composition_path.py"])
def test_native_path_consumer_is_identical_to_issuer(name):
    # Universal newline conversion is only for Git's platform EOL policy.
    assert (SKILL / "tools" / name).read_text(encoding="utf-8") == (
        ROOT / "src" / "runtime_security" / name
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["missing", "file", "directory"])
def test_actual_target_snapshot_and_digest_match_issuer(tmp_path, kind):
    target = tmp_path / "目标_😀.txt"
    if kind == "file":
        target.write_text("paid_total = 412.74\n", encoding="utf-8")
    elif kind == "directory":
        target.mkdir()
    expected = issuer_probe(str(target), tmp_path)
    observed = body_probe(str(target), tmp_path)
    assert observed == expected
    # This is the fixed ASCII-key snapshot schema, not a claim that the
    # independent capability encoder accepts every general contract value.
    assert consumer._sha(observed) == canonical_sha256(expected)
    with pytest.raises(consumer.CapabilityGrantError, match="floats"):
        consumer._sha({**observed, "size_bytes": 0.5})


@pytest.mark.parametrize("target", ["../escape.txt", "file:///escape", "//host/share/file",
                                    "payload.txt:stream", "NUL.txt", "trailing. ",
                                    ".omni_backups/new.txt", "missing/child.txt"])
def test_consumer_keeps_unsafe_target_rejection(tmp_path, target):
    (tmp_path / ".omni_backups").mkdir()
    for probe in (issuer_probe, body_probe):
        with pytest.raises((ValueError, OSError)):
            probe(target, tmp_path)


def test_consumer_keeps_hardlink_rejection(tmp_path):
    target = tmp_path / "original.txt"
    target.write_text("bound", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    os.link(target, alias)
    for probe in (issuer_probe, body_probe):
        with pytest.raises(ValueError, match="hard link"):
            probe(str(alias), tmp_path)


def test_signed_consumer_and_dynamic_body_package_run_without_gateway_siblings(tmp_path):
    from tests.test_body_verified_profile_consumption import invocation
    from tests.test_omni_capability_guard import CapabilityFixture

    fixture = CapabilityFixture(tmp_path)
    call, _ = invocation(fixture, nonce="standalone-leaf-consumer")
    input_path = tmp_path / "invocation.json"
    input_path.write_text(json.dumps(call), encoding="utf-8")
    script = textwrap.dedent('''
        import importlib.abc, importlib.util, json, pathlib, sys, threading
        root, input_path = map(pathlib.Path, sys.argv[1:])
        forbidden = {'contracts', 'runtime_security', 'total_gateway', 'life_service',
                     'world_understanding', 'communication_service'}
        class NoGatewaySiblings(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] in forbidden:
                    raise AssertionError('forbidden sibling import: ' + fullname)
        sys.meta_path.insert(0, NoGatewaySiblings())
        sys.path.insert(0, str(root / 'app' / 'backend' / 'tiangong-backend'))
        skill = root / 'src' / 'omni_body_skill'
        spec = importlib.util.spec_from_file_location('standalone_verifier', skill / 'tools' / 'omni_capability.py')
        capability = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(capability)
        assert capability.__package__ == ''
        call = json.loads(input_path.read_text(encoding='utf-8'))
        kwargs = dict(action=call['action'], target=call['target'], args=call['args'],
                      workspace=call['workspace'], runtime_meta=call['__runtime'])
        verified = capability.verify_capability_grant(call['__capability_grant'], **kwargs)
        try:
            capability.verify_capability_grant(call['__capability_grant'], **kwargs)
        except capability.CapabilityGrantError as exc:
            assert 'replay' in str(exc)
        else:
            raise AssertionError('replayed nonce accepted')
        package_name = '_isolated_skill_under_arbitrary_package_name'
        spec = importlib.util.spec_from_file_location(package_name, skill / '__init__.py',
                                                      submodule_search_locations=[str(skill)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
        body = importlib.import_module(package_name + '.tools.omni_body_tool')
        sealed = verified['composition_execution_binding']
        runtime = body.BodyRuntime.__new__(body.BodyRuntime)
        runtime.workspace = pathlib.Path(call['workspace'])
        runtime.config = body.BodyRuntimeConfig(workspace=call['workspace'], allow_absolute_paths=True,
            execution_profile_id=sealed['execution_profile_id'],
            execution_profile_sha256=sealed['execution_profile_sha256'],
            target_snapshot_sha256=sealed['target_snapshot_sha256'])
        runtime.backup_dir = runtime.workspace / '.omni_backups'
        runtime.backup_dir.mkdir()
        runtime._execution_state = threading.local()
        result = runtime._action_file_write('leaf-write', call['target'], call['args'])
        assert result['write_evidence']['changed'] is True
        assert pathlib.Path(call['target']).read_text(encoding='utf-8') == call['args']['content']
        try:
            runtime._action_file_write('stale-write', call['target'], {'content': 'must not write'})
        except body.OmniBodyError as exc:
            assert 'signed target snapshot changed' in str(exc)
        else:
            raise AssertionError('stale target snapshot accepted')
        assert pathlib.Path(call['target']).read_text(encoding='utf-8') == call['args']['content']
        assert not (forbidden & {name.split('.')[0] for name in sys.modules})
        print('standalone signed capability and Body handler verified')
    ''')
    result = subprocess.run([sys.executable, "-I", "-c", script, str(ROOT), str(input_path)],
                            env={**os.environ, **fixture.env()}, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "standalone signed capability and Body handler verified" in result.stdout
