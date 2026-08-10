# WORLD UNDERSTANDING PHASE 06 — L0-L3 SOFTWARE WORLD FIRST

Status: implementation plan frozen for `agent/world-understanding-v0.1`.

## Goal

Implement the first concrete World Understanding domain over Tiangong V3's own software world:

`WorldFrame -> Perception -> Entity -> Relation -> Sparse World Graph`

P6 consumes already-observed/compiled Known records plus explicit Git commit/diff deltas. It does not scan the repository, call Tools, call an LLM, or change Runtime/Gateway execution behavior.

## Frozen P6 boundaries

P6 implements only L0-L3.

Out of scope:
- L4 semantic hypotheses;
- L5 World Cognition absorption;
- WorldState materialization;
- context projection;
- Self-Will integration;
- independent daemon/worker;
- new Runtime/Gateway/Tool execution authority.

## L0 — SoftwareWorldFrame

A frame explicitly contains:
- life and principal through `WorldScope`;
- workspace;
- repository;
- worktree;
- branch;
- commit;
- environment;
- time;
- optional WorldCut.

`frame_id` is stable for the same life/world/principal/workspace/repository/worktree/branch.
`frame_revision_hash` changes with commit/environment/time/cut.
Different branches produce different frame IDs and cannot share one mutable graph accidentally.

## L1 — Typed Perception

Existing `DirectKnownRecord` / `DerivedKnownRecord` are classified without changing their truth/authority:
- IDENTITY;
- STRUCTURE;
- EVENT;
- OBSERVATION.

P6 materialization is gated by Γ. Non-current/unstable Known records do not become graph facts.
FALSE/UNKNOWN identity or structure claims are not materialized as graph nodes/edges.

## L2 — Entity

First entity vocabulary:
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

P6 reuses the existing `WorldEntity` contract.

Identity rules:
- explicit identity Known uses a stable source anchor;
- explicit Git RENAME/MOVE preserves the existing File entity ID when the old path resolves uniquely;
- DELETE retires the existing entity;
- later ADD is not silently treated as the old entity;
- ambiguous identity produces an ambiguity candidate/diagnostic and never strong-merges candidates;
- duplicate observations are idempotent.

## L3 — Deterministic Relations

Materialized predicates:
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

The following are explicitly deferred to L4/L5 and are not deterministic L3 facts:
- GUARDED_BY
- AUTHORITATIVE_FOR
- IS_BOUNDARY_OF

## Sparse World Graph

The graph stores only `WorldEntity` and `WorldRelation` latest revisions.
Derivation DAG records never enter the World Graph.

Indexes are maintained incrementally by entity ID, name/alias, file path and relation endpoints.
There is no `scan_repo`, `os.walk`, filesystem crawler, Git subprocess, network client or model call.

## Git incremental update contract

`GitCommitDelta` accepts already-observed changes:
- ADD
- MODIFY
- DELETE
- RENAME
- MOVE

It must match the current frame repository/worktree/branch/commit.
Only listed changed paths are processed.
A previously applied delta ID is not applied twice.
Relations touching a changed/retired entity are invalidated locally; unrelated subgraphs remain intact.

## P6 Gate

- [ ] rename does not default to a new Entity
- [ ] move preserves identity when explicit lineage exists
- [ ] delete/create is distinguishable from rename
- [ ] ambiguous identity is not strong-merged
- [ ] branch/worldframe mismatch fails closed
- [ ] incremental update does not rescan the repository
- [ ] one-file change does not rebuild the whole graph
- [ ] World Graph contains only Entity/Relation, not Derivation DAG
- [ ] Git commit/diff updates only affected subgraph
- [ ] relation materialization classes are enforced
- [ ] semantic relations are deferred to L4/L5
- [ ] Γ blocks unstable graph promotion
- [ ] same Git delta replay is idempotent
- [ ] P4 CALL_REACHABLE can materialize as P6 DERIVED_CACHE
- [ ] existing P2/P3/P4/P5 focused regressions remain green in the available harness

## Rollback

Rollback target is the P5 final report head:

`6e18d292d76bda515bf83fd7bf7204b238b5d5ba`

Rolling back to it removes only P6.
