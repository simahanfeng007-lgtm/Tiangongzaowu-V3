"""P8 L4 Semantic Pipeline: admission -> provider-neutral LLM -> deterministic WorldHypothesis materialization."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldClaim, WorldRecordRef
from contracts.world_understanding.hypothesis import WorldHypothesis
from contracts.world_understanding.transform_metrics import TransformCostObservation
from world_understanding.common.budgets import WorkCost
from world_understanding.common.event import RhythmEvent
from world_understanding.common.epistemic import EpistemicPlane
from .admission import SemanticAdmissionController, SemanticAdmissionOutcome, SemanticFactors
from .inputs import SemanticInputBundle
from .model import (
    SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, SEMANTIC_SYSTEM_INSTRUCTION,
    SemanticModel, SemanticModelRequest, SemanticModelResponse, SemanticModelUnavailable,
    SemanticOutputRejected, parse_semantic_output,
)

SEMANTIC_TRANSFORM_ID = "world.semantic.l4"
SEMANTIC_TRANSFORM_VERSION = "v1"

@dataclass(frozen=True, slots=True)
class SemanticTrace:
    status: str
    attention_milli: int
    voi_milli: int
    admission_disposition: str
    admission_reason_code: str
    model_ref: str | None
    model_sha256: str | None
    prompt_version: str
    schema_version: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    source_refs: tuple[WorldRecordRef, ...]
    output_sha256: str | None
    hypothesis_refs: tuple[WorldRecordRef, ...]
    failure_type: str | None = None
    empirical_evidence_weight_milli: int = 0
    may_authorize: bool = False
    may_execute: bool = False
    token_measurement: str = "UNAVAILABLE"
    @property
    def trace_sha256(self) -> str:
        return canonical_sha256({
            "domain": "tiangong.world.semantic-trace.v1",
            "status": self.status,
            "attention_milli": self.attention_milli,
            "voi_milli": self.voi_milli,
            "admission_disposition": self.admission_disposition,
            "admission_reason_code": self.admission_reason_code,
            "model_ref": self.model_ref,
            "model_sha256": self.model_sha256,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_ms": self.latency_ms,
            "source_refs": [ref.model_dump(mode="json") for ref in self.source_refs],
            "output_sha256": self.output_sha256,
            "hypothesis_refs": [ref.model_dump(mode="json") for ref in self.hypothesis_refs],
            "failure_type": self.failure_type,
            "token_measurement": self.token_measurement,
        })

@dataclass(frozen=True, slots=True)
class SemanticRunResult:
    status: str
    hypotheses: tuple[WorldHypothesis, ...]
    trace: SemanticTrace
    cost_observation: TransformCostObservation


def hypothesis_ref(hypothesis: WorldHypothesis) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_hypothesis", record_id=hypothesis.hypothesis_id, revision=None, sha256=hypothesis.hypothesis_sha256)


def _request(bundle: SemanticInputBundle) -> SemanticModelRequest:
    payload_json = bundle.model_payload_json()
    return SemanticModelRequest(
        prompt_version=SEMANTIC_PROMPT_VERSION,
        schema_version=SEMANTIC_SCHEMA_VERSION,
        system_instruction=SEMANTIC_SYSTEM_INSTRUCTION,
        payload_json=payload_json,
        payload_sha256=canonical_sha256({"domain": "tiangong.world.semantic-input.v1", "payload": bundle.model_payload()}),
    )


_FAILURE_TYPE_MAP = {
    "LLM_UNAVAILABLE": "llm.unavailable",
    "MODEL_ERROR": "model.error",
    "OUTPUT_REJECTED": "output.rejected",
    "SEMANTIC_QUEUE_EMPTY": "semantic.queue.empty",
    "SEMANTIC_ATTENTION_FLOOR": "semantic.attention.floor",
    "SEMANTIC_VOI_FLOOR": "semantic.voi.floor",
    "SEMANTIC_QUEUE_REQUIRED": "semantic.queue.required",
    "SEMANTIC_ADMISSION_FLOOR": "semantic.admission.floor",
    "QUEUE_CAPACITY": "queue.capacity",
    "BUDGET_RESERVE": "budget.reserve",
}

def _metric_failure_type(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    mapped = _FAILURE_TYPE_MAP.get(reason_code)
    if mapped is not None:
        return mapped
    # Metrics failure_type is an OpaqueId. Preserve deterministic semantics without
    # trying to smuggle arbitrary error text into the contract.
    return "semantic.failure." + canonical_sha256({"reason_code": reason_code})[:16]

def _empty_cost(*, created_at_ms: int, input_count: int, success: bool, failure_type: str | None = None, response: SemanticModelResponse | None = None) -> TransformCostObservation:
    return TransformCostObservation(
        transform_id=SEMANTIC_TRANSFORM_ID,
        transform_version=SEMANTIC_TRANSFORM_VERSION,
        input_count=input_count,
        output_count=0,
        token_cost=0 if response is None else response.total_tokens,
        cpu_time_ms=0,
        wall_time_ms=0 if response is None else response.latency_ms,
        io_bytes=0,
        llm_latency_ms=0 if response is None else response.latency_ms,
        success=success,
        failure_type=_metric_failure_type(failure_type),
        created_at_ms=created_at_ms,
    )

class SemanticPipeline:
    __slots__ = ("model", "admission", "epistemic_plane")
    def __init__(self, *, model: SemanticModel | None, admission: SemanticAdmissionController | None = None, epistemic_plane: EpistemicPlane | None = None) -> None:
        self.model = model
        self.admission = admission or SemanticAdmissionController()
        self.epistemic_plane = epistemic_plane or EpistemicPlane()

    def run(
        self,
        bundle: SemanticInputBundle,
        *,
        factors: SemanticFactors,
        expected_gap_reduction_milli: int,
        expected_cost_milli: int,
        created_at_ms: int,
        event: RhythmEvent | None = None,
        expected_cost: WorkCost = WorkCost(),
    ) -> SemanticRunResult:
        refs = bundle.refs
        # Model absence is not a world failure: L0-L3 state remains untouched and no fake hypothesis is created.
        if self.model is None or not self.model.is_available():
            admission = SemanticAdmissionOutcome(False, 0, 0, "DEFERRED", "LLM_UNAVAILABLE")
            trace = SemanticTrace("LLM_UNAVAILABLE", 0, 0, admission.disposition, admission.reason_code, None, None,
                                  SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, 0, 0, 0, refs, None, (), "LLM_UNAVAILABLE")
            return SemanticRunResult("LLM_UNAVAILABLE", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=False, failure_type="LLM_UNAVAILABLE"))

        admission = self.admission.admit(
            factors=factors,
            expected_gap_reduction_milli=expected_gap_reduction_milli,
            expected_cost_milli=expected_cost_milli,
            event=event,
            cost=expected_cost,
        )
        if not admission.admitted:
            trace = SemanticTrace("NOT_ADMITTED", admission.attention_milli, admission.voi_milli, admission.disposition, admission.reason_code,
                                  None, None, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, 0, 0, 0, refs, None, (), admission.reason_code)
            return SemanticRunResult("NOT_ADMITTED", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=False, failure_type=admission.reason_code))

        if self.admission.rhythm is not None:
            serviced = self.admission.rhythm.service_one("SEMANTIC", now_ms=created_at_ms)
            if serviced is None:
                trace = SemanticTrace("NOT_ADMITTED", admission.attention_milli, admission.voi_milli, admission.disposition, "SEMANTIC_QUEUE_EMPTY",
                                      None, None, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, 0, 0, 0, refs, None, (), "SEMANTIC_QUEUE_EMPTY")
                return SemanticRunResult("NOT_ADMITTED", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=False, failure_type="SEMANTIC_QUEUE_EMPTY"))

        request = _request(bundle)
        response: SemanticModelResponse | None = None
        try:
            response = self.model.generate(request)
        except SemanticModelUnavailable:
            trace = SemanticTrace("LLM_UNAVAILABLE", admission.attention_milli, admission.voi_milli, admission.disposition, admission.reason_code,
                                  None, None, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, 0, 0, 0, refs, None, (), "LLM_UNAVAILABLE")
            return SemanticRunResult("LLM_UNAVAILABLE", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=False, failure_type="LLM_UNAVAILABLE"))
        except Exception:
            trace = SemanticTrace("MODEL_ERROR", admission.attention_milli, admission.voi_milli, admission.disposition, admission.reason_code,
                                  None, None, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION, 0, 0, 0, refs, None, (), "MODEL_ERROR")
            return SemanticRunResult("MODEL_ERROR", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=Falsl , failure_type="MODEL_ERROR"))

        try:
            proposals = parse_semantic_output(response.output_text, refs=refs, prior_indices=bundle.prior_indices)
        except SemanticOutputRejected:
            trace = SemanticTrace("OUTPUT_REJECTED", admission.attention_milli, admission.voi_milli, admission.disposition, admission.reason_code,
                                response.model_ref, response.model_sha256, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION,
                                  response.prompt_tokens, response.completion_tokens, response.latency_ms, refs, response.output_sha256, (), "OUTPUT_REJECTED", token_measurement=response.token_measurement)
            return SemanticRunResult("OUTPUT_REJECTED", (), trace, _empty_cost(created_at_ms=created_at_ms, input_count=len(refs), success=False, failure_type="OUTPUT_REJECTED", response=response))

        hypotheses: list[WorldHypothesis] = []
        seen_hypothesis_hashes: set[str] = set()
        for proposal in proposals:
            subject_ref = refs[proposal.subject_ref_index]
            basis_refs = tuple(refs[index] for index in proposal.basis_ref_indices)
            counter_refs = tuple(refs[index] for index in proposal.counter_ref_indices)
            prior_refs = tuple(refs[index] for index in proposal.prior_ref_indices)
            claim = WorldClaim(subject_ref=subject_ref, predicate=proposal.predicate, value=proposal.value)
            hypothesis_id = "whyp_" + canonical_sha256({
                "domain": "tiangong.world.hypothesis-id.v1",
                "world_scope_hash": bundle.scope.world_scope_hash,
                "claim": claim.model_dump(mode="json"),
                "hypothesis_kind": proposal.hypothesis_kind,
                "proposal_origin": "llm_synthesis",
                "basis_refs": [item.model_dump(mode="json") for item in basis_refs],
                "created_at_ms": created_at_ms,
            })
            hypothesis = WorldHypothesis(
                hypothesis_id=hypothesis_id,
                scope=bundle.scope,
                claim=claim,
                hypothesis_kind=proposal.hypothesis_kind,
                proposal_origin="llm_synthesis",
                basis_refs=basis_refs,
                counter_refs=counter_refs,
                derivation_refs=(),
                interpretive_prior_refs=prior_refs,
                uncertainty_milli=proposal.uncertainty_milli,
                proposal_model_ref=response.model_ref,
                proposal_model_sha256=response.model_sha256,
                created_at_ms=created_at_ms,
                valid_until_ms=None,
                hypothesis_sha256="0" * 64,
            ).with_computed_hash()
            self.epistemic_plane.validate_non_evidence_object(hypothesis)
            if hypothesis.hypothesis_sha256 in seen_hypothesis_hashes:
                continue
            seen_hypothesis_hashes.add(hypothesis.hypothesis_sha256)
            hypotheses.append(hypothesis)

        hypothesis_refs = tuple(sorted((hypothesis_ref(item) for item in hypotheses), key=lambda ref: ref.sort_key()))
        trace = SemanticTrace("COMPLETED", admission.attention_milli, admission.voi_milli, admission.disposition, admission.reason_code,
                              response.model_ref, response.model_sha256, SEMANTIC_PROMPT_VERSION, SEMANTIC_SCHEMA_VERSION,
                              response.prompt_tokens, response.completion_tokens, response.latency_ms, refs, response.output_sha256, hypothesis_refs, token_measurement=response.token_measurement)
        cost = TransformCostObservation(
            transform_id=SEMANTIC_TRANSFORM_ID,
            transform_version=SEMANTIC_TRANSFORM_VERSION,
            input_count=len(refs),
            output_count=len(hypotheses),
            token_cost=response.total_tokens,
            cpu_time_ms=0,
            wall_time_ms=response.latency_ms,
            io_bytes=0,
            llm_latency_ms=response.latency_ms,
            success=True,
            failure_type=None,
            created_at_ms=created_at_ms,
        )
        return SemanticRunResult("COMPLETED", tuple(hypotheses), trace, cost)


__all__ = [
    "SEMANTIC_TRANSFORM_ID", "SEMANTIC_TRANSFORM_VERSION", "SemanticTrace", "SemanticRunResult",
    "SemanticPipeline", "hypothesis_ref",
]
