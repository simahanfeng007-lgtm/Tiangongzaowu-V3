# P3 — Skill Method World

Base: `main @ ccc5da5a0aa9b27631d652dd67d3a29c03107b86`.

## Purpose

P3 establishes a deterministic, read-only Skill Method World. It does not
replace the current Static Skill Planner yet. Existing static Skills are used
only as migration evidence to identify reusable problem-solving methods.

The future semantic chain is:

`legacy static Skill sources`
→ `zero-authority lifecycle-phase evidence`
→ `many-to-one reusable method decomposition`
→ `SkillSourcePrimitiveV1`
→ `Skill Method World snapshot`

WorldState integration remains deferred to P6.

## Required distinction

A Skill Method is not a Tool and is not executable.

It may describe:

- goal classes;
- preconditions and expected postconditions;
- method steps;
- required capability classes;
- failure modes;
- fallback patterns;
- verification intent;
- composition tags.

It must not contain or produce:

- handlers;
- ActionPermission;
- allowed action IDs;
- CapabilityGrant;
- ExecutionTicket;
- Runtime routes;
- effects or side effects;
- Completion decisions.

## Migration rule

P3 rejects a one-to-one copy of a legacy Skill into a new method primitive.

A migrated P3 method must be supported by at least two independent legacy
Skills and must declare the lifecycle phases used as decomposition evidence.
The legacy evidence corpus retains only:

- immutable Skill/source identity;
- source hash;
- broad lifecycle-phase presence.

Natural-language method semantics remain reviewed source candidates. P3 validates
their identity, provenance and structural contract; it does not claim to prove a
free-form semantic interpretation from the legacy text or accept an LLM guess
as a stable method fact.

It deliberately discards the original action lists and full procedural
instructions, so the method world cannot become a second Static Skill Planner
or Action Registry.

## Reviewed production method set

The production P3 seed catalog is implemented in
`src/world_understanding/skill_method_world/production_catalog.py`.
It compiles the checked-in Static Skill catalog into five initial reusable
method primitives:

- `decompose_goal`
- `generate_then_verify`
- `retry_after_diagnosis`
- `acceptance_review`
- `finalize_verified_artifact`

Each seed is content-addressed, non-authorizing and supported by multiple real
Static Skills. The production compiler checks those exact Skill identities,
source hashes and required lifecycle phases before emitting a Method World
snapshot. It never copies their Action IDs or full instructions.

The five methods are a conservative cold-start set, not a claim that all future
method semantics have already been discovered. Additional methods require the
same reviewed-source, many-to-one evidence and deterministic validation path.

## Deterministic phase evidence

The migration observer maps only field presence:

- `starter_actions` → `PREPARATION`
- `inspection_actions` → `INSPECTION`
- `production_actions` → `PRODUCTION`
- `quality_gates` → `VERIFICATION`
- `repair_actions` → `REPAIR`
- `final_actions` → `FINALIZATION`
- non-empty `acceptance` → `ACCEPTANCE`

No action ID is copied into the evidence corpus.

## Provenance

Each method source revision is bound to:

- at least two exact legacy Skill IDs;
- exact source paths, versions, lifecycle evidence and source SHA-256 values;
- an immutable method descriptor hash.

The World snapshot separately binds the complete legacy evidence corpus. This
keeps global catalog drift auditable without invalidating an unrelated method
source revision when only an unused legacy Skill changes.

Any selected source, phase, descriptor, ordering, or binding drift fails closed.

## Graph relations

P3 permits only method-semantic relations:

- `DECLARES_GOAL_CLASS`
- `REQUIRES_PRECONDITION`
- `EXPECTS_POSTCONDITION`
- `REQUIRES_CAPABILITY_CLASS`
- `HAS_METHOD_STEP`
- `PRECEDES`
- `HAS_CONTROL_FLOW_HINT`
- `HAS_FAILURE_MODE`
- `FALLS_BACK_TO_PATTERN`
- `DECLARES_VERIFICATION_INTENT`
- `HAS_COMPOSITION_TAG`
- `SOURCE_REVISION_OF`
- `DERIVED_FROM_LEGACY_SKILL`

Relations such as `EXECUTES`, `ALLOWS_ACTION`, `GRANTS`, `HANDLED_BY`, or
`COMPILES_TO_ACTION` are rejected by construction.

## Authority invariants

- `may_authorize = false`
- `may_execute = false`
- no second Runtime
- no second Gateway
- no second Action Registry
- no Skill activation
- no Memory write
- no WorldState store
- no change to the existing Static Skill production path in P3
- no generated mirror edited manually

## P3 gate

Before P4 begins, the P3 branch must pass:

- source-authority Ubuntu/Windows;
- full-regression Ubuntu/Windows;
- P14 repository perception validation;
- P19-R2 Verification Plane Golden Gate.

Static Skill decommission remains deferred until P12 after shadow and limited
production cutover.
