# PHASE 09 — L6 WORLD STATE MATERIALIZER PLAN

## 1. Frozen goal

P9 materializes the current coherent `WorldState`; it does not copy the lower-layer world.

The frozen state surface is:
- current heads;
- selected current entities / relations;
- stable cognition;
- active semantic hypotheses as non-evidence references;
- current delta;
- uncertainty / conflict references;
- `WorldCut`;
- snapshot / delta lineage.

`WorldCut` remains the source-watermark boundary for Git, filesystem, runtime, memory, knowledge, conversation and other domains.

## 2. Existing files reused, not replaced

P9 reuses without changing their authority semantics:
- `src/contracts/world_understanding/state.py` — canonical P1 `WorldState` contract;
- `src/contracts/world_understanding/world_cut.py` — canonical P1 cut/watermark contracts;
- `src/world_understanding/common/world_cut.py` — P5 cut compatibility mathematics;
- `src/world_understanding/software_world/graph.py` and frame contracts — P6 L0-L3 structural heads;
- `src/world_understanding/cognition/l5.py` — P7 stable cognition view;
- `src/world_understanding/cognition/stability.py` — existing P7 evidence-support mathematics;
- `src/world_understanding/semantic/*` / `WorldHypothesis` — P8 hypothesis references only.

P9 does not modify Runtime, Total Gateway, WorldUnderstandingFacade, Tools, P4 Known closure, P6 graph semantics, P7 cognition store/consolidator, or P8 semantic model authority.

## 3. New files

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

## 4. Materialization rules

1. Exact life/world/principal/privacy scope is required.
2. The graph frame revision must equal the materialization frame revision.
3. A frame-bound cut must be `SAME` as the requested cut.
4. A current stream update rejects:
   - `INCOMPATIBLE` cut;
   - a regressing (`RIGHT_DOMINATES`) cut;
   - `DISJOINT` cut where continuity cannot be proved.
5. Branch/worktree frame identity partitions current-state streams.
6. Current heads are stored as deterministic `WorldRecordRef` manifests, not copied lower-layer payloads.
7. State history is immutable and bounded independently from the current-head pointer.
8. Optional durable persistence is reference-only. `root=None` performs no filesystem I/O; an explicit root is created only on first successful publish.
9. Persistence uses per-state snapshots plus a compact stream index and validates hashes/references again on reconstruction.

## 5. Precise invalidation and cognition support

P9 records a compact dependency manifest:
- materialized record ref;
- source watermark keys;
- for cognition only, exact supporting/counter evidence IDs whose source root is invalidated by that dependency.

When a source watermark changes:
1. only heads bound to that source key are candidates for invalidation;
2. a refreshed revision/hash is not marked stale merely because its predecessor depended on the changed source;
3. for an affected stable cognition, P9 removes only the exact invalidated evidence IDs and delegates remaining-support math to the existing P7 stability evaluator;
4. if the remaining support still meets the current stability level, cognition remains in the current state and is recorded as revalidated;
5. if support is insufficient, or exact support roots/evaluator are unavailable, only that cognition is excluded from the current cognition manifest and marked stale;
6. no P7 cognition record is rewritten by P9.

`STALE` remains epistemic state, never a truth value.

## 6. Snapshot bounds

P9 enforces configurable hard caps for:
- entity heads;
- relation heads;
- cognition heads;
- active hypotheses;
- uncertainty refs;
- dependency bindings;
- conflicts;
- stale refs;
- history per frame stream.

The state store does not persist full repository files, logs, tool outputs, documents, model prompts, graph values, or cognition payload bodies beyond the already-canonical contract/reference objects needed for reconstruction.

## 7. Authority boundary

`WorldState` remains:
- `empirical_evidence_weight_milli = 0` at the wrapper/materialization level;
- `may_authorize = false`;
- `may_execute = false`.

A WorldState is a coherent projection over referenced lower-layer records. It is not a new evidence producer and is not an execution entry.

## 8. Frozen P9 gate

P9 is complete only when all are demonstrated:
- incompatible cuts do not merge;
- current state is reconstructable;
- current heads are separated from history;
- snapshot size is controlled;
- lower-layer raw data is not duplicated;
- changed-source invalidation is precise;
- losing one evidence root does not mechanically stale all cognition;
- remaining support is re-evaluated.
