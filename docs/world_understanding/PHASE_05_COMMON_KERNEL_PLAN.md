# WORLD UNDERSTANDING PHASE 05 — COMMON KERNEL / Γ / Λ PLAN

Status: implementation plan for `agent/world-understanding-v0.1`.

## Frozen basis

P5 implements the shared deterministic physical rules required by the roadmap:

- Γ Epistemic Plane V1: scope, time, provenance, authority-domain compatibility, observability, WorldCut, evidence independence, invalidation and self-proof exclusion.
- Λ Rhythm V1: event coalescing, queue classes, budget snapshots, priority, debounce, backpressure, semantic/revalidation admission hooks and telemetry.

P5 does not implement L0-L3 Software World, L4 semantics, WorldState materialization, context projection, Self-Will or execution integration.

## Files

Add `src/world_understanding/common/`: `__init__.py`, `identity.py`, `scope.py`, `time.py`, `provenance.py`, `event.py`, `observability.py`, `world_cut.py`, `transaction.py`, `epistemic.py`, `rhythm.py`, `budgets.py`.

Modify only inside World Understanding:
- `src/world_understanding/known/authority_matrix.py` to reuse common time/provenance/observability primitives.
- `src/world_understanding/known/closure.py` so P4 deterministic promotion is Γ-validated.

Add focused tests:
- `tests/test_world_understanding_p5_common_kernel.py`

## Γ invariants

1. Scope mismatch fails closed.
2. Truth and epistemic state remain separate: `STALE` never becomes a truth value.
3. Open world: `UNKNOWN != FALSE`.
4. Positive empirical weight requires intact provenance.
5. Negative evidence requires explicit observation coverage.
6. Model/Prediction/Hypothesis/Inquiry/Self-Will/Projection classes may not self-prove reality.
7. Evidence independence is counted by native source family, not duplicated revisions/hashes.
8. Invalidation marks descendants dirty; it does not mutate truth or force `STALE`.
9. WorldCut comparisons are scope-bound and reject contradictory/crossed source watermarks.
10. P4 Derived Known remains bounded by parent authority and now uses Γ before stable derivation.

## Λ invariants

1. No worker/thread/daemon is created in P5.
2. Event coalescing key includes a hard boundary containing life/world/principal/queue and optional WorldCut/transaction identity.
3. Coalescing never crosses a hard boundary.
4. Queues are finite and overload returns backpressure.
5. Queues are priority ordered deterministically.
6. Budget admission accounts for already queued resource commitments.
7. Background/semantic/revalidation work cannot spend the interactive reserve.
8. Interactive work may consume its reserve.
9. Semantic and revalidation admission use configurable conservative thresholds; no learned parameters in V1.
10. Adaptive debounce follows the frozen λ/μ/ρ formula using exact rational arithmetic.
11. Telemetry collects arrival rate, service rate, queue age, transform latency, token cost and IO cost.

## P5 Gate

- STALE not truth state.
- UNKNOWN not FALSE.
- negative evidence requires coverage.
- scope mismatch stops stable promotion.
- incompatible WorldCut rejected.
- event coalescing does not cross hard boundary.
- queue overload triggers backpressure.
- interactive reserve is not exhausted by background work.
- P4 deterministic closure remains compatible.
- Runtime/Gateway/Facade/native execution files remain untouched.
- OFF behavior remains unchanged.
