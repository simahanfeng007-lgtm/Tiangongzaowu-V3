# P7 — Production Activation through the Existing Authority Chain

Base: P6 implementation head `b776cfbea078af5c959516a1a8abba263d4969c6`, after the P6 PR passed its protected gates and was merged to `main`.

## Objective

P7 begins the production execution seam for a system-compiled
`CapabilityCompositionPlanV1`:

`ONE WorldState / ONE WORLD_CONTEXT_SLOT`
→ model `CompositionProposalV1`
→ system Plan Compiler
→ Tri-State Validator
→ **Total Gateway Composition Activation Gate**
→ existing Policy / ExecutionTicket / CapabilityGrant chain
→ existing Omni Body / BodyRuntime
→ existing Effect / Fact return
→ existing P19 Verification Plane
→ existing CompletionGate

P7 does not introduce a second execution authority. The first P7 slice freezes
activation and step-level requests inside `src/total_gateway`; actual execution
must still be delegated to the already-authoritative Gateway port.

## Activation preconditions

An activation is created only when all of the following remain current:

- Plan SHA-256 is valid;
- Validation SHA-256 is valid;
- Validation is bound to the same Plan ID and Plan SHA-256;
- validation result is either:
  - `PROVED_VALID`, or
  - `UNKNOWN + PROVISIONAL_ALLOW + mandatory_verification=true`;
- `PROVED_INVALID`, `UNKNOWN + REJECT`, or malformed validation cannot activate;
- request ID, run ID, generation and principal scope exactly match;
- WorldState reference and SHA-256 exactly match;
- Action Registry SHA-256 is valid;
- capability manifest SHA-256 matches the current Action Registry;
- Plan permission requirements equal the exact union of step Action IDs;
- every step Action/version/source manifest matches an existing
  `ActionPermission`;
- composition risk is not A5;
- activation time does not predate Plan or Validation evidence;
- activation expiry is finite and monotonic.

The activation ID and digest are system-derived. A model cannot mint or modify
an activation.

## Frozen P1 activation ABI

P7 reuses the already-frozen `CompositionActivationContractV1`. It does not
invent a replacement contract. The constructor maps only system-derived values
into the frozen ABI, rejects any unmapped required ABI field, then recomputes
and verifies the activation digest.

The P7 `CompositionActivationEnvelopeV1` additionally binds:

- frozen activation contract and hash;
- Plan ID/hash;
- Validation hash;
- Action Registry hash;
- capability manifest hash;
- WorldState hash;
- exact allowed Action IDs;
- mandatory-verification state;
- issue and expiry times.

The envelope itself has `may_execute=false` and `model_generated=false`.

## Step authorization

A Plan step can be emitted only when:

- the Activation and frozen contract hashes are valid;
- Activation, Plan, Validation, Registry, Manifest and WorldState bindings agree;
- the Activation is currently valid;
- the step exists in the Plan;
- its Action is in the activated permission union;
- every declared dependency step is completed;
- the current `ActionPermission` matches Action ID/version/manifest;
- Action arguments are represented by a canonical SHA-256;
- object-grant references are canonical and explicit;
- the step expiry is inside the Activation expiry.

`CompositionStepAuthorizationV1` is deliberately non-executing. It states:

- `requires_existing_policy_ticket_grant=true`;
- `may_execute=false`;
- `model_generated=false`.

It is not an `ExecutionTicket` and is not a `CapabilityGrant`.

## Existing Gateway delegation

`dispatch_via_existing_gateway(...)` accepts only an implementation of the
existing-Gateway step port. Before delegation it verifies:

- step-authorization hash;
- Action Registry continuity;
- canonical arguments hash.

The adapter invokes exactly one method:

`authorize_and_execute_composition_step(...)`

That method belongs to the existing Total Gateway integration seam and must
perform the ordinary Policy → Ticket → Grant → Omni Body path. P7 contains no
direct Runtime, subprocess, SQLite, Tool handler, CompletionDecision, Ticket, or
Grant implementation.

## Failure policy

P7 fails closed on:

- cross-request or cross-run activation;
- generation mismatch;
- principal-scope mismatch;
- WorldState drift;
- capability-manifest or Registry drift;
- validation/Plan mismatch;
- permission expansion;
- Action/version/source mismatch;
- A5 composition;
- incomplete dependencies;
- expired activation or step authorization;
- argument hash mismatch;
- missing existing-Gateway port.

No fallback to direct Tool execution exists.

## Current P7 scope and remaining work

This first P7 slice establishes the activation authority boundary and
step-delegation contract. Before P7 can be declared complete, the PR must also:

- bind the port to the repository's concrete existing Policy/Ticket/Grant
  implementation rather than a parallel executor;
- prove Action results return through the existing Effect/Fact ingress;
- prove P19 receives the Plan-bound verification obligations;
- prove CompletionGate is the only terminal completion authority;
- add restart/resume and expiry tests;
- pass all protected Ubuntu/Windows, P14 and P19-R2 gates.

P8 source compilation and P9 Skill source compilation are not part of P7.
