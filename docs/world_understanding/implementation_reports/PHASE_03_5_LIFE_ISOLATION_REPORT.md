# PHASE 03.5 LIFE ISOLATION REPORT

> This report records the mandatory Life Isolation Hardening requested during P3. It was **merged into P3 implementation**, not delivered as a separate engine or side branch.

## SHA

- Start SHA: `757d9a018540b8559181f6e2a461bf5c3cacca61`
- Integrated P3 implementation SHA: `f0483aa749babcf6394064f7fbbe9f82d6f9c567`
- Main SHA verified after implementation: `ae3404b3300c09a0de0f7782e7d739fe24c93c05`
- Rollback point: `757d9a018540b8559181f6e2a461bf5c3cacca61`

## Real pre-change findings

1. `WorldScope.life_id` was already required.
2. `derive_world_id(...)` already included `life_id`.
3. `derive_world_scope_hash(...)` already included `life_id`.
4. `WorldIngressEnvelope.life_id` was optional before this phase.
5. There was no mandatory `envelope.life_id == scope_hint.life_id` validation before this phase.
6. There was no mandatory top-level principal-scope equality validation before this phase.
7. P2 compiler results were invoked then discarded; there was no uniform post-compiler scope/lineage validator.
8. Direct/DerivedKnown IDs were already scope-hash sensitive.
9. ContextPacket and WorldInquiry were already `WorldScope` bound.
10. WorldCuriosity had a standalone `life_id` rather than `WorldScope`.
11. InquiryOutcome had no `WorldScope`.
12. DerivationRef / DerivationEdge had no `WorldScope`.
13. PredictionOutcome had no `WorldScope`.
14. P2 CompilerRegistry itself had no life-private state.

## Changes

- made ingress `life_id` mandatory;
- enforced ingress-life == scope-life;
- enforced redundant principal-scope equality when supplied;
- added shared `scope_guard.py`;
- added uniform compiler-output boundary validation;
- guaranteed DirectKnown scope/source lineage equals input envelope lineage;
- kept CompilerRegistry shared/stateless with respect to life identity;
- migrated WorldCuriosity to `scope: WorldScope` as its single life identity source;
- added `WorldScope` to InquiryOutcome, PredictionOutcome, DerivationRef and DerivationEdge;
- made those stable identities scope-sensitive;
- added P4 precondition guard for mixed-life Known parents without implementing P4 closure;
- added concurrency isolation tests using one shared Facade/Registry.

## INV-LIFE audit

- [x] INV-LIFE-01 Engine Shared
- [x] INV-LIFE-02 State Life Scoped at current contracts; future graph/store/projection feedback remain frozen requirements
- [x] INV-LIFE-03 Ingress life explicit
- [x] INV-LIFE-04 Ingress double-life consistency
- [x] INV-LIFE-05 Principal scope consistency
- [x] INV-LIFE-06 Compiler has no scope decision authority
- [x] INV-LIFE-07 Shared Registry has no current-life state
- [x] INV-LIFE-08 Same source under A/B -> different scope/known identity
- [x] INV-LIFE-09 `K*_life = Closure(K0_life)` frozen before P4
- [x] INV-LIFE-10 Derivation objects scope-bound; mixed-life parent guard frozen
- [x] INV-LIFE-11 future World Graph life+world-scope double key frozen; graph not implemented
- [x] INV-LIFE-12 future Store life+world-scope double key frozen; store not implemented
- [x] INV-LIFE-13 Context output single-source scope confirmed; Runtime insertion not implemented
- [x] INV-LIFE-14 Inquiry single-source scope confirmed; Self-Will dispatch not implemented
- [x] INV-LIFE-15 post-commit adapter inherits explicit scope; no workspace/path life guessing API
- [x] INV-LIFE-16 cross-life Cognition transfer prohibited by default; transfer mechanism not implemented

## Required test mapping

- TEST-LIFE-01 Life A Source -> Life A DirectKnown: covered
- TEST-LIFE-02 Life B Source -> Life B DirectKnown: covered
- TEST-LIFE-03 same payload/native A/B -> different scope hash / Known id: covered
- TEST-LIFE-04 envelope A + scope B -> REJECT: covered
- TEST-LIFE-05 principal mismatch -> REJECT: covered
- TEST-LIFE-06 malicious compiler changes life scope -> REJECT: covered
- TEST-LIFE-07 shared Registry A/B no current-life state: covered
- TEST-LIFE-08 concurrent 1000 A + 1000 B through one Facade -> zero observed cross-life contamination: covered
- TEST-LIFE-09 Context scope contract: structurally checked; full projection not implemented
- TEST-LIFE-10 Inquiry scope contract: structurally checked; Self-Will integration not implemented
- TEST-LIFE-11 P4 mixed-life parent precondition -> SCOPE_MISMATCH/no derivation: covered; closure engine not implemented

## Real test command / result

Exact current core command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py /mnt/data/wu_p3_exact_core/tests/test_world_understanding_p3_core.py
```

Result:

```text
51 passed in 0.96s
```

Exact syntax command:

```text
/opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src
```

Exit code: `0`.

A complete high-level P1 output-contract import run was not possible in the reconstructed fixture because it does not contain the full authoritative P1 package exports. This is recorded as **NOT RUN**, not PASS.

Exact P3 implementation SHA has no GitHub Actions runs (`total_count=0`).

## Compatibility impact

Yes. World Understanding contract compatibility changed before Runtime wiring:

- ingress life is now mandatory;
- Curiosity switches from duplicate top-level life id to WorldScope;
- InquiryOutcome / PredictionOutcome / Derivation gain WorldScope and scope-sensitive ids;
- P1/P2 test builders were migrated accordingly.

No Runtime or Gateway implementation file changed.

## OFF behavior

No intentional change. OFF remains lazy/no-op and creates no world store, worker, Tool, Runtime or LLM activity.

## Gate

**Life Isolation Hardening gate: PASS WITH FULL-AUTHORITATIVE-CHECKOUT TEST LIMITATION RECORDED.**

Native producer automatic forwarding is outside the hardening patch and remains explicitly deferred; the generic post-commit adapter seam is present.
