# PHASE 07 — ABSORB EXISTING WORLD COGNITION AS L5

## Goal

Absorb the already implemented off-main World Cognition contracts/core into the canonical World Understanding tree without creating a second cognition engine, Runtime, Gateway, ingress, or public World Understanding facade.

Canonical flow for P7:

`K* / WorldEvent / WorldEntity / WorldRelation / WorldHypothesis -> reference-only evidence adapter -> existing Cognition Core C0-C4 -> L5 context-only view`

## Authoritative sources

P7 reconciles these existing off-main commits per the P0 baseline manifest; it does not merge/cherry-pick their full branch trees:

- Cognition contracts: `c777561bc51546dafe01fd68de25a332d25420d5`
- Cognition core: `a79a5b46a54258798bce3529b8424cb3b3ab4d2c`
- P7 baseline / P6 final: `09bd95f139f9beac12f08f98f05906d316b48ae7`

## Existing files absorbed unchanged

Contracts retained with the original stable IDs/hash/revision semantics:

- `src/contracts/cognition_evidence.py`
- `src/contracts/cognition_prior.py`
- `src/contracts/cognition_revision.py`
- `src/contracts/cognition_statement.py`

Core implementation retained with the original C0-C4, CAS store, evidence/stability, consolidation, prior and retrieval semantics:

- `consolidator.py`
- `evidence.py`
- `facade.py` (legacy compatibility only; not exported by canonical L5 package)
- `priors.py`
- `retrieval.py`
- `stability.py`
- `store.py`

They are physically hosted once under `src/world_understanding/cognition/`.

## New P7 files

- `src/world_understanding/cognition/bridge.py`
- `src/world_understanding/cognition/l5.py`
- canonical package `__init__.py`
- thin legacy `v3.world_cognition.*` re-export modules
- `tests/test_world_understanding_p7_cognition_l5.py`

The legacy modules own no store, policy, state, or implementation; they re-export the canonical classes so old imports and tests do not create a dual implementation.

## Frozen invariants

1. C0-C4 semantics are preserved.
2. Cognition stable IDs and revision chains are preserved.
3. C4 means protected/global cognitive consolidation only; it is not a world fact.
4. Every L5 Cognition output has empirical evidence weight 0.
5. Cognition output is context-only and cannot authorize, execute, confirm, or change risk.
6. New World Understanding evidence enters by first-class reference only. No World object is embedded into cognition evidence.
7. Allowed evidence object classes: Known, Event, Entity, Relation, Hypothesis.
8. Every reference is checked against object class, ID, revision, canonical hash and exact Life/World/Principal scope.
9. Γ-stale or inadmissible Known cannot be promoted into cognition support.
10. Entity/Relation graph records must be TRUE/CURRENT.
11. Hypothesis remains zero empirical authority and is mapped to zero-weight legacy model-inference evidence.
12. Entity materialization is not counted as independent empirical evidence; its contribution is zero.
13. Evidence lineage roots are preserved from Known provenance / graph observation refs / events / hypotheses.
14. Cross-Life evidence fails closed.
15. No Runtime/Gateway/Tool/LLM/native producer integration is added in P7.
16. The only public World Understanding physical attachment remains `WorldUnderstandingFacade.accept(...)`.

## Compatibility

The original test files from the contracts/core source commits are copied unchanged into the implementation branch. Their imports continue through thin `v3.world_cognition.*` wrappers. The canonical L5 package deliberately does not export `WorldCognitionFacade`; the old class exists only to keep pre-P7 callers/tests compatible and is not connected as a second World Understanding ingress.

## Explicitly deferred

P7 does not implement:

- P8 L4 semantic hypothesis generation;
- P9 WorldState;
- P10 WorldContextPacket prompt insertion;
- P11 WorldInquiry/Self-Will integration;
- Runtime/Gateway attachment;
- Tool/LLM calls;
- a background cognition worker/daemon;
- cross-Life knowledge transfer.

## Gate

P7 may pass only when:

- original C0-C4 contracts/core are present once in canonical code;
- legacy imports are aliases/re-exports, not a copied engine;
- original tests are retained unchanged;
- first-class WU reference adaptation is deterministic and Life-scoped;
- malformed/tampered/cross-Life/stale evidence fails closed;
- C4 L5 output remains context-only with empirical weight 0;
- P2-P6 focused regression remains green in the available harness;
- P7 diff contains no Runtime/Gateway/native execution path modification;
- unexecuted full-repository tests are reported as NOT RUN.

## Rollback

Rollback point: `09bd95f139f9beac12f08f98f05906d316b48ae7`.
