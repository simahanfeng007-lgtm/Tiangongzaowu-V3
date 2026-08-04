"""Single catalog, selection, activation, and fact-derived Skill authority."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    CapabilityManifest,
    SkillActivationGrant,
    SkillSelectionRecord,
    canonical_sha256,
)

from .fact_ledger import FactLedger
from .skill_selection import SkillResolution, SkillSelectionError, SkillSelectionService
from .store import GatewayStateStore, SkillSelectionRegistration


class SkillAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorizedSkillResolution:
    resolution: SkillResolution
    selection: SkillSelectionRegistration
    activation: SkillActivationGrant | None


@dataclass(frozen=True)
class SkillStepStatus:
    request_id: str
    run_id: str
    generation: int
    skill_id: str
    skill_version: str
    skill_sha256: str
    activation_id: str
    activation_sha256: str
    catalog_sha256: str
    capability_manifest_sha256: str
    current_stage: str
    completed_actions: tuple[str, ...]
    failed_actions: tuple[str, ...]
    pending_actions: tuple[str, ...]
    allowed_next_actions: tuple[str, ...]
    supporting_fact_ids: tuple[str, ...]
    complete: bool
    reason_code: str

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "skill_sha256": self.skill_sha256,
            "activation_id": self.activation_id,
            "activation_sha256": self.activation_sha256,
            "catalog_sha256": self.catalog_sha256,
            "capability_manifest_sha256": self.capability_manifest_sha256,
            "current_stage": self.current_stage,
            "completed_actions": list(self.completed_actions),
            "failed_actions": list(self.failed_actions),
            "pending_actions": list(self.pending_actions),
            "allowed_next_actions": list(self.allowed_next_actions),
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "complete": self.complete,
            "reason_code": self.reason_code,
        }


class SkillAuthority:
    """The only writer of Skill selections and activations."""

    def __init__(
        self,
        selection: SkillSelectionService,
        capability_manifest: CapabilityManifest,
        store: GatewayStateStore,
        facts: FactLedger,
    ) -> None:
        if not capability_manifest.has_valid_sha256():
            raise SkillAuthorityError("Skill capability manifest digest is invalid")
        self.selection = selection
        self.capability_manifest = capability_manifest
        self.store = store
        self.facts = facts
        # Construction is a startup drift gate.  No request may substitute a
        # second catalog or manifest after this immutable snapshot is bound.
        if not selection.catalog.sha256:
            raise SkillAuthorityError("Skill catalog digest is invalid")

    @property
    def catalog_sha256(self) -> str:
        return self.selection.catalog.sha256

    def system_recommend(
        self,
        query: str,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        decided_at_ms: int,
        limit: int = 3,
    ) -> SkillSelectionRecord:
        record = self.selection.system_recommend(
            query,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            capability_manifest=self.capability_manifest,
            decided_at_ms=decided_at_ms,
            limit=limit,
        )
        if record.skill_catalog_hash != self.catalog_sha256:
            raise SkillAuthorityError("system Skill selection crossed catalog snapshots")
        self.store.record_skill_selection(record)
        return record

    def model_request(
        self,
        operation: str,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        principal_scope_hash: str,
        decided_at_ms: int,
        query: str | None = None,
        skill_id: str | None = None,
        decline: bool = False,
        limit: int = 32,
    ) -> AuthorizedSkillResolution:
        try:
            resolution = self.selection.model_request(
                operation,  # type: ignore[arg-type]
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                capability_manifest=self.capability_manifest,
                decided_at_ms=decided_at_ms,
                query=query,
                skill_id=skill_id,
                decline=decline,
                limit=limit,
            )
        except (SkillSelectionError, ValueError) as exc:
            raise SkillAuthorityError(str(exc)) from exc
        record = resolution.record
        if (
            record.skill_catalog_hash != self.catalog_sha256
            or record.capability_manifest_hash != self.capability_manifest.sha256
        ):
            raise SkillAuthorityError("model Skill selection crossed authority snapshots")
        selection = self.store.record_skill_selection(record)
        activation = None
        if record.decision == "activate":
            candidate = next(
                (
                    item
                    for item in record.candidates
                    if item.skill_id == record.selected_skill_id
                ),
                None,
            )
            if candidate is None or not candidate.compatible or not candidate.required_actions:
                raise SkillAuthorityError("incompatible Skill cannot receive activation")
            activation_id = "skill_activation_" + canonical_sha256(
                {
                    "domain": "tiangong.gateway.skill-activation.v1",
                    "principal_scope_hash": principal_scope_hash,
                    "selection_id": record.selection_id,
                }
            )
            activation = SkillActivationGrant(
                activation_id=activation_id,
                selection_id=record.selection_id,
                request_id=record.request_id,
                run_id=record.run_id,
                generation=record.generation,
                principal_scope_hash=principal_scope_hash,
                skill_catalog_hash=record.skill_catalog_hash,
                capability_manifest_hash=record.capability_manifest_hash,
                skill_id=candidate.skill_id,
                skill_version=candidate.version,
                skill_sha256=candidate.sha256,
                allowed_action_ids=candidate.required_actions,
                issued_at_ms=decided_at_ms,
                expires_at_ms=decided_at_ms + 3_600_000,
                activation_sha256="0" * 64,
            ).with_computed_sha256()
            self.store.record_skill_activation(activation)
        return AuthorizedSkillResolution(resolution, selection, activation)

    def step_check(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        principal_scope_hash: str,
        skill_id: str,
        activation_sha256: str,
        checked_at_ms: int,
    ) -> SkillStepStatus:
        registration = self.store.get_skill_activation_by_sha256(activation_sha256)
        if registration is None:
            raise SkillAuthorityError("Skill activation does not exist")
        grant = registration.grant
        if (
            grant.request_id != request_id
            or grant.run_id != run_id
            or grant.generation != generation
            or grant.principal_scope_hash != principal_scope_hash
            or grant.skill_id != skill_id
            or grant.skill_catalog_hash != self.catalog_sha256
            or grant.capability_manifest_hash != self.capability_manifest.sha256
            or not grant.issued_at_ms <= checked_at_ms <= grant.expires_at_ms
        ):
            raise SkillAuthorityError("Skill step check crossed its activation authority")
        definition = self.selection.catalog.get(skill_id)
        if (
            definition is None
            or definition.version != grant.skill_version
            or definition.sha256 != grant.skill_sha256
            or definition.required_actions != grant.allowed_action_ids
        ):
            raise SkillAuthorityError("Skill catalog drifted after activation")

        latest: dict[str, tuple[int, str, str]] = {}
        fact_ids: list[str] = []
        for fact in self.facts.list_request_facts(
            request_id,
            run_id=run_id,
            generation=generation,
        ):
            if fact.action_id not in grant.allowed_action_ids:
                continue
            fact_ids.append(fact.fact_id)
            candidate = (fact.observed_at_ms, fact.fact_id, fact.fact_type)
            previous = latest.get(fact.action_id)
            if previous is None or candidate[:2] > previous[:2]:
                latest[fact.action_id] = candidate

        success_types = {"execution.succeeded", "artifact.qc_passed"}
        completed = tuple(
            sorted(action for action, value in latest.items() if value[2] in success_types)
        )
        failed = tuple(
            sorted(action for action, value in latest.items() if value[2] not in success_types)
        )
        pending = tuple(
            action for action in grant.allowed_action_ids if action not in set(completed)
        )
        if failed:
            stage = "repair"
            allowed = failed[:8]
            reason = "skill.fact_failure_requires_repair"
        elif not pending:
            stage = "complete"
            allowed = ()
            reason = "skill.all_required_facts_verified"
        else:
            quality = tuple(
                action
                for action in pending
                if action.startswith("qc.") or action.endswith(".audit")
            )
            non_quality = tuple(action for action in pending if action not in set(quality))
            if non_quality:
                stage = "execute"
                allowed = non_quality[:8]
                reason = "skill.required_action_facts_pending"
            else:
                stage = "quality_gate"
                allowed = quality[:8]
                reason = "skill.quality_facts_pending"
        return SkillStepStatus(
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            skill_id=grant.skill_id,
            skill_version=grant.skill_version,
            skill_sha256=grant.skill_sha256,
            activation_id=grant.activation_id,
            activation_sha256=grant.activation_sha256,
            catalog_sha256=grant.skill_catalog_hash,
            capability_manifest_sha256=grant.capability_manifest_hash,
            current_stage=stage,
            completed_actions=completed,
            failed_actions=failed,
            pending_actions=pending,
            allowed_next_actions=allowed,
            supporting_fact_ids=tuple(sorted(fact_ids)),
            complete=not pending and not failed,
            reason_code=reason,
        )


__all__ = [
    "AuthorizedSkillResolution",
    "SkillAuthority",
    "SkillAuthorityError",
    "SkillStepStatus",
]
