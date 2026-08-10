"""Deterministic source->DirectKnown compiler primitives."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldValue
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord,derive_direct_known_id
from contracts.world_understanding.observability import ObservabilityState
from contracts.world_understanding.source import WorldSourceRef

@dataclass(frozen=True, slots=True)
class CompilerSpec:
    source_kind:str
    compiler_id:str
    compiler_version:str
    proposition_type:str
    predicate:str
    authority_domain:str
    authority_ceiling_milli:int
    empirical_evidence_weight_milli:int

_DEFAULT_OBS=ObservabilityState(mode="OBSERVED",access_milli=1000,scope_coverage_milli=1000,time_coverage_milli=1000,adapter_quality_milli=1000,measurement_quality_milli=1000,combined_quality_milli=1000)

def payload_text(payload:dict[str,Any],payload_sha256:str)->str:
    for key in ("text","content","claim","summary","message","question","status","decision","event_kind","action"):
        value=payload.get(key)
        if isinstance(value,str) and value:
            return value[:20_000]
    return f"sha256:{payload_sha256}"

def make_direct_known(envelope:WorldIngressEnvelope,spec:CompilerSpec,*,proposition_type:str|None=None,predicate:str|None=None,object_text:str|None=None,subject_ref:str|None=None,authority_ceiling_milli:int|None=None,empirical_evidence_weight_milli:int|None=None,authority_domain:str|None=None)->DirectKnownRecord:
    ptype=proposition_type or spec.proposition_type
    pred=predicate or spec.predicate
    subject=subject_ref or envelope.source_native_id
    obj=WorldValue(kind="string",string_value=(object_text if object_text is not None else payload_text(envelope.payload_inline or {},envelope.payload_sha256))[:20_000])
    known_id=derive_direct_known_id(world_scope_hash=envelope.scope_hint.world_scope_hash,proposition_type=ptype,subject_ref=subject,predicate=pred,object_value=obj,object_ref=None,source_envelope_id=envelope.envelope_id)
    ceiling=spec.authority_ceiling_milli if authority_ceiling_milli is None else authority_ceiling_milli
    weight=spec.empirical_evidence_weight_milli if empirical_evidence_weight_milli is None else empirical_evidence_weight_milli
    if authority_domain is None and envelope.native_authority_domain is not None and envelope.native_authority_domain != spec.authority_domain:
        raise ValueError("native authority domain does not match compiler source semantics")
    domain=authority_domain or spec.authority_domain
    source_ref=WorldSourceRef(source_kind=envelope.source_kind,object_id=envelope.source_native_id,sha256=envelope.payload_sha256,authority_domain=domain,authority_ceiling_milli=ceiling,provenance_integrity_milli=min(1000,ceiling))
    row=DirectKnownRecord(known_id=known_id,proposition_type=ptype,subject_ref=subject,predicate=pred,object_value=obj,world_scope=envelope.scope_hint,time=envelope.source_time,authority_domain=domain,authority_ceiling_milli=ceiling,observability_state=envelope.observability_hint or _DEFAULT_OBS,truth_state="TRUE",epistemic_state="CURRENT",provenance_refs=(source_ref,),empirical_evidence_weight_milli=weight,record_hash="0"*64,source_envelope_id=envelope.envelope_id,source_kind=envelope.source_kind,source_native_id=envelope.source_native_id,source_payload_hash=envelope.payload_sha256,compiler_id=spec.compiler_id,compiler_version=spec.compiler_version)
    return row.with_computed_hash()

class DeterministicSourceCompiler:
    __slots__=("spec",)
    def __init__(self,spec:CompilerSpec)->None: self.spec=spec
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!=self.spec.source_kind: raise ValueError("compiler source_kind mismatch")
        return (make_direct_known(envelope,self.spec),)

__all__=["CompilerSpec","DeterministicSourceCompiler","make_direct_known","payload_text"]
