# P5 — Capability Experience / Memory

Base: `main @ dd77ff29b611e2ffcc785a538ea1ec6d4f4448b2`.

## Purpose

P5 adds capability-combination experience admission, negative evidence,
fixed-point confidence, lifecycle management, source-stale detection and exact
scope recall inside the existing Memory architecture.

P5 does **not** add a second memory database, a sixth memory layer, a motor
memory runtime, a scheduler, a Gateway, a WorldState store, or an execution
path. The existing P15 generic promotion policy is unchanged.

The authority chain is:

`existing Runtime / Effect / Fact`
→ `existing P19 Verification Plane`
→ `existing CompletionGate`
→ bounded post-completion Attribution Trace
→ `AttributionIntegrityV1`
→ capability-specific P5 admission/statistics
→ non-writing L3 DATA intent
→ **existing MemoryCoordinator**
→ **existing LifeShadowStore**

## Attribution integrity

A positive experience requires an exact, continuous machine lineage:

- valid system-compiled `CapabilityCompositionPlanV1`;
- exact request, run and generation;
- exact principal and privacy scope;
- machine `CompletionDecision` with a valid digest;
- `outcome = COMPLETED`;
- when acceptance obligations exist, `VerificationMode = PLAN_BOUND`;
- active VerificationPlan complete and bound to Completion;
- verification records present;
- Effect/Fact lineage complete;
- Completion supporting facts present in terminal facts;
- Method and Action source revisions exactly match the Plan;
- source revisions continuous for the run;
- no human takeover;
- no alternate execution chain;
- no unknown external overwrite or unknown side effect;
- no unresolved reconciliation;
- no credential/secret content;
- no prompt-injection content;
- no truncated context identity.

Any failure produces `AttributionIntegrityV1.state = FAIL` with deterministic
reason codes. A model cannot create a PASS because the input completion object
must expose a valid machine digest and `model_generated = false`.

## Positive and negative admission

Positive admission requires all attribution and completion gates plus a minimum
quality score. Incomplete, unverified or attribution-broken outcomes cannot
become positive experience.

Negative evidence covers at least:

- validator invalid;
- runtime failure;
- permission denied;
- Tool unavailable;
- verification failure;
- stale source mismatch;
- ambiguous effect;
- invalid/truncated context identity;
- principal/privacy mismatch;
- prompt injection;
- secret presence;
- reconciliation required.

Positive and negative counters are disjoint. A failure increments only the
failure pool and creates `NegativeCapabilityEvidenceV1`; it never becomes
positive “muscle memory.”

## Experience key and source binding

The aggregate key binds:

`GoalClass + CompositionTopology + SourceRevisionFamily + EnvironmentClass`

The family hash uses stable source identity (`source_kind`, semantic ID and
version). Exact source hashes separately bind complete `SourceRevisionRefV1`
records.

- same family, changed exact revision → `REVALIDATION_REQUIRED`;
- changed family/version/identity → `STALE`.

P5 emits a non-writing invalidation intent. The existing Memory invalidation DAG
remains the persistence authority; P5 does not invalidate database rows itself.

## Fixed-point statistics

All confidence math is integer-only and deterministic across Windows/Linux:

- Beta(1,1) posterior mean in milli-units;
- Wilson lower confidence bound using integer scaling and `math.isqrt`;
- no platform float arithmetic.

A new aggregate is always `PROBATION`.

The first stable policy requires:

- `success_count >= 5`;
- `independent_context_count >= 4`;
- `failure_count <= 1`;
- Wilson lower confidence threshold passed;
- average quality threshold passed;
- source family and exact revision current;
- recent positive evidence inside the configured decay window.

Lifecycle values remain:

- `PROBATION`
- `STABLE`
- `STALE`
- `REVALIDATION_REQUIRED`
- `RETIRED`

## Memory representation

The memory payload and intent are explicitly:

- layer: `L3_EXPERIENCE`;
- semantic domain: `CAPABILITY_KNOWLEDGE`;
- context section: `DATA`;
- instruction authority: false;
- world authority: false;
- world-candidate eligible: false;
- may authorize: false;
- may execute: false;
- may write store: false;
- coordinator required: true.

The pure P5 bridge produces the existing `MemoryPromotionDisposition`. Only
`MemoryCoordinator` may materialize it through `LifeShadowStore`. Generic P15
`memory_promotion.py` is not modified or made dependent on P5.

## Recall isolation

Recall requires exact equality for:

- principal scope hash;
- privacy scope hash;
- goal class;
- environment class;
- source revision family;
- exact source hashes.

`STALE`, `REVALIDATION_REQUIRED` and `RETIRED` experiences are excluded.
`PROBATION` is optional and remains lower priority than `STABLE`.

## P5 non-goals

P5 does not:

- inject experience into `WORLD_CONTEXT_SLOT`—that is P6;
- create mature World graph edges—P6/P11 evidence governs later use;
- execute or activate a composition—P7;
- evolve Tool or Method source—P8/P9;
- decommission Static Skill planning—P12;
- modify P19 or CompletionGate;
- modify generic P15 promotion thresholds.

## Gate

Before P6 begins, P5 must prove:

- G5-1 Capability Experience is DATA, never INSTRUCTION;
- G5-2 incomplete/unverified work cannot form positive experience;
- G5-3 failure cannot enter the positive pool;
- G5-4 source changes produce stale/revalidation intent;
- G5-5 cross-principal and cross-privacy recall is zero;
- one MemoryCoordinator and one LifeShadowStore remain authoritative;
- no Composition Core Store write exists;
- fixed-point results replay identically;
- source-authority and full regression pass on Ubuntu/Windows;
- P14 repository perception and P19-R2 Golden Gate remain green.
