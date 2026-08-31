"""Machine-only completion decision across text, execution, artifacts, and delivery parts."""

from __future__ import annotations

import hashlib
from typing import Callable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    ArtifactManifest,
    DeliveryReceipt,
    OutboundPlan,
    canonical_sha256,
)

from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore


class CompletionGateError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CompletionRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    text_required: bool = False
    required_execution_effect_ids: tuple[str, ...] = Field(default=(), max_length=256)
    required_artifact_revision_ids: tuple[str, ...] = Field(default=(), max_length=256)
    delivery_requirement: Literal["NONE", "CHANNEL_ACCEPTED", "DELIVERED"] = "NONE"
    verification_mode: Literal["NONE", "PLAN_BOUND"] = "NONE"

    @model_validator(mode="after")
    def validate_requirements(self) -> Self:
        for values, prefix in (
            (self.required_execution_effect_ids, "eff_"),
            (self.required_artifact_revision_ids, "arv_"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("completion requirement identities must be sorted and unique")
            if any(not value.startswith(prefix) or len(value) != 68 for value in values):
                raise ValueError("completion requirement identity is invalid")
        if (
            not self.text_required
            and not self.required_execution_effect_ids
            and not self.required_artifact_revision_ids
        ):
            raise ValueError("completion requirements cannot be empty")
        return self


class CompletionPartAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    part_id: str = Field(min_length=1, max_length=160)
    index: int = Field(ge=0, le=999)
    kind: Literal["text", "artifact"]
    artifact_revision_id: str | None = None
    stage: Literal[
        "NOT_REQUIRED",
        "MISSING",
        "PLANNED",
        "FETCHED",
        "UPLOADED",
        "CHANNEL_ACCEPTED",
        "DELIVERED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "AMBIGUOUS",
    ]
    requirement_satisfied: bool
    platform_delivered: bool
    reason_code: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.kind == "artifact" and self.artifact_revision_id is None:
            raise ValueError("artifact assessment requires a revision")
        if self.kind == "text" and self.artifact_revision_id is not None:
            raise ValueError("text assessment cannot carry an artifact revision")
        if self.platform_delivered != (self.stage == "DELIVERED"):
            raise ValueError("platform delivery flag disagrees with part stage")
        return self


class CompletionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    outcome: Literal["IN_PROGRESS", "COMPLETED", "PARTIAL", "FAILED", "RECONCILE_REQUIRED"]
    reason_code: str = Field(min_length=1, max_length=160)
    text_ready: bool
    execution_ready: bool
    artifacts_ready: bool
    delivery_ready: bool
    can_transition_request_completed: bool
    can_claim_platform_delivered: bool
    needs_reconciliation: bool
    execution_effect_states: tuple[tuple[str, str], ...]
    artifact_revision_states: tuple[tuple[str, str], ...]
    delivery_parts: tuple[CompletionPartAssessment, ...]
    supporting_fact_ids: tuple[str, ...]
    outbound_plan_sha256: str | None = None
    delivery_receipt_sha256: str | None = None
    candidate_text_sha256: str | None = None
    verification_mode: Literal["NONE", "PLAN_BOUND"] = "NONE"
    verification_ready: bool = True
    verification_plan_sha256: str | None = None
    verification_readiness_sha256: str | None = None
    model_generated: Literal[False] = False
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.execution_effect_states != tuple(sorted(self.execution_effect_states)):
            raise ValueError("execution assessments must be sorted")
        if self.artifact_revision_states != tuple(sorted(self.artifact_revision_states)):
            raise ValueError("artifact assessments must be sorted")
        if self.supporting_fact_ids != tuple(sorted(set(self.supporting_fact_ids))):
            raise ValueError("supporting fact IDs must be sorted and unique")
        if self.can_transition_request_completed != (self.outcome == "COMPLETED"):
            raise ValueError("only a completed machine decision may transition the request")
        if self.can_claim_platform_delivered and not self.can_transition_request_completed:
            raise ValueError("platform delivery cannot be claimed for an incomplete request")
        if self.needs_reconciliation != (self.outcome == "RECONCILE_REQUIRED"):
            raise ValueError("reconciliation flag disagrees with completion outcome")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"decision_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_sha256()})


class CompletionGate:
    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
        head_state_reader: "Callable[[str], str | None] | None" = None,
    ) -> None:
        self._object_store = object_store
        self._fact_ledger = fact_ledger
        # 草案不变量 11 + §3：effect head（gateway.sqlite3）是唯一执行状态权威，
        # FactLedger（facts.sqlite3）只是证据投影；两者不一致时 fail-closed 进入对账，
        # 不得仅凭投影判成功。head_state_reader 缺省（旧装配路径）时保持原语义并记录降级。
        self._head_state_reader = head_state_reader

    def evaluate(
        self,
        requirements: CompletionRequirements,
        *,
        candidate_text: str | None = None,
        artifacts: tuple[ArtifactManifest, ...] = (),
        outbound_plan: OutboundPlan | None = None,
        delivery_receipt: DeliveryReceipt | None = None,
        delivery_failure: Literal["FAILED_FINAL", "AMBIGUOUS"] | None = None,
        verification_readiness=None,
        active_plan=None,
        verification_disposition=None,
        verification_failure_evidence=None,
    ) -> CompletionDecision:
        if candidate_text is not None and (
            not candidate_text.strip() or "\x00" in candidate_text or len(candidate_text) > 100_000
        ):
            raise CompletionGateError("completion.text.invalid")
        fact_ids: set[str] = set()
        execution_states, execution_ready, execution_failed, execution_ambiguous = (
            self._execution_assessments(requirements, fact_ids)
        )
        artifact_states, artifacts_ready, artifacts_failed = self._artifact_assessments(
            requirements,
            artifacts,
            fact_ids,
        )

        text_ready = not requirements.text_required
        delivery_ready = requirements.delivery_requirement == "NONE"
        delivered = False
        parts: tuple[CompletionPartAssessment, ...] = ()
        delivery_failed = False
        delivery_ambiguous = False
        some_delivery_success = False
        plan_sha256: str | None = None
        receipt_sha256: str | None = None
        if requirements.delivery_requirement == "NONE":
            if (
                outbound_plan is not None
                or delivery_receipt is not None
                or delivery_failure is not None
            ):
                raise CompletionGateError("completion.delivery.unexpected_evidence")
            if requirements.text_required:
                text_ready = candidate_text is not None
        else:
            if outbound_plan is None:
                if delivery_failure is not None:
                    raise CompletionGateError("completion.delivery.failure_without_plan")
                parts = ()
                text_ready = not requirements.text_required
                delivery_ready = False
            else:
                self._validate_plan(requirements, outbound_plan, artifacts)
                if delivery_receipt is not None and delivery_failure is not None:
                    raise CompletionGateError("completion.delivery.conflicting_evidence")
                plan_sha256 = outbound_plan.plan_sha256
                text_parts = tuple(part for part in outbound_plan.parts if part.kind == "text")
                if requirements.text_required:
                    text_ready = bool(text_parts)
                    if candidate_text is not None:
                        planned_text = "\n".join(part.text or "" for part in text_parts)
                        if candidate_text != planned_text:
                            raise CompletionGateError("completion.text.plan_mismatch")
                if delivery_failure is not None:
                    stage = delivery_failure
                    parts = tuple(
                        CompletionPartAssessment(
                            part_id=part.part_id,
                            index=part.index,
                            kind=part.kind,
                            artifact_revision_id=(
                                None if part.artifact is None else part.artifact.artifact_revision_id
                            ),
                            stage=stage,
                            requirement_satisfied=False,
                            platform_delivered=False,
                            reason_code=(
                                "completion.delivery.ambiguous"
                                if stage == "AMBIGUOUS"
                                else "completion.delivery.failed_final"
                            ),
                        )
                        for part in outbound_plan.parts
                    )
                    delivery_ambiguous = stage == "AMBIGUOUS"
                    delivery_failed = stage == "FAILED_FINAL"
                elif delivery_receipt is None:
                    parts = tuple(
                        CompletionPartAssessment(
                            part_id=part.part_id,
                            index=part.index,
                            kind=part.kind,
                            artifact_revision_id=(
                                None if part.artifact is None else part.artifact.artifact_revision_id
                            ),
                            stage="MISSING",
                            requirement_satisfied=False,
                            platform_delivered=False,
                            reason_code="completion.delivery.receipt_missing",
                        )
                        for part in outbound_plan.parts
                    )
                else:
                    parts = self._assess_receipt(
                        requirements,
                        outbound_plan,
                        delivery_receipt,
                    )
                    receipt_sha256 = delivery_receipt.receipt_sha256
                    delivery_failed = any(item.stage == "FAILED_FINAL" for item in parts)
                    delivery_ambiguous = any(item.stage == "AMBIGUOUS" for item in parts)
                    some_delivery_success = any(
                        item.stage in {"CHANNEL_ACCEPTED", "DELIVERED"} for item in parts
                    )
                    delivery_ready = all(item.requirement_satisfied for item in parts)
                    delivered = all(item.platform_delivered for item in parts)

        # M4.1 HOTFIX §4: PLAN_BOUND REQUIRES the active plan — bare
        # readiness is never accepted. Every binding must match exactly.
        legacy_core_ready = text_ready and execution_ready and artifacts_ready
        if requirements.verification_mode == "PLAN_BOUND":
            if verification_readiness is None:
                raise CompletionGateError(
                    "completion.verification.plan_bound_missing_readiness"
                )
            if not verification_readiness.has_valid_identity():
                raise CompletionGateError(
                    "completion.verification.readiness_identity_invalid"
                )
            if (
                verification_readiness.request_id != requirements.request_id
                or verification_readiness.run_id != requirements.run_id
                or verification_readiness.generation != requirements.generation
            ):
                raise CompletionGateError(
                    "completion.verification.readiness_lineage_mismatch"
                )
            # HOTFIX §4: active_plan is MANDATORY (not optional)
            if active_plan is None:
                raise CompletionGateError(
                    "completion.verification.plan_bound_missing_active_plan"
                )
            if not active_plan.has_valid_identity():
                raise CompletionGateError(
                    "completion.verification.active_plan_identity_invalid"
                )
            if (
                active_plan.request_id != requirements.request_id
                or active_plan.run_id != requirements.run_id
                or active_plan.generation != requirements.generation
            ):
                raise CompletionGateError(
                    "completion.verification.active_plan_lineage_mismatch"
                )
            if (
                verification_readiness.verification_plan_id
                != active_plan.verification_plan_id
            ):
                raise CompletionGateError(
                    "completion.verification.readiness_plan_mismatch"
                )
            if (
                verification_readiness.verification_plan_sha256
                != active_plan.plan_sha256
            ):
                raise CompletionGateError(
                    "completion.verification.readiness_plan_hash_mismatch"
                )
            if (
                verification_readiness.registry_snapshot_sha256
                != active_plan.registry_snapshot_sha256
            ):
                raise CompletionGateError(
                    "completion.verification.readiness_registry_mismatch"
                )
            verification_ready = verification_readiness.verification_ready
            # M4.1 §11: failure-class → outcome mapping
            verification_failure_class = verification_readiness.failure_class
        else:
            if verification_readiness is not None:
                raise CompletionGateError(
                    "completion.verification.none_mode_with_readiness"
                )
            verification_ready = True
            verification_failure_class = "NONE"
        core_ready = legacy_core_ready and verification_ready
        ambiguous = execution_ambiguous or delivery_ambiguous
        failed = execution_failed or artifacts_failed or delivery_failed
        # M5 Final §17: disposition takes PRIORITY over failure class.
        # M5 Final Correction #7: the Gate VALIDATES the disposition's
        # identity and lineage before consuming it — a caller-forged
        # disposition with correct-looking action is rejected.
        if verification_disposition is not None:
            if not hasattr(verification_disposition, "has_valid_identity"):
                raise CompletionGateError(
                    "completion.verification.disposition_invalid_type"
                )
            if not verification_disposition.has_valid_identity():
                raise CompletionGateError(
                    "completion.verification.disposition_identity_invalid"
                )
            if (
                verification_disposition.request_id != requirements.request_id
                or verification_disposition.run_id != requirements.run_id
                or verification_disposition.generation != requirements.generation
            ):
                raise CompletionGateError(
                    "completion.verification.disposition_lineage_mismatch"
                )
            # P1-9: the Gate is the FINAL completion authority — it must
            # not rely on caller self-discipline. With active_plan +
            # readiness present, the disposition must fully bind to the
            # CURRENT plan and the CURRENT readiness through its
            # FailureEvidence.
            if active_plan is not None:
                if (
                    verification_disposition.verification_plan_id
                    != active_plan.verification_plan_id
                ):
                    raise CompletionGateError(
                        "completion.verification.disposition_plan_mismatch"
                    )
                if verification_disposition.plan_entry_id not in {
                    e.plan_entry_id for e in active_plan.entries
                }:
                    raise CompletionGateError(
                        "completion.verification.disposition_entry_not_in_plan"
                    )
            # P1-9: the disposition must carry the authoritative policy
            # configuration — a decision from an unknown policy cannot
            # drive completion.
            from total_gateway.verification_repair_policy import (
                DEFAULT_POLICY,
                POLICY_VERSION,
            )

            if (
                verification_disposition.policy_version != POLICY_VERSION
                or verification_disposition.policy_config_sha256
                != DEFAULT_POLICY.config_sha256()
            ):
                raise CompletionGateError(
                    "completion.verification.disposition_policy_mismatch"
                )
            if verification_failure_evidence is not None:
                if not hasattr(
                    verification_failure_evidence, "has_valid_identity"
                ) or not verification_failure_evidence.has_valid_identity():
                    raise CompletionGateError(
                        "completion.verification.evidence_identity_invalid"
                    )
                if (
                    verification_failure_evidence.failure_evidence_id
                    != verification_disposition.failure_evidence_id
                    or verification_failure_evidence.failure_evidence_sha256
                    != verification_disposition.failure_evidence_sha256
                ):
                    raise CompletionGateError(
                        "completion.verification.disposition_evidence_mismatch"
                    )
                if (
                    verification_failure_evidence.request_id
                    != verification_disposition.request_id
                    or verification_failure_evidence.run_id
                    != verification_disposition.run_id
                    or verification_failure_evidence.generation
                    != verification_disposition.generation
                    or verification_failure_evidence.plan_entry_id
                    != verification_disposition.plan_entry_id
                ):
                    raise CompletionGateError(
                        "completion.verification.evidence_lineage_mismatch"
                    )
                if (
                    verification_readiness is not None
                    and verification_failure_evidence.readiness_sha256
                    != verification_readiness.readiness_sha256
                ):
                    raise CompletionGateError(
                        "completion.verification.disposition_stale_readiness"
                    )
            elif verification_readiness is not None:
                raise CompletionGateError(
                    "completion.verification.disposition_without_evidence"
                )
            _da = verification_disposition.action
            if _da == "RECONCILE":
                ambiguous = True  # → RECONCILE_REQUIRED
            elif _da == "BLOCK":
                failed = True     # → FAILED
            # REPAIR / WAIT / REVIEW → stays IN_PROGRESS (repair pending /
            # evidence pending / review pending; not terminal FAILED)
            # Note: legacy execution/artifact/delivery failures (already in
            # `failed`) are NOT overridden by the disposition.
        elif verification_failure_class == "AUTHORITY_ERROR":
            ambiguous = True  # → RECONCILE_REQUIRED
        elif verification_failure_class == "PLAN_CONFIG_ERROR":
            ambiguous = True  # → RECONCILE_REQUIRED
        elif verification_failure_class == "VERIFICATION_FAILED" and not failed:
            failed = True     # → FAILED (no disposition → M4 fail-closed)
        # MISSING_EVIDENCE / INCONCLUSIVE → stays IN_PROGRESS (no flag)
        if ambiguous:
            outcome = "RECONCILE_REQUIRED"
            reason = "completion.reconciliation_required"
        elif failed:
            if some_delivery_success:
                outcome = "PARTIAL"
                reason = "completion.partial_delivery"
            else:
                outcome = "FAILED"
                reason = "completion.required_evidence_failed"
        elif core_ready and delivery_ready:
            outcome = "COMPLETED"
            reason = (
                "completion.platform_delivered"
                if requirements.delivery_requirement == "DELIVERED"
                else "completion.requirements_satisfied"
            )
        else:
            outcome = "IN_PROGRESS"
            reason = "completion.required_evidence_pending"

        decision = CompletionDecision(
            request_id=requirements.request_id,
            run_id=requirements.run_id,
            generation=requirements.generation,
            outcome=outcome,
            reason_code=reason,
            text_ready=text_ready,
            execution_ready=execution_ready,
            artifacts_ready=artifacts_ready,
            delivery_ready=delivery_ready,
            can_transition_request_completed=outcome == "COMPLETED",
            can_claim_platform_delivered=(
                outcome == "COMPLETED"
                and requirements.delivery_requirement == "DELIVERED"
                and delivered
            ),
            needs_reconciliation=outcome == "RECONCILE_REQUIRED",
            execution_effect_states=execution_states,
            artifact_revision_states=artifact_states,
            delivery_parts=parts,
            supporting_fact_ids=tuple(sorted(fact_ids)),
            outbound_plan_sha256=plan_sha256,
            delivery_receipt_sha256=receipt_sha256,
            candidate_text_sha256=(
                None if candidate_text is None else hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
            ),
            verification_mode=requirements.verification_mode,
            verification_ready=verification_ready,
            verification_plan_sha256=(
                verification_readiness.verification_plan_sha256
                if verification_readiness is not None
                else None
            ),
            verification_readiness_sha256=(
                verification_readiness.readiness_sha256
                if verification_readiness is not None
                else None
            ),
            decision_sha256="0" * 64,
        ).with_computed_sha256()
        return decision

    def _execution_assessments(
        self,
        requirements: CompletionRequirements,
        fact_ids: set[str],
    ) -> tuple[tuple[tuple[str, str], ...], bool, bool, bool]:
        facts = self._fact_ledger.list_request_facts(
            requirements.request_id,
            run_id=requirements.run_id,
            generation=requirements.generation,
        )
        by_effect: dict[str, list[object]] = {}
        for fact in facts:
            if fact.fact_type.startswith("execution."):
                by_effect.setdefault(fact.effect_id, []).append(fact)
        states: list[tuple[str, str]] = []
        failed = ambiguous = False
        for effect_id in requirements.required_execution_effect_ids:
            matching = by_effect.get(effect_id, [])
            if not matching:
                states.append((effect_id, "MISSING"))
                continue
            if len(matching) != 1:
                raise CompletionGateError("completion.execution.fact_conflict")
            fact = matching[0]
            batch = self._fact_ledger.get_batch_for_fact(fact.fact_id)
            if batch is None or batch.result.effect_id != effect_id:
                raise CompletionGateError("completion.execution.fact_unbound")
            fact_ids.add(fact.fact_id)
            state = batch.result.status
            # 草案不变量 11：证据投影（FactLedger）必须与状态权威（effect head）一致。
            # 仅投影有成功证据而 head 仍 STARTED/AMBIGUOUS（或反过来）→ 不得报成功，
            # 统一映射为 MISMATCH，fail-closed 进入对账。
            if self._head_state_reader is not None:
                head_state = self._head_state_reader(effect_id)
                if head_state is None:
                    state = "MISMATCH"
                elif head_state != state and not (
                    head_state == "RECONCILED" and state == "SUCCEEDED"
                ):
                    state = "MISMATCH"
            states.append((effect_id, state))
            failed = failed or state in {"FAILED_FINAL", "CANCELLED", "FENCED"}
            ambiguous = ambiguous or state in {"AMBIGUOUS", "MISMATCH"}
        ready = all(state == "SUCCEEDED" for _, state in states)
        return tuple(states), ready, failed, ambiguous

    def _artifact_assessments(
        self,
        requirements: CompletionRequirements,
        artifacts: tuple[ArtifactManifest, ...],
        fact_ids: set[str],
    ) -> tuple[tuple[tuple[str, str], ...], bool, bool]:
        by_revision = {item.artifact_revision_id: item for item in artifacts}
        if len(by_revision) != len(artifacts):
            raise CompletionGateError("completion.artifact.duplicate_revision")
        expected = set(requirements.required_artifact_revision_ids)
        if set(by_revision) - expected:
            raise CompletionGateError("completion.artifact.unexpected_revision")
        states: list[tuple[str, str]] = []
        failed = False
        for revision_id in requirements.required_artifact_revision_ids:
            artifact = by_revision.get(revision_id)
            if artifact is None:
                states.append((revision_id, "MISSING"))
                continue
            self._validate_artifact(requirements, artifact, fact_ids)
            state = {
                "PENDING": "QC_PENDING",
                "PASSED": "QC_PASSED",
                "FAILED": "QC_FAILED",
            }[artifact.qc_state]
            states.append((revision_id, state))
            failed = failed or state == "QC_FAILED"
        ready = all(state == "QC_PASSED" for _, state in states)
        return tuple(states), ready, failed

    def _validate_artifact(
        self,
        requirements: CompletionRequirements,
        artifact: ArtifactManifest,
        fact_ids: set[str],
    ) -> None:
        if (
            not artifact.has_valid_manifest_sha256()
            or artifact.request_id != requirements.request_id
            or artifact.run_id != requirements.run_id
            or artifact.generation != requirements.generation
        ):
            raise CompletionGateError("completion.artifact.manifest_invalid")
        reference = self._object_store.get_reference(artifact.content_object_id)
        if (
            reference is None
            or reference.kind != "artifact"
            or reference.sha256 != artifact.sha256
            or reference.size_bytes != artifact.size_bytes
            or reference.tenant_id != artifact.tenant_id
            or reference.link_account_id != artifact.link_account_id
            or reference.conversation_scope_hash != artifact.conversation_scope_hash
        ):
            raise CompletionGateError("completion.artifact.object_binding_invalid")
        data = self._object_store.read_bytes(artifact.content_object_id)
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise CompletionGateError("completion.artifact.readback_invalid")
        if artifact.qc_state == "PENDING":
            return
        for evidence in artifact.qc_evidence:
            qc = self._fact_ledger.get_artifact_qc(
                artifact.artifact_revision_id,
                check_id=evidence.check_id,
                check_version=evidence.check_version,
            )
            if (
                qc is None
                or qc.manifest != artifact
                or qc.fact.fact_id != evidence.tool_fact_id
                or qc.result.qc_result_sha256 != evidence.evidence_sha256
                or qc.result.status != evidence.status
            ):
                raise CompletionGateError("completion.artifact.qc_fact_invalid")
            fact_ids.add(qc.fact.fact_id)

    @staticmethod
    def _validate_plan(
        requirements: CompletionRequirements,
        plan: OutboundPlan,
        artifacts: tuple[ArtifactManifest, ...],
    ) -> None:
        if (
            not plan.has_valid_plan_sha256()
            or plan.request_id != requirements.request_id
            or plan.run_id != requirements.run_id
            or plan.generation != requirements.generation
        ):
            raise CompletionGateError("completion.delivery.plan_invalid")
        expected = set(requirements.required_artifact_revision_ids)
        planned = {
            part.artifact.artifact_revision_id
            for part in plan.parts
            if part.artifact is not None
        }
        if planned != expected:
            raise CompletionGateError("completion.delivery.artifact_set_mismatch")
        supplied = {item.artifact_revision_id: item for item in artifacts}
        for part in plan.parts:
            if part.artifact is not None and supplied.get(part.artifact.artifact_revision_id) != part.artifact:
                raise CompletionGateError("completion.delivery.artifact_manifest_mismatch")

    @staticmethod
    def _assess_receipt(
        requirements: CompletionRequirements,
        plan: OutboundPlan,
        receipt: DeliveryReceipt,
    ) -> tuple[CompletionPartAssessment, ...]:
        if (
            not receipt.has_valid_receipt_sha256()
            or receipt.delivery_id != plan.delivery_id
            or receipt.effect_id != plan.effect_id
            or receipt.request_id != plan.request_id
            or receipt.run_id != plan.run_id
            or receipt.generation != plan.generation
            or receipt.channel != plan.channel
            or len(receipt.parts) != len(plan.parts)
        ):
            raise CompletionGateError("completion.delivery.receipt_invalid")
        assessments: list[CompletionPartAssessment] = []
        for planned, observed in zip(plan.parts, receipt.parts, strict=True):
            expected_artifact_id = None if planned.artifact is None else planned.artifact.artifact_id
            expected_revision_id = (
                None if planned.artifact is None else planned.artifact.artifact_revision_id
            )
            if (
                observed.part_id != planned.part_id
                or observed.index != planned.index
                or observed.kind != planned.kind
                or observed.artifact_id != expected_artifact_id
                or observed.artifact_revision_id != expected_revision_id
            ):
                raise CompletionGateError("completion.delivery.part_binding_invalid")
            if requirements.delivery_requirement == "DELIVERED":
                satisfied = observed.stage == "DELIVERED"
            else:
                satisfied = observed.stage in {"CHANNEL_ACCEPTED", "DELIVERED"}
            reason = {
                "PLANNED": "completion.delivery.planned",
                "FETCHED": "completion.delivery.fetched",
                "UPLOADED": "completion.delivery.uploaded",
                "CHANNEL_ACCEPTED": "completion.delivery.channel_accepted",
                "DELIVERED": "completion.delivery.delivered",
                "FAILED_RETRYABLE": "completion.delivery.retry_pending",
                "FAILED_FINAL": "completion.delivery.failed_final",
                "AMBIGUOUS": "completion.delivery.ambiguous",
            }[observed.stage]
            assessments.append(
                CompletionPartAssessment(
                    part_id=planned.part_id,
                    index=planned.index,
                    kind=planned.kind,
                    artifact_revision_id=expected_revision_id,
                    stage=observed.stage,
                    requirement_satisfied=satisfied,
                    platform_delivered=observed.stage == "DELIVERED",
                    reason_code=reason,
                )
            )
        return tuple(assessments)


__all__ = [
    "CompletionDecision",
    "CompletionGate",
    "CompletionGateError",
    "CompletionPartAssessment",
    "CompletionRequirements",
]
