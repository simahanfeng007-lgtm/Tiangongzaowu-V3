# P7B.2 — GatewayStateStore Registration, UoW, Restart and Expiry

Base: `main @ 14f9ef400786d01b65fd1cb495ea42aaa55473e1`
(P7B.1 merged).

## Purpose

P7B.2 makes the P7B.1 A0-only limited-production eligibility registration
durable inside the **existing** `GatewayStateStore`.

It does not authorize or execute a composition. It does not create a second
Store, connection owner, transaction manager, Gateway, Registry, Policy engine,
Ticket issuer, Grant issuer, Runtime, verifier result, readiness result, or
Completion authority.

The atomic write is:

```text
P7A authoritative shadow proposal
        ↓
P7B.1 limited registration compile
        ↓
ONE GatewayStateStore UoW
        ├─ P19 RegistrySnapshot
        ├─ P19 VerificationPlan
        ├─ P19 VerificationPlanActivation
        └─ CompositionActivationRegistration
```

Any failure rolls back the complete group.

## Schema revision

`GatewayStateStore` advances additively from schema v29 to v30.

The new STRICT table is:

```text
composition_activation_registration
```

It stores:

- stable logical registration ID;
- immutable P7B.1 registration JSON and SHA-256;
- P7A shadow proposal and differential trace hashes;
- composition activation ID/hash;
- composition plan ID/hash;
- P19 VerificationPlan ID/hash;
- exact P19 VerificationPlanActivation ID;
- Validation hash and mode;
- Request/Run/Generation and Principal scope;
- WorldState, source manifest, capability manifest, Action Registry and
  Verification Registry hashes;
- exact Action IDs and versions;
- issue, expiry and first-write timestamps;
- lifecycle (`ACTIVE` or `EXPIRED`);
- lifecycle SHA-256.

Unique constraints enforce:

- one row per logical registration;
- one row per composition activation;
- one registration per Request/Run/Generation.

The row has foreign keys to the existing P19 VerificationPlan and
VerificationPlanActivation tables.

## Write-boundary revalidation

The Store accepts a registration only when:

- the P7B.1 registration identity is valid;
- the first-write timestamp equals the canonical registration timestamp;
- the current Request/Run/Generation is still the active Gateway generation;
- the referenced P19 RegistrySnapshot exists and has a valid identity;
- the referenced VerificationPlan exists, has a valid identity and exact
  Request/Run/Generation/Registry binding;
- the active VerificationPlanActivation exists and matches the same Plan,
  hash and RegistrySnapshot;
- the registration remains inside its activation lifetime;
- no prior row reuses its registration, activation or lineage identity with
  different authority content.

Self-consistent JSON or recalculated hashes are insufficient if the referenced
P19 or Gateway authorities do not exist in the same Store.

## Idempotency and concurrency

The P7B.1 registration ID is independent of retry wall-clock time.

- identical retry: returns the first durable row, no second insert;
- retry with a later timestamp: preserves the first writer timestamp and row
  hash;
- same logical identity with different authority content: conflict;
- two connections racing: SQLite `BEGIN IMMEDIATE` serializes the existing UoW;
  one writer creates the row and the other converges on the same durable row.

No in-memory production dictionary participates in the decision.

The `GatewayStateStore` object itself is deliberately **not** the public P7B.1
registration port. The bundle method first rebuilds the expected registration
from P7A/P7B.1 authorities, creates a transaction-local private port bound to
that exact object, and presents that port to the registrar. The underlying
Store insert is private and requires a module-local unforgeable identity token.
Calling the Store directly therefore cannot bypass the authoritative rebuild.

## Restart and expiry

Opening the existing Store performs additive migration, expires every durable
registration whose `expires_at_ms <= now_ms`, and then runs the normal full
health check.

Expiry is monotonic:

```text
ACTIVE → EXPIRED
```

An expired row is not deleted and cannot be reactivated by replay. It remains
available as audit evidence. Active reads additionally require the current
Gateway generation binding; a released, cancelled or superseded generation
fails closed.

## Integrity scan

Gateway Store health validation re-parses every registration using strict
Pydantic validation, verifies canonical JSON, all duplicated SQL columns,
registration identity, lifecycle digest, P19 RegistrySnapshot,
VerificationPlan, VerificationPlanActivation and exact lineage bindings.

Any mismatch marks the Store unhealthy.

## P19 compatibility revision

Because `store.py` is part of the frozen P19 authority surface and schema v30
adds a P19-bound activation row, P7B.2 explicitly advances:

```text
VERIFICATION_PLANE_VERSION = 1.1
```

The freeze manifest is regenerated explicitly. P19 PASS semantics, verifier
implementations, readiness derivation, repair policy and CompletionGate are not
changed.

Historical migration fixtures are also advanced explicitly. A current v30 test
database first removes the additive composition-registration table and its
migration record, returns to exact v29, and then follows the existing historical
downgrade chain. Current-schema assertions now require v30; no older fixture is
silently redefined.

## Authority boundary after P7B.2

A durable registration remains:

```text
eligibility_only=true
authorizes=false
confirms=false
changes_risk=false
may_execute=false
```

P7C may consume only an `ACTIVE`, non-expired, current-generation registration
and must still pass through the existing Policy → ExecutionTicket →
CapabilityGrant chain. Runtime execution remains outside P7B.2.

## Final closure evidence

Final production head closes the direct-Store write bypass by retaining only a
transaction-local registration port and a token-guarded private Store sink. The
rollback regression injects failure at that private sink and proves that the P19
RegistrySnapshot, VerificationPlan, VerificationPlanActivation and composition
registration still roll back as one existing-Store UoW.

The closure run completed generated-source synchronization and exact mirror
checking, compiled the authoritative and generated Gateway surfaces, and passed
68 focused P7B.1/P7B.2/P19 tests. All one-shot repair scripts and workflows were
removed before the final branch commit; none is part of the P7B.2 production
diff.
