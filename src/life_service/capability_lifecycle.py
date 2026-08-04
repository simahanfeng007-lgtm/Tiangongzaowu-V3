"""G5 capability lifecycle: candidate -> compile+fixture -> QC -> pointer CAS.

Hard rules (spec 10.2/10.7): evidence must be terminal facts of the current
revision; model self-score is never sufficient; a failed candidate never
replaces the prior CURRENT; rollback preserves evidence.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from contracts import SkillDefinitionCore, canonical_sha256

from .store import LifeShadowStore, LifeShadowStoreError


class CapabilityLifecycleError(RuntimeError):
    pass


FixtureRunner = Callable[[str, Mapping[str, Any]], list[Mapping[str, Any]]]


class CapabilityLifecycle:
    def __init__(
        self,
        store: LifeShadowStore,
        *,
        fixture_runner: FixtureRunner | None = None,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._fixture_runner = fixture_runner
        self._now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)

    @staticmethod
    def evidence_guard(fact_records: list[Mapping[str, Any]]) -> None:
        """Terminal-facts-only and current-revision-only evidence rules."""
        if not fact_records:
            raise CapabilityLifecycleError("capability evidence is empty")
        for fact in fact_records:
            if not bool(fact.get("terminal")):
                raise CapabilityLifecycleError("capability evidence must be terminal facts")
            if int(fact.get("revision") or 0) != int(fact.get("current_revision") or -1):
                raise CapabilityLifecycleError("capability evidence must be current revision")

    def register_candidate(
        self,
        *,
        life_id: str,
        skill_id: str,
        skill_version: str,
        definition: SkillDefinitionCore,
        source_fact_refs: tuple[str, ...],
    ) -> str:
        if not definition.has_valid_sha256():
            raise CapabilityLifecycleError("skill definition digest is invalid")
        candidate_id = "cap_" + canonical_sha256(
            {
                "domain": "tiangong.v21.capability-candidate.v1",
                "life_id": life_id,
                "skill_id": skill_id,
                "skill_version": skill_version,
                "artifact_sha256": definition.artifact_sha256,
                "definition_sha256": definition.definition_sha256,
            }
        )
        payload = canonical_sha256(
            {
                "candidate_id": candidate_id,
                "definition_sha256": definition.definition_sha256,
                "source_fact_refs": tuple(sorted(set(source_fact_refs))),
            }
        )
        self._store.put_capability_candidate(
            candidate_id=candidate_id,
            life_id=life_id,
            skill_id=skill_id,
            skill_version=skill_version,
            artifact_sha256=definition.artifact_sha256,
            source_fact_refs=tuple(sorted(set(source_fact_refs))),
            phase="COMPILED",
            payload_sha256=payload,
            created_at_ms=self._now_fn(),
        )
        return candidate_id

    def run_fixtures(
        self,
        *,
        candidate_id: str,
        payload_sha256: str,
        fixture_set: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        """Execute success + failure fixtures; a wrong result rejects the candidate."""
        if self._fixture_runner is None:
            raise CapabilityLifecycleError("fixture runner is not installed")
        results = self._fixture_runner(candidate_id, fixture_set)
        if not results:
            raise CapabilityLifecycleError("fixture run produced no evidence")
        for item in results:
            fixture_kind = str(item.get("fixture_kind") or "")
            passed = bool(item.get("passed"))
            if fixture_kind == "success" and not passed:
                raise CapabilityLifecycleError("success fixture failed")
            if fixture_kind == "failure" and not passed:
                raise CapabilityLifecycleError("failure fixture unexpectedly succeeded")
        self._store.advance_capability_candidate(
            candidate_id=candidate_id,
            to_phase="EXECUTION_TESTED",
            expected_phase="COMPILED",
            payload_sha256=payload_sha256,
        )
        return results

    def qc_pass(self, *, candidate_id: str, payload_sha256: str) -> None:
        self._store.advance_capability_candidate(
            candidate_id=candidate_id,
            to_phase="QC_PASSED",
            expected_phase="EXECUTION_TESTED",
            payload_sha256=payload_sha256,
        )

    def stage_shadow(self, *, candidate_id: str, payload_sha256: str) -> None:
        self._store.advance_capability_candidate(
            candidate_id=candidate_id,
            to_phase="SHADOW",
            expected_phase="QC_PASSED",
            payload_sha256=payload_sha256,
        )

    def promote(
        self,
        *,
        life_id: str,
        skill_id: str,
        candidate_id: str,
        artifact_sha256: str,
        payload_sha256: str,
        expected_pointer_sha256: str | None,
    ) -> str:
        """Pointer CAS to CURRENT; a stale/mutation/failure never replaces prior current."""
        pointer_sha256 = canonical_sha256(
            {
                "domain": "tiangong.v21.capability-pointer.v1",
                "life_id": life_id,
                "skill_id": skill_id,
                "candidate_id": candidate_id,
                "artifact_sha256": artifact_sha256,
            }
        )
        try:
            self._store.put_capability_pointer(
                life_id=life_id,
                skill_id=skill_id,
                candidate_id=candidate_id,
                artifact_sha256=artifact_sha256,
                pointer_sha256=pointer_sha256,
                expected_pointer_sha256=expected_pointer_sha256,
                now_ms=self._now_fn(),
            )
            self._store.advance_capability_candidate(
                candidate_id=candidate_id,
                to_phase="CURRENT",
                expected_phase="SHADOW",
                payload_sha256=payload_sha256,
            )
        except LifeShadowStoreError as exc:
            raise CapabilityLifecycleError(str(exc)) from exc
        return pointer_sha256

    def retire(self, *, candidate_id: str, payload_sha256: str, expected_phase: str) -> None:
        self._store.advance_capability_candidate(
            candidate_id=candidate_id,
            to_phase="RETIRED",
            expected_phase=expected_phase,
            payload_sha256=payload_sha256,
        )

    def rollback_pointer(
        self,
        *,
        life_id: str,
        skill_id: str,
        previous_candidate_id: str,
        previous_artifact_sha256: str,
        expected_pointer_sha256: str,
    ) -> str:
        """CAS rollback to a prior LKG pointer; evidence rows are preserved."""
        pointer_sha256 = canonical_sha256(
            {
                "domain": "tiangong.v21.capability-pointer.v1",
                "life_id": life_id,
                "skill_id": skill_id,
                "candidate_id": previous_candidate_id,
                "artifact_sha256": previous_artifact_sha256,
                "rollback": True,
            }
        )
        try:
            self._store.put_capability_pointer(
                life_id=life_id,
                skill_id=skill_id,
                candidate_id=previous_candidate_id,
                artifact_sha256=previous_artifact_sha256,
                pointer_sha256=pointer_sha256,
                expected_pointer_sha256=expected_pointer_sha256,
                now_ms=self._now_fn(),
            )
        except LifeShadowStoreError as exc:
            raise CapabilityLifecycleError(str(exc)) from exc
        return pointer_sha256
