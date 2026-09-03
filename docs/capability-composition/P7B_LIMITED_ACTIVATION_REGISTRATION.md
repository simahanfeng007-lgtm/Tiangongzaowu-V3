# P7B.1 — Limited Production Activation Registration

Base: P7A shadow activation after P6 `ONE WorldState / ONE WorldContext`.

## Purpose

P7B.1 converts a fully validated P7A shadow proposal into one durable,
content-addressed **limited-production eligibility registration**.

It does not execute the plan. It does not create a second Gateway or Store. It
does not mint a `PolicyDecision`, `ActionPermission`, `OmniCapabilityGrant`,
`ExecutionTicket`, `VerificationRecord`, `CompletionDecision`, or Runtime call.

The intended chain is:

`P7A ShadowCompositionActivationProposalV1`
→ exact eligibility revalidation
→ `LimitedCompositionActivationRegistrationV1`
→ existing Gateway State Store single-writer port
→ content-addressed registration receipt.

## Admission rules

Registration is accepted only when all of the following remain true at the
write boundary:

- P7A proposal hash is valid;
- proposal remains proposed-only and non-authorizing;
- activation hash is valid;
- P19 VerificationPlan has current full identity integrity;
- differential trace hash is valid;
- `limited_production_eligible = true`;
- no limited-production rejection code exists;
- exact action set is proved;
- every action is a current Action Registry member;
- source manifest is exact;
- Action versions are exact;
- verification bindings are complete;
- registration time is inside the activation lifetime;
- activation, VerificationPlan, trace and registries agree on request, run,
  generation, Plan, allowed Actions and hashes.

The first limited-production batch therefore remains the P7A subset:

- A0/A1 only;
- read/verify effects only;
- no Shell or Python;
- no credential access;
- no destructive/irreversible side effects;
- no external write or external send;
- complete deterministic P19 verification bindings.

## Authority boundary

Both registration and receipt explicitly carry:

```text
authorizes=false
may_execute=false
```

The registration additionally carries:

```text
activation_mode=LIMITED_PRODUCTION
eligibility_only=true
writer_authority=EXISTING_GATEWAY_STATE_STORE
```

A registration means only that the exact composition may proceed to the next
Gateway authorization stage. It cannot replace Policy, Confirmation, Ticket,
Grant, Runtime, Effect/Fact, P19 readiness, or CompletionGate.

## Single-writer seam

`ExistingGatewayActivationRegistrationPort` is a deliberately narrow port:

- read registration by content-addressed ID;
- compare an existing record for idempotency;
- write once with `expected_absent = true`;
- report a collision or CAS conflict explicitly.

The production implementation must be hosted by the existing
`GatewayStateStore` and its current unit-of-work discipline. P7B.1 does not
create an in-module dictionary, SQLite connection, table owner, or alternate
persistence service.

## Replay semantics

A repeated command with the same P7A proposal and the same gateway-recorded
write timestamp derives the same registration identity. It returns an
idempotent receipt and performs no second write. A different payload under the
same identity is rejected as a collision.

P7B.2 will bind the port to the existing Gateway Store migration/UoW surface
and define recovery behavior for process restart, expired activations and
write-race reconciliation.

## Gate before P7B.2

- focused P7B.1 tests pass;
- invalid, expired, ineligible and cross-request bindings fail closed;
- first write occurs exactly once;
- replay performs no second write;
- arbitrary writer authority is rejected;
- no Ticket/Grant/Runtime/Completion construction exists;
- Ubuntu/Windows source-authority and full regression pass;
- P14 repository perception remains green;
- P19-R2 Golden Gate remains green;
- no unresolved review thread remains.
