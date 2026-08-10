# PHASE 09 REPORT — L6 WORLD STATE MATERIALIZER

## 1. Status

P9 implements the frozen L6 World State Materializer on the existing World Understanding branch.

The resulting path is:

`WorldCut + current P6 Graph heads + stable P7 Cognition + active P8 Hypothesis refs + uncertainty/conflict + dependency lineage -> coherent reference-only WorldState + DeltaManifest + bounded current/history store`

P9 does not create a second Runtime, Gateway, ingress, execution path, tool path, cognition engine, graph engine, or semantic engine. It materializes a coherent projection over already-existing lower-layer records.

## 2. Baseline / commits

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Implementation branch: `agent/world-understanding-v0.1`
- P9 rollback point / P8 final: `7f8d141c0912736a83030eca2fe8f3988ec1d728`
- Main rechecked immediately before P9 commit: `da714694074acade7539a02de94e7c3265f788bd`
- P9 implementation commit: `91c77a8428d6163362eaf0007b28b9e3dcce5996`
- P9 implementation tree: `4ce375559ea9685f51949ed6114989b5374eb098`
- Branch update: fast-forward only, `force=false`

P8-final -> P9-core compare is exactly one commit and nine added files. The implementation branch remains ahead of current main and behind by zero.

## 3. Frozen P9 gate implemented

The frozen P9 gate requires:

1. incompatible cuts do not merge;
2. current state is reconstructable;
3. current heads and history are separate;
4. snapshot size is controlled;
5. lower-layer raw data is not copied into WorldState;
6. changed-source invalidation is precise;
7. losing one evidence root does not mechanically stale all cognition;
8. remaining support is re-evaluated.

All eight are implemented in the P9 focused surface and covered by executed tests, subject to the test-environment limitations in section 11.

## 4. Existing modules reused without replacement

P9 reuses:

- `src/contracts/world_understanding/state.py`
  - the existing P1 `WorldState` contract;
  - wrapper empirical weight remains zero;
  - `may_authorize=false`, `may_execute=false`.
- `src/contracts/world_understanding/world_cut.py`
  - existing `WorldCut` and `SourceWatermark` contracts.
- `src/world_understanding/common/world_cut.py`
  - existing P5 cut compatibility mathematics.
- `src/world_understanding/software_world/*`
  - existing P6 frame / sparse graph / entity / relation heads.
- `src/world_understanding/cognition/l5.py`
  - existing stable L5 cognition view.
- `src/world_understanding/cognition/stability.py`
  - existing P7 `evaluate_evidence` and `highest_eligible_level` mathematics.
- `src/contracts/world_understanding/hypothesis.py`
  - existing P8 hypothesis contract; hypotheses remain non-evidence proposals.

P9 does not modify P7 cognition store/consolidator. Remaining-support evaluation is a read-only adapter over the existing P7 stability mathematics.

## 5. New files

Implementation:

- `src/world_understanding/world_state/__init__.py`
- `src/world_understanding/world_state/manifests.py`
- `src/world_understanding/world_state/invalidation.py`
- `src/world_understanding/world_state/support.py`
- `src/world_understanding/world_state/store.py`
- `src/world_understanding/world_state/materializer.py`

Tests:

- `tests/test_world_understanding_p9_world_state.py`
- `tests/test_world_understanding_p9_world_state_store.py`

Plan:

- `docs/world_understanding/PHASE_09_WORLD_STATE_PLAN.md`

The focused tests are split into two files to keep connector publication payloads bounded; the exact split files were rerun together before commit.

## 6. WorldState materialization semantics

`WorldStateMaterializer` requires exact scope consistency among frame, cut and graph.

It additionally enforces:

- the graph frame revision equals the requested materialization frame revision;
- if the frame is cut-bound, the frame cut and materialization cut must be `SAME`;
- for an existing current stream:
  - `INCOMPATIBLE` cut -> reject;
  - `RIGHT_DOMINATES` -> reject as regression;
  - `DISJOINT` -> reject because continuity is unproved;
  - `SAME` or forward-dominating cut -> allowed.

A frame identity partitions a current-state stream. Therefore separate branch/worktree frames do not accidentally share sequence/history heads.

Genesis starts at `world_sequence=0`; each accepted update advances exactly one sequence. The delta references the previous state, preserving snapshot/delta lineage.

## 7. Reference-only manifests and bounded snapshots

P9 stores deterministic manifests containing `WorldRecordRef`s rather than lower-layer payloads:

- entity head manifest;
- relation head manifest;
- cognition head manifest;
- active hypothesis manifest;
- uncertainty manifest;
- dependency manifest;
- delta manifest.

Configurable caps exist for entity, relation, cognition, hypothesis, uncertainty, dependency, conflict and stale-ref counts, plus bounded history per frame stream.

The P9 store does not persist repository file contents, tool-result bodies, logs, documents, model prompts, graph semantic values, or duplicated Cognition bodies as a second database. Durable snapshots contain only the canonical WorldState/WorldCut objects and the compact reference/lineage manifests required to reconstruct the state projection.

## 8. Precise invalidation

A dependency binding records:

- the materialized record reference;
- exact source watermark keys;
- for cognition only, exact Cognition evidence IDs associated with the invalidated root.

When source watermarks change:

- only refs bound to changed source keys are candidates for invalidation;
- an entity/relation/cognition/hypothesis that arrives with a refreshed revision/hash is not marked stale merely because its prior head depended on the changed source;
- unrelated source changes do not trigger cognition re-evaluation;
- a removed watermark is itself treated as a changed source key.

`STALE` is represented only in the epistemic stale-ref surface. P9 never rewrites stale as logical FALSE.

## 9. One evidence root lost: remaining-support re-evaluation

P9 does not implement a second cognition scoring model.

`ExistingCognitionSupportEvaluator`:

1. loads the exact support/counter evidence IDs referenced by the current Cognition statement;
2. fails closed if requested evidence cannot be loaded exactly;
3. removes only the explicit evidence IDs bound to the changed source root;
4. calls the existing P7 `evaluate_evidence(...)`;
5. calls the existing P7 `highest_eligible_level(...)`;
6. compares the remaining eligibility with the existing statement stability level (`C4` uses the existing P7 `C3` evidence requirement).

If remaining support is sufficient, the cognition remains in the current manifest and is recorded in `revalidated_cognition_refs`. If insufficient, only that cognition is excluded and marked stale in the L6 state. P9 does not mutate the P7 cognition store.

A real bug was found during P9 testing: after a cognition successfully revalidated, the generic dependency invalidator still marked it stale. The fix explicitly exempts revalidated identities from generic stale propagation.

## 10. Optional durable state store

`WorldStateStore(root=None)` is memory-only and performs no filesystem I/O.

When an explicit persistence root is supplied:

- construction does not create directories;
- first successful publish creates a reference-only snapshot and stream index;
- snapshots are reconstructed using the strict contract JSON-validation path;
- manifest, delta, dependency and state/cut reference integrity is rechecked on load;
- current and bounded history are reconstructed from the index;
- tampered manifest hashes fail closed;
- durable publication writes snapshot first and index second;
- live in-memory current/history advance only after both atomic replacements succeed;
- if index publication fails, the new snapshot is removed best-effort and the live head remains unchanged.

The store deliberately has no worker/daemon and is not attached to Runtime in P9.

## 11. Executed tests

### Compile

Executed:

`python -m compileall -q .../src/world_understanding/world_state`

Result: PASS.

### P9 focused final

Executed against the local reconstructed World Understanding harness:

- `tests/test_world_understanding_p9_world_state.py`
- `tests/test_world_understanding_p9_world_state_store.py`

Final result:

`29 passed in 0.20s`

Coverage includes cut incompatibility/regression/disjoint rejection, exact scope/frame checks, bounded manifests/history, reference-only materialization, precise invalidation, refreshed-head exemption, evidence-root revalidation, insufficient-support stale behavior, uncertainty/conflict references, branch stream partition, persistence reconstruction, tamper detection, OFF-style memory-only I/O quietness, and index-write fault rollback.

### P2 -> P9 focused combination

Executed:

- P2 ingress focused tests;
- P3 source/life-isolation focused tests available in the harness;
- P4 Known closure;
- P5 Common Kernel;
- P6 software world;
- P7 cognition bridge;
- P8 semantic pipeline + guards;
- both P9 focused files.

Final result:

`183 passed in 4.31s`

### Earlier failures actually observed

P9 did not pass on first attempt.

Observed during development:

- initial focused run: `20 passed, 2 failed`;
  - one failure was a disjoint-cut test fixture that incorrectly assumed every cut contained a Git watermark;
  - one was the real cognition double-stale bug described above.
- after persistence was added: `26 passed, 2 failed`;
  - strict Pydantic contracts correctly rejected JSON arrays passed through Python-mode validation for tuple fields;
  - recovery was fixed to use `model_validate_json`, preserving strict contract policy rather than loosening contracts.

The final results above are after those fixes.

## 12. Test limitations / not claimed

The local execution environment is a reconstructed World Understanding focused harness, not a complete authenticated authoritative repository checkout.

Not run / not claimed:

- full authoritative repository `pytest`;
- complete P0-P9 regression from a fresh authoritative checkout;
- Windows runtime smoke;
- production Linux runtime smoke;
- production long-duration state-store stress;
- real crash/power-loss filesystem fault injection beyond the executed index-write exception test;
- Runtime/Gateway integration (not a P9 attachment point);
- real Tool or LLM E2E (P9 must not call either);
- GitHub Actions CI.

GitHub combined status for the P9 core commit returned no statuses; this is not reported as CI PASS.

## 13. P9 gate result

P9 gate result:

**PASS WITH FULL-REPOSITORY / PRODUCTION-PERSISTENCE TEST-EXECUTION LIMITATIONS RECORDED.**

Rollback point remains the P8 final commit:

`7f8d141c0912736a83030eca2fe8f3988ec1d728`

P10 has not started.
