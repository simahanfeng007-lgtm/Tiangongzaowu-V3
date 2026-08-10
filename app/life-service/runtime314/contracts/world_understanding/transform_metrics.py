"""System telemetry for transform cost/quality. Not World Data and not LLM context."""
from __future__ import annotations
from typing import Literal
from pydantic import Field
from ._base import WorldContractModel
from ..models import OpaqueId

class TransformCostObservation(WorldContractModel):
    transform_id:OpaqueId
    transform_version:OpaqueId
    input_count:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    output_count:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    token_cost:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    cpu_time_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    wall_time_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    io_bytes:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    llm_latency_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    success:bool
    failure_type:OpaqueId|None=None
    created_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    telemetry_only:Literal[True]=True
    empirical_evidence_weight_milli:Literal[0]=0

class TransformQualityProfile(WorldContractModel):
    transform_id:OpaqueId
    transform_version:OpaqueId
    domain:OpaqueId
    precision_estimate_milli:int|None=Field(default=None,ge=0,le=1000,strict=True)
    recall_estimate_milli:int|None=Field(default=None,ge=0,le=1000,strict=True)
    coverage_estimate_milli:int=Field(ge=0,le=1000,strict=True)
    false_merge_rate_milli:int|None=Field(default=None,ge=0,le=1000,strict=True)
    false_relation_rate_milli:int|None=Field(default=None,ge=0,le=1000,strict=True)
    downstream_challenge_rate_milli:int=Field(ge=0,le=1000,strict=True)
    mean_cost_milli:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    p95_latency_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    sample_count:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    last_calibrated_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    telemetry_only:Literal[True]=True
    empirical_evidence_weight_milli:Literal[0]=0
