"""Thin post-commit envelope builders. They inherit scope; they never infer or choose a life."""
from __future__ import annotations
from typing import Any
from contracts.canonical import canonical_sha256
from contracts.world_understanding.ingress import WorldIngressEnvelope,derive_ingress_dedup_key,derive_ingress_envelope_id
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.source import SourceKind
from contracts.world_understanding.time import WorldTime

def build_post_commit_source_envelope(*,source_kind:SourceKind,source_native_id:str,producer_ref:str,payload:dict[str,Any],source_time:WorldTime,scope:WorldScope,correlation_id:str,run_id:str|None=None,request_id:str|None=None,session_id:str|None=None,conversation_id:str|None=None,workspace_id:str|None=None,native_authority_domain=None,observability_hint=None,integrity_ref=None)->WorldIngressEnvelope:
    if source_kind in {"CONTEXT_REQUEST","UNCLASSIFIED_SOURCE"}: raise ValueError("post-commit source adapter requires a concrete reality source kind")
    payload_sha256=canonical_sha256(payload)
    dedup_key=derive_ingress_dedup_key(envelope_kind="SOURCE_RECORD",source_kind=source_kind,source_native_id=source_native_id,payload_sha256=payload_sha256,world_scope_hash=scope.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dedup_key),envelope_kind="SOURCE_RECORD",source_kind=source_kind,source_native_id=source_native_id,producer_ref=producer_ref,payload_inline=payload,payload_sha256=payload_sha256,source_time=source_time,life_id=scope.life_id,run_id=run_id,request_id=request_id,session_id=session_id,conversation_id=conversation_id,workspace_id=workspace_id,principal_scope_hash=scope.principal_scope_hash,scope_hint=scope,native_authority_domain=native_authority_domain,observability_hint=observability_hint,integrity_ref=integrity_ref,correlation_id=correlation_id,dedup_key=dedup_key)
__all__=["build_post_commit_source_envelope"]
