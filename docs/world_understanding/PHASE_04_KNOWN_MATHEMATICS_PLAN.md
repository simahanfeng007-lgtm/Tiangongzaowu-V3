# WORLD UNDERSTANDING PHASE 04 — KNOWN MATHEMATICS / DETERMINISTIC CLOSURE

Status: implementation plan frozen for `agent/world-understanding-v0.1`.

## Baseline

- P4 implementation/rollback base: `0aaf83be9ddf31445e4d5295f739a42d82f56fda`
- Latest main synchronized before P4: `da714694074acade7539a02de94e7c3265f788bd`
- P0-P3 remain intact.

## Mathematical contract

P4 implements a life-scoped least fixed point only:

`K*_{life} = Closure(K0_{life})`

with iteration:

`K_(n+1) = K_n ∪ { f(x) | f ∈ F_det, x ⊆ K_n, P4-hard-valid(f,x) }`

and termination when `K_(n+1) = K_n`.

There is no global closure followed by life filtering. Mixed-life/world/principal parents fail closed before a Derived Known record is emitted.

P4 does not claim the full Γ epistemic plane. P5 owns full Γ. P4 applies only conservative pre-Γ hard validity required to make deterministic closure safe: exact scope identity, TRUE parents, authority-domain intersection, authority ceiling, time intersection, provenance preservation, canonical validation, finite active cut, and same-revision cycle exclusion.

## New implementation package

`src/world_understanding/known/`

- `set.py` — finite life-scoped active Known cut, indexes, canonical dedup, snapshots and incremental fork.
- `rule.py` — deterministic rule/candidate/diagnostic contracts.
- `registry.py` — shared stateless rule registry.
- `authority_matrix.py` — conservative authority/provenance/time/observability intersection; never widens parent authority.
- `closure.py` — semi-naive least-fixed-point engine, incremental recompute, cycle detection, derivation materialization.
- `rules/__init__.py` — first deterministic P4 rules.
- `__init__.py` — P4 engine assembly only.

No world database is added.

## First deterministic rules

1. Filesystem pre/post transition:
   - `FILE_EXISTS false -> true` => `FILE_CREATED`
   - `FILE_EXISTS true -> false` => `FILE_DELETED`
   - file hash change => `FILE_CONTENT_CHANGED`
   - same verified hash => `FILE_VERIFIED_UNCHANGED`
2. Hash equality => `HASH_EQUAL`.
3. Same-subject event order => `EVENT_PRECEDES`.
4. Same direct-source root grouping => `SHARES_SOURCE_ROOT`.
5. Explicit Git structural normalization:
   - `GIT_CONTAINS` => `CONTAINS`
   - `GIT_IMPORTS` => `IMPORTS`
   - `GIT_DIRECT_CALLS` => `DIRECT_CALLS`
6. Call graph reachability:
   - direct edges may derive `CALL_REACHABLE` with an explicit path.
   - `DIRECT_CALLS(A,B) + DIRECT_CALLS(B,C)` MUST NOT derive `DIRECT_CALLS(A,C)`.
7. Explicit scope binding => `SCOPE_CONTAINS`.
8. Explicit WorldFrame identity inputs => WorldFrame identity propositions.

No filename-based semantic role inference is permitted.

## Closure invariants

- finite active cut with hard `max_records`.
- canonical record-hash dedup.
- deterministic rule ordering.
- rule IDs and versions are explicit.
- rule failure aborts that transform only; existing K0/K* is preserved.
- all accepted parents must have exactly the same `WorldScope`.
- all P4 deterministic parents must be `truth_state=TRUE`.
- child authority ceiling <= minimum compatible parent ceiling.
- child empirical evidence weight <= minimum parent empirical weight and child ceiling.
- P4 does not widen authority domains.
- every parent provenance root is preserved in the child/DerivationRef.
- valid-time interval is intersected; empty time intersection is rejected.
- same-revision semantic cycles are rejected.
- every accepted derived record emits a life-scoped `DerivationRef` and `DerivationEdge` set.
- World Graph is not created or mutated by P4; the derivation DAG stays separate.

## Incremental recompute

A prior `ClosureResult` may be forked as the next active cut. Existing indexes and ancestry metadata are inherited; only the new delta is admitted and rules are re-evaluated semi-naively from that delta. No full 10k revalidation/rebuild is required for a one-record append.

## Explicit non-goals

P4 does NOT:

- implement L4 semantic hypotheses or call an LLM;
- implement full Γ or Λ (P5);
- materialize Entity/Relation/World Graph (P6);
- create WorldState or a WorldState DB (P9);
- connect Context output (P10);
- connect Self-Will (P11);
- call Tools, network, Runtime, or Total Gateway;
- modify existing Runtime/Gateway behavior;
- attach the closure engine to the Facade yet.

Therefore existing OFF behavior remains unchanged.

## Gate

P4 may pass only if all are demonstrated:

- closure terminates at a least fixed point for the deterministic test set;
- identical input is canonical/order stable;
- same-revision cycle is rejected;
- cross-life parents are rejected before derivation;
- authority cannot increase or change to an incompatible domain;
- provenance cannot disappear;
- generic direct-call transitivity is forbidden; only explicit reachability is derived;
- rule-version change changes derived revision/content hash and Derivation identity without silently rewriting the stable proposition slot;
- finite active cut overflow fails closed;
- rule exceptions do not erase Direct Known;
- every accepted derived record has a derivation DAG record/edges in the same scope;
- 10k Known records support bounded incremental recompute and the one-record incremental run is faster than rebuilding the 10k baseline in the same process;
- P2 ingress and P3 core source/life isolation regressions remain compatible in the available execution harness;
- P4 code commit contains no Runtime/Gateway/LLM/Tool integration changes.
