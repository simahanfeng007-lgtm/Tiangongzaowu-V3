"""P19-R2 M3 Gateway EffectStateOracle — RECORD ONLY.

Status: implementation-present / descriptor-registered /
production-unwired.

Evaluates explicit ``AcceptancePredicate``s against the authoritative
effect ledger (``GatewayStateStore.effect_ledger``) plus bound
``write_evidence.v2`` facts. Core discipline:

* lineage comes from the effect ledger claim itself — callers never
  supply request/run/generation;
* ``EffectResult.status == SUCCEEDED`` only proves the execution result
  state. Target predicates re-observe the target from v2 evidence:
  ``target_exists``/``target_sha256_matches`` need verified post rows,
  ``required_change_observed`` needs the observed mutation set;
* AMBIGUOUS never PASSes; FAILED_FINAL proves terminal_succeeded FAIL;
  missing final-state evidence is INCONCLUSIVE, never a guess;
* authority failures (unknown effect, corrupt ledger row) are ERROR.
"""

from __future__ import annotations

from typing import Any

from contracts.verification import AcceptancePredicate, RegistrySnapshot, VerificationRecord
from total_gateway.outcome_oracles._common import (
    OracleSnapshotInvalid,
    assemble_record,
    bind_snapshot_and_descriptor,
)
from total_gateway.verification_oracle_config import (
    EFFECT_DESCRIPTOR_EXPECTATIONS,
    EFFECT_IMPLEMENTED_PREDICATE_TYPES,
    EFFECT_INSPECTOR_SEMANTIC_VERSION,
    EFFECT_VERIFIER_ID,
    effect_oracle_config_sha256,
)

_EFFECT_IMPLEMENTATION_REF = "src/total_gateway/outcome_oracles/effect_state.py"

#: Placeholder lineage for authority failures where the ledger cannot
#: supply a binding (unknown/corrupt effect). All-zero ids satisfy the
#: record contract while carrying no lineage claims.
_UNBOUND_LINEAGE = {
    "request_id": "req_" + "0" * 64,
    "run_id": "run_" + "0" * 64,
    "generation": 0,
}

#: head states that PROVE the effect did not terminal-succeed.
_PROVEN_NOT_SUCCEEDED = frozenset({"FAILED_FINAL"})
#: head states that prove nothing either way.
_UNPROVEN_STATES = frozenset(
    {"CLAIMED", "SIDE_EFFECT_STARTED", "AMBIGUOUS", "RECONCILED"}
)


class EffectStateOracle:
    """Deterministic effect oracle over the authoritative effect ledger."""

    def __init__(self, *, snapshot: RegistrySnapshot, store) -> None:
        snapshot, descriptor = bind_snapshot_and_descriptor(
            snapshot,
            verifier_id=EFFECT_VERIFIER_ID,
            verifier_version=EFFECT_INSPECTOR_SEMANTIC_VERSION,
            config_sha256=effect_oracle_config_sha256(),
            supported_predicate_types=EFFECT_IMPLEMENTED_PREDICATE_TYPES,
            expectations=EFFECT_DESCRIPTOR_EXPECTATIONS,
            implementation_ref=_EFFECT_IMPLEMENTATION_REF,
            timeout_ms=EFFECT_DESCRIPTOR_EXPECTATIONS["timeout_ms"],
        )
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_store", store)

    @property
    def descriptor(self):
        return self._descriptor  # type: ignore[attr-defined]

    def evaluate(
        self,
        effect_id: str,
        predicate: AcceptancePredicate,
        *,
        evaluated_at_ms: int,
        evaluation_phase: str = "POST_EXECUTION",
    ) -> VerificationRecord:
        if predicate.subject_kind != "effect" or not predicate.has_valid_identity():
            raise ValueError("predicate failed full semantic identity validation")
        status, reason_codes, observation, lineage, evidence_sha = (
            self._evaluate_to_status(effect_id, predicate)
        )
        return assemble_record(
            descriptor=self._descriptor,  # type: ignore[attr-defined]
            snapshot=self._snapshot,  # type: ignore[attr-defined]
            predicate=predicate,
            subject_kind="effect",
            subject_identity=effect_id,
            request_id=lineage["request_id"],
            run_id=lineage["run_id"],
            generation=lineage["generation"],
            status=status,
            reason_codes=reason_codes,
            evidence_refs=(
                f"effect_head:{effect_id}",
                f"effect_state:{observation.get('head_state', '')}",
                f"effect_result_sha256:{observation.get('result_sha256', '')}",
            )
            + (
                (f"write_evidence:{evidence_sha}",) if evidence_sha else ()
            ),
            observation=observation,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=evaluation_phase,
        )

    # -- internals ---------------------------------------------------------

    def _evaluate_to_status(self, effect_id, predicate):
        # 1. Authority: the ledger row is the only lineage/state source.
        try:
            record = self._store.get_effect(effect_id)  # type: ignore[attr-defined]
        except Exception:
            return (
                "ERROR",
                ("authority:ledger_corrupt",),
                {},
                dict(_UNBOUND_LINEAGE),
                None,
            )
        if record is None:
            return (
                "ERROR",
                ("authority:effect_not_found",),
                {},
                dict(_UNBOUND_LINEAGE),
                None,
            )
        claim = record.claim
        lineage = {
            "request_id": claim.request_id,
            "run_id": claim.run_id,
            "generation": claim.generation,
        }
        observation: dict[str, Any] = {
            "head_state": record.state,
            "result_sha256": record.result.result_sha256 if record.result else "",
            "verifier_version": self._descriptor.verifier_version,  # type: ignore[attr-defined]
        }
        # 2. Bound write_evidence.v2 for this effect (authoritative store).
        try:
            evidence_rows = self._store.list_write_evidence_for_effect(  # type: ignore[attr-defined]
                effect_id
            )
        except Exception:
            return "ERROR", ("authority:evidence_store_failure",), observation, lineage, None
        evidence = evidence_rows[-1] if evidence_rows else None
        if evidence is not None:
            observation["write_evidence_sha256"] = evidence["evidence_sha256"]
            observation["write_evidence_strength"] = evidence["provenance"][
                "strength"
            ]

        kind = predicate.predicate_type
        if kind == "effect.terminal_succeeded":
            if record.state == "SUCCEEDED":
                return "PASS", (), observation, lineage, (
                    evidence["evidence_sha256"] if evidence else None
                )
            if record.state in _PROVEN_NOT_SUCCEEDED:
                return "FAIL", ("effect.terminal_state_not_succeeded",), observation, lineage, (
                    evidence["evidence_sha256"] if evidence else None
                )
            # CLAIMED / SIDE_EFFECT_STARTED / AMBIGUOUS / RECONCILED prove
            # nothing either way.
            return "INCONCLUSIVE", ("effect.terminal_state_unproven",), observation, lineage, (
                evidence["evidence_sha256"] if evidence else None
            )
        if kind == "effect.target_exists":
            return self._target_predicate(
                predicate, evidence, observation, lineage, mode="exists"
            )
        if kind == "effect.target_sha256_matches":
            return self._target_predicate(
                predicate, evidence, observation, lineage, mode="sha256"
            )
        if kind == "effect.required_change_observed":
            return self._required_change(predicate, evidence, observation, lineage)
        return "INCONCLUSIVE", ("predicate_not_implemented",), observation, lineage, None

    def _target_predicate(self, predicate, evidence, observation, lineage, *, mode):
        params = predicate.param_mapping()
        target_path = str(params["target_path"])
        if evidence is None:
            return (
                "INCONCLUSIVE",
                ("effect.write_evidence_missing",),
                observation,
                lineage,
                None,
            )
        post_rows = evidence["verified_final_state"]["post_rows"]
        row = next(
            (r for r in post_rows if str(r.get("path")) == target_path), None
        )
        if row is None:
            # No verified final state for this target — broker-only
            # evidence cannot prove presence or absence.
            return (
                "INCONCLUSIVE",
                ("effect.target_state_unverified",),
                observation,
                lineage,
                evidence["evidence_sha256"],
            )
        if mode == "exists":
            deleted = target_path in evidence["observed_mutation"]["deleted_paths"]
            if bool(row.get("exists")) and not deleted:
                return "PASS", (), observation, lineage, evidence["evidence_sha256"]
            return (
                "FAIL",
                ("effect.target_missing",),
                observation,
                lineage,
                evidence["evidence_sha256"],
            )
        expected = str(params["sha256"])
        actual = str(row.get("sha256") or "")
        if not actual:
            return (
                "INCONCLUSIVE",
                ("effect.target_hash_unverified",),
                observation,
                lineage,
                evidence["evidence_sha256"],
            )
        if actual == expected:
            return "PASS", (), observation, lineage, evidence["evidence_sha256"]
        return (
            "FAIL",
            ("effect.target_sha256_mismatch",),
            observation,
            lineage,
            evidence["evidence_sha256"],
        )

    def _required_change(self, predicate, evidence, observation, lineage):
        params = predicate.param_mapping()
        required = tuple(params["target_paths"])
        if evidence is None:
            return (
                "INCONCLUSIVE",
                ("effect.write_evidence_missing",),
                observation,
                lineage,
                None,
            )
        mutation = evidence["observed_mutation"]
        changed = set(mutation["changed_paths"])
        deleted = set(mutation["deleted_paths"])
        missing = [p for p in required if p not in changed]
        if not missing:
            return "PASS", (), observation, lineage, evidence["evidence_sha256"]
        proven_absent = [p for p in missing if p in deleted]
        if len(proven_absent) == len(missing):
            # every required change is provably absent (targets deleted)
            return (
                "FAIL",
                ("effect.required_change_not_observed",),
                observation,
                lineage,
                evidence["evidence_sha256"],
            )
        return (
            "INCONCLUSIVE",
            ("effect.required_change_unverified",),
            observation,
            lineage,
            evidence["evidence_sha256"],
        )


__all__ = ["EffectStateOracle", "OracleSnapshotInvalid"]
