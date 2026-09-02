# P4 — Proposal ABI, System Compiler, Tri-State Validator

Base: `main @ 3ce15faf8d463a8c5b5b3bfaa855b10ec94a1b2e`.

## Purpose

P4 implements the bounded reasoning-to-plan boundary:

`P2 Tool Capability World + P3 Skill Method World`
→ bounded candidate snapshot
→ model-authored `CompositionProposalV1`
→ deterministic system compiler
→ `CapabilityCompositionPlanV1`
→ conservative Tri-State Validator

P4 does not activate or execute the Plan. No Grant, Ticket, Runtime call,
Effect, Fact, P19 decision, Completion decision, or Memory write is produced.

## Candidate boundary

Candidate IDs are assigned by the system:

- Methods: `M01`–`M15`
- Actions: `A01`–`A30`

The candidate snapshot is content-addressed and explicitly:

- `may_authorize = false`
- `may_execute = false`

It binds exact P2/P3 snapshot hashes, Tool/Method descriptors, Tool source
revisions, Action versions, and the existing Action Registry identity. Candidate
selection is bounded; P4 does not expose the full Tool or Static Skill catalog to
the model.

## Small model ABI

The model may provide only:

- goal reference;
- selected Method candidate IDs;
- selected Action candidate IDs;
- ordered/DAG steps;
- dependency edges;
- output bindings;
- control-flow mode;
- optional rationale tags.

The raw proposal supports two provider tiers:

1. strict JSON using `proposal_schema = tiangong.composition-proposal.v1`;
2. a strict, bounded text DSL for providers without guaranteed structured output.

The parser rejects:

- duplicate or unknown fields;
- caller-supplied hash, risk, permission, version, Registry, Grant, Ticket, Fact,
  or Completion fields;
- unknown/hallucinated candidate IDs;
- Method candidates used as executable steps;
- inconsistent selected Action and step sets;
- invalid dependency identities;
- JSON numbers and non-finite values;
- oversized proposal output.

Exactly one externally produced repair proposal may be parsed. A second parse
failure is final and fail-closed; there is no unbounded retry loop.

## System compiler

The compiler, not the model, derives:

- request/run/generation and principal bindings;
- WorldState and context bindings;
- Method and Action `SourceRevisionRefV1` records;
- Action IDs and versions;
- dependency-graph hash;
- source-manifest hash;
- candidate/context/Registry binding hash;
- leaf permission union;
- expected effects and resource classes;
- leaf risk floor;
- composition risk and information-flow findings;
- verification intents;
- immutable Plan ID and Plan SHA-256.

The compiler cannot expand beyond Action candidates already present in the
existing `ActionRegistrySnapshot` and bound to the exact capability manifest.
Memory experience references remain empty until P5.

## Composition risk

P4 does not use only `max(leaf risk)`. It performs deterministic composition
checks, including:

- multiple writes;
- sensitive/credential/private source to external sink;
- privileged shell/Python result to external sink;
- destructive sequence with external sink;
- execution result flowing to an external sink.

A prohibited composition is raised to `A5`. P4 still does not execute it; the
Validator returns `PROVED_INVALID`.

## Tri-State Validator

The fixed result set is:

- `PROVED_VALID`
- `PROVED_INVALID`
- `UNKNOWN`

The Validator validates mechanically decidable properties only. It does not
claim to prove that natural-language output must satisfy the user's goal.

Checks include:

- Proposal, Candidate, Context, Registry, Plan and source hashes;
- compiler reconstruction equality;
- exact Action/version/manifest bindings;
- permission expansion;
- dependency type compatibility;
- unordered overlapping write sets;
- Action availability;
- verifier availability;
- idempotency/determinism uncertainty;
- composition risk and information flow.

An attacker cannot make a modified Plan valid merely by recalculating its Plan
hash: the Validator reconstructs the expected Plan from the original Proposal,
Candidate Snapshot, Compile Context, and current Action Registry.

### UNKNOWN policy

`PROVISIONAL_ALLOW + mandatory verification` is possible only when all of the
following hold:

- risk is A0/A1;
- every Action is read/verify only;
- no credential, private, shell, Python, destructive, external-send, or external-
  write class is present;
- all declared verification intents have an available verifier.

All other UNKNOWN states are `REJECT` in P4. In particular, A2+ UNKNOWN is never
silently treated as valid.

## Early evaluation

The repository includes a deterministic 24-task × 3-protocol-profile preflight:

- structured JSON;
- JSON requiring exactly one repair;
- strict DSL.

This produces per-profile parse, repair, compile, and validation metrics without
executing any Action.

The CI preflight is explicitly tagged `RECORDED_FIXTURE`. It proves protocol and
implementation behavior, not live performance of any named model. Live provider
outputs can be fed into the same harness under `LIVE_PROVIDER`; those measurements
must be reported separately before claiming cross-model production performance.

## Preserved authorities

P4 does not modify or replace:

- Total Gateway;
- Policy Engine;
- Action Manifest / Action Registry / ActionPermission;
- ExecutionTicket or CapabilityGrant;
- Omni Body or BodyRuntime;
- WorldState authority;
- Memory SSoT;
- P19 Verification Plane;
- CompletionGate;
- current Static Skill planner/context path.

## Gate

Before P5 begins, this branch must pass:

- P4 focused parser/compiler/validator tests;
- 24 × 3 recorded protocol preflight;
- source-authority Ubuntu/Windows;
- full-regression Ubuntu/Windows;
- P14 repository-perception validation;
- P19-R2 Verification Plane Golden Gate.

A live three-model benchmark is an evidence task, not something CI may fabricate.
Its results must identify exact provider/model revisions and be stored separately
from the recorded protocol preflight.
