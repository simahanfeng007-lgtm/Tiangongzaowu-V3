from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import pytest

from contracts import ActionIntent, ActionPermission, ResourceEnvelope, SourceRef
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from life_service.action_intents import ActionIntentReceipt, LifeActionIntentEmitter
from world_understanding.facade import WorldUnderstandingFacade
from world_understanding.inquiry import CuriosityGenerator, ExistingSelfWillAdapter, KnowledgeGapGenerator, SelfWillGatewayBridge, build_inquiry_outcome
from world_understanding.source_adapters import build_autonomous_execution_feedback_envelope
from world_understanding.source_compilers import SPECS, ToolResultCompiler

PRINCIPAL = "a" * 64


def _scope() -> WorldScope:
    bindings=(ScopeBinding(key="repository", value="repo.main"),)
    wid=derive_world_id(life_id="life.A", namespace_anchor="primary")
    return WorldScope(
        life_id="life.A", world_id=wid, domain_id="software", scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(life_id="life.A", world_id=wid, domain_id="software", scope_bindings=bindings),
        principal_scope_hash=PRINCIPAL, privacy_scope="system",
    )


def _inquiry():
    scope=_scope(); subject=WorldRecordRef(record_type="world_entity",record_id="ent.1",revision=1,sha256="1"*64)
    frame=WorldRecordRef(record_type="world_frame",record_id="frame.1",revision=1,sha256="2"*64)
    state=SimpleNamespace(world_state_id="wst.fixture",world_sequence=0,state_sha256="3"*64,scope=scope,frame_ref=frame,unresolved_conflict_refs=(),stale_refs=(subject,),has_valid_hash=lambda:True)
    snapshot=SimpleNamespace(state=state,uncertainty=None)
    gap=KnowledgeGapGenerator().generate(snapshot)[0]
    generator=CuriosityGenerator(); curiosity=generator.build_curiosity(gap,frame_ref=frame,created_at_ms=1_000,expires_at_ms=61_000)
    state_ref=WorldRecordRef(record_type="world_state",record_id="wst.fixture",revision=1,sha256="3"*64)
    return generator.build_inquiry(gap,curiosity,correlation_id="corr.p11",source_world_state_ref=state_ref,inquiry_budget_remaining=8)


def _action_intent(inquiry, source_ref: SourceRef) -> ActionIntent:
    return ActionIntent(
        intent_id="intent.selfwill.1",
        source="life_scheduler",
        life_id=inquiry.scope.life_id,
        principal_scope_hash=inquiry.scope.principal_scope_hash,
        conversation_scope_hash="4"*64,
        request_id="req_"+"5"*64,
        run_id="run_"+"6"*64,
        generation=0,
        action_id="file.read",
        action_version="1.0.0",
        arguments_sha256="7"*64,
        workspace_id="ws.main",
        workspace_scope_hash="8"*64,
        requested_side_effects=("read",),
        requested_resources=ResourceEnvelope(max_runtime_ms=1_000,max_output_bytes=4_096,max_tool_calls=1),
        source_refs=(source_ref,),
        life_snapshot_revision=1,
        life_snapshot_sha256="9"*64,
        created_at_ms=2_000,
        expires_at_ms=3_000,
        intent_sha256="0"*64,
    ).with_computed_sha256()


class _Transport:
    def __init__(self): self.intents=[]
    def submit(self, intent):
        self.intents.append(intent)
        receipt=ActionIntentReceipt(intent.intent_id,intent.intent_sha256,"AUTHORIZED","policy.fixture","")
        return replace(receipt, receipt_sha256=receipt.computed_sha256())


def test_self_will_gateway_bridge_uses_existing_emitter_and_rejects_fake_user_provenance():
    inquiry=_inquiry()
    _, autonomous=ExistingSelfWillAdapter(lambda _:{"decision":"ACCEPT","goal":"Observe the stale entity","reason_codes":["ok"]}).decide(inquiry,decided_at_ms=2_000)
    assert autonomous is not None
    transport=_Transport(); emitter=LifeActionIntentEmitter(transport)
    bridge=SelfWillGatewayBridge(
        emitter=emitter,
        action_intent_factory=lambda _a,q:_action_intent(q,SourceRef(source_type="EXTERNAL_DATA",object_id=q.inquiry_id,object_revision=q.revision,sha256=q.inquiry_sha256)),
    )
    receipt=bridge.submit(autonomous,inquiry)
    assert receipt.status=="AUTHORIZED" and len(transport.intents)==1
    assert transport.intents[0].source=="life_scheduler"

    bad=SelfWillGatewayBridge(
        emitter=emitter,
        action_intent_factory=lambda _a,q:_action_intent(q,SourceRef(source_type="CURRENT_USER_INSTRUCTION",object_id=q.inquiry_id,object_revision=q.revision,sha256=q.inquiry_sha256)),
    )
    with pytest.raises(ValueError,match="provenance"):
        bad.submit(autonomous,inquiry)
    assert len(transport.intents)==1


def test_existing_a5_action_permission_remains_non_executable():
    with pytest.raises(ValueError,match="A5 action cannot be executable"):
        ActionPermission(
            action_id="file.read", action_version="1.0.0", registry_risk="A0", effective_risk="A5",
            effect="read", handler="fixture.read", allowed_side_effects=("read",), path_policy="no_path",
            allow_absolute_paths=False, allow_shell=False, allow_python=False, requires_confirmation=False,
            source_manifest_sha256="1"*64, permission_sha256="0"*64,
        )


def test_failed_autonomous_tool_result_reenters_same_ingress_without_write_success_promotion():
    inquiry=_inquiry()
    envelope=build_autonomous_execution_feedback_envelope(
        source_kind="TOOL_RESULT",
        source_native_id="tool.result.1",
        producer_ref="gateway.runtime",
        payload={"observed_write_effect":True,"write_evidence":{"authoritative":True,"changed_files":["x.txt"]}},
        source_time=WorldTime(valid_from_ms=3_000,observed_at_ms=3_000,recorded_at_ms=3_000),
        scope=inquiry.scope,
        correlation_id=inquiry.correlation_id,
        source_inquiry_id=inquiry.inquiry_id,
        autonomous_intent_id="waut.fixture",
        gateway_intent_id="intent.selfwill.1",
        terminal_status="failure",
    )
    assert envelope.payload_inline["observed_write_effect"] is False
    assert "write_evidence" not in envelope.payload_inline
    rows=ToolResultCompiler(SPECS["TOOL_RESULT"])(envelope)
    assert all(getattr(row,"predicate","") != "filesystem.write_observed" for row in rows)
    receipt=WorldUnderstandingFacade(enabled=True).accept(envelope)
    assert receipt.disposition=="ACCEPTED" and receipt.processed
    assert receipt.ack_only and not receipt.semantic_output


def test_inquiry_outcome_closes_lineage_but_failure_remains_unresolved():
    inquiry=_inquiry()
    _, autonomous=ExistingSelfWillAdapter(lambda _:{"decision":"ACCEPT","goal":"Observe the stale entity","reason_codes":["ok"]}).decide(inquiry,decided_at_ms=2_000)
    assert autonomous is not None
    with pytest.raises(ValueError,match="independent reality results"):
        build_inquiry_outcome(inquiry,self_will_decision="ACCEPT",closed_at_ms=3_000,autonomous_intent=autonomous,resolved=True,residual_gap_milli=0,information_gain_milli=1000)
    failure_ref=WorldRecordRef(record_type="world_source_envelope",record_id="wenv.failure",revision=1,sha256="f"*64)
    outcome=build_inquiry_outcome(
        inquiry,self_will_decision="ACCEPT",closed_at_ms=3_001,autonomous_intent=autonomous,
        resulting_source_envelope_refs=(failure_ref,),resolved=False,residual_gap_milli=900,information_gain_milli=100,
    )
    assert outcome.has_valid_hash() and not outcome.resolved
    assert outcome.inquiry_id==inquiry.inquiry_id and outcome.autonomous_intent_id==autonomous.autonomous_intent_id
    assert outcome.empirical_evidence_weight_milli==0 and outcome.evidence_authority=="none"


def test_p11_has_no_second_scheduler_executor_gateway_or_direct_tool_path():
    root=Path(__file__).resolve().parents[1]/"src"/"world_understanding"/"inquiry"
    text="\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for forbidden in ("threading.Thread","WorldUnderstandingExecutor","AutonomousGateway","omni_body","ToolCall","check_tool_permission"):
        assert forbidden not in text
    policy=(Path(__file__).resolve().parents[1]/"src"/"total_gateway"/"policy_engine.py").read_text(encoding="utf-8")
    assert 'if computed_risk == "A5":' in policy and 'outcome = "REJECT"' in policy
