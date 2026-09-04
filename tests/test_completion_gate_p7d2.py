from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from total_gateway.completion_gate import (
    CompletionGate,
    CompletionGateError,
    CompletionRequirements,
)
from total_gateway.verification_repair_policy import DEFAULT_POLICY, POLICY_VERSION


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EFFECT_A = "eff_" + "a" * 64
EFFECT_B = "eff_" + "b" * 64
HASH_A = "a" * 64
HASH_B = "b" * 64


@dataclass(frozen=True)
class _Fact:
    fact_id: str
    effect_id: str
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1
    ticket_id: str = "ticket.p7d2"
    action_id: str = "skill.list"
    action_version: str = "1.0.0"
    payload_sha256: str = HASH_A
    fact_type: str = "execution.succeeded"

    def has_valid_sha256(self) -> bool:
        return True


@dataclass(frozen=True)
class _Result:
    effect_id: str
    fact_ids: tuple[str, ...]
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1
    ticket_id: str = "ticket.p7d2"
    action_id: str = "skill.list"
    action_version: str = "1.0.0"
    result_payload_sha256: str = HASH_A
    status: str = "SUCCEEDED"


@dataclass(frozen=True)
class _Batch:
    result: _Result
    facts: tuple[_Fact, ...]


class _FactLedger:
    def __init__(self, batches: tuple[_Batch, ...]) -> None:
        self._by_effect = {item.result.effect_id: item for item in batches}

    def list_request_facts(self, request_id, *, run_id, generation):
        assert (request_id, run_id, generation) == (REQUEST_ID, RUN_ID, 1)
        return tuple(
            fact
            for batch in self._by_effect.values()
            for fact in batch.facts
        )

    def get_batch_for_effect(self, effect_id, *, verify_payload=True):
        assert verify_payload is True
        return self._by_effect.get(effect_id)


class _Objects:
    pass


def _batch(effect_id: str, fact_ids: tuple[str, ...]) -> _Batch:
    result = _Result(effect_id=effect_id, fact_ids=fact_ids)
    return _Batch(
        result=result,
        facts=tuple(_Fact(fact_id=item, effect_id=effect_id) for item in fact_ids),
    )


def test_multi_fact_batch_is_one_exact_effect_and_all_facts_support_completion() -> None:
    fact_ids = ("fact.p7d2.01", "fact.p7d2.02")
    gate = CompletionGate(
        _Objects(),
        _FactLedger((_batch(EFFECT_A, fact_ids),)),
        head_state_reader=lambda _effect_id: "SUCCEEDED",
    )
    decision = gate.evaluate(
        CompletionRequirements(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            required_execution_effect_ids=(EFFECT_A,),
            execution_lineage_effect_ids=(EFFECT_A,),
        )
    )
    assert decision.outcome == "COMPLETED"
    assert decision.supporting_fact_ids == fact_ids


def test_explicit_execution_lineage_rejects_orphan_fact_batch() -> None:
    gate = CompletionGate(
        _Objects(),
        _FactLedger(
            (
                _batch(EFFECT_A, ("fact.p7d2.a",)),
                _batch(EFFECT_B, ("fact.p7d2.b",)),
            )
        ),
        head_state_reader=lambda _effect_id: "SUCCEEDED",
    )
    with pytest.raises(
        CompletionGateError, match="completion.execution.unexpected_fact"
    ):
        gate.evaluate(
            CompletionRequirements(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                required_execution_effect_ids=(EFFECT_A,),
                execution_lineage_effect_ids=(EFFECT_A,),
            )
        )


def test_execution_lineage_accepts_parent_plus_256_attempts() -> None:
    lineage = tuple(f"eff_{index:064x}" for index in range(257))
    requirements = CompletionRequirements(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        required_execution_effect_ids=(lineage[-1],),
        execution_lineage_effect_ids=lineage,
    )
    assert len(requirements.execution_lineage_effect_ids) == 257

    with pytest.raises(ValidationError):
        CompletionRequirements(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            required_execution_effect_ids=(lineage[-1],),
            execution_lineage_effect_ids=(
                *lineage,
                "eff_" + "f" * 64,
            ),
        )


@dataclass(frozen=True)
class _Entry:
    plan_entry_id: str
    required: bool = True


@dataclass(frozen=True)
class _Assessment:
    plan_entry_id: str
    status: str


@dataclass(frozen=True)
class _Plan:
    entries: tuple[_Entry, ...]
    verification_plan_id: str = "vpl.p7d2"
    plan_sha256: str = HASH_A
    registry_snapshot_sha256: str = HASH_B
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1

    def has_valid_identity(self) -> bool:
        return True


@dataclass(frozen=True)
class _Readiness:
    entry_assessments: tuple[_Assessment, ...]
    verification_readiness_id: str = "vrd_" + "c" * 64
    verification_plan_id: str = "vpl.p7d2"
    verification_plan_sha256: str = HASH_A
    registry_snapshot_sha256: str = HASH_B
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1
    verification_ready: bool = False
    failure_class: str = "MISSING_EVIDENCE"
    readiness_sha256: str = "c" * 64

    def has_valid_identity(self) -> bool:
        return True


@dataclass(frozen=True)
class _FailureEvidence:
    failure_evidence_id: str
    plan_entry_id: str
    failure_evidence_sha256: str
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1
    readiness_sha256: str = "c" * 64

    def has_valid_identity(self) -> bool:
        return True


@dataclass(frozen=True)
class _Disposition:
    verification_disposition_id: str
    plan_entry_id: str
    failure_evidence_id: str
    failure_evidence_sha256: str
    action: str
    request_id: str = REQUEST_ID
    run_id: str = RUN_ID
    generation: int = 1
    verification_plan_id: str = "vpl.p7d2"
    policy_version: str = POLICY_VERSION
    policy_config_sha256: str = DEFAULT_POLICY.config_sha256()
    disposition_sha256: str = HASH_B

    def has_valid_identity(self) -> bool:
        return True


def _verification_set(actions: tuple[str, str]):
    entry_ids = ("vpe.p7d2.01", "vpe.p7d2.02")
    plan = _Plan(tuple(_Entry(item) for item in entry_ids))
    readiness = _Readiness(tuple(_Assessment(item, "MISSING") for item in entry_ids))
    evidence = tuple(
        _FailureEvidence(
            failure_evidence_id=f"vfe.p7d2.{index}",
            plan_entry_id=entry_id,
            failure_evidence_sha256=str(index) * 64,
        )
        for index, entry_id in enumerate(entry_ids, start=1)
    )
    dispositions = tuple(
        _Disposition(
            verification_disposition_id=f"vds.p7d2.{index}",
            plan_entry_id=entry_id,
            failure_evidence_id=item.failure_evidence_id,
            failure_evidence_sha256=item.failure_evidence_sha256,
            action=action,
        )
        for index, (entry_id, item, action) in enumerate(
            zip(entry_ids, evidence, actions, strict=True), start=1
        )
    )
    by_id = {item.verification_disposition_id: item for item in dispositions}
    return (
        plan,
        readiness,
        evidence,
        dispositions,
        by_id.get,
        lambda **_lineage: readiness,
    )


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (("BLOCK", "WAIT"), "FAILED"),
        (("BLOCK", "RECONCILE"), "RECONCILE_REQUIRED"),
        (("REPAIR", "REVIEW"), "IN_PROGRESS"),
    ],
)
def test_all_p19_dispositions_are_aggregated_deterministically(actions, expected) -> None:
    (
        plan,
        readiness,
        evidence,
        dispositions,
        authority,
        readiness_authority,
    ) = _verification_set(actions)
    decision = CompletionGate(_Objects(), _FactLedger(())).evaluate(
        CompletionRequirements(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            text_required=True,
            verification_mode="PLAN_BOUND",
        ),
        candidate_text="A0 result is available locally.",
        active_plan=plan,
        verification_readiness=readiness,
        verification_dispositions=dispositions,
        verification_failure_evidences=evidence,
        disposition_authority_reader=authority,
        readiness_authority_reader=readiness_authority,
    )
    assert decision.outcome == expected


def test_plural_disposition_set_must_cover_every_non_pass_entry() -> None:
    (
        plan,
        readiness,
        evidence,
        dispositions,
        authority,
        readiness_authority,
    ) = _verification_set(("WAIT", "WAIT"))
    with pytest.raises(
        CompletionGateError,
        match="completion.verification.disposition_coverage_mismatch",
    ):
        CompletionGate(_Objects(), _FactLedger(())).evaluate(
            CompletionRequirements(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                text_required=True,
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="A0 result is available locally.",
            active_plan=plan,
            verification_readiness=readiness,
            verification_dispositions=dispositions[:1],
            verification_failure_evidences=evidence[:1],
            disposition_authority_reader=authority,
            readiness_authority_reader=readiness_authority,
        )


def test_plural_completion_rejects_an_old_but_internally_consistent_bundle() -> None:
    (
        plan,
        old_readiness,
        evidence,
        dispositions,
        authority,
        _,
    ) = _verification_set(("WAIT", "WAIT"))
    current_readiness = _Readiness(
        old_readiness.entry_assessments,
        verification_readiness_id="vrd_" + "d" * 64,
        readiness_sha256="d" * 64,
    )

    with pytest.raises(
        CompletionGateError,
        match="completion.verification.readiness_not_current",
    ):
        CompletionGate(_Objects(), _FactLedger(())).evaluate(
            CompletionRequirements(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                text_required=True,
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="A0 result is available locally.",
            active_plan=plan,
            verification_readiness=old_readiness,
            verification_dispositions=dispositions,
            verification_failure_evidences=evidence,
            disposition_authority_reader=authority,
            readiness_authority_reader=lambda **_lineage: current_readiness,
        )


def test_all_pass_empty_plural_set_still_rejects_stale_readiness() -> None:
    entry_ids = ("vpe.p7d2.01", "vpe.p7d2.02")
    plan = _Plan(tuple(_Entry(item) for item in entry_ids))
    assessments = tuple(_Assessment(item, "PASS") for item in entry_ids)
    old_readiness = _Readiness(
        assessments,
        verification_ready=True,
        failure_class="NONE",
    )
    current_readiness = _Readiness(
        assessments,
        verification_readiness_id="vrd_" + "d" * 64,
        verification_ready=True,
        failure_class="NONE",
        readiness_sha256="d" * 64,
    )

    with pytest.raises(
        CompletionGateError,
        match="completion.verification.readiness_not_current",
    ):
        CompletionGate(_Objects(), _FactLedger(())).evaluate(
            CompletionRequirements(
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                text_required=True,
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="A0 result is available locally.",
            active_plan=plan,
            verification_readiness=old_readiness,
            verification_dispositions=(),
            verification_failure_evidences=(),
            disposition_authority_reader=lambda _item: None,
            readiness_authority_reader=lambda **_lineage: current_readiness,
        )
