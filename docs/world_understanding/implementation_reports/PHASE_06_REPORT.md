# PHASE 06 REPORT — L0-L3 SOFTWARE WORLD FIRST

## 1. Status

P6 implements the first concrete World Understanding domain over Tiangong V3's own software world:

`SoftwareWorldFrame -> typed Perception -> WorldEntity -> WorldRelation -> Sparse World Graph`

P6 consumes already-observed/compiled Known records and explicit Git commit/diff deltas. It does not scan the repository, call Tools, call an LLM, create a worker/daemon, materialize L4 semantic hypotheses, create WorldState, inject context, or integrate Self-Will.

## 2. SHA / branch

- Implementation branch: `agent/world-understanding-v0.1`
- P6 baseline / P5 final report head: `6e18d292d76bda515bf83fd7bf7204b238b5d5ba`
- Main observed at P6 start: `da714694074acade7539a02de94e7c3265f788bd`
- Main rechecked before P6 commit and remained: `da714694074acade7539a02de94e7c3265f788bd`
- P6 implementation SHA: `0757f5c0c7256284b77894bd8e2534c9b9455b83`
- P6 implementation tree: `2366e73d1821a32558ae40ae25d2ce0cdf85188f`
- Rollback point: `6e18d292d76bda515bf83fd7bf7204b238b5d5ba`
- Branch update: fast-forward only; `force=false`

## 3. Changed files

Added by the P6 implementation commit only:

- `docs/world_understanding/PHASE_06_SOFTWARE_WORLD_PLAN.md`
- `src/world_understanding/software_world/__init__.py`
- `src/world_understanding/software_world/frame.py`
- `src/world_understanding/software_world/perception.py`
- `src/world_understanding/software_world/git_delta.py`
- `src/world_understanding/software_world/entity.py`
- `src/world_understanding/software_world/graph.py`
- `src/world_understanding/software_world/relation.py`
- `src/world_understanding/software_world/updater.py`
- `tests/test_world_understanding_p6_software_world.py`

No P1 contract file was modified. Existing `WorldEntity`, `EntityResolutionCandidate` and `WorldRelation` contracts are reused.

No P2/P3/P4/P5 implementation file was modified.
No Runtime, Total Gateway, Facade, Ingress, Tool, LLM, `zongdiaodu.py`, `duihua_qiaojie.py`, FactKernel, ToolResult or native producer file was modified.

## 4. L0 — SoftwareWorldFrame

`SoftwareWorldFrame` explicitly binds:

- life / principal / world through `WorldScope`;
- workspace;
- repository;
- worktree;
- branch;
- commit;
- environment;
- time;
- optional WorldCut.

`frame_id` is stable for one life/world/principal/workspace/repository/worktree/branch identity.
`frame_revision_hash` changes with commit/environment/time/cut.

Therefore a normal commit advance on the same branch revises one frame, while a different branch is a different frame and cannot reuse the same mutable graph accidentally.

## 5. L1 — Typed Perception

P6 wraps existing `DirectKnownRecord` / `DerivedKnownRecord` as `SoftwarePerception` without changing their truth or authority.

Perception classes:

- `IDENTITY`
- `STRUCTURE`
- `EVENT`
- `OBSERVATION`

Every Known record must match the frame's exact WorldScope before it can be perceived.

P6 uses the P5 Γ plane before graph promotion. A record that is not Γ-admissible/stable is not materialized into the graph. In addition, identity/structure graph facts require `truth_state == TRUE`; FALSE/UNKNOWN claims are not graph facts.

## 6. L2 — Entity

Frozen first entity vocabulary is implemented:

- Repository
- Worktree
- File
- Module
- Class
- Function
- Method
- Tool
- Runtime
- Gateway
- Grant
- ExecutionTicket
- KnowledgeDocument
- MemoryStore

P6 reuses the P1 `WorldEntity` stable-ID and revision-lineage contract.

### Identity behavior

Explicit identity Known records use a deterministic stable source anchor.

Git file identity uses explicit commit/diff lineage:

- RENAME/MOVE preserves the existing File entity ID when the old path resolves uniquely;
- the new revision supersedes the previous entity hash and preserves the old path as an alias;
- DELETE retires the existing entity;
- a later unrelated ADD is a new entity by default, not silently interpreted as the deleted entity returning;
- an explicit future identity anchor may deliberately link an ADD;
- ambiguous path identity does not strong-merge candidates; when a source basis exists, an `EntityResolutionCandidate(state=AMBIGUOUS)` is emitted;
- repeated identical identity observations are idempotent and do not manufacture a new revision.

## 7. L3 — Deterministic relations

P6 materializes only the frozen L3 deterministic relation vocabulary:

- CONTAINS
- DEFINES
- IMPORTS
- DIRECT_CALLS
- CALL_REACHABLE
- USES
- READS
- WRITES
- REGISTERED_AS
- BELONGS_TO
- LOCATED_IN

Materialization classes:

- STRUCTURAL: CONTAINS, DEFINES, REGISTERED_AS, BELONGS_TO, LOCATED_IN
- MATERIALIZED: IMPORTS, DIRECT_CALLS, USES, READS, WRITES
- DERIVED_CACHE: CALL_REACHABLE

`CALL_REACHABLE` may therefore consume the deterministic P4 closure output and become a P6 derived-cache graph relation, while `DIRECT_CALLS` remains a distinct direct structural fact.

The following are explicitly not deterministic L3 facts and are deferred to L4/L5:

- GUARDED_BY
- AUTHORITATIVE_FOR
- IS_BOUNDARY_OF

P6 does not infer those relations from filenames, module names, comments, or model guesses.

## 8. Sparse World Graph

`SparseWorldGraph` stores only latest materialized `WorldEntity` and `WorldRelation` records.

Derivation DAG records do not enter the graph.

Indexes are maintained incrementally for:

- entity ID;
- canonical name / alias;
- active file path;
- relation endpoints;
- applied Git delta IDs.

P6 does not copy/rebuild the whole graph for one changed file.
The update journal records only touched entities/relations and the previous frame revision so a failed update can roll back touched graph state.

## 9. Explicit Git delta contract

`GitCommitDelta` accepts already-observed changes only:

- ADD
- MODIFY
- DELETE
- RENAME
- MOVE

It performs no repository access.
The delta must match the current frame's repository/worktree/branch/commit.
Only listed changed paths are processed.

A previously applied `delta_id` is not applied again; replay is idempotent and emits `GIT_DELTA_ALREADY_APPLIED`.

Relations touching a changed/retired entity are invalidated locally. Unrelated subgraphs remain intact.

## 10. No independent scanner or execution authority

Static package checks and code inspection confirm the P6 package introduces no:

- `subprocess`
- `requests`
- OpenAI/Anthropic client
- `total_gateway`
- `zongdiaodu`
- `os.walk`
- `Path(...).glob(...)` repository crawler

No Tool/Runtime/Gateway call is added.

## 11. Real tests executed

Execution environment: reconstructed local World Understanding harness under `/mnt/data/wu_p3_exact_core` using the P1-P5 contract/engine semantics required by P6. This is not a complete authenticated checkout of the authoritative GitHub repository.

### 11.1 P6 package compile

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src/world_understanding/software_world
```

Result: PASS, exit code 0.

### 11.2 Expanded P6 engineering Gate harness

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p6_software_world.py
```

Result:

```text
44 passed in 0.56s
```

This expanded local harness includes the frozen Gate plus additional checks for ambiguity, touched-relation invalidation, no scanner imports, FALSE/STALE rejection, duplicate delta replay and real P4 CALL_REACHABLE -> P6 DERIVED_CACHE integration.

### 11.3 Repository-committed focused P6 suite

The test file committed to GitHub is a compact focused Gate suite.

Command executed before commit:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_world_understanding_p6_software_world_commit.py
```

Result:

```text
35 passed in 0.48s
```

The first draft of this compact suite produced `34 passed, 1 failed` because the test helper placed the string `B|path=A>B` inside `source_native_id`, violating the existing ingress OpaqueId pattern. The production P6 code was not at fault. The helper was corrected to use a canonical hash for source identity and the suite was rerun to 35/35 PASS. This failed draft is recorded rather than hidden.

### 11.4 P2/P3-core/P4/P5 + expanded P6 regression

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q \
  /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py \
  /mnt/data/wu_p3_exact_core/tests/test_p3_core_after_p4.py \
  /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py \
  /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py \
  /mnt/data/wu_p3_exact_core/tests/test_p6_software_world.py
```

Result:

```text
136 passed in 4.35s
```

### 11.5 P2/P3-core/P4/P5 + repository-committed focused P6 suite

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q \
  /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py \
  /mnt/data/wu_p3_exact_core/tests/test_p3_core_after_p4.py \
  /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py \
  /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py \
  /mnt/data/wu_p3_exact_core/tests/test_world_understanding_p6_software_world_commit.py
```

Result:

```text
127 passed in 4.11s
```

## 12. Gate coverage

PASS in the available harness:

- rename does not default to a new Entity;
- move preserves identity when explicit lineage is available;
- delete/create is distinguished from rename;
- ambiguous identity is not strong-merged;
- branch/WorldFrame mismatch fails closed;
- one-file update consumes explicit delta only;
- 1000-entity graph test examines one changed file entity and reports `full_rescan=False`;
- World Graph contains Entity/Relation only and remains separate from Derivation DAG;
- Git delta invalidates only affected subgraph relations;
- 14 frozen Entity types materialize;
- 11 frozen L3 relation classes materialize with correct materialization class;
- semantic relations are deferred to L4/L5;
- Γ blocks unstable graph promotion;
- FALSE/UNKNOWN structural claims are not promoted;
- cross-life Known cannot enter another life's Software World;
- duplicate Git delta replay is idempotent;
- P4 deterministic CALL_REACHABLE can materialize as P6 DERIVED_CACHE graph relation.

## 13. Tests not executed / limitations

NOT RUN:

- full authoritative-repository `pytest`;
- exact authenticated authoritative checkout P0-P6 full regression;
- Windows runtime smoke;
- production Linux runtime smoke;
- Runtime/Gateway native integration;
- real native Git producer -> ingress -> Known -> P6 end-to-end integration;
- production-scale repository graph stress;
- GitHub CI for the P6 implementation SHA. Combined status query returned no statuses.

The local harness is sufficient to execute the P6 algorithms against the relevant contract semantics but is not a substitute for full repository validation.

## 14. Contract compatibility impact

P6 adds an engine package only and does not change P1 contracts.

Existing `WorldEntity`, `EntityResolutionCandidate`, `WorldRelation`, `WorldScope`, Direct/Derived Known and Γ contracts remain authoritative.

Expected contract compatibility impact from P6 itself: none.

## 15. Runtime / Gateway / OFF behavior

P6 is not attached to `WorldUnderstandingFacade` or native Runtime/Gateway producers.

Therefore existing OFF behavior is unchanged:

- no new DB;
- no daemon/worker/thread;
- no LLM;
- no Tool;
- no prompt change;
- no execution-result change;
- no repository crawl.

## 16. Rollback

Rollback target:

`6e18d292d76bda515bf83fd7bf7204b238b5d5ba`

Rolling back to it removes only P6 and preserves P0-P5 plus the latest synchronized main lineage.

## 17. Gate conclusion

P6 L0-L3 Software World gate:

`PASS WITH FULL-REPOSITORY TEST-EXECUTION LIMITATION RECORDED`

This gate authorizes planning P7 absorption of the existing World Cognition as L5, but does not imply that L4 semantic hypotheses, L5 absorption, WorldState, projection, context injection, or Self-Will integration already exists.
