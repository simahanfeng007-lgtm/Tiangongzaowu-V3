# P9 R1 — Method Source add/update/remove lifecycle foundation — 2026-09-07

Status: **P9 IN PROGRESS — R1 lifecycle planning implemented; publication/World revision cutover not started.**

Baseline: `main @ 34668e03b9f5097adde92a32e6dc8c2d7e541753` (merged P8 checkpoint).
Branch: `codex/capability-composition-p9-method-source-evolution-v1`.

## Scope

P9 is the Skill/Method Source compiler lifecycle. A Method Source is semantic
method material for composition; it is not a Tool Action and never owns an
execution handler, permission, Ticket/Grant, Runtime route, Effect, or
Completion decision.

R1 deliberately implements only the deterministic lifecycle planning boundary:

`current Skill Method World snapshot`
→ `content-addressed ADD / UPDATE / REMOVE candidate`
→ `structural/source-identity validation`
→ `deterministic lifecycle plan`
→ `next primitive set + invalidation intents`

R1 does **not** publish that plan, mutate the current Skill Method World, write
Memory, call Life learning, create a permanent `SKILL.md`, alter a running
composition, or authorize execution. `may_publish`, `may_authorize`, and
`may_execute` remain false.

## Why R1 does not change the P3 snapshot schema yet

The current P3 cold-start snapshot requires every reviewed method to have one
`MethodMigrationBindingV1`, and P4/P6 consumers validate those bindings. Future
P9-native Method Sources will no longer all be legacy-Skill decompositions.
Changing that schema and publication path in the same first edit would mix:

1. lifecycle candidate validation;
2. provenance generalization;
3. World revision publication;
4. downstream P4/P6 compatibility.

R1 isolates (1). R2 will generalize reviewed provenance/publication without
creating a second WorldState, Method registry, Gateway, Runtime, or current
catalog.

## R1 contracts and invariants

New `lifecycle.py` defines:

- `MethodSourceCandidateV1`
- `MethodSourceChangeV1`
- `MethodSourceLifecyclePlanV1`
- `compile_method_source_lifecycle(...)`

Candidate rules:

- every candidate binds the exact current `SkillMethodWorldSnapshotV1` hash;
- candidate identity and payload are content-addressed;
- one operation per `method_id` in a lifecycle plan;
- ADD rejects an existing method;
- UPDATE/REMOVE reject a missing method and require the exact previous
  descriptor hash;
- UPDATE must advance version, source revision, and descriptor identity;
- source kind remains `SKILL_METHOD`, `manifest_sha256` remains `None`;
- source paths are sorted, unique, repository-relative POSIX paths with no
  traversal;
- source spans must be deterministic and contained by declared source files;
- semantic prefix/descriptor validation reuses the existing P3 compiler rules;
- REMOVE cannot erase the entire Method World;
- candidate ordering cannot change the compiled plan identity.

The simulation evidence field is an immutable SHA-256 binding only. R1 does not
accept a candidate's self-assertion that the simulation passed. R2 publication
must independently verify the referenced test/simulation evidence before a new
World revision can become current.

## Invalidation output

R1 emits deterministic invalidation intents for changed methods:

- `method:<method_id>`
- `skill-method-world:current`
- `capability-composition:method-candidates`
- `world-context:method-candidates`

These are intents in the lifecycle plan, not direct cache/store mutation. Memory
experience staleness/revalidation remains a later integrated concern (P15); R1
does not make Memory a World authority.

## Verification performed for this checkpoint

The test workspace was reconstructed from the immutable P8 C5 native source
revision. The unchanged authoritative P9 dependencies were independently
matched to current main Git blobs:

- `skill_method_world/compiler.py` → `860f6adaf782eff348f8c144c8f1292fb9be8731`
- `skill_method_world/models.py` → `59e8331a21c5c34ea09baed5c5b10e84cecc0f61`
- `skill_method_world/production_catalog.py` → `075c37a87979a1dcad15b6305e6971f36fc92275`
- `contracts/capability_composition.py` → `888dbdbfe46f018f3655cff8785c1abc826e6511`

Focused local regression after adding R1:

```text
python -m pytest -q \
  tests/test_skill_method_source_lifecycle_p9.py \
  tests/test_skill_method_world_p3.py \
  tests/test_skill_method_world_p3_production.py \
  tests/test_capability_composition_p4.py \
  tests/test_capability_composition_p4_cross_phase.py \
  tests/test_one_world_context_p6.py \
  tests/test_capability_context_evaluation_p6.py
```

Result after lifecycle self-consistency hardening: **58 passed, 0 failed, 0 skipped**; five existing Pydantic field-shadow
warnings. The earlier smaller P3/P9 group also passed **36/36**. Additional R1 tests reject rehashed plans with a drifted next-source set, mismatched invalidations, malformed change shapes, and non-opaque candidate IDs.

The authoritative source-to-runtime generator was run in the reconstructed
workspace using the existing `scripts/sync-generated-sources.py --write`, then
`--check`; source-authority validation reported **17 independent authorities,
1 alias, 24 generated targets, 1 closed-world boundary**. The embedded runtime
world-understanding target is git-ignored in a fresh source checkout, so R1 does
not manually commit that generated runtime copy.

These are focused intermediate checks, not P9 final acceptance. Full Python,
Node, Ubuntu/Windows, P14 and P19 final gates are deferred until the P9 final
candidate, per the accepted stage cadence.

## Next P9 packages

R2 — reviewed Method Source provenance + evidence verification + one existing
Skill Method World revision publication path.

R3 — production World ingest/current-revision swap, add/update/remove
invalidation/retrieval integration, restart/replan identity tests; no running
plan drift.

R4 — adversarial/fault matrix, final exact-head full gates, PR review/merge and
main ancestry verification.

P10 Life learning cutover is explicitly out of scope until P9 closes.
