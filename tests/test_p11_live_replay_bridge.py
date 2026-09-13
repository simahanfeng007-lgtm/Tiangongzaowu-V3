from __future__ import annotations

from dataclasses import replace
import json

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from total_gateway.p11_live_replay_bridge import (
    P11LiveReplayError,
    observe_p11_live_dynamic_path,
)
from total_gateway.run_observation import ModelSnapshot, build_run_observation
from world_understanding.capability_composition import (
    P11ModelProfileV1,
    P11StaticPathObservationV1,
    compile_capability_composition_plan,
    parse_composition_proposal,
    plan_binds_proposal,
)

from tests.test_capability_composition_p4 import _single_read_fixture
from tests.test_composition_activation_shadow_p7a import (
    _propose,
    _validated_read_plan,
)


TASK_ID = "live.core.001"
TASK_INPUT = "3" * 64
H = "a" * 64
H2 = "b" * 64


def _profile() -> P11ModelProfileV1:
    return P11ModelProfileV1(
        profile_id="live.primary",
        provider_id="provider.live",
        model_id="model.alpha",
        model_revision="2026-09-13",
        role="PRIMARY",
        profile_sha256="0" * 64,
    ).with_computed_sha256()


def _static() -> P11StaticPathObservationV1:
    return P11StaticPathObservationV1(
        task_id=TASK_ID,
        query_sha256=TASK_INPUT,
        selection_id="selection.live.core.001",
        selection_sha256="4" * 64,
        skill_catalog_sha256="5" * 64,
        capability_manifest_sha256=H,
        selected_skill_id="skill.legacy.read",
        planned_action_ids=("artifact.read",),
        context_tokens=2_400,
        proposed_only=True,
        authorizes=False,
        may_execute=False,
        observation_sha256="0" * 64,
    ).with_computed_sha256()


def _legacy_output(static: P11StaticPathObservationV1) -> str:
    return canonical_json_bytes(
        {**static.payload(), "observation_sha256": static.observation_sha256}
    ).decode("utf-8")


def _run_observation(
    candidate_output: str | None,
    *,
    created_at_ms: int = 1,
    model: str = "model.alpha@2026-09-13",
    legacy_output: str | None = None,
):
    return build_run_observation(
        scenario_cluster_id="p11.live.core",
        gold_id=TASK_ID,
        gold_version=1,
        input_sha256=TASK_INPUT,
        context_sha256="6" * 64,
        memory_revision=1,
        registry_sha256="7" * 64,
        policy_sha256="8" * 64,
        coverage_sha256="9" * 64,
        model_snapshot=ModelSnapshot(
            provider="provider.live",
            model=model,
            temperature="0",
            top_p="1",
            seed_strategy="provider-default",
            tool_schema_sha256=H2,
            timeout_ms=30_000,
            retry_limit=0,
            fallback="none",
        ),
        router_code_sha256="c" * 64,
        prompt_sha256="d" * 64,
        candidate_output=candidate_output,
        legacy_output=(
            _legacy_output(_static())
            if legacy_output is None
            else legacy_output
        ),
        latency_ms=100,
        terminal_state=("incomplete" if candidate_output is None else "completed"),
        incomplete_reasons=(
            ("shadow.candidate_output_missing",)
            if candidate_output is None
            else ()
        ),
        created_at_ms=created_at_ms,
    )


def _valid_inputs():
    action_registry, plan, validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    _same_registry, candidates, _context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    assert plan_binds_proposal(plan, proposal)
    static = _static()
    shadow = _propose(
        action_registry,
        plan,
        validation,
        verifier_registry,
        bindings,
        legacy_allowed_action_ids=static.planned_action_ids,
    )
    return candidates, document, plan, validation, shadow, static


def test_live_bridge_binds_raw_pair_exact_model_revision_and_plan() -> None:
    candidates, document, plan, validation, shadow, static = _valid_inputs()
    result = observe_p11_live_dynamic_path(
        task_id=TASK_ID,
        task_input_sha256=TASK_INPUT,
        goal_fingerprint_sha256=plan.goal_fingerprint,
        model=_profile(),
        static_path=static,
        candidates=candidates,
        primary_observation=_run_observation(document),
        context_tokens=620,
        plan=plan,
        validation=validation,
        shadow=shadow,
    )

    assert result.has_valid_sha256()
    assert result.binding.has_valid_sha256()
    assert result.binding.selected_attempt == "PRIMARY"
    assert result.binding.plan_sha256 == plan.plan_sha256
    assert result.dynamic_path.has_valid_sha256()
    assert result.dynamic_path.activation_ready is True
    assert result.dynamic_path.live_replay_binding_sha256 == (
        result.binding.binding_sha256
    )
    assert result.dynamic_path.proposed_only is True
    assert result.dynamic_path.authorizes is False
    assert result.dynamic_path.may_execute is False


def test_live_bridge_records_one_parse_repair_without_hiding_primary() -> None:
    candidates, document, plan, validation, shadow, static = _valid_inputs()
    result = observe_p11_live_dynamic_path(
        task_id=TASK_ID,
        task_input_sha256=TASK_INPUT,
        goal_fingerprint_sha256=plan.goal_fingerprint,
        model=_profile(),
        static_path=static,
        candidates=candidates,
        primary_observation=_run_observation("not a proposal", created_at_ms=1),
        repair_observation=_run_observation(document, created_at_ms=2),
        context_tokens=640,
        plan=plan,
        validation=validation,
        shadow=shadow,
    )

    assert result.binding.primary_observation_sha256 != (
        result.binding.repair_observation_sha256
    )
    assert result.binding.selected_attempt == "REPAIR"
    assert result.dynamic_path.repair_attempted is True
    assert result.dynamic_path.repaired is True
    assert result.dynamic_path.parse_succeeded is True


def test_live_bridge_rejects_repair_record_without_candidate_output() -> None:
    candidates, _document, plan, _validation, _shadow, static = _valid_inputs()
    with pytest.raises(P11LiveReplayError, match="p11.live.repair_output_missing"):
        observe_p11_live_dynamic_path(
            task_id=TASK_ID,
            task_input_sha256=TASK_INPUT,
            goal_fingerprint_sha256=plan.goal_fingerprint,
            model=_profile(),
            static_path=static,
            candidates=candidates,
            primary_observation=_run_observation("not a proposal", created_at_ms=1),
            repair_observation=_run_observation(None, created_at_ms=2),
            context_tokens=640,
        )


def test_live_bridge_rejects_primary_record_without_candidate_output() -> None:
    candidates, _document, plan, _validation, _shadow, static = _valid_inputs()
    with pytest.raises(P11LiveReplayError, match="p11.live.primary_output_missing"):
        observe_p11_live_dynamic_path(
            task_id=TASK_ID,
            task_input_sha256=TASK_INPUT,
            goal_fingerprint_sha256=plan.goal_fingerprint,
            model=_profile(),
            static_path=static,
            candidates=candidates,
            primary_observation=_run_observation(None),
            context_tokens=640,
        )


def test_live_bridge_rejects_non_completed_pair_even_with_outputs() -> None:
    candidates, document, plan, _validation, _shadow, static = _valid_inputs()
    timeout = _run_observation(document).model_copy(
        update={"terminal_state": "timeout", "record_sha256": "0" * 64}
    ).with_computed_sha256()
    with pytest.raises(
        P11LiveReplayError, match="p11.live.run_observation_not_completed"
    ):
        observe_p11_live_dynamic_path(
            task_id=TASK_ID,
            task_input_sha256=TASK_INPUT,
            goal_fingerprint_sha256=plan.goal_fingerprint,
            model=_profile(),
            static_path=static,
            candidates=candidates,
            primary_observation=timeout,
            context_tokens=640,
        )


def test_live_bridge_preserves_failed_parse_with_task_goal() -> None:
    candidates, _document, plan, _validation, _shadow, static = _valid_inputs()
    result = observe_p11_live_dynamic_path(
        task_id=TASK_ID,
        task_input_sha256=TASK_INPUT,
        goal_fingerprint_sha256=plan.goal_fingerprint,
        model=_profile(),
        static_path=static,
        candidates=candidates,
        primary_observation=_run_observation("not a proposal"),
        context_tokens=620,
    )

    assert result.has_valid_sha256()
    assert result.dynamic_path.parse_succeeded is False
    assert result.dynamic_path.plan_succeeded is False
    assert result.dynamic_path.error_code == "proposal.dsl.missing_end"
    assert result.dynamic_path.goal_fingerprint_sha256 == plan.goal_fingerprint


@pytest.mark.parametrize(
    ("observation", "code"),
    (
        (
            _run_observation(
                "not a proposal",
                model="model.alpha@wrong-revision",
            ),
            "p11.live.run_observation_model_drift",
        ),
        (
            _run_observation(
                "not a proposal",
                legacy_output=json.dumps({"forged": True}),
            ),
            "p11.live.run_observation_static_drift",
        ),
        (
            _run_observation("not a proposal").model_copy(
                update={"record_sha256": "0" * 64}
            ),
            "p11.live.run_observation_hash_invalid",
        ),
    ),
)
def test_live_bridge_rejects_model_static_or_record_drift(
    observation, code: str
) -> None:
    candidates, _document, plan, _validation, _shadow, static = _valid_inputs()
    with pytest.raises(P11LiveReplayError, match=code):
        observe_p11_live_dynamic_path(
            task_id=TASK_ID,
            task_input_sha256=TASK_INPUT,
            goal_fingerprint_sha256=plan.goal_fingerprint,
            model=_profile(),
            static_path=static,
            candidates=candidates,
            primary_observation=observation,
            context_tokens=620,
        )


def test_live_bridge_rejects_plan_compiled_from_different_raw_proposal() -> None:
    candidates, document, plan, _validation, _shadow, static = _valid_inputs()
    changed = json.loads(document)
    changed["rationale_tags"] = ["rationale.different"]
    changed_document = json.dumps(changed, ensure_ascii=False, sort_keys=True)
    changed_proposal = parse_composition_proposal(changed_document, candidates)
    action_registry, _same_candidates, context, _same_document = (
        _single_read_fixture()
    )
    changed_plan = compile_capability_composition_plan(
        changed_proposal,
        candidates,
        context,
        action_registry,
    )
    assert changed_plan.plan_sha256 != plan.plan_sha256

    with pytest.raises(
        P11LiveReplayError, match="p11.live.plan_proposal_binding_invalid"
    ):
        observe_p11_live_dynamic_path(
            task_id=TASK_ID,
            task_input_sha256=TASK_INPUT,
            goal_fingerprint_sha256=plan.goal_fingerprint,
            model=_profile(),
            static_path=static,
            candidates=candidates,
            primary_observation=_run_observation(document),
            context_tokens=620,
            plan=changed_plan,
        )


def test_live_bridge_has_no_execution_or_business_store_surface() -> None:
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "p11_live_replay_bridge.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "GatewayStateStore",
        "ExecutionTicket",
        "OmniCapabilityGrant",
        "BodyRuntime",
        "CompletionGate",
        "RunObservationStore",
        ".append(",
        ".execute(",
    ):
        assert forbidden not in source
