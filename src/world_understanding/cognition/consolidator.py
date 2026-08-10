"""Evidence-gated cognition consolidation for World Cognition Core.

LLMs may propose structured cognition. Only deterministic policy, explicit
system authority, or migration may commit revision decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts.cognition_revision import CognitionRevision, derive_cognition_revision_id
from contracts.cognition_statement import CognitionStatement, CognitionValue, derive_cognition_id

from .stability import StabilityPolicy, StabilityReport, challenge_is_material, evaluate_evidence, highest_eligible_level
from .store import CognitionIntegrityError, WorldCognitionStore

POLICY_REF = "policy.world_cognition.stability.v1"
_LEVEL_ORDER = {"C0": 0, "C1": 1, "C2": 2, "C3": 3, "C4": 4}
_LEVEL_STATE = {
    "C0": ("CANDIDATE", "C0"),
    "C1": ("PROVISIONAL", "C1"),
    "C2": ("STABLE", "C2"),
    "C3": ("CORE", "C3"),
    "C4": ("CORE", "C4"),
}


@dataclass(frozen=True, slots=True)
class CognitionProposal:
    life_id: str
    domain: str
    world_scope_hash: str
    principal_scope_hash: str
    privacy_scope: str
    claim_kind: str
    subject_ref: str
    predicate: str
    value: CognitionValue
    proposal_origin: str = "deterministic_extraction"
    condition_object_id: str | None = None
    condition_sha256: str | None = None
    prior_ids: tuple[str, ...] = ()
    valid_until_ms: int | None = None

    @property
    def cognition_id(self) -> str:
        return derive_cognition_id(
            life_id=self.life_id,
            domain=self.domain,
            world_scope_hash=self.world_scope_hash,
            principal_scope_hash=self.principal_scope_hash,
            claim_kind=self.claim_kind,
            subject_ref=self.subject_ref,
            predicate=self.predicate,
            condition_sha256=self.condition_sha256,
        )


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    cognition_id: str
    head: CognitionStatement | None
    changed: bool
    transitions: tuple[str, ...]
    report: StabilityReport
    reason: str


def _same_value(left: CognitionValue, right: CognitionValue) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _sorted_ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(str(value) for value in values if str(value))))


class CognitionConsolidator:
    def __init__(self, store: WorldCognitionStore, *, policy: StabilityPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or StabilityPolicy()

    def _load_evidence_exact(self, evidence_ids: Iterable[str]) -> list:
        requested = _sorted_ids(evidence_ids)
        loaded = self.store.get_evidence_many(requested)
        loaded_ids = {item.evidence_id for item in loaded}
        missing = [item for item in requested if item not in loaded_ids]
        if missing:
            raise CognitionIntegrityError(f"unknown cognition evidence IDs: {missing}")
        return loaded

    @staticmethod
    def _validate_evidence_scope(proposal: CognitionProposal, evidence: Iterable[object]) -> None:
        for item in evidence:
            if (
                item.life_id != proposal.life_id
                or item.domain != proposal.domain
                or item.world_scope_hash != proposal.world_scope_hash
                or item.principal_scope_hash != proposal.principal_scope_hash
            ):
                raise CognitionIntegrityError("cross-scope evidence cannot be attached to cognition")

    @staticmethod
    def _validate_existing_slot(head: CognitionStatement, proposal: CognitionProposal) -> None:
        if (
            head.life_id != proposal.life_id
            or head.domain != proposal.domain
            or head.world_scope_hash != proposal.world_scope_hash
            or head.principal_scope_hash != proposal.principal_scope_hash
        ):
            raise CognitionIntegrityError("proposal scope differs from existing cognition slot")
        # privacy_scope is deliberately not part of cognition_id. Therefore it is
        # immutable inside a slot in V0.1; otherwise a private cognition could be
        # silently re-projected as public through an ordinary consolidation.
        if head.privacy_scope != proposal.privacy_scope:
            raise CognitionIntegrityError("cognition privacy scope cannot change inside an existing slot")
        if head.condition_object_id != proposal.condition_object_id:
            raise CognitionIntegrityError("cognition condition object cannot change inside an existing slot")

    def _report(self, proposal: CognitionProposal, *, support_ids: Iterable[str], counter_ids: Iterable[str], now_ms: int) -> StabilityReport:
        support_ids = _sorted_ids(support_ids)
        counter_ids = _sorted_ids(counter_ids)
        if set(support_ids) & set(counter_ids):
            raise CognitionIntegrityError("one evidence ID cannot support and counter the same cognition")
        support = self._load_evidence_exact(support_ids)
        counter = self._load_evidence_exact(counter_ids)
        self._validate_evidence_scope(proposal, (*support, *counter))
        return evaluate_evidence(
            cognition_id=proposal.cognition_id,
            life_id=proposal.life_id,
            domain=proposal.domain,
            world_scope_hash=proposal.world_scope_hash,
            principal_scope_hash=proposal.principal_scope_hash,
            support=support,
            counter=counter,
            now_ms=now_ms,
            policy=self.policy,
        )

    def _statement(
        self,
        *,
        proposal: CognitionProposal,
        previous: CognitionStatement | None,
        value: CognitionValue,
        status: str,
        level: str,
        confidence_milli: int,
        support_ids: Iterable[str],
        counter_ids: Iterable[str],
        now_ms: int,
        carry_previous_identity: bool,
    ) -> CognitionStatement:
        revision = 1 if previous is None else previous.revision + 1
        carry = previous is not None and carry_previous_identity
        valid_from_ms = previous.valid_from_ms if carry else now_ms
        privacy_scope = previous.privacy_scope if carry else proposal.privacy_scope
        proposal_origin = previous.proposal_origin if carry else proposal.proposal_origin
        prior_ids = previous.prior_ids if carry else _sorted_ids(proposal.prior_ids)
        valid_until_ms = previous.valid_until_ms if carry else proposal.valid_until_ms
        last_verified = now_ms if status in {"STABLE", "CORE"} else (previous.last_verified_at_ms if previous is not None else None)
        statement = CognitionStatement(
            cognition_id=proposal.cognition_id,
            life_id=proposal.life_id,
            domain=proposal.domain,
            world_scope_hash=proposal.world_scope_hash,
            principal_scope_hash=proposal.principal_scope_hash,
            privacy_scope=privacy_scope,
            claim_kind=proposal.claim_kind,
            subject_ref=proposal.subject_ref,
            predicate=proposal.predicate,
            value=value,
            condition_object_id=proposal.condition_object_id,
            condition_sha256=proposal.condition_sha256,
            proposal_origin=proposal_origin,
            status=status,
            stability_level=level,
            confidence_milli=max(0, min(1000, int(confidence_milli))),
            supporting_evidence_ids=_sorted_ids(support_ids),
            counterevidence_ids=_sorted_ids(counter_ids),
            prior_ids=prior_ids,
            valid_from_ms=valid_from_ms,
            valid_until_ms=valid_until_ms,
            last_verified_at_ms=last_verified,
            revision=revision,
            supersedes_statement_sha256=previous.statement_sha256 if previous is not None else None,
            statement_sha256="0" * 64,
        )
        return statement.with_computed_statement_sha256()

    def _decision(
        self,
        *,
        previous: CognitionStatement | None,
        statement: CognitionStatement,
        transition: str,
        report: StabilityReport,
        trigger_ids: Iterable[str],
        now_ms: int,
        authority: str,
        reason: str,
    ) -> CognitionRevision:
        latest = self.store.get_latest_revision(statement.cognition_id)
        revision_id = derive_cognition_revision_id(
            cognition_id=statement.cognition_id,
            sequence=statement.revision,
            from_statement_sha256=previous.statement_sha256 if previous is not None else None,
            to_statement_sha256=statement.statement_sha256,
        )
        decision = CognitionRevision(
            cognition_revision_id=revision_id,
            life_id=statement.life_id,
            cognition_id=statement.cognition_id,
            sequence=statement.revision,
            previous_revision_sha256=latest.revision_sha256 if latest is not None else None,
            from_statement_sha256=previous.statement_sha256 if previous is not None else None,
            to_statement_sha256=statement.statement_sha256,
            from_status=previous.status if previous is not None else None,
            to_status=statement.status,
            from_stability_level=previous.stability_level if previous is not None else None,
            to_stability_level=statement.stability_level,
            transition=transition,
            trigger_evidence_ids=_sorted_ids(trigger_ids),
            support_independence_groups=report.support_groups,
            counter_independence_groups=report.counter_groups,
            support_milli=report.support_milli,
            counter_milli=report.counter_milli,
            correlation_discount_milli=report.correlation_discount_milli,
            staleness_penalty_milli=report.staleness_penalty_milli,
            decision_authority=authority,
            policy_ref=POLICY_REF,
            policy_sha256=self.policy.sha256,
            reason_codes=(reason,),
            created_at_ms=now_ms,
            revision_sha256="0" * 64,
        )
        return decision.with_computed_revision_sha256()

    def _commit(
        self,
        *,
        previous: CognitionStatement | None,
        statement: CognitionStatement,
        transition: str,
        report: StabilityReport,
        trigger_ids: Iterable[str],
        now_ms: int,
        reason: str,
        authority: str = "deterministic_policy",
    ) -> CognitionStatement:
        decision = self._decision(
            previous=previous,
            statement=statement,
            transition=transition,
            report=report,
            trigger_ids=trigger_ids,
            now_ms=now_ms,
            authority=authority,
            reason=reason,
        )
        self.store.commit_transition(
            statement,
            decision,
            expected_head_sha256=previous.statement_sha256 if previous is not None else None,
        )
        return statement

    @staticmethod
    def _eligible_at_least(report: StabilityReport, level: str, policy: StabilityPolicy) -> bool:
        # C4 is a protection class, not a stronger empirical evidence class. C4
        # remains evidence-eligible whenever C3 evidence remains live.
        required = "C3" if level == "C4" else level
        return _LEVEL_ORDER[highest_eligible_level(report, policy)] >= _LEVEL_ORDER[required]

    def _promote_as_far_as_possible(
        self,
        *,
        proposal: CognitionProposal,
        head: CognitionStatement,
        report: StabilityReport,
        support_ids: tuple[str, ...],
        counter_ids: tuple[str, ...],
        now_ms: int,
        transitions: list[str],
    ) -> CognitionStatement:
        target = highest_eligible_level(report, self.policy)
        while head.stability_level in {"C0", "C1", "C2"} and _LEVEL_ORDER[head.stability_level] < _LEVEL_ORDER[target]:
            next_level = {"C0": "C1", "C1": "C2", "C2": "C3"}[head.stability_level]
            status, level = _LEVEL_STATE[next_level]
            statement = self._statement(
                proposal=proposal, previous=head, value=head.value, status=status, level=level,
                confidence_milli=report.net_milli, support_ids=support_ids, counter_ids=counter_ids,
                now_ms=now_ms, carry_previous_identity=True,
            )
            head = self._commit(
                previous=head, statement=statement, transition="PROMOTE", report=report,
                trigger_ids=support_ids, now_ms=now_ms, reason="cognition.promote",
            )
            transitions.append("PROMOTE")
        return head

    def consolidate(
        self,
        proposal: CognitionProposal,
        *,
        support_evidence_ids: Iterable[str] = (),
        counterevidence_ids: Iterable[str] = (),
        now_ms: int,
        decision_authority: str = "deterministic_policy",
    ) -> ConsolidationResult:
        if decision_authority not in {"deterministic_policy", "explicit_system_authority", "migration"}:
            raise CognitionIntegrityError("LLM or caller-defined revision authority is forbidden")
        if (proposal.condition_object_id is None) != (proposal.condition_sha256 is None):
            raise CognitionIntegrityError("proposal condition binding must be all-or-none")
        incoming_support = _sorted_ids(support_evidence_ids)
        incoming_counter = _sorted_ids(counterevidence_ids)
        if set(incoming_support) & set(incoming_counter):
            raise CognitionIntegrityError("one evidence ID cannot support and counter the same proposal")

        head = self.store.get_head(proposal.cognition_id)
        transitions: list[str] = []
        if head is None:
            report = self._report(proposal, support_ids=incoming_support, counter_ids=incoming_counter, now_ms=now_ms)
            candidate = self._statement(
                proposal=proposal, previous=None, value=proposal.value, status="CANDIDATE", level="C0",
                confidence_milli=report.net_milli, support_ids=incoming_support, counter_ids=incoming_counter,
                now_ms=now_ms, carry_previous_identity=False,
            )
            head = self._commit(
                previous=None, statement=candidate, transition="GENESIS", report=report,
                trigger_ids=incoming_support, now_ms=now_ms, authority=decision_authority,
                reason="cognition.genesis",
            )
            transitions.append("GENESIS")
            head = self._promote_as_far_as_possible(
                proposal=proposal, head=head, report=report, support_ids=incoming_support,
                counter_ids=incoming_counter, now_ms=now_ms, transitions=transitions,
            )
            return ConsolidationResult(proposal.cognition_id, head, True, tuple(transitions), report, "created")

        self._validate_existing_slot(head, proposal)
        if head.status == "RETIRED":
            raise CognitionIntegrityError("retired cognition cannot be silently revived")
        same_value = _same_value(head.value, proposal.value)

        if same_value:
            support_ids = _sorted_ids((*head.supporting_evidence_ids, *incoming_support))
            counter_ids = _sorted_ids((*head.counterevidence_ids, *incoming_counter))
            report = self._report(proposal, support_ids=support_ids, counter_ids=counter_ids, now_ms=now_ms)

            if head.status in {"CHALLENGED", "REVERIFYING"}:
                if head.status == "CHALLENGED":
                    reverifying = self._statement(
                        proposal=proposal, previous=head, value=head.value, status="REVERIFYING", level=head.stability_level,
                        confidence_milli=report.net_milli, support_ids=support_ids, counter_ids=counter_ids,
                        now_ms=now_ms, carry_previous_identity=True,
                    )
                    head = self._commit(
                        previous=head, statement=reverifying, transition="BEGIN_REVERIFY", report=report,
                        trigger_ids=(*incoming_support, *incoming_counter), now_ms=now_ms,
                        reason="cognition.begin_reverify",
                    )
                    transitions.append("BEGIN_REVERIFY")
                if self._eligible_at_least(report, head.stability_level, self.policy):
                    status, level = _LEVEL_STATE[head.stability_level]
                    confirmed = self._statement(
                        proposal=proposal, previous=head, value=head.value, status=status, level=level,
                        confidence_milli=report.net_milli, support_ids=support_ids, counter_ids=counter_ids,
                        now_ms=now_ms, carry_previous_identity=True,
                    )
                    head = self._commit(
                        previous=head, statement=confirmed, transition="CONFIRM", report=report,
                        trigger_ids=(*incoming_support, *incoming_counter), now_ms=now_ms,
                        reason="cognition.confirm",
                    )
                    transitions.append("CONFIRM")
                return ConsolidationResult(proposal.cognition_id, head, bool(transitions), tuple(transitions), report, "reverified")

            if challenge_is_material(report, current_level=head.stability_level):
                challenged = self._statement(
                    proposal=proposal, previous=head, value=head.value, status="CHALLENGED", level=head.stability_level,
                    confidence_milli=report.net_milli, support_ids=support_ids, counter_ids=counter_ids,
                    now_ms=now_ms, carry_previous_identity=True,
                )
                head = self._commit(
                    previous=head, statement=challenged, transition="CHALLENGE", report=report,
                    trigger_ids=counter_ids, now_ms=now_ms, reason="cognition.challenge",
                )
                transitions.append("CHALLENGE")
                return ConsolidationResult(proposal.cognition_id, head, True, tuple(transitions), report, "challenged")

            original_level = head.stability_level
            head = self._promote_as_far_as_possible(
                proposal=proposal, head=head, report=report, support_ids=support_ids,
                counter_ids=counter_ids, now_ms=now_ms, transitions=transitions,
            )
            if not transitions and self._eligible_at_least(report, original_level, self.policy):
                changed_material = (
                    support_ids != head.supporting_evidence_ids
                    or counter_ids != head.counterevidence_ids
                    or report.net_milli != head.confidence_milli
                )
                if changed_material:
                    refreshed = self._statement(
                        proposal=proposal, previous=head, value=head.value, status=head.status, level=head.stability_level,
                        confidence_milli=report.net_milli, support_ids=support_ids, counter_ids=counter_ids,
                        now_ms=now_ms, carry_previous_identity=True,
                    )
                    head = self._commit(
                        previous=head, statement=refreshed, transition="REFRESH", report=report,
                        trigger_ids=(*incoming_support, *incoming_counter), now_ms=now_ms,
                        reason="cognition.refresh",
                    )
                    transitions.append("REFRESH")
            return ConsolidationResult(proposal.cognition_id, head, bool(transitions), tuple(transitions), report, "same_value")

        report = self._report(proposal, support_ids=incoming_support, counter_ids=incoming_counter, now_ms=now_ms)
        if head.status == "CANDIDATE":
            replacement = self._statement(
                proposal=proposal, previous=head, value=proposal.value, status="CANDIDATE", level="C0",
                confidence_milli=report.net_milli, support_ids=incoming_support, counter_ids=incoming_counter,
                now_ms=now_ms, carry_previous_identity=False,
            )
            head = self._commit(
                previous=head, statement=replacement, transition="REPLACE_CANDIDATE", report=report,
                trigger_ids=incoming_support, now_ms=now_ms, reason="cognition.replace_candidate",
            )
            transitions.append("REPLACE_CANDIDATE")
            head = self._promote_as_far_as_possible(
                proposal=proposal, head=head, report=report, support_ids=incoming_support,
                counter_ids=incoming_counter, now_ms=now_ms, transitions=transitions,
            )
            return ConsolidationResult(proposal.cognition_id, head, True, tuple(transitions), report, "candidate_replaced")

        if head.status not in {"CHALLENGED", "REVERIFYING"}:
            old_support = head.supporting_evidence_ids
            old_counter = _sorted_ids((*head.counterevidence_ids, *incoming_support))
            old_report = self._report(proposal, support_ids=old_support, counter_ids=old_counter, now_ms=now_ms)
            new_claim_material = report.net_milli >= self.policy.provisional_threshold_milli and report.support_group_count >= 1
            if not new_claim_material or not challenge_is_material(old_report, current_level=head.stability_level):
                return ConsolidationResult(proposal.cognition_id, head, False, (), report, "insufficient_challenge")
            challenged = self._statement(
                proposal=proposal, previous=head, value=head.value, status="CHALLENGED", level=head.stability_level,
                confidence_milli=old_report.net_milli, support_ids=old_support, counter_ids=old_counter,
                now_ms=now_ms, carry_previous_identity=True,
            )
            head = self._commit(
                previous=head, statement=challenged, transition="CHALLENGE", report=old_report,
                trigger_ids=incoming_support, now_ms=now_ms, reason="cognition.challenge_new_value",
            )
            transitions.append("CHALLENGE")

        if head.status == "CHALLENGED":
            old_report = self._report(
                proposal, support_ids=head.supporting_evidence_ids,
                counter_ids=head.counterevidence_ids, now_ms=now_ms,
            )
            reverifying = self._statement(
                proposal=proposal, previous=head, value=head.value, status="REVERIFYING", level=head.stability_level,
                confidence_milli=old_report.net_milli, support_ids=head.supporting_evidence_ids,
                counter_ids=head.counterevidence_ids, now_ms=now_ms, carry_previous_identity=True,
            )
            head = self._commit(
                previous=head, statement=reverifying, transition="BEGIN_REVERIFY", report=old_report,
                trigger_ids=incoming_support, now_ms=now_ms, reason="cognition.begin_reverify_new_value",
            )
            transitions.append("BEGIN_REVERIFY")

        if head.status != "REVERIFYING":
            return ConsolidationResult(proposal.cognition_id, head, bool(transitions), tuple(transitions), report, "not_reverifying")
        required_level = head.stability_level
        required_for_new = "C3" if required_level == "C4" else required_level
        if not self._eligible_at_least(report, required_for_new, self.policy):
            return ConsolidationResult(proposal.cognition_id, head, bool(transitions), tuple(transitions), report, "awaiting_new_value_evidence")
        if required_level == "C4" and decision_authority not in {"explicit_system_authority", "migration"}:
            return ConsolidationResult(proposal.cognition_id, head, bool(transitions), tuple(transitions), report, "protected_c4_requires_authority")

        status, level = _LEVEL_STATE[required_level]
        superseding = self._statement(
            proposal=proposal, previous=head, value=proposal.value, status=status, level=level,
            confidence_milli=report.net_milli, support_ids=incoming_support, counter_ids=incoming_counter,
            now_ms=now_ms, carry_previous_identity=False,
        )
        head = self._commit(
            previous=head, statement=superseding, transition="SUPERSEDE", report=report,
            trigger_ids=incoming_support, now_ms=now_ms, authority=decision_authority,
            reason="cognition.supersede",
        )
        transitions.append("SUPERSEDE")
        return ConsolidationResult(proposal.cognition_id, head, True, tuple(transitions), report, "superseded")

    def protect(self, cognition_id: str, *, now_ms: int, decision_authority: str) -> CognitionStatement:
        if decision_authority not in {"explicit_system_authority", "migration"}:
            raise CognitionIntegrityError("C4 protection requires explicit system authority or migration")
        head = self.store.get_head(cognition_id)
        if head is None or (head.status, head.stability_level) != ("CORE", "C3"):
            raise CognitionIntegrityError("only CORE/C3 cognition may enter protected C4")
        proposal = CognitionProposal(
            life_id=head.life_id, domain=head.domain, world_scope_hash=head.world_scope_hash,
            principal_scope_hash=head.principal_scope_hash, privacy_scope=head.privacy_scope,
            claim_kind=head.claim_kind, subject_ref=head.subject_ref, predicate=head.predicate,
            value=head.value, proposal_origin=head.proposal_origin,
            condition_object_id=head.condition_object_id, condition_sha256=head.condition_sha256,
            prior_ids=head.prior_ids, valid_until_ms=head.valid_until_ms,
        )
        report = self._report(
            proposal, support_ids=head.supporting_evidence_ids,
            counter_ids=head.counterevidence_ids, now_ms=now_ms,
        )
        if highest_eligible_level(report, self.policy) != "C3":
            raise CognitionIntegrityError("C4 protection requires currently valid CORE-level evidence")
        protected = self._statement(
            proposal=proposal, previous=head, value=head.value, status="CORE", level="C4",
            confidence_milli=report.net_milli, support_ids=head.supporting_evidence_ids,
            counter_ids=head.counterevidence_ids, now_ms=now_ms, carry_previous_identity=True,
        )
        return self._commit(
            previous=head, statement=protected, transition="PROTECT", report=report,
            trigger_ids=head.supporting_evidence_ids, now_ms=now_ms,
            authority=decision_authority, reason="cognition.protect",
        )


__all__ = ["CognitionConsolidator", "CognitionProposal", "ConsolidationResult", "POLICY_REF"]
