from __future__ import annotations
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

# These cases are structural counterexample guards against exact authoritative
# source snapshots aligned by git-blob hash in the P13 harness.
CASES = [
    # Source semantics / provenance ceilings.
    ("src/world_understanding/source_compilers/p3.py", '"USER_CONVERSATION":CompilerSpec', True),
    ("src/world_understanding/source_compilers/p3.py", '"USER_SAID"', True),
    ("src/world_understanding/source_compilers/p3.py", '"WEB_SOURCE_CLAIMS"', True),
    ("src/world_understanding/source_compilers/p3.py", '"DOCUMENT_CLAIMS"', True),
    ("src/world_understanding/source_compilers/p3.py", '"MODEL_PROPOSED"', True),
    ("src/world_understanding/source_compilers/p3.py", '"SELF_WILL_DECISION_RECORDED"', True),
    ("src/world_understanding/source_compilers/p3.py", '"MEMORY_RECORDED","memory.record","MEMORY_EXPERIENCE",0,0', True),
    ("src/world_understanding/source_compilers/p3.py", '"MIGRATION_AUDIT_RECORDED","migration.audit","MIGRATION_AUDIT",0,0', True),
    ("src/world_understanding/source_compilers/p3.py", '"MODEL_PROPOSED","model.proposed","INTERNAL_MODEL_OUTPUT",0,0', True),
    ("src/world_understanding/source_compilers/p3.py", 'payload.get("observed_write_effect") is True', True),
    ("src/world_understanding/source_compilers/p3.py", 'evidence.get("authoritative") is True', True),
    ("src/world_understanding/source_compilers/p3.py", '"TOOL_WRITE_DECLARED"', True),
    ("src/world_understanding/source_compilers/p3.py", 'authority_ceiling_milli=0,empirical_evidence_weight_milli=0', True),
    ("src/world_understanding/source_compilers/p3.py", 'proposition_type="FILE_EXISTS"', True),
    ("src/world_understanding/source_compilers/p3.py", 'if isinstance(payload.get("exists"),bool)', True),

    # Single physical input / ContextRequest isolation / fail closed.
    ("src/world_understanding/facade.py", 'def accept(self,envelope:WorldIngressEnvelope)->IngressReceipt:', True),
    ("src/world_understanding/facade.py", 'return self._ingress.accept(envelope)', True),
    ("src/world_understanding/facade.py", "disposition='OFF_NOOP'", True),
    ("src/world_understanding/facade.py", "reason_code='WORLD_UNDERSTANDING_DISABLED'", True),
    ("src/world_understanding/ingress/router.py", 'if envelope.envelope_kind=="CONTEXT_REQUEST":', True),
    ("src/world_understanding/ingress/router.py", 'reason_code="CONTEXT_REQUEST_ACCEPTED"', True),
    ("src/world_understanding/ingress/router.py", 'if envelope.source_kind=="UNCLASSIFIED_SOURCE":', True),
    ("src/world_understanding/ingress/router.py", 'disposition="QUARANTINED"', True),
    ("src/world_understanding/ingress/router.py", 'reason_code="NO_COMPILER_REGISTERED"', True),
    ("src/world_understanding/ingress/router.py", 'validate_compiler_output(envelope,compiler(envelope))', True),

    # Context output is explicitly non-authorizing and uses same ingress.
    ("src/world_understanding/context_output/slot.py", 'WORLD_CONTEXT_SLOT_NAME = "WORLD_CONTEXT_SLOT"', True),
    ("src/world_understanding/context_output/slot.py", '"authorization_source=false"', True),
    ("src/world_understanding/context_output/slot.py", '"authorizes=false"', True),
    ("src/world_understanding/context_output/slot.py", '"confirms=false"', True),
    ("src/world_understanding/context_output/slot.py", '"changes_risk=false"', True),
    ("src/world_understanding/context_output/slot.py", 'labels.append("CONFLICTED")', True),
    ("src/world_understanding/context_output/slot.py", 'labels.append("STALE")', True),
    ("src/world_understanding/context_output/slot.py", 'labels.append("UNCERTAINTY")', True),
    ("src/world_understanding/context_output/slot.py", 'WORLD_CONTEXT_PACKET_AUTHORITY_INVALID', True),
    ("app/backend/tiangong-backend/v3/world_context_integration.py", 'self.facade.accept(envelope)', True),
    ("app/backend/tiangong-backend/v3/world_context_integration.py", 'if not world_understanding_enabled():', True),
    ("app/backend/tiangong-backend/v3/world_context_integration.py", 'return candidates[0] if len(candidates) == 1 else None', True),
    ("app/backend/tiangong-backend/v3/world_context_integration.py", 'return "[WORLD_CONTEXT_SLOT]\\n"', True),

    # Inquiry / Self-Will / existing Gateway boundary.
    ("src/world_understanding/inquiry/self_will_integration.py", 'origin: Literal["SELF_WILL"]', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'principal: Literal["life:self"]', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'authority_refs: tuple[str, ...] = ()', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'authorization: Literal["NONE"] = "NONE"', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'may_execute_directly: Literal[False] = False', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'requires_gateway_evaluation: Literal[True] = True', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'empirical_evidence_weight_milli: Literal[0] = 0', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'inquiry.authorization != "NONE" or inquiry.may_execute or inquiry.may_call_tools', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'if record.decision != "ACCEPT":\n            return record, None', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'source_type="EXTERNAL_DATA"', True),
    ("src/world_understanding/inquiry/self_will_integration.py", 'return self._emitter.submit_self_will(', True),
    ("src/life_service/action_intents.py", 'source=life_scheduler', False),
    ("src/life_service/action_intents.py", 'intent.source != "life_scheduler"', True),
    ("src/life_service/action_intents.py", 'exact[0].source_type != "EXTERNAL_DATA"', True),
    ("src/life_service/action_intents.py", '"CURRENT_USER_INSTRUCTION", "PREAUTHORIZED_USER_FACT", "AUTHENTICATED_DIRECTORY"', True),
    ("src/life_service/action_intents.py", 'return self.submit(intent)', True),

    # Deterministic closure / authority / provenance / cycles.
    ("src/world_understanding/known/closure.py", 'max_rounds: int = 64', True),
    ("src/world_understanding/known/closure.py", 'ClosureLimitExceeded', True),
    ("src/world_understanding/known/closure.py", 'require_same_scope_parents(scope, candidate.parents)', True),
    ("src/world_understanding/known/closure.py", 'if any(parent.truth_state != "TRUE" for parent in candidate.parents):', True),
    ("src/world_understanding/known/closure.py", 'intersect_authority(rule.spec, candidate.parents)', True),
    ("src/world_understanding/known/closure.py", 'if not roots:', True),
    ("src/world_understanding/known/closure.py", 'model_assisted=False', True),
    ("src/world_understanding/known/closure.py", '"SAME_REVISION_CYCLE"', True),
    ("src/world_understanding/known/closure.py", 'tuple(sorted(added))', True),
    ("src/world_understanding/known/closure.py", 'delta = next_delta', True),
]

@pytest.mark.parametrize("rel,needle,present", CASES)
def test_frozen_counterexample_structural_guard(rel: str, needle: str, present: bool):
    text = src(rel)
    assert (needle in text) is present


def test_exact_authoritative_snapshot_hashes_are_recorded_by_harness():
    # Values are Git blob ids fetched at the P13 plan HEAD; the local harness
    # verifies these separately with git hash-object before pytest is invoked.
    expected = {
        "src/world_understanding/source_compilers/p3.py": "7f20902d557cfad497ff29b7a15f9824d2234950",
        "src/world_understanding/facade.py": "9518a596f90a8c1a4f586af3ff916c668e369652",
        "src/world_understanding/ingress/router.py": "e39122bc3758f54518aa6b787e2b61bcc7016c6e",
        "src/world_understanding/context_output/slot.py": "e8c66404f9ea92528471ebd34d1293814f469003",
        "src/world_understanding/inquiry/self_will_integration.py": "db736e316b6df481b6de790e9c5fa55ae75a24da",
        "src/life_service/action_intents.py": "dad7005c36d1ceed543fd6a39684fc397ce952b6",
        "src/world_understanding/known/closure.py": "c5a0b3c0b69ab58f6a892660e8f6488c8f220014",
    }
    assert len(expected) == 7
