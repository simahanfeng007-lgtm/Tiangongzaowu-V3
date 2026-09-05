from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from contracts import canonical_sha256
from omni_body_skill.tool_contracts import build_action_schema_catalog
from total_gateway.action_registry import ActionRegistryError, compile_action_authority
from v3.fact_kernel import compile_manifest
from total_gateway.tool_manifest_evolution import (
    ManifestEvolutionError,
    review_manifest_evolution,
)


class TestOnlyRuntime:
    def _action_skill_list(self):
        raise AssertionError("compilation must not invoke an action")

    def _action_demo_read(self):
        raise AssertionError("compilation must not invoke an action")


def actions():
    return {
        "skill.list": {"risk": "A0", "effect": "read", "implemented": True},
        "demo.read": {"risk": "A0", "effect": "read", "implemented": True},
        "demo.alias": {"risk": "A0", "effect": "read", "implemented": True,
                       "alias_to": "demo.read"},
        "demo.future": {"risk": "A2", "effect": "write", "implemented": False},
    }


def compiled(metadata=None, *, dynamic_actions=()):
    metadata = actions() if metadata is None else metadata
    return compile_manifest(metadata, TestOnlyRuntime, dynamic_actions=dynamic_actions,
                            action_schema_catalog=build_action_schema_catalog(metadata))


def rehash(document):
    document["source_hash"] = canonical_sha256(document["capabilities"])
    document["validation"]["source_hash"] = document["source_hash"]
    return document


def review(before, after, requested=("demo.read",)):
    return review_manifest_evolution(before, after, requested_action_ids=requested)


def test_existing_compiler_projections_keep_all_rows_and_both_hash_domains():
    result = compiled()
    runtime, gateway = result.to_dict(), result.to_gateway_dict()
    assert gateway["capabilities"] == runtime["capabilities"]
    assert gateway["source_hash"] == canonical_sha256(gateway["capabilities"])
    assert runtime["source_hash"] == canonical_sha256({
        "runtime_class": runtime["runtime_class"],
        "dynamic_actions": runtime["dynamic_actions"],
        "capabilities": runtime["capabilities"],
    })
    assert runtime["source_hash"] != gateway["source_hash"]
    authority = compile_action_authority(gateway, generated_at_ms=0)
    assert authority.manifest_sha256 == canonical_sha256(gateway)
    assert {row.action_id for row in authority.registry.permissions} == {
        "demo.alias", "demo.read", "skill.list"
    }
    assert gateway["capabilities"]["demo.future"]["executable"] is False
    assert gateway["total"] == 4 and gateway["unavailable"] == 1


def test_gateway_projection_is_detached_and_rejects_mutated_compiler_output():
    result = compiled()
    gateway = result.to_gateway_dict()
    gateway["capabilities"]["demo.read"]["argument_schema"]["action"] = "changed"
    assert result.to_gateway_dict()["capabilities"]["demo.read"]["argument_schema"]["action"] == "demo.read"
    result.capabilities["demo.read"].argument_schema["action"] = "changed"
    with pytest.raises(ValueError, match="changed or is unhealthy"):
        result.to_gateway_dict()


def test_gateway_projection_rejects_unhealthy_validation():
    result = compiled()
    for invalid in ({"ok": False}, {"ok": True, "source_hash": result.source_hash,
                                   "executable_without_route": ["demo.read"]}):
        with pytest.raises(ValueError, match="unhealthy"):
            replace(result, validation=invalid).to_gateway_dict()


def test_no_manifest_delta_never_means_source_publication_is_approved():
    document = compiled().to_gateway_dict()
    result = review(document, document)
    assert result.has_valid_sha256()
    assert result.deltas == ()
    assert result.requested_without_manifest_delta == ("demo.read",)
    assert result.may_authorize is False and result.may_execute is False and result.may_publish is False
    assert not replace(result, may_publish=True).has_valid_sha256()


def test_metadata_change_does_not_create_permission_hash_churn():
    metadata = actions()
    metadata["demo.read"]["summary"] = "new description"
    before, after = compiled().to_gateway_dict(), compiled(metadata).to_gateway_dict()
    result = review(before, after)
    assert len(result.deltas) == 1
    delta = result.deltas[0]
    assert delta.action_id == "demo.read"
    assert delta.changed_fields == ("metadata_sha256", "summary")
    assert delta.permission_changed_fields == ()
    assert delta.before_permission_sha256 != delta.after_permission_sha256
    assert result.unexpected_action_ids == ()
    assert before["capabilities"]["demo.read"].get("summary") == ""


def test_effect_floor_downgrade_exposes_unchanged_alias_collateral():
    high_risk = actions()
    high_risk["demo.read"]["effect"] = "execute"
    before, after = compiled(high_risk).to_gateway_dict(), compiled().to_gateway_dict()
    result = review(before, after)
    assert result.unexpected_action_ids == ("demo.alias",)
    assert result.risk_downgraded_action_ids == ("demo.alias", "demo.read")
    assert result.newly_a0_action_ids == ("demo.alias", "demo.read")
    alias = result.deltas[0]
    assert alias.changed_fields == ()
    assert "effective_risk" in alias.permission_changed_fields
    assert "EFFECTIVE_RISK_DOWNGRADED" in alias.review_reasons
    assert alias.before_effective_risk == "A3" and alias.after_effective_risk == "A0"


def test_added_removed_and_newly_executable_actions_are_all_reported():
    metadata = actions()
    del metadata["demo.alias"]
    metadata["demo.future"]["implemented"] = True
    metadata["demo.new"] = {"risk": "A0", "effect": "verify", "implemented": True}
    before = compiled().to_gateway_dict()
    after = compiled(metadata, dynamic_actions=("demo.future", "demo.new")).to_gateway_dict()
    result = review(before, after, ("demo.alias", "demo.future", "demo.new"))
    assert [(delta.action_id, delta.kind) for delta in result.deltas] == [
        ("demo.alias", "REMOVED"), ("demo.future", "MODIFIED"), ("demo.new", "ADDED")
    ]
    assert result.newly_executable_action_ids == ("demo.future", "demo.new")
    assert result.newly_a0_action_ids == ("demo.new",)
    assert result.unexpected_action_ids == ()


def test_a5_candidate_is_rejected_by_the_existing_gateway_compiler():
    metadata = actions()
    metadata["demo.read"]["risk"] = "A5"
    with pytest.raises(ActionRegistryError, match="A5"):
        review(compiled().to_gateway_dict(), compiled(metadata).to_gateway_dict())


def test_alias_remap_reports_canonical_action_and_schema_changes():
    metadata = actions()
    metadata["demo.alias"]["alias_to"] = "skill.list"
    result = review(compiled().to_gateway_dict(), compiled(metadata).to_gateway_dict(), ("demo.alias",))
    assert len(result.deltas) == 1
    assert result.deltas[0].before_canonical_action_id == "demo.read"
    assert result.deltas[0].after_canonical_action_id == "skill.list"
    assert "SCHEMA_OR_VALIDATOR_CHANGED" in result.deltas[0].review_reasons


@pytest.mark.parametrize("mutate, message", [
    (lambda value: value.update(source_hash="0" * 64), "source hash"),
    (lambda value: value.update(total=9000), "counts"),
    (lambda value: value.update(unavailable=False), "unavailable count"),
    (lambda value: value["capabilities"]["demo.future"].update(id="forged"), "action row"),
    (lambda value: value["capabilities"]["demo.future"].update(executable="false"), "action row"),
])
def test_invalid_manifest_not_treated_as_reviewable(mutate, message):
    before = compiled().to_gateway_dict()
    after = copy.deepcopy(before)
    mutate(after)
    with pytest.raises(ValueError, match=message):
        review(before, after)


def test_rehashed_schema_forgery_is_rejected_by_existing_schema_authority():
    before = compiled().to_gateway_dict()
    after = copy.deepcopy(before)
    after["capabilities"]["demo.read"]["argument_schema_sha256"] = "f" * 64
    with pytest.raises(ActionRegistryError, match="schema hash"):
        review(before, rehash(after))


@pytest.mark.parametrize("requested", [("unknown",), ("demo.read", "demo.read"), ("skill.list", "demo.read")])
def test_review_rejects_missing_or_ambiguous_requested_actions(requested):
    document = compiled().to_gateway_dict()
    with pytest.raises(ManifestEvolutionError):
        review(document, document, requested)


def test_review_deterministic_under_input_order_and_detached_from_mutation():
    metadata = actions()
    metadata["demo.read"]["summary"] = "new summary"
    before, after = compiled().to_gateway_dict(), compiled(metadata).to_gateway_dict()
    first = review(before, after)
    reordered = copy.deepcopy(after)
    reordered["capabilities"] = dict(reversed(list(reordered["capabilities"].items())))
    assert review(before, reordered) == first
    after["capabilities"]["demo.read"]["summary"] = "mutated after observation"
    assert first.has_valid_sha256()


def test_new_source_manifest_does_not_mutate_an_already_loaded_authority():
    old_document = compiled().to_gateway_dict()
    pinned = compile_action_authority(old_document, generated_at_ms=0)
    snapshot = pinned.manifest
    metadata = actions()
    metadata["demo.read"]["effect"] = "execute"
    next_document = compiled(metadata).to_gateway_dict()
    next_authority = compile_action_authority(next_document, generated_at_ms=0)
    review(old_document, next_document)
    assert pinned.manifest == snapshot
    assert pinned.manifest_sha256 != next_authority.manifest_sha256
    assert next(item for item in pinned.registry.permissions if item.action_id == "demo.read").effective_risk == "A0"
    assert next(item for item in next_authority.registry.permissions if item.action_id == "demo.read").effective_risk == "A3"
