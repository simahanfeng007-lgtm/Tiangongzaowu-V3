# P7B.1 — Limited Production Activation Registration

Base: P7A shadow activation after P6 `ONE WorldState / ONE WorldContext`.

## Purpose

P7B.1 converts a fully validated P7A shadow proposal into one durable,
content-addressed **limited-production eligibility registration**.

It does not execute the plan. It does not create a second Gateway or Store. It
does not mint a `PolicyDecision`, `ActionPermission`, `OmniCapabilityGrant`,
`ExecutionTicket`, `VerificationRecord`, `CompletionDecision`, or Runtime call.

The chain is:

`P7A ShadowCompositionActivationProposalV1`
→ rebuild P7A from authoritative Plan / Validation / registries / current scope
→ exact equality with the submitted shadow object
→ independently enforce the first-batch A0 ceiling
→ `LimitedCompositionActivationRegistrationV1`
→ existing Gateway State Store single-writer port
→ content-addressed registration receipt.

## Authoritative rebuild boundary

A self-consistent hash is not authority. Before registration, P7B.1 reruns the
P7A builder from all authoritative inputs:

- `CapabilityCompositionPlanV1`;
- `CompositionValidationResultV1`;
- current `ActionRegistrySnapshot`;
- current P19 `RegistrySnapshot`;
- system-resolved `SystemVerificationBindingV1` set;
- current WorldState hash;
- current principal-scope hash;
- the proposal's bounded issue/expiry interval and legacy comparison set.

The rebuilt P7A proposal must be exactly equal to the submitted proposal. A
caller that changes WorldState, principal, Plan, validation, Action versions,
registries, verifier bindings, lifetime, or any nested record and merely
recomputes public hashes is rejected with
`limited_registration.shadow_rebuild_mismatch` or
`limited_registration.authoritative_rebuild_failed`.

## First-batch ceiling

The rollout master fixes the initial Limited batch at **A0 only**. P7B.1 applies
that ceiling independently from P7A shadow telemetry. A P7A trace may represent
an A1 candidate for a future second batch, but it is not registrable now.

Every first-batch registration therefore requires:

- `plan.risk_floor = A0`;
- `plan.composition_risk = A0`;
- every current `ActionPermission.effective_risk = A0`;
- effect is `read` or `verify`;
- no Shell or Python;
- no credential read;
- no destructive or irreversible effect;
- no external write or external send;
- complete deterministic P19 verification bindings.

Any A1 or higher plan is rejected with
`limited_registration.first_batch_a0_only` unless it was already rejected by a
stricter P7A eligibility rule. A1 admission belongs to the second rollout batch
and requires a separate audited promotion; it cannot be enabled by model input,
Context, or a rehashed proposal.

## Admission rules

Registration is accepted only when all of the following remain true at the
write boundary:

- P7A proposal hash is valid;
- the authoritative P7A rebuild is byte-equivalent;
- proposal remains proposed-only and non-authorizing;
- activation hash is valid;
- P19 VerificationPlan has current full identity integrity;
- differential trace hash is valid;
- `limited_production_eligible = true`;
- no limited-production rejection code exists;
- the independent A0 first-batch ceiling passes;
- exact action set is proved;
- every action is a current Action Registry member;
- source manifest is exact;
- Action versions are exact;
- verification bindings are complete;
- registration time is inside the activation lifetime;
- activation, VerificationPlan, trace and registries agree on request, run,
  generation, Plan, allowed Actions and hashes.

## Registration identity and row integrity

The logical `registration_id` is stable for one system-derived composition
activation and its request/run/generation lineage. It does **not** depend on the
wall-clock time of a retry.

The immutable `registration_sha256` covers the complete stored row, including
the first successful `registered_at_ms`. Consequently:

- retrying the same activation later resolves to the same logical ID;
- the first Gateway Store writer owns the persisted timestamp and row hash;
- a retry compares stable authority content while preserving the first row;
- reusing the same activation identity with different authority content is an
  explicit collision, not a second registration;
- the registration records the exact P7A proposal hash and differential-trace
  hash for audit and restart reconstruction.

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

- read registration by stable content-derived ID;
- compare existing stable authority content for idempotency;
- write once with `expected_absent = true`;
- reconcile a write race by rereading the first committed row;
- report a collision or CAS conflict explicitly.

The production implementation must be hosted by the existing
`GatewayStateStore` and its current unit-of-work discipline. P7B.1 does not
create an in-module dictionary, SQLite connection, table owner, or alternate
persistence service.

## Replay semantics

A repeated command with the same authoritative inputs derives the same logical
registration ID even when the retry arrives at a later valid timestamp. It
returns an idempotent receipt, references the first persisted row hash, and
performs no second write. A different authority payload under the same logical
identity is rejected.

A retry after activation expiry remains rejected. Registration eligibility does
not revive an expired activation.

P7B.2 binds the port to the existing Gateway Store migration/UoW surface and
adds restart recovery, expiry state, lookup by activation/lineage, and
transactional write-race enforcement.

## Gate before P7B.2

- focused P7B.1 tests pass;
- A1 shadow eligibility is explicitly rejected by the first-batch boundary;
- rehashed WorldState/principal/Plan/registry/verifier forgeries fail closed;
- invalid, expired, ineligible and cross-request bindings fail closed;
- first write occurs exactly once;
- replay at a later valid timestamp performs no second write;
- write races preserve the first committed row;
- same logical ID with different authority content is rejected;
- arbitrary writer authority is rejected;
- no Ticket/Grant/Runtime/Completion construction exists;
- Ubuntu/Windows source-authority and full regression pass;
- P14 repository perception remains green;
- P19-R2 Golden Gate remains green;
- no unresolved review thread remains.
