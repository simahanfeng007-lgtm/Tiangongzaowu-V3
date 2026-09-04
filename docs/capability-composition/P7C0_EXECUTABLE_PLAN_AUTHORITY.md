# P7C.0 — Executable Composition Plan Authority

Base: `main @ 14f6946a9d994e70654b9d64ecfcaae9c74baba4`.

Status: design contract for P7C.0. P7C.0 is the restart-exact,
non-authorizing invocation-template authority. It persists an executable-plan
companion; it does **not** authorize, issue, grant, dispatch, execute, verify a
runtime result, or decide completion.

## 1. Purpose

P7B.2 durably records that one exact A0 composition is eligible to proceed to
the next Gateway stage. It deliberately does not record enough material to
reconstruct an invocation template. P7C.0 closes only that
restart/reconstruction gap by adding a system-compiled, content-addressed
`ExecutableCompositionPlanV1` beside the existing
`CapabilityCompositionPlanV1` and by persisting it in schema v31 of the
**existing** `GatewayStateStore`.

The authority chain after P7C.0 is:

```text
CapabilityCompositionPlanV1                 semantic/authority Plan
        +
system-owned invocation and value bindings
        ↓
compile_executable_composition_plan(...)
        ↓
ExecutableCompositionPlanV1                 immutable invocation template
        ↓
ONE existing GatewayStateStore v31 UoW
        ├─ P19 RegistrySnapshot
        ├─ P19 VerificationPlan
        ├─ P19 VerificationPlanActivation
        ├─ P7B LimitedCompositionActivationRegistrationV1
        └─ P7C.0 ExecutableCompositionPlanV1
```

This remains a restart-exact invocation template, not permission. The future
execution chain is still:

```text
active registration + exact executable plan
        → current Action Registry revalidation
        → existing Policy / Risk / A5 boundary
        → existing ExecutionTicket authority
        → existing Omni capability-grant authority
        → existing BackendClient
        → existing Omni Body / BodyRuntime
        → existing Effect head + FactLedger
        → existing P19 verification/readiness
        → existing CompletionGate
```

No new Gateway, Store, Policy engine, Registry, Ticket issuer, Grant issuer,
Runtime, Effect ledger, Fact ledger, verifier, readiness source, repair
authority, or Completion authority is introduced.

## 2. Why schema v30 cannot resume an execution

Schema v30 proves registration identity and eligibility, but it does not retain
the preimage of the composition Plan hash.

`composition_activation_registration` stores:

- `composition_plan_id` and `composition_plan_sha256`;
- activation, validation, WorldState, source-manifest, capability-manifest,
  Action Registry and P19 Registry hashes;
- allowed Action IDs and versions;
- request/run/generation, principal, lifetime and lifecycle fields;
- canonical `LimitedCompositionActivationRegistrationV1` JSON.

It does not store the `CapabilityCompositionPlanV1` JSON. Its registration JSON
contains the same identity/binding summary, not the missing Plan body. The
stored P19 `VerificationPlan` describes what must be checked after execution;
it is not an invocation plan.

There is a second, independent gap in the current Plan contract. A
`CompositionPlanStepV1` identifies a step, Action/version, dependencies,
expected-effect references and verification-intent references. The enclosing
Plan carries only `bindings_sha256`. It does not carry the actual target,
argument template, input selectors, result selectors, or downstream
step-output bindings required to invoke the Action.

Consequently, after restart v30 can prove that a hash was registered, but it
cannot answer any of these questions without guessing:

- which canonical arguments belong to a step;
- which workspace/target scope those arguments address;
- which immutable input object and JSON location supplies a value;
- how a downstream step obtains a value from an upstream result;
- which argument/result schema hashes were frozen at compilation;
- which exact P19 plan, activation and registry govern post-execution
  verification.

A SHA-256 digest is not a serialization format and its preimage cannot be
recovered. Re-running a model, reading the latest WorldState, or recompiling
against the latest Action Registry could produce different material while
retaining plausible IDs. All three are forbidden reconstruction paths.

The invariant is therefore:

> No durable invocation-template body, no eligible input to the later execution
> authority chain. P7C.0 never authorizes by itself. Never infer a hash preimage,
> replay the model, or substitute current mutable state.

## 3. Companion contract

P7C.0 adds the companion `ExecutableCompositionPlanV1`. An authoritative
persisted companion is accepted only when the existing Store bundle rebuilds
it with `compile_executable_composition_plan(...)` from the authoritative
inputs and matches the rebuilt object exactly. The legacy semantic Plan
remains embedded as `legacy_plan`; it is not replaced or weakened.

The companion freezes, at minimum:

- its own content-derived ID and SHA-256;
- the complete `legacy_plan` and its exact Plan ID/hash;
- request/run/generation and principal scope;
- composition activation ID/hash and bounded lifetime;
- the WorldState hash;
- source-manifest, capability-manifest and Action Registry hashes;
- top-level P19 VerificationPlan ID/hash, VerificationPlanActivation ID and
  RegistrySnapshot hash;
- one ordered `StepExecutionBindingV1` for every legacy Plan step;
- exact dependency and binding graph hashes;
- explicit non-authority flags.

The initial binding vocabulary is closed and typed:

- `LiteralValueBindingV1` — bounded canonical JSON fixed in the companion;
- `PlanInputValueBindingV1` — a selector over one immutable, hash-bound Plan
  input object;
- `StepOutputValueBindingV1` — a hash-bound reference to an output declaration
  of a declared upstream step;
- `WorkspaceBindingV1` — the workspace identity, canonical root and scope hash
  frozen for later policy evaluation.

Each `StepExecutionBindingV1` binds the legacy step identity and Action/version
to its candidate/source revision, permission digest, canonical argument
template, destination JSON pointers, tagged value bindings, static target,
input and result schema hashes, declared dependencies and output declarations.
Effect and verification-intent references remain only in the embedded legacy
Plan. P19 authority is bound at the executable-plan top level; P7C.0 does not
add or claim a per-step P19 entry mapping. The exact field set is part of the
contract identity; unknown fields fail strict validation.

Here, "typed" means explicit discriminant tags, canonical content hashes,
canonical JSON-pointer syntax and declared dependency topology. P7C.0 pins
argument/result schema digests but does not load schema bodies or prove that a
selector or materialized value conforms to those schemas. Dispatch-time schema
validation remains mandatory.

The embedded `ActionPermission` is also a hash-bound projection of the selected
Action Registry row, not a newly enforced authorization boundary. Its
`permission.path_policy`, including the `object_grant_only` value, is preserved
in the companion identity, but the existing Gateway Policy/Ticket/Grant path
does not currently execute that field as a target/argument authorization check.
P7C.0 therefore does not rely on it for protection. P7C.1 must enforce its
semantics against the materialized target and arguments immediately before
dispatch, or reject the dispatch closed.

All set-like fields are sorted and unique. Step order is deterministic. The
compiler must prove a one-to-one mapping with `legacy_plan.steps`; a caller may
not add, remove, reorder semantically, or change an Action through the
companion.

The companion carries explicit constants equivalent to:

```text
eligibility_only=true
authorizes=false
confirms=false
changes_risk=false
issues_ticket=false
issues_grant=false
may_execute=false
may_record_verification=false
may_complete=false
schema_compatibility_proven=false
dispatch_schema_validation_required=true
path_policy_enforced=false
dispatch_path_policy_validation_required=true
```

These values are part of the hashed body, not documentation-only promises.

P7C.0 also introduces explicit canonical-JSON resource limits:

- the complete stored `executable_plan_json` is at most 16 MiB
  (16,777,216 bytes);
- each dynamic JSON-bearing field (`inline_value`, literal `value`, or one
  step's `args_skeleton`) is at most 1 MiB (1,048,576 canonical bytes);
- recursive JSON depth is at most 64;
- the complete JSON tree contains at most 50,000 nodes.

The contract validates each bounded dynamic field and the complete stored plan
before identity derivation. The row codec independently enforces the 16 MiB
stored-payload bound, reparses canonical JSON, and reruns the contract checks.
The DDL also caps the complete stored payload at 16 MiB; Store integrity reruns
the codec. The 1 MiB bound does not apply independently to every collection in
the embedded legacy plan; that plan remains covered by the complete-plan,
depth, and node bounds. These are storage and reconstruction limits, not proof
that a value conforms to an argument or result schema body.

## 4. Dynamic `STEP_OUTPUT` semantics

`StepOutputValueBindingV1` is dynamic because its argument value does not exist
when P7C.0 compiles and registers the plan. P7C.0 persists the deterministic
reference topology, never a guessed value. Dynamic targets are outside the
first P7C.0 slice: every step target must be a statically resolvable canonical
string, and any `STEP_OUTPUT` or other dynamic target binding is rejected.

A `STEP_OUTPUT` reference contains:

- upstream `producer_step_id`;
- upstream `output_binding_id`;
- the exact `output_declaration_sha256`;
- its own canonical content digest.

The referenced `OutputDeclarationV1` separately pins its source kind, selector
(JSON pointer or ordinal) and value-schema digest. An `ArgumentSlotV1`
separately pins the downstream destination JSON pointer. P7C.0 does not encode
required/optional schema semantics.

Compile-time rules are fail-closed:

1. The source step exists exactly once.
2. The source step is an earlier step named by an explicit direct dependency
   edge; self, future and undeclared references are rejected. The embedded
   legacy graph separately remains acyclic.
3. Every JSON pointer is syntactically canonical and every schema digest is
   pinned. Array-index tokens accept only canonical ASCII decimal digits, so
   Unicode digit aliases cannot address one slot under distinct hashes. P7C.0
   does not claim schema-body compatibility.
4. One destination has one writer; overlapping JSON-pointer writes are
   rejected.
5. The binding cannot change Action ID/version, permission, risk, effect class,
   workspace root, or verification obligations.
6. The selected current primitive and permission must both remain A0
   read/verify, with no declared write/send/destructive effect and no
   shell/Python privilege in the first batch. P7C.0 does not infer secret
   classification from arbitrary JSON values.
7. The step target is a statically resolvable string; dynamic target slots are
   rejected in this first batch.

Future runtime resolution is also fail-closed. P7D may resolve a `STEP_OUTPUT`
only from an exact Gateway `FactLedger` execution fact whose canonical Effect
head is `SUCCEEDED` and whose request/run/generation, executable-plan hash,
step, Action/version and result identity all match. Missing facts, an ambiguous
or non-success Effect head, Fact/Effect disagreement, a missing JSON pointer,
schema mismatch, oversized material, or lineage drift blocks the dependent
step. It must not fall back to another attempt, the latest result, live
WorldState, model output, or a default value.

Resolution never mutates the immutable executable plan. P7D will persist the
resolved value/object reference and canonical resolved-arguments hash in its
existing execution/effect evidence before dispatch. That later persistence is
outside P7C.0.

The dynamic output value does not exist when P7C.0 applies its stored-JSON
limits. P7C.1/P7D must therefore validate every resolved output and materialized
argument against the selected backend's current byte, depth, node-count,
schema and transport limits before dispatch; a hash-bound declaration is not a
waiver of backend limits.

## 5. Compiler boundary

`compile_executable_composition_plan(...)` is a deterministic system compiler.
It accepts the authoritative legacy Plan, the already rebuilt P7A/P7B lineage,
system-owned binding material, and current frozen registry/schema evidence. It
does not accept model-authored authority fields.

The compiler must:

- validate the legacy Plan and every existing content identity;
- require exact request/run/generation, principal, WorldState and lifetime
  equality with the composition activation and limited registration;
- require the Action set and versions to equal both the legacy Plan and current
  `ActionRegistrySnapshot` subset;
- bind exact argument/result schema hashes from the registered Tool source,
  while explicitly recording that schema compatibility is unproved;
- validate canonical argument templates, explicit null-hole destinations,
  non-overlapping writes and complete hash-bound reference topology, without
  claiming schema-required-field coverage;
- validate the full dependency/binding graph including `STEP_OUTPUT` edges;
- bind the exact top-level P19 plan, activation and registry identities without
  claiming a new per-step P19 mapping;
- reject dynamic targets and require each first-batch target to be a statically
  resolvable canonical string;
- require `ObjectGrant` input `(object_id, revision)` pairs to be internally
  unique and scope-consistent;
  the authoritative Store bundle additionally accepts each input only when it
  exactly matches an AVAILABLE inbound attachment identity and scope. Checking
  the corresponding ObjectStore object's existence and bytes remains a P7C.1
  dispatch-time duty;
- recompute all nested and root identities from canonical JSON;
- independently re-enforce the first-batch A0/read-or-verify ceiling on both
  the source primitive and the persisted permission, including exact
  permission action/version identity and a fail-closed side-effect allowlist;
- emit no Policy decision, Ticket, Grant, Effect, Fact, P19 result, readiness or
  Completion decision.

Self-consistent caller-supplied hashes are insufficient. The Store bundle must
rebuild the expected limited registration and executable companion from
authoritative inputs and compare exact objects before writing.

## 6. Existing `GatewayStateStore` schema v31

P7C.0 advances `STORE_SCHEMA_VERSION` additively from 30 to 31 and adds one
STRICT companion table, provisionally named:

```text
composition_executable_plan
```

It remains owned by the existing `GatewayStateStore` connection, lock,
`BEGIN IMMEDIATE` unit of work, migration registry, integrity scan, lifecycle
and health check. There is no second SQLite file or repository abstraction.

The table must retain and constrain at least:

- `executable_plan_id` primary key and `executable_plan_sha256`;
- unique `registration_id` plus exact `registration_sha256`;
- composition activation and legacy Plan IDs/hashes;
- P19 VerificationPlan ID/hash;
- request/run/generation and principal scope;
- WorldState, source, capability, Action Registry and P19 Registry hashes;
- seal/first-write and expiry timestamps;
- canonical strict executable-plan JSON.

Every companion row has exactly one foreign-key parent in
`composition_activation_registration`, and each parent has zero or one
companion. The parent carries a durable companion-required marker: `0` means a
historical or independently registered P7B audit-only row; `1` means the P7C.0
bundle committed that parent only together with its required companion. A
marker-`0` parent has zero companions; a marker-`1` parent has exactly one. Its
lifetime and executability are derived from that existing registration and
current request generation; the companion does not invent a second lifecycle
authority. A schema-fingerprinted SQLite trigger makes the marker monotonic:
once set to `1`, it cannot return to `0`.

The companion row itself is append-only. A schema-fingerprinted identity
`BEFORE INSERT` guard blocks `INSERT OR REPLACE`; unconditional `BEFORE UPDATE`
and `BEFORE DELETE` guards reject post-seal mutation or removal, including a
caller that recomputes every nested and root content hash. The existing Store
transaction may insert or verify the exact row; it may not replace, rewrite or
garbage-collect the durable execution authority afterward.

The table and single public bundle API above are the reviewed implementation
surface. The one-existing-Store, parent-zero-or-one-companion, strict,
content-addressed and fail-closed semantics are mandatory.

## 7. One atomic registration UoW

The v31 boundary is
`GatewayStateStore.register_executable_composition_plan_bundle(...)`; it reuses
the existing P7B registration path inside one existing-Store transaction. That
transaction must perform the complete group:

```text
BEGIN IMMEDIATE
  validate/rebuild the expected P7A/P7B lineage
  compile/rebuild expected ExecutableCompositionPlanV1
  put/verify P19 RegistrySnapshot
  put/verify P19 VerificationPlan
  activate the exact P19 VerificationPlan
  insert/verify LimitedCompositionActivationRegistrationV1
  set its companion-required marker to 1
  for each matched attachment ObjectGrant, create/verify its request-wide
    REQUEST owner
  insert/verify ExecutableCompositionPlanV1 companion
  cross-check every duplicated lineage/hash column
COMMIT
```

Failure at any point rolls back the entire durable group. There is no
state in which a registration newly created **through the P7C.0 bundle** with
its companion-required marker set to `1` is visible without its exact
executable plan, or in which an executable plan is visible without the P19
plan, P19 activation and registration. The independent P7B registration path
remains valid for audit-only rows and leaves the marker at `0`.

The request-wide `REQUEST` owner is created or verified for every ObjectGrant
that exactly matched an AVAILABLE inbound attachment. It keeps the immutable
plan input owned for the whole request so future ObjectStore GC cannot classify
it as unowned. Its retention must be at least as long as every historical
executable-plan companion that references it; request terminal state alone is
not permission to delete that owner. This UoW binds attachment/grant/object
identity and ownership metadata only; P7C.0 does not read or attest the
ObjectStore bytes. Existence and byte verification remain mandatory at P7C.1
dispatch. If one accepted envelope pins multiple revisions with the same
`object_id`, they must still describe the same immutable object content and
scope and reuse one request-wide owner whose `owner_id` is exactly the
`request_id`; the compiler rejects a hash/size/MIME/scope disagreement within
that plan. Independently, the owner ledger enforces `object_id -> sha256` as a
Store-wide single-valued authority, not merely a per-request convention. The
public owner API, executable bundle validation and outbox-owner path share the
same pre-insert hash check; schema-fingerprinted insert/replace/update/delete
guards close direct SQL, delete-then-rebind and future-writer gaps, and
integrity scanning rejects divergent legacy rows during open/health
validation. The owner ledger does not promote size, MIME or scope to a
cross-request global metadata authority; exact ObjectStore bytes and scope
remain dispatch-time P7C.1 checks.

Authoritative compilation and insertion are inseparable inside the single
`register_executable_composition_plan_bundle(...)` UoW. There is no separate
method that accepts an already materialized executable plan. No
executable-plan write path relies on a module-readable token. Immediately
after compilation, the bundle path canonical-JSON round-trips the result
through strict contract validation before it changes the parent marker or
writes a row; caller-owned `model_copy` inputs are not trusted as already
validated.

Idempotency is content-based:

- an exact retry returns the first durable row and timestamp;
- a concurrent exact retry converges on the first writer;
- the same logical ID with different executable material is a collision;
- a different executable body under the same registration is a conflict;
- retry after expiry or generation supersession fails closed.

## 8. Restart, integrity and legacy v30 rows

Opening a Store with the exact supported v30 schema and valid migration history
applies the additive v31 migration. The migration must not fabricate executable
plans for existing rows. A v30 registration contains only hashes and cannot be
losslessly backfilled. Migrated v30 parents receive companion-required marker
`0`.

Legacy behavior is therefore explicit:

- a migrated v30 or independently registered P7B parent with marker `0` remains
  durable audit/eligibility evidence and expires monotonically under its
  existing lifecycle;
- it has no companion and is **not executable**;
- registration-based executable-plan lookup returns no plan for marker `0`, and
  active recovery omits it; both paths fail closed without claiming a typed
  missing-companion diagnosis;
- P7C.1/P7D must never treat the Plan hash, registration JSON or P19 Plan as the
  executable body;
- no model replay, latest-state rebuild or silent legacy fallback is allowed;
- eligibility for later execution requires a new v31 atomic registration
  produced from complete, still-authoritative inputs in the current generation.

Lookup, recovery and health scanning use the durable parent marker, not an
inferred creation date. A marker-`0` parent without a companion is
non-executable but remains readable: lookup returns no plan and recovery omits
it. A marker-`1` parent without its required companion is Store corruption in
historical lookup, active lookup, restart recovery and health scanning; it must
never be downgraded to `None` or silently omitted. Any malformed or cross-bound
companion is likewise Store corruption. Existing P7B historical reads, active
reads, recovery, expiry and exact bundle replay run the full companion parser
and cross-authority verifier for every registration they operate on when its
parent is marker-`1`; checking only that a companion row exists is not
sufficient. These operational paths use a registration-scoped companion query
so accumulated historical plans do not turn a single lookup into a full-table
scan. Store open/health validation separately scans every parent and companion.

Full integrity validation reparses strict JSON and checks canonical bytes,
nested hashes, duplicated columns, the unique companion-to-parent binding,
parent-marker cardinality, legacy Plan/activation/P19 lineage, lifetime,
Store-wide object content identity, canonical request ownership, and every
foreign key. Any discrepancy makes the Store unhealthy.

## 9. Explicit P7C.0 non-goals

P7C.0 does not:

- make a registration authorizing;
- evaluate Policy or risk at dispatch time;
- mint or consume an `ExecutionTicket`;
- mint or consume an `OmniCapabilityGrant`;
- call `BackendClient`, Omni Body or `BodyRuntime`;
- claim/start/finish an Effect;
- write the Gateway `FactLedger`;
- execute P19 or record PASS/FAIL;
- dispatch repair;
- call `CompletionGate` or mark a request complete;
- write capability experience Memory;
- enable A1+ or any external write/send;
- prove argument/result schema-body compatibility;
- accept a dynamic step target in the first P7C.0 batch;
- prove ObjectStore object existence or bytes for an `ObjectGrant` input;
- create a new Runtime or scheduler.

Imports and structural tests must prove these absences. Persisting an
invocation template changes recoverability, not authorization.

## 10. Staged continuation

### P7C.1 — Current-policy authorization binding

P7C.1 consumes only an active, unexpired, current-generation registration plus
its exact v31 companion. Immediately before each future dispatch it revalidates
the current Action Registry, loads the pinned schema body and validates the
materialized target and arguments, verifies each referenced ObjectStore object
exists with the exact granted bytes, resolves current target/impact evidence,
and uses the existing Policy → ExecutionTicket → Omni grant authorities. A
returned result remains subject to its separately pinned result-schema and P19
checks. P7C.1 must not derive permission from the companion's A0 label. It
proves exact request/run/generation/plan/step/Action/arguments/workspace/effect
binding and nonce/expiry behavior, and introduces no alternate Runtime.

### P7D.1 — Single-worker A0 execution seam

P7D.1 integrates through the existing `GatewayOrchestrationWorker`; it does not
use `ExecutionEngine` as a second scheduler. Durable step/effect progress reuses
the existing regenerative execution seam and canonical Gateway Effect/Fact
authorities. The first slice is one-step/linear A0 read-or-verify through
`BackendClient` → existing Omni Body → existing `BodyRuntime`. Cross-boundary
timeouts become `AMBIGUOUS`/reconcile-required and are never blindly replayed.
This stage is not production-complete until P7D.2 closes verification and
completion.

### P7D.2 — DAG, `STEP_OUTPUT`, P19 and Completion closeout

P7D.2 adds durable topological scheduling, exact `STEP_OUTPUT` materialization,
restart/resume/reconcile coverage, and all-leaf Effect/Fact closeout. Only after
all required leaf Effects have authoritative successful heads and exact
Gateway execution facts may the existing P19 executor derive readiness. The
existing `CompletionGate` remains the sole source of completed status. A0 first
batch disables any repair dispatch that would escape read/verify-only scope.
No channel delivery or other external send is part of this first batch.

## 11. Evidence gates for every stage

P7C.0, P7C.1, P7D.1 and P7D.2 each require focused contract, migration,
restart, tamper and fail-closed tests plus local generated-source sync/check
before remote CI. These focused/local results are evidence, but they are not
substitutes for the repository's nine required GitHub checks.

All nine checks must pass on the **same final candidate SHA** for every stage:

1. `source-authority-ubuntu-latest`
2. `source-authority-windows-latest`
3. `Architecture full-regression-ubuntu-latest`
4. `Architecture full-regression-windows-latest`
5. `p14-focused-ubuntu-latest`
6. `p14-focused-windows-latest`
7. `P14 full-regression-ubuntu`
8. `p19-r2-golden-ubuntu-latest`
9. `p19-r2-golden-windows-latest`

After the remote checks, head-match evidence must prove:

- local reviewed `HEAD` equals the pushed branch tip;
- branch tip equals the PR head SHA;
- every required check ran against that exact SHA;
- the worktree has no unreviewed change;
- no commit was added after the evidence run.

Any post-CI commit invalidates the evidence and requires local evidence refresh,
all nine remote checks, and head-match again. A stage is not complete or
merge-ready until its evidence row in `P7C_P7D_PROGRESS.md` is populated with
the exact command/run URL, result and SHA.
