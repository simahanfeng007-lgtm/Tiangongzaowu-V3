# P6 — ONE WorldState / ONE WorldContext

Base: `main @ 52439b0efaa5d5208ee1d44cb0c0ab4fec9b1ae7`.

## Purpose

P6 places Software World, Tool Capability World and Skill Method World into one
active-frame WorldState and one existing `WORLD_CONTEXT_SLOT`.

P6 does **not** add a ToolWorldStateStore, SkillWorldStateStore,
CapabilityWorldStateStore, second materializer, second context authority, second
Gateway, second Runtime, or second Memory store.

The materialization chain is:

`one exact SoftwareWorldFrame + one WorldCut`
→ Software/Tool/Method `WorldDomainContributionV1`
→ transaction-local merged `SparseWorldGraph`
→ **one call to existing WorldStateMaterializer**
→ **one current WorldState in existing WorldStateStore**

The context chain is:

`one WorldContextPacket bound to that WorldState`
→ typed capability context packet
→ identity-first budget enforcement
→ **the existing WORLD_CONTEXT_SLOT**

## Exact FrameBinding

Repository-bound Tool and Method descriptors require an exact
`FrameBindingV1` containing:

- life ID;
- principal scope hash;
- workspace ID;
- world scope hash;
- frame ID and frame revision hash;
- repository;
- worktree;
- branch;
- commit;
- environment;
- WorldCut reference;
- deterministic binding hash.

The materialization adapter compares the complete binding with the frame and cut
being materialized. It never chooses a latest frame and rejects generic values
such as `generic`, `current`, `latest`, `runtime`, or `unknown` for
repository-bound capability descriptors.

## WorldDomainContribution

Each contribution is content-addressed and contains:

- kind: `SOFTWARE`, `TOOL_CAPABILITY`, or `SKILL_METHOD`;
- exact frame binding;
- exact source revisions;
- ordinary `WorldEntity` and `WorldRelation` records;
- dependency source keys;
- `may_authorize = false`;
- `may_execute = false`.

Tool and Method descriptors are represented as regular non-authorizing World
entities. Their structural relationships become regular World relations with
full descriptor provenance. They do not become Action permissions, Grants,
Tickets, Effects, Facts, or Completion claims.

## One materialization authority

`bind_domain_contributions(...)` creates a transaction-local graph. It preserves
the base Software graph, applied Git deltas and existing dependency bindings,
then merges exact-frame contribution records.

Revision continuity is checked when a contribution replaces an existing entity
or relation. Contribution ordering is canonical and does not change the
transaction identity.

`materialize_one_world_state(...)` accepts only the existing
`WorldStateMaterializer` type and contains exactly one call to
`materializer.materialize(...)`. Publication, current-head CAS, persistence,
state sequence, stale detection and manifests remain owned by the existing P9
materializer/store.

## Capability context sections

The existing context slot receives the following typed sections:

- `[CURRENT_WORLD]`
- `[METHOD_CANDIDATES]`
- `[ACTION_CANDIDATES]`
- `[PROCEDURAL_EXPERIENCE]`
- `[NEGATIVE_EVIDENCE]`
- `[COMPOSITION_ABI]`

Every capability packet and rendered slot remains:

- `context_only = true`
- `authorization_source = false`
- `authorizes = false`
- `confirms = false`
- `changes_risk = false`
- `may_execute = false`

Procedural experience and negative evidence are accepted only as DATA. P6 does
not convert them into instructions or World authority.

## Candidate and evidence budgets

The configurable hard ranges are:

- Method candidates: 8–15 maximum budget;
- Action candidates: 12–30 maximum budget;
- procedural experiences: 3–8 maximum budget;
- negative evidence: 0–5 maximum budget.

The actual candidate count may be lower when fewer exact, eligible candidates
exist. The builder never pads the context with invented candidates.

## NEVER_COMPRESS identities

The following fields, when present, are rendered before optional summaries and
are never truncated:

- `candidate_id`
- `method_ref`
- `action_ref`
- `source_revision`
- `world_state_ref`
- `plan_ref`
- `activation_ref`
- `verification_plan_ref`

If these mandatory identities do not fit, capability context is unavailable.
The renderer may drop only optional Method summaries.

## Failure policy

P6 implements explicit modes:

- `SHADOW`: capability context failure returns the base World slot and records
  fallback usage;
- `LIMITED`: base fallback is allowed only with an explicit audited migration
  flag;
- `DEFAULT`: no implicit Static Skill fallback is indicated. The result is
  `CAPABILITY_CONTEXT_UNAVAILABLE` and the caller must fail closed or enter an
  explicitly designed reduced mode.

P6 does not yet switch the production planner. That activation belongs to P7
and later cutover phases.

## P6 gate

Before P7 begins, P6 must prove:

- one current WorldState per active frame;
- Software, Tool and Method entities coexist in that WorldState;
- every contribution is bound to the exact active frame/cut;
- generic frames cannot carry repository-bound descriptors;
- context authority flags remain false;
- protected identity fields are byte-for-byte preserved;
- contribution order does not change state input identity;
- exactly one existing materializer call occurs;
- no second WorldState store or context authority exists;
- focused and full regression pass on Ubuntu/Windows;
- P14 repository perception and P19-R2 Golden Gate remain green.

A 50–60 task context-size/quality evaluation will be reported as recorded
engineering evidence unless exact live provider outputs are separately captured;
fixtures must not be represented as live-model performance.
