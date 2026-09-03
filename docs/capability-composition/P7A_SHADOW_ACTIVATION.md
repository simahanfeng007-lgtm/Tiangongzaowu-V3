# P7A — Shadow Activation Adapter

Base: `main @ ae601a9fd28b936f3f2d5e2b711669152697b271`.

## Purpose

P7A connects a validated `CapabilityCompositionPlanV1` to the existing Action
Registry and P19 verifier RegistrySnapshot in **shadow only**.

It emits:

- a proposed `CompositionActivationContractV1`;
- a proposed current-version P19 `VerificationPlan`;
- exact allowed Action IDs and versions;
- a deterministic differential trace against the legacy allowed Action set;
- a first-batch limited-production eligibility assessment.

It does not persist or activate any of them.

## Input authority

P7A accepts only:

- a valid system-compiled Composition Plan;
- a valid `PROVED_VALID` validation result, or `UNKNOWN + PROVISIONAL_ALLOW + mandatory_verification`;
- the existing valid `ActionRegistrySnapshot`;
- the existing valid P19 `RegistrySnapshot`;
- system-resolved verification bindings;
- bounded issue/expiry times;
- an optional legacy Action set used only for differential reporting.

A `PROVED_INVALID`, rejected UNKNOWN, mismatched Plan, stale validation, malformed
hash, source drift, unregistered Action, version mismatch, incomplete verifier
binding, or Registry mismatch fails closed.

## System-resolved Verification binding

The model never names the verifier.

`build_system_verification_binding(...)` receives an already-authoritative
`AcceptancePredicate`, subject identity and evaluation phase. It searches the
current P19 RegistrySnapshot and requires exactly one verifier that:

- has a valid descriptor hash;
- is `L0_DETERMINISTIC`;
- is deterministic;
- supports the predicate type;
- supports the subject kind.

The selected verifier ID/version and RegistrySnapshot hash are then frozen into
`SystemVerificationBindingV1`.

Every `plan.verification_intents` item must have exactly one binding. The adapter
constructs complete `VerificationPlanEntryV2` records and one current-version
`VerificationPlan`. It never creates a `VerificationRecord`, never invokes a
verifier, and cannot output PASS.

## Action and source checks

The proposed allowed set must equal all three of:

- the sorted unique Action IDs used by Plan steps;
- `plan.permission_requirements`;
- the exact set of Action source revisions carried by the Plan.

For every Action, P7A requires:

- presence in the existing Action Registry;
- exact Action version equality across Plan step, source revision and Registry;
- exact capability-manifest equality;
- `source_kind = TOOL_ACTION`;
- exact recomputation of the Plan source-manifest hash.

The model cannot expand permissions by changing the proposed Action list.

## Proposed activation

The embedded `CompositionActivationContractV1` binds:

- Plan ID/hash;
- request/run/generation;
- principal scope;
- WorldState hash;
- source manifest;
- capability manifest;
- allowed Action IDs and versions;
- proposed VerificationPlan ID;
- issue/expiry time;
- deterministic activation hash.

The outer shadow proposal also binds:

- validation hash;
- Action Registry hash;
- P19 RegistrySnapshot hash;
- VerificationPlan hash;
- differential trace hash.

The complete output is fixed as:

- `proposed_only = true`
- `persistence_allowed = false`
- `authorizes = false`
- `confirms = false`
- `changes_risk = false`
- `may_execute = false`

## Differential trace

The trace records:

- planned Action set;
- proposed Action set;
- legacy comparison set;
- additions and removals;
- exact set/Registry/source/version/verifier checks;
- limited-production eligibility and rejection reasons;
- `persisted = false`;
- `authorizes = false`;
- `may_execute = false`.

It is evidence for P7B/P7C cutover analysis, not an execution credential.

## Limited-production eligibility

P7A computes, but does not enact, the initial limited-production eligibility
specified by the execution master:

- composition risk A0/A1;
- every Action risk A0/A1;
- read/verify effect only;
- no Shell;
- no Python;
- no credential read;
- no destructive or irreversible effect;
- no external write/send;
- every VerificationPlan entry required and identity-valid.

A2+ and write/execute compositions may still be observed in shadow, but are
marked ineligible for the first limited-production batch.

## Preserved authorities

P7A does not import or call:

- `GatewayStateStore`;
- `put_verification_plan`;
- `ExecutionTicket` construction;
- `OmniCapabilityGrant` construction;
- `VerificationPlanExecutor`;
- `VerificationRecorder`;
- Omni Body or BodyRuntime;
- P19 readiness/failure/repair execution;
- CompletionGate.

No P19 frozen contract or implementation file is modified. The current Static
Skill path remains the production path.

## P7A gate

Before P7B begins, P7A must prove:

- proposed Action set is an exact Registry subset;
- no permission expansion;
- Action versions and source revisions are exact;
- capability/source/Action Registry/P19 Registry hashes are exact;
- every Verification Intent has one complete deterministic P19 entry;
- missing/ambiguous verifier resolution fails closed;
- validation must be valid or explicitly provisional UNKNOWN;
- proposed output has no authorization, persistence or execution power;
- the adapter cannot emit PASS or write any P19 record;
- A0/A1 limited eligibility is reported independently from shadow proposal
  generation;
- source-authority/full-regression and P19 Golden Gate remain green on Ubuntu
  and Windows.
