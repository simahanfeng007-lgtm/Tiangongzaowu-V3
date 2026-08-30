"""P19-R2 M4.1 Final — VerificationPlanExecutor (production dispatch).

M4.1 Final Fix §1-§3: the production component that dispatches an
active plan's entries to the real oracles, records results through the
authoritative Recorder (unified path for PASS/FAIL and ERROR records),
and materializes the readiness via the authoritative builder.

Architecture boundary: NOT a second Runtime, NOT a second Gate.
"""

from __future__ import annotations

from typing import Any

from contracts.canonical import canonical_sha256
from contracts.verification import (
    VerificationPlan,
    VerificationPlanEntryV2,
    VerificationReadiness,
)
from total_gateway.outcome_oracles.artifact_content import ArtifactContentOracle
from total_gateway.outcome_oracles.effect_state import EffectStateOracle
from total_gateway.outcome_oracles.repository_state import RepositoryStateOracle
from total_gateway.verification_readiness import build_readiness
from total_gateway.verification_recording import VerificationRecorder


class VerificationPlanExecutorError(RuntimeError):
    """Verification dispatch failure — never falls back to NONE."""


class VerificationPlanExecutor:
    """Dispatches the active plan's entries to the existing oracles."""

    def __init__(
        self,
        *,
        snapshot,
        store,
        object_store,
        fact_ledger,
        plan: VerificationPlan,
    ) -> None:
        if plan.registry_snapshot_sha256 != snapshot.snapshot_sha256:
            raise VerificationPlanExecutorError(
                "plan registry snapshot does not match the executor snapshot"
            )
        self._snapshot = snapshot
        self._store = store
        self._plan = plan
        self._recorder = VerificationRecorder(snapshot=snapshot, store=store)
        self._artifact_oracle = ArtifactContentOracle(
            snapshot=snapshot, object_store=object_store, fact_ledger=fact_ledger,
        )
        self._effect_oracle = EffectStateOracle(snapshot=snapshot, store=store)
        self._repository_oracle = RepositoryStateOracle(
            snapshot=snapshot, store=store,
        )

    def execute(
        self,
        *,
        evaluated_at_ms: int,
        artifact_manifests: tuple = (),
    ) -> VerificationReadiness:
        """Run all plan entries; produce records + readiness.

        ``artifact_manifests``: the authoritative ArtifactManifests that
        already passed ArtifactGate/QC in the production flow (supplied
        by orchestration context, not re-discovered).
        """
        manifests_by_revision = {
            m.artifact_revision_id: m for m in artifact_manifests
        }
        for entry in self._plan.entries:
            try:
                self._dispatch_entry(
                    entry, evaluated_at_ms=evaluated_at_ms,
                    manifests_by_revision=manifests_by_revision,
                )
            except Exception as exc:
                # infrastructure failure → ERROR record with REAL lineage
                self._record_error(
                    entry, evaluated_at_ms=evaluated_at_ms,
                    error_detail=f"{type(exc).__name__}: {exc}"[:200],
                )
        readiness = build_readiness(
            plan=self._plan,
            snapshot=self._snapshot,
            store=self._store,
            evaluated_at_ms=evaluated_at_ms,
        )
        self._store.put_verification_readiness(
            readiness, recorded_at_ms=evaluated_at_ms + 1,
        )
        return readiness

    def _dispatch_entry(
        self,
        entry: VerificationPlanEntryV2,
        *,
        evaluated_at_ms: int,
        manifests_by_revision: dict,
    ) -> None:
        kind = entry.predicate.subject_kind
        if kind == "artifact":
            manifest = manifests_by_revision.get(entry.subject_identity)
            if manifest is None:
                raise VerificationPlanExecutorError(
                    f"artifact manifest not in execution context:"
                    f" {entry.subject_identity}"
                )
            record = self._artifact_oracle.evaluate(
                manifest, entry.predicate,
                evaluated_at_ms=evaluated_at_ms,
                evaluation_phase=entry.evaluation_phase,
            )
        elif kind == "effect":
            record = self._effect_oracle.evaluate(
                entry.subject_identity, entry.predicate,
                evaluated_at_ms=evaluated_at_ms,
                evaluation_phase=entry.evaluation_phase,
            )
        elif kind == "repository":
            bindings = self._store.list_repository_bindings_for_subject(
                entry.subject_identity
            )
            sorted_bindings = sorted(
                bindings,
                key=lambda b: (b["observation_role"], b["observed_at_ms"]),
            )
            pre = next(
                (b for b in sorted_bindings if b["observation_role"] == "PRE"), None
            )
            post = next(
                (b for b in sorted_bindings if b["observation_role"] == "POST"), None
            )
            if pre is None or post is None:
                raise VerificationPlanExecutorError(
                    "repository PRE/POST bindings incomplete"
                )
            record = self._repository_oracle.evaluate(
                subject_effect_id=entry.subject_identity,
                pre_binding_id=pre["binding_id"],
                post_binding_id=post["binding_id"],
                predicate=entry.predicate,
                evaluated_at_ms=evaluated_at_ms,
                evaluation_phase=entry.evaluation_phase,
            )
        else:
            raise VerificationPlanExecutorError(
                f"unsupported subject_kind: {kind}"
            )
        # M4.1 §2: unified path — normal records also go through the
        # Recorder (never direct store puts).
        self._recorder.record(record, recorded_at_ms=evaluated_at_ms)

    def _record_error(
        self, entry: VerificationPlanEntryV2, *, evaluated_at_ms: int,
        error_detail: str,
    ) -> None:
        """ERROR record with REAL plan lineage, via the Recorder."""
        from contracts.verification import VerificationRecord
        from contracts.verification import derive_verification_record_id

        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            request_id=self._plan.request_id,
            run_id=self._plan.run_id,
            generation=self._plan.generation,
            verifier_id=entry.verifier_id,
            verifier_version=entry.verifier_version,
            registry_snapshot_sha256=self._snapshot.snapshot_sha256,
            predicate_id=entry.predicate.predicate_id,
            predicate_type=entry.predicate.predicate_type,
            subject_kind=entry.predicate.subject_kind,
            subject_identity=entry.subject_identity,
            evaluation_phase=entry.evaluation_phase,
            status="ERROR",
            enforcement="RECORD",
            reason_codes=("executor.oracle_dispatch_failure",),
            evidence_refs=(
                f"predicate_sha256:{entry.predicate.predicate_sha256}",
            ),
            evidence_sha256=canonical_sha256(
                [f"predicate_sha256:{entry.predicate.predicate_sha256}"]
            ),
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=evaluated_at_ms,
            result_sha256="0" * 64,
        )
        record = VerificationRecord(**payload).with_computed_sha256()
        record = record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )
        self._recorder.record(record, recorded_at_ms=evaluated_at_ms)


__all__ = [
    "VerificationPlanExecutor",
    "VerificationPlanExecutorError",
]
