"""P19-R2 M6 Workflow A: Golden Trace harness.

Runs a canonical Verification-Plane scenario against the REAL Store,
Gate, executor, policy and repair loop, then collects every authority
object into a machine-readable trace.

Normalization policy (M6 §5):
- wall clock / volatile ids never enter the comparison;
- BUT hashes and identities that belong to the trust boundary are not
  dropped — they are INTERNED (same value → same placeholder) so the
  comparator still verifies authority topology, subject lineage and
  predicate identity linkage;
- semantic fields (status, action, outcome, predicate type, reason
  codes...) are compared verbatim.

Baseline discipline (M6 §6): the comparator is compare-only in CI;
baselines are (re)written ONLY when the UPDATE_GOLDEN=1 environment
variable is set explicitly.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

BASELINES = Path(__file__).resolve().parent / "baselines"

_ID_RE = re.compile(
    r"^(req|run|vpl|vpe|vpd|vrs|vfe|vds|vrd|vra|vss|arv|eff|web|rob|"
    r"fact|lev|inbound|intent|out|lease)_[0-9a-f]{16,}$"
)
#: snake-case authority prefix + a long hex tail — covers composite
#: ids like fact_qc_<sha>, oref_<sha>, execution-ticket-<sha>-derived
#: strings, without touching semantic tokens (reason codes, predicate
#: types) which never end in 12+ hex chars.
_VOLATILE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12,}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{32}$")  # uuid4().hex dispatch claims
_TS_KEYS = (
    "_at_ms",
    "evaluated_at_ms",
    "observed_at_ms",
)


class _Normalizer:
    def __init__(self) -> None:
        self._interned: dict[str, str] = {}

    def intern(self, value: str) -> str:
        if value not in self._interned:
            self._interned[value] = f"ref{len(self._interned)}"
        return self._interned[value]

    def _part(self, part: str) -> str:
        if (
            _SHA_RE.match(part)
            or _UUID_RE.match(part)
            or _ID_RE.match(part)
            or _VOLATILE_RE.match(part)
        ):
            return self.intern(part)
        return part

    def normalize(self, value, key: str = ""):
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            # timestamps (and only timestamps) collapse
            if key.endswith(_TS_KEYS) or key.endswith("_at_ms"):
                return "<ts>"
            return value
        if isinstance(value, str):
            if key.endswith("_at_ms") or key in (
                "evaluated_at_ms", "observed_at_ms",
            ):
                return "<ts>"
            if ":" in value:
                # composite evidence refs ("artifact_revision:arv_<sha>")
                # intern each volatile segment, keep the semantic prefix
                return ":".join(self._part(p) for p in value.split(":"))
            return self._part(value)
        if isinstance(value, dict):
            return {
                str(k): self.normalize(v, key=str(k))
                for k, v in sorted(value.items())
            }
        if isinstance(value, (list, tuple)):
            return [self.normalize(item, key=key) for item in value]
        return value


def canonical_json(obj) -> str:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, indent=1,
        separators=(",", ": "),
    )


def normalize_trace(trace: dict) -> dict:
    normalized = _Normalizer().normalize(trace)
    # The completion decision's per-artifact state list carries a
    # RUN-DEPENDENT ordering (artifact iteration order); its topology
    # is already fully locked by records/successors elsewhere in the
    # trace, so the revision reference is folded away and only the
    # multiset of QC states is compared — order-independently.
    decision = normalized.get("completion_decision")
    if isinstance(decision, dict) and isinstance(
        decision.get("artifact_revision_states"), list
    ):
        decision["artifact_revision_states"] = sorted(
            _REF_TOKEN.sub("?", canonical_json(entry))
            for entry in decision["artifact_revision_states"]
        )
    # Non-temporal object arrays are canonically SORTED after
    # normalization — eliminating run-to-run ordering noise from
    # wall-clock ORDER BY keys. authority_events keeps its order: it
    # IS the temporal authority-transition sequence.
    for key in (
        "verification_records",
        "failure_evidence",
        "dispositions",
        "repair_directives",
        "repair_bindings",
        "repair_attempts",
        "subject_successors",
    ):
        if key in normalized and isinstance(normalized[key], list):
            normalized[key] = sorted(
                normalized[key],
                # sort by the STRUCTURE with placeholders erased —
                # sorting on refN tokens would couple the order to the
                # arbitrary interning order (self-referential noise)
                key=lambda el: _REF_TOKEN.sub(
                    "?", canonical_json(el)
                ),
            )
    return normalized


def collect_trace(
    fixture_store,
    *,
    plan,
    request_id: str,
    run_id: str,
    generation: int,
    golden_case_id: str,
    input_class: str,
    completion_decision=None,
    runtime_execution_count: int | None = None,
) -> dict:
    """Collect every Verification-Plane authority object from the REAL
    Store (never from caller-supplied state)."""
    records = [
        r.model_dump(mode="json")
        for r in fixture_store.list_verification_records(
            request_id=request_id, run_id=run_id, generation=generation,
        )
    ]
    readiness_obj = fixture_store.get_latest_verification_readiness(
        request_id=request_id, run_id=run_id, generation=generation,
    )
    evidence = []
    dispositions = []
    directives = []
    bindings = []
    attempts = []
    successors = []
    for entry in plan.entries:
        evidence.extend(
            fe.model_dump(mode="json")
            for fe in fixture_store.list_verification_failure_evidence(
                entry.plan_entry_id
            )
        )
        dispositions.extend(
            d.model_dump(mode="json")
            for d in fixture_store.list_verification_dispositions(
                entry.plan_entry_id
            )
        )
        directives.extend(
            d.model_dump(mode="json")
            for d in fixture_store.list_repair_directives(
                entry.plan_entry_id
            )
        )
        attempts.extend(
            a.model_dump(mode="json")
            for a in fixture_store.list_repair_attempts(entry.plan_entry_id)
        )
        successors.extend(
            s.model_dump(mode="json")
            for s in fixture_store.list_verification_subject_successors(
                entry.plan_entry_id
            )
        )
        resolution = fixture_store.resolve_verification_subject(
            entry.plan_entry_id
        )
        successors.append({"__resolution__": resolution})
        for attempt_no in range(1, 8):
            binding = (
                fixture_store.get_repair_execution_binding_by_attempt(
                    entry.plan_entry_id, attempt_no
                )
            )
            if binding is not None:
                bindings.append(binding)
    trace = {
        "golden_case_id": golden_case_id,
        "input_class": input_class,
        "authority_events": _authority_events(
            records=records,
            dispositions=dispositions,
            bindings=bindings,
            attempts=attempts,
            successors=successors,
            completion_decision=completion_decision,
        ),
        "verification_records": records,
        "readiness": (
            readiness_obj.model_dump(mode="json")
            if readiness_obj is not None else None
        ),
        "failure_evidence": evidence,
        "dispositions": dispositions,
        "repair_directives": directives,
        "repair_bindings": bindings,
        "repair_attempts": attempts,
        "subject_successors": successors,
        "completion_decision": (
            completion_decision.model_dump(mode="json")
            if completion_decision is not None else None
        ),
    }
    if runtime_execution_count is not None:
        trace["runtime_execution_count"] = runtime_execution_count
    return trace


def _authority_events(
    *, records, dispositions, bindings, attempts, successors,
    completion_decision,
) -> list[dict]:
    """The canonical authority-transition sequence — the semantic spine
    the golden comparator actually locks."""
    events: list[dict] = []
    for record in records:
        events.append(
            {
                "kind": "verification_record",
                "predicate_type": record["predicate_type"],
                "subject_kind": record["subject_kind"],
                "status": record["status"],
                "enforcement": record["enforcement"],
            }
        )
    for disposition in dispositions:
        events.append(
            {
                "kind": "disposition",
                "action": disposition["action"],
                "attempt_no": disposition["attempt_no"],
                "reason_codes": disposition["reason_codes"],
            }
        )
    for binding in bindings:
        events.append(
            {
                "kind": "repair_binding",
                "state": binding["state"],
                "claim_revision": binding.get("claim_revision"),
            }
        )
    for attempt in attempts:
        events.append(
            {
                "kind": "repair_attempt",
                "execution_outcome": attempt["execution_outcome"],
                "repair_attempt_no": attempt["repair_attempt_no"],
            }
        )
    for successor in successors:
        if "__resolution__" not in successor:
            events.append(
                {
                    "kind": "subject_successor",
                    "repair_attempt_no": successor["repair_attempt_no"],
                }
            )
        else:
            events.append(
                {
                    "kind": "successor_resolution",
                    "successor_depth": successor["__resolution__"][
                        "successor_depth"
                    ],
                }
            )
    if completion_decision is not None:
        events.append(
            {
                "kind": "completion_decision",
                "outcome": completion_decision.outcome,
                "verification_mode": completion_decision.verification_mode,
                "verification_ready": completion_decision.verification_ready,
            }
        )
    return events


def compare_or_update(trace: dict) -> None:
    """Compare the normalized trace against its baseline.

    Compare-only unless UPDATE_GOLDEN=1 — a failing test can never
    silently rewrite the baseline (M6 §6).
    """
    case_id = trace["golden_case_id"]
    normalized = normalize_trace(trace)
    baseline_path = BASELINES / f"{case_id}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        BASELINES.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(
            (canonical_json(normalized) + "\n").encode("utf-8")
        )
        return
    if not baseline_path.exists():
        raise AssertionError(
            f"golden baseline missing for {case_id} — generate it"
            " explicitly with UPDATE_GOLDEN=1"
        )
    expected = json.loads(baseline_path.read_text(encoding="utf-8"))
    if _stable_text(expected) != _stable_text(normalized):
        raise AssertionError(
            f"golden trace mismatch for {case_id} (compare-only;"
            " update baselines explicitly with UPDATE_GOLDEN=1)"
        )


_REF_TOKEN = re.compile(r"\bref[0-9]+\b")


def _stable_text(obj) -> str:
    """Canonical text with volatile placeholders renamed by FIRST
    OCCURRENCE IN THE TEXT (not traversal order) — isomorphic traces
    normalize to byte-identical text regardless of which random ids
    happened to be interned first."""
    mapping: dict[str, str] = {}

    def rename(match: "re.Match[str]") -> str:
        token = match.group(0)
        if token not in mapping:
            mapping[token] = f"ref{len(mapping)}"
        return mapping[token]

    return _REF_TOKEN.sub(rename, canonical_json(obj))
