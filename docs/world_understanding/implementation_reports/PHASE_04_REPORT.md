# PHASE 04 REPORT — KNOWN MATHEMATICS / DETERMINISTIC CLOSURE

## 1. Status

P4 implements the frozen deterministic Known mathematics:

`K0_life -> deterministic F_det -> least fixed point K*_life`

The implementation is life/world/principal scoped from the first input record. It does not compute a global closure and filter by life afterward.

Full Γ and Λ are not claimed here; they remain P5. P4 uses only conservative hard-validity checks needed to make deterministic closure safe.

## 2. SHA / branch

- Implementation branch: `agent/world-understanding-v0.1`
- Main observed at P4 start: `da714694074acade7539a02de94e7c3265f788bd`
- P3 head before main sync: `58f85899c1075bdf9117056283524b7f39fa39ce`
- Pre-P4 latest-main sync merge: `0aaf83be9ddf31445e4d5295f739a42d82f56fda`
- P4 implementation SHA: `51c4ddb2bd537a2cf61714dbec4d71f035aa5881`
- P4 implementation tree: `415459e2592f3cc30db1d2719f0be698474a947e`
- Rollback point: `0aaf83be9ddf31445e4d5295f739a42d82f56fda`
- P4 branch update: fast-forward only; `force=false`
- Main rechecked after implementation and remained `da714694074acade7539a02de94e7c3265f788bd`.

## 3. Main sync performed before P4

The main branch advanced after P3 with `fix: reconcile timed-out actions before retry`. It modified native conversation/scheduler recovery behavior and its tests. P4 first synchronized those exact latest-main files into the implementation branch through a two-parent merge.

This sync is separate from the P4 code commit. The P4 implementation commit itself does not modify `app/backend/...`, Runtime, Total Gateway, `zongdiaodu.py`, `duihua_qiaojie.py`, FactKernel, ToolResult, or execution policy.

## 4. P4 changed files

Added by the P4 implementation commit only:

- `docs/world_understanding/PHASE_04_KNOWN_MATHEMATICS_PLAN.md`
- `src/world_understanding/known/__init__.py`
- `src/world_understanding/known/set.py`
- `src/world_understanding/known/rule.py`
- `src/world_understanding/known/registry.py`
- `src/world_understanding/known/authority_matrix.py`
- `src/world_understanding/known/closure.py`
- `src/world_understanding/known/rules/__init__.py`
- `tests/test_world_understanding_p4_known_closure.py`

No P1 contract file was changed in P4.

## 5. Implemented mathematics

### 5.1 Finite life-scoped active cut

`KnownSet` accepts only canonical `DirectKnownRecord` / `DerivedKnownRecord` records matching exactly the active `WorldScope`. Mixed life/world/principal scope fails closed through the P3 scope guard.

The active cut has a hard `max_records` bound and canonical content-hash dedup. Snapshot identity includes life ID, world scope hash and the sorted record hashes.

### 5.2 Least fixed point

`KnownClosureEngine` performs semi-naive rounds from a `delta` until no new Derived Known record is admitted. It has a hard `max_rounds` bound; exceeding it raises `ClosureLimitExceeded` instead of claiming convergence.

A transform exception emits a diagnostic and aborts that rule invocation only. Existing Direct Known and already admitted Known records remain intact.

### 5.3 Incremental recompute

A prior `ClosureResult` can be forked. `KnownSet.fork()` preserves current indexes and the closure result preserves ancestry signatures, allowing a one-record append to start from the prior active cut instead of rebuilding all prior Known records.

### 5.4 Central materialization boundary

Concrete deterministic rules only propose `DerivedCandidate`. The closure engine centrally materializes every accepted child and enforces:

- same WorldScope for all parents;
- TRUE parents for P4 deterministic derivation;
- canonical parent refs;
- authority-domain compatibility;
- authority ceiling non-increase;
- empirical evidence weight non-increase;
- provenance-root preservation;
- conservative time intersection;
- conservative observability intersection;
- canonical DerivedKnown hash;
- same-revision semantic cycle exclusion;
- life-scoped DerivationRef and DerivationEdge emission.

### 5.5 Rule/version identity

The existing P1 `DerivedKnownRecord` stable `known_id` remains a proposition slot: the same rule ID, parent set and proposition preserve the stable Known ID. Changing `transform_version` changes the derivation hash / record hash and changes the versioned `DerivationRef.derivation_id`. P4 did not rewrite P1 identity semantics.

## 6. First deterministic rules implemented

- file create/delete from explicit FILE_EXISTS pre/post observations;
- file content changed / verified unchanged from explicit FILE_HASH_AT observations;
- hash equality;
- same-subject event ordering;
- same Direct Known source-root grouping;
- explicit Git structural normalization for contains/import/direct-call facts;
- call-graph reachability with explicit path;
- explicit scope containment;
- explicit WorldFrame identity derivations.

The call rule specifically forbids manufacturing a direct call from transitivity:

`DIRECT_CALLS(A,B) + DIRECT_CALLS(B,C)` does not produce `DIRECT_CALLS(A,C)`.

It may produce `CALL_REACHABLE(A,C,path=A>B>C)`.

P4 performs no filename-based semantic role inference and does not produce L4 semantic hypotheses.

## 7. Authority / provenance behavior

P4's `authority_matrix.py` is deliberately conservative and is not the full Γ plane.

For a P4 derivation:

- all parent authority domains must have a non-empty compatible intersection; current P4 deterministic rules require one matching domain;
- a rule cannot switch the output to a different authority domain;
- child authority ceiling is the minimum parent ceiling;
- child empirical evidence weight is bounded by both the child ceiling and minimum parent empirical weight;
- provenance is the sorted union of all parent provenance refs;
- empty provenance roots stop derivation;
- valid-time intervals are intersected and an empty interval stops derivation;
- observability uses conservative component minima;
- epistemic state uses the most conservative parent state.

This implements P4 safety only. Full proposition/scope/time/observability Γ semantics remains P5.

## 8. Derivation DAG

Each admitted Derived Known record emits:

- one life-scoped `DerivationRef`;
- `SOURCE_TO_DERIVATION` edge(s) from every parent;
- one `DERIVATION_TO_TARGET` edge.

World Graph is not created or modified in P4. Derivation DAG and World Graph remain separate.

## 9. Real tests executed

Execution environment: local reconstructed World Understanding test harness under `/mnt/data/wu_p3_exact_core`, using the current authoritative `known.py` / P3 derivation contract semantics and the P4 implementation content committed to GitHub. This is not a complete authoritative repository checkout.

### 9.1 Compile P4 package

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src/world_understanding/known
```

Result: PASS, exit code 0.

### 9.2 P4 Gate tests

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py
```

Result:

```text
18 passed in 2.96s
```

### 9.3 P2 ingress + P4 compatibility

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py
```

Result:

```text
29 passed in 2.90s
```

### 9.4 P3 core Source/Life subset + P4

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p3_core_after_p4.py /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py
```

Result:

```text
58 passed in 3.70s
```

`test_p3_core_after_p4.py` is an execution-harness-only filtered copy of the P3 integrated tests, excluding high-level P1 contract classes absent from the reduced local harness. It was not committed to GitHub.

### 9.5 10k incremental benchmark

Final command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q -s /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py::test_10k_known_incremental_recompute_benchmark
```

Final result:

```text
P4_BENCH baseline_10k=0.600716s incremental_1=0.004798s
1 passed in 2.90s
```

The first implementation of this benchmark FAILED: the prior implementation rebuilt the full KnownSet/index/ancestry for an incremental append, and the observed incremental run (~0.652s) was slower than the 10k baseline build (~0.609s). The implementation was changed to `KnownSet.fork()` plus inherited ancestry/index state. The benchmark was then rerun and passed. The initial failing terminal output is not retained as a committed artifact; the failure and corrective change are recorded here rather than being hidden.

## 10. Gate coverage

PASS in the available P4 harness:

- closure termination / fixed point;
- canonical input-order stability;
- mixed-life input rejection;
- same-revision semantic cycle rejection;
- authority ceiling and empirical weight cannot increase;
- incompatible authority-domain widening is rejected;
- provenance roots preserved;
- rule exception does not erase Direct Known;
- finite active cut overflow fails closed;
- direct-call transitivity is not fabricated;
- explicit CALL_REACHABLE path is derived;
- rule version changes content/revision hash and Derivation identity;
- derivation DAG records/edges emitted in the same life/world scope;
- first deterministic rule families produce expected facts;
- 10k Known incremental recompute benchmark passes after optimization.

## 11. Tests not executed / limitations

NOT RUN:

- full authoritative-repository `pytest`;
- exact authoritative checkout full P1/P2/P3/P4 import/regression suite;
- Windows runtime smoke;
- production Linux runtime smoke;
- real Runtime/Gateway integration, because P4 intentionally is not attached to Runtime/Gateway/Facade;
- GitHub Actions for the P4 implementation SHA. Query returned zero workflow runs.

Attempting to collect the complete existing P3 integrated test in the reconstructed harness is blocked by that harness not containing all P1 high-level exports (for example `WorldContextPacket`). This is a harness completeness limitation; it is not reported as PASS and is not treated as a P4 assertion failure.

## 12. Contract compatibility impact

P4 adds an engine package only and does not modify P1 contracts. Existing `DirectKnownRecord`, `DerivedKnownRecord`, `WorldScope`, `DerivationRef` and `DerivationEdge` remain the source contracts.

Expected contract compatibility impact: none from P4 itself.

## 13. Runtime / Gateway / OFF behavior

The P4 implementation commit contains no Runtime, Gateway, Tool, LLM, network, Facade or native producer modifications.

P4 is not attached to `WorldUnderstandingFacade` yet. Therefore existing OFF behavior is unchanged:

- no new DB;
- no worker/thread;
- no LLM;
- no Tool;
- no prompt change;
- no execution-result change.

The latest-main Runtime/conversation recovery modifications visible on the implementation branch came from the explicit pre-P4 sync merge, not from P4.

## 14. Rollback

Rollback target:

`0aaf83be9ddf31445e4d5295f739a42d82f56fda`

Rolling back to this commit removes only P4 Known Mathematics while preserving P0-P3 and the latest-main retry/recovery reconciliation.

## 15. Gate conclusion

P4 deterministic Known Mathematics gate: PASS WITH FULL-REPOSITORY TEST-EXECUTION LIMITATION RECORDED.

This gate authorizes planning P5 Common Kernel / Γ / Λ, but does not imply that full Γ, semantic inference, WorldState, Context projection, or Self-Will integration already exists.
