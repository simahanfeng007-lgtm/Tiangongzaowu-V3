"""Existing source compiler input rejection, not runtime risk approval."""

import pytest

from v3.fact_kernel import compile_manifest


class CompilerOnlyRuntime:
    def _action_demo_read(self):
        raise AssertionError("metadata compilation must not execute a handler")


@pytest.mark.parametrize("field,value", [
    ("effect", "exeucte"), ("effect", "READ"), ("effect", " read "),
    ("effect", ""), ("effect", None), ("effect", False), ("effect", []),
    ("risk", "A9"), ("risk", "a0"), ("risk", " A0 "),
    ("risk", ""), ("risk", None), ("risk", False), ("risk", 0),
    ("implemented", "false"), ("implemented", "true"),
    ("implemented", 0), ("implemented", 1), ("implemented", None),
    ("implemented", []), ("implemented", {}),
    ("alias_to", None), ("alias_to", False), ("alias_to", 1),
    ("alias_to", []), ("alias_to", " demo.read "),
])
def test_explicit_malformed_authority_metadata_is_never_defaulted(field, value):
    metadata = {"risk": "A0", "effect": "read", "implemented": True}
    metadata[field] = value
    with pytest.raises(ValueError, match="source action metadata.*" + field):
        compile_manifest({"demo.read": metadata}, CompilerOnlyRuntime)


@pytest.mark.parametrize("actions", [
    None, [], {"": {}}, {" demo.read": {}}, {1: {}},
    {"demo.read": None}, {"demo.read": []},
])
def test_malformed_source_rows_cannot_be_ignored_or_coerced(actions):
    with pytest.raises(ValueError, match="source action"):
        compile_manifest(actions, CompilerOnlyRuntime)


@pytest.mark.parametrize("dynamic", [
    "demo.read", None, (None,), (1,), ("",), (" demo.read",), ("undeclared.action",),
])
def test_dynamic_route_identifiers_cannot_be_coerced(dynamic):
    with pytest.raises(ValueError, match="dynamic action"):
        compile_manifest({"demo.read": {"implemented": True}}, CompilerOnlyRuntime,
                         dynamic_actions=dynamic)


def test_legacy_omissions_keep_their_existing_semantics_without_approval():
    manifest = compile_manifest({
        "demo.read": {"implemented": True},
        "demo.planned": {},
        "demo.write": {"risk": "A2", "implemented": False},
    }, CompilerOnlyRuntime)
    assert manifest.capabilities["demo.read"].effect == "read"
    assert manifest.capabilities["demo.read"].risk == "A0"
    assert manifest.capabilities["demo.read"].executable is True
    assert manifest.capabilities["demo.planned"].executable is False
    assert manifest.capabilities["demo.write"].effect == "write"


@pytest.mark.parametrize("risk", ["A0", "A1", "A2", "A3", "A4", "A5"])
@pytest.mark.parametrize("effect", ["read", "verify", "create", "write", "update", "execute"])
def test_valid_risk_and_effect_are_preserved_for_existing_gateway_review(risk, effect):
    manifest = compile_manifest({"demo.read": {
        "risk": risk, "effect": effect, "implemented": True,
    }}, CompilerOnlyRuntime)
    row = manifest.capabilities["demo.read"]
    assert row.risk == risk and row.effect == effect
    # Compiling A5 here is not admission: the existing Gateway rejects it.


def test_truthful_unavailable_declaration_is_not_promoted_by_a_present_handler():
    manifest = compile_manifest({"demo.read": {
        "risk": "A0", "effect": "read", "implemented": False,
    }}, CompilerOnlyRuntime)
    row = manifest.capabilities["demo.read"]
    assert row.executable is False and row.handler == ""
