# PHASE 05 REPORT — COMMON KERNEL / Γ EPISTEMIC PLANE / Λ RHYTHM PLANE

## 1. Status

P5 implements the frozen Common Kernel foundation used by later World Understanding layers:

- Γ Epistemic Integrity Plane V1 answers whether a record is eligible to be trusted/promoted.
- Λ Rhythm & Resource Plane V1 answers whether work should be admitted now under queue and resource pressure.

P5 does not implement L0-L3 Software World, L4 semantic hypothesis generation, WorldState materialization, context projection, Self-Will integration, Runtime execution, Tool execution, or an independent worker/daemon.

## 2. SHA / branch

- Implementation branch: `agent/world-understanding-v0.1`
- P5 baseline / P4 report head: `ebb37beaa5711f2ac1e5bd00b63a9effd5680421`
- Main observed at P5 start: `da714694074acade7539a02de94e7c3265f788bd`
- Main rechecked after P5 implementation and remained: `da714694074acade7539a02de94e7c3265f788bd`
- P5 implementation SHA: `b7e06cdaf6505786084f399d0c625dd08f0f2911`
- P5 implementation tree: `c78732c7f84b5a1aecdd6222d6403063d39ebc75`
- Rollback point: `ebb37beaa5711f2ac1e5bd00b63a9effd5680421`
- Branch update: fast-forward only; `force=false`

## 3. Frozen design mapping

The roadmap requires P5 to add shared deterministic primitives for:

Γ V1:
- scope;
- time;
- provenance;
- authority domain;
- observability;
- WorldCut;
- evidence independence;
- invalidation;
- self-proof exclusion.

Λ V1:
- event coalescing;
- queue class;
- budget snapshot;
- priority;
- debounce;
- backpressure;
- semantic admission hooks;
- revalidation admission hooks;
- telemetry for arrival rate, service rate, queue age, transform latency, token cost and IO cost.

P5 uses configurable conservative parameters only. No learned/adaptive model parameters are introduced. The only adaptive formula implemented in V1 is the deterministic debounce lower-bound calculation from the frozen λ/μ/ρ equation.

## 4. Changed files

Added common kernel:

- `src/world_understanding/common/__init__.py`
- `src/world_understanding/common/identity.py`
- `src/world_understanding/common/scope.py`
- `src/world_understanding/common/time.py`
- `src/world_understanding/common/provenance.py`
- `src/world_understanding/common/event.py`
- `src/world_understanding/common/observability.py`
- `src/world_understanding/common/world_cut.py`
- `src/world_understanding/common/transaction.py`
- `src/world_understanding/common/epistemic.py`
- `src/world_understanding/common/rhythm.py`
- `src/world_understanding/common/budgets.py`

P4 reuse points modified:

- `src/world_understanding/known/authority_matrix.py`
- `src/world_understanding/known/closure.py`

Tests / plan:

- `tests/test_world_understanding_p5_common_kernel.py`
- `docs/world_understanding/PHASE_05_COMMON_KERNEL_PLAN.md`

The P5 implementation commit contains no Runtime, Total Gateway, Facade, Ingress, Tool, LLM, `zongdiaodu.py`, `duihua_qiaojie.py`, FactKernel, ToolResult or native producer modification.

## 5. Common identity and scope

`ScopeIdentity` derives one exact shared identity from the existing P1 `WorldScope` fields:

- `life_id`
- `world_id`
- `world_scope_hash`
- `principal_scope_hash`

`require_exact_scope` fails closed when any of these boundaries differ. It never chooses one side or guesses a life/world/principal identity.

Scope containment is conservative and cannot cross life, world, principal or domain boundaries.

## 6. Common time

`intersect_world_times` centralizes deterministic valid/observed/recorded-time intersection. Empty valid-time intersection fails with `TIME_INTERSECTION_EMPTY`.

Freshness is epistemic only. `epistemic_freshness` may return `STALE`, but it never rewrites a truth value. P5 therefore preserves the frozen separation:

`truth_state != epistemic_state`.

## 7. Provenance and evidence independence

`merge_provenance` is now the common deterministic provenance union used by P4 authority materialization.

A record with positive empirical evidence weight and no provenance is not eligible for stable promotion (`PROVENANCE_BROKEN`).

Evidence independence V1 groups evidence by native source family `(source_kind, object_id)`. Multiple revisions/hashes from the same native source therefore do not become multiple independent witnesses merely because their bytes changed.

This is intentionally conservative. Richer source-correlation modelling can be added later without weakening V1.

## 8. Observability and negative evidence

Open-world behavior is explicit:

- `UNKNOWN` is not converted to `FALSE`.
- `NOT_OBSERVED` is not negative evidence.
- negative evidence requires explicit OBSERVED/PARTIAL coverage.

V1 recognizes explicit negative propositions including `FILE_EXISTS(...)=false` and proposition families prefixed `NOT_`, `NO_`, `MISSING_`, or `ABSENT_`.

If observation coverage is below the configured floor, stable promotion is blocked with `NEGATIVE_EVIDENCE_REQUIRES_COVERAGE`.

## 9. WorldCut consistency

P5 implements deterministic WorldCut comparison using existing P1 `WorldCut` / `SourceWatermark` contracts.

Relations:

- `SAME`
- `LEFT_DOMINATES`
- `RIGHT_DOMINATES`
- `DISJOINT`
- `INCOMPATIBLE`

Hard rejection occurs when:

- scopes differ;
- the same watermark key has the same sequence but a different value;
- differing opaque watermark values have no comparable sequence;
- different source dimensions cross in time (for example Git is newer in cut A while Runtime is newer in cut B).

Monotonic dominance is allowed. Disjoint cuts are not automatically declared contradictory, but P5 does not authorize a future WorldState to mix arbitrary disjoint cuts; P9 WorldState materialization must provide its own required-source completeness policy on top of this primitive.

## 10. Γ Epistemic Plane V1

`EpistemicPlane.evaluate_known` returns a `GammaDecision` with separate:

- `admissible`
- `stable_promotion`
- `truth_state`
- `epistemic_state`
- effective coverage
- independent evidence count
- reason codes.

Stable promotion is blocked by, among other reasons:

- `SCOPE_MISMATCH`
- `WORLD_CUT_INCOMPATIBLE`
- `PROVENANCE_BROKEN`
- `SELF_PROOF_EMPIRICAL_FORBIDDEN`
- `NEGATIVE_EVIDENCE_REQUIRES_COVERAGE`
- `OPEN_WORLD_UNKNOWN`
- `EPISTEMIC_STALE`
- challenged/reverifying/retired epistemic states.

### Self-proof exclusion

Sources that are statements, memory, model proposals, autonomy decisions or external claims may remain records in the world model, but V1 forbids them from acquiring non-zero empirical reality evidence merely by being processed by World Understanding.

The forbidden empirical source set includes:

- MODEL_OUTPUT
- AUTONOMY
- CONTEXT_CONTINUITY
- MEMORY
- KNOWLEDGE
- WEB_EXTERNAL

The common boundary also defines non-evidence object classes such as Prediction, Hypothesis, Curiosity, Inquiry, WorldQuery and WorldContextPacket. This does not delete those objects; it prevents them from self-proving reality.

## 11. Invalidation

`propagate_invalidation` computes dirty descendants incrementally from changed upstream hashes.

It explicitly returns:

- `truth_mutated = false`
- `epistemic_state_mutated = false`

Therefore:

`dirty != false`

and:

`dirty != automatically stale`.

Later layers must rerun their own evidence equation before changing cognition/state status.

## 12. P4 integration

P5 does not create a second Known engine.

P4 `authority_matrix.py` now reuses the common:

- time intersection;
- provenance merge;
- observability intersection.

P4 `KnownClosureEngine` receives an additive optional `epistemic_plane` dependency and defaults to P5 `EpistemicPlane()`.

Before deterministic child materialization, every parent must be Γ-stable in the exact life/world/principal scope. A rejected parent creates a closure diagnostic and does not produce a Derived Known child.

Authority-domain intersection/ceiling logic remains owned by the P4 Known layer; P5 did not replace the existing P1/P4 contracts.

This is an intentional behavior tightening: a stale/unknown/negative-without-coverage parent can still exist in Known, but is no longer eligible to silently produce a stable deterministic child.

## 13. Λ Rhythm & Resource Plane V1

P5 adds a synchronous bounded rhythm plane. It starts no worker, thread, daemon or scheduler.

Queue classes:

- INTERACTIVE
- FAST
- SEMANTIC
- REVALIDATION
- BACKGROUND

Every queued event carries a hard boundary containing:

- life_id
- world_scope_hash
- principal_scope_hash
- queue_class
- optional WorldCut ID
- optional transaction reference.

The event coalescing identity is `(hard_boundary_hash, coalesce_key)`. Therefore same-looking events cannot debounce/coalesce across life, world, principal, queue, cut or transaction boundaries.

## 14. Priority, debounce and admission

Queues are finite and deterministically ordered by:

1. higher priority first;
2. earlier arrival;
3. stable event ID.

Semantic and revalidation work use configurable conservative priority floors in V1. No model learns these thresholds.

The adaptive debounce helper implements the frozen inequality:

`Δ >= max(0, p/(rho_target * mu) - 1/lambda)`

using exact rational arithmetic and returns milliseconds.

## 15. Budget and interactive reserve

`BudgetLedger` tracks:

- token budget;
- compute milliseconds;
- IO bytes;
- latency milliseconds.

Each also supports a configured interactive reserve.

Background/FAST/SEMANTIC/REVALIDATION work is admitted only from the non-reserved portion. INTERACTIVE work may consume the reserve.

Admission also subtracts resource commitments of work already queued but not yet serviced. This closes a common queue bug where background tasks could collectively overbook the interactive reserve merely because the ledger was charged only at service time.

When capacity or budget is unavailable, admission returns backpressure instead of silently dropping the reserve invariant.

## 16. Telemetry

Per queue, V1 records:

- arrival_count
- service_count
- arrival_rate_milli_per_sec
- service_rate_milli_per_sec
- rho_milli when service rate is available
- oldest_queue_age_ms
- transform_latency_total_ms
- token_cost_total
- io_cost_total
- queue_depth.

These are measurements only. P5 does not introduce learned control parameters from telemetry.

## 17. Scoped transaction primitive

P5 adds an in-memory `ScopedTransaction` for deterministic common-kernel batches.

It enforces exact scope and compatible WorldCuts before commit and supports explicit rollback. It is not a database transaction manager and creates no storage directory.

## 18. Real tests executed

Execution environment: reconstructed current World Understanding harness under `/mnt/data/wu_p3_exact_core`. It contains the current P2/P3-core/P4 implementation semantics plus the exact P5 code committed to GitHub. It is not a complete authenticated checkout of the authoritative repository.

### 18.1 Common + Known compileall

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src/world_understanding/common /mnt/data/wu_p3_exact_core/src/world_understanding/known
```

Result: PASS, exit code 0.

### 18.2 P5 focused tests

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py
```

Result:

```text
23 passed in 0.09s
```

### 18.3 P4 + P5 regression

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py
```

Result:

```text
41 passed in 2.96s
```

### 18.4 Available P2 / P3-core / P4 / P5 regression

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py /mnt/data/wu_p3_exact_core/tests/test_p3_core_after_p4.py /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py
```

Result:

```text
92 passed in 3.82s
```

`test_p3_core_after_p4.py` is an execution-harness-only filtered P3 test used because the reduced harness does not contain all higher P1 contract exports. It is not committed to GitHub.

## 19. Harness limitations encountered

During the first P5 collection attempt, the reconstructed harness lacked the authoritative P1 `WorldCutId` export and the authoritative observability helper `compute_observability_quality_milli`. Those missing pieces were restored in the local harness from the authoritative contract source before executing P5 tests.

These were harness-completeness defects, not changes to the GitHub product contracts, and are not counted as P5 product failures.

## 20. P5 Gate results

PASS in the available harness:

- STALE remains epistemic and never becomes truth state;
- UNKNOWN remains distinct from FALSE;
- negative evidence requires coverage;
- scope mismatch stops stable promotion;
- broken provenance blocks empirical stable promotion;
- model/non-evidence self-proof is excluded;
- duplicated revisions of one native source are not counted as independent witnesses;
- incompatible/crossed WorldCuts are rejected;
- event coalescing never crosses tested hard boundaries;
- finite queue overload triggers backpressure;
- background work cannot exhaust interactive reserve;
- queued work cannot overcommit reserved budget before service;
- priority queue services higher priority first;
- semantic/revalidation admission floors operate conservatively;
- λ/μ/queue-age/latency/token/IO telemetry is recorded;
- invalidation marks dirty descendants without changing truth/staleness;
- P4 closure uses Γ and blocks stable derivation from a stale parent;
- P4 regression remains passing in the available harness.

## 21. Tests not executed / limitations

NOT RUN:

- full authoritative repository `pytest`;
- exact authenticated checkout full P0-P5 import/regression suite;
- full P1 high-level contract regression inside the reduced local harness;
- Windows runtime smoke;
- production Linux runtime smoke;
- native Runtime/Gateway integration;
- real-model E2E;
- load/stress against a production queue/service implementation, because Λ V1 is not attached to Runtime and intentionally creates no worker.

GitHub combined status for P5 implementation SHA returned no status checks. Therefore CI is not reported as PASS.

## 22. Contract compatibility impact

P5 modifies no P1 contract file.

Compatibility changes inside the engine:

- `KnownClosureEngine.__init__` gains an optional `epistemic_plane`; existing calls remain valid.
- default deterministic promotion becomes stricter because Γ rejects epistemically unstable parents.
- P4 time/provenance/observability calculations are centralized in common primitives but retain conservative semantics.

No public physical ingress/output count changes.

## 23. Runtime / Gateway / OFF behavior

P5 does not modify:

- WorldUnderstandingFacade;
- WorldUnderstandingIngress;
- Runtime;
- Total Gateway;
- Tool execution;
- FactKernel;
- ToolResult native producer;
- prompt/context assembly;
- Self-Will.

Therefore current OFF behavior remains unchanged:

- no world DB;
- no worker/thread;
- no LLM;
- no Tool;
- no prompt change;
- no execution-result change.

## 24. Deviations

No architectural deviation from the frozen P5 roadmap was introduced.

One deliberate conservative choice is that P5 WorldCut compatibility only proves contradiction/dominance for comparable overlapping watermarks; it does not pretend disjoint cuts are automatically one coherent WorldState. Required-source completeness remains a P9 WorldState policy.

Another deliberate conservative choice is evidence-independence grouping by `(source_kind, object_id)` rather than attempting learned correlation in V1.

## 25. Rollback

Rollback target:

`ebb37beaa5711f2ac1e5bd00b63a9effd5680421`

Rolling back to this commit removes only P5 while preserving P0-P4 and the latest-main retry/recovery reconciliation already present before P5.

## 26. Gate conclusion

P5 Common Kernel / Γ / Λ gate: PASS WITH FULL-REPOSITORY TEST-EXECUTION LIMITATION RECORDED.

This gate authorizes planning P6 L0-L3 Software World First. It does not mean L0-L8, World Graph, L5 Cognition, semantic inference, WorldState, WORLD_CONTEXT_SLOT or Self-Will integration already exists.
