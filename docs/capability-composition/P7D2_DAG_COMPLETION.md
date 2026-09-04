# P7D.2 Durable A0 DAG and Completion Closeout

Status: implementation candidate under final local and remote verification.
This stage closes the limited A0 read/verify production loop; it does not
activate an automatic planner for ordinary chat and does not enable A1+,
external write, external send, shell, Python or destructive actions.

## One authority path

P7D.2 extends the existing path without adding another scheduler or truth
store:

```text
GatewayOrchestrationWorker
  -> persisted ExecutableCompositionPlanV1
  -> existing PolicyEngine / ExecutionTicket / CapabilityGrant
  -> CompositionStepExecutionCoordinator (worker-owned adapter)
  -> existing BackendClient / embedded Omni Body / BodyRuntime
  -> existing FactLedger, then Effect terminal head
  -> existing P19 VerificationPlanExecutor
  -> existing CompletionGate
```

The coordinator never writes the regenerative `execution_frontier`. Its DAG
frontier is a fixed-point projection of the immutable plan plus current Store
Effect heads and exact FactLedger batches. Live dispatch recovery is scoped to
the exact Effects in that plan; only worker startup may perform the existing
global recovery scan.

## Durable continuation and current authority

Before the parent backend boundary, the worker seals one insert-only
`NON_EXECUTABLE_CONTINUATION`. It contains no signature, nonce, Ticket, Grant
or direct Runtime right. After the in-memory parent authority disappears, the
only legal successor is a fresh current-epoch V2 Policy/Ticket/Grant chain
issued by the existing Omni authority from that sealed evidence.

- a live same-epoch V1 pre-start authorization is replayed byte-for-byte;
- an expired or stale pre-start authorization may be superseded once by a
  distinct attempt-2 Effect after Store proves that no dispatch permit,
  `STARTED`, nonce consumption, Fact or terminal result exists;
- attempt 3 is forbidden;
- `STARTED` is never replayed;
- a successful parent or child is recovered only from exact immutable Fact
  evidence and a resulting or already-current exact `SUCCEEDED` Effect head;
- startup stale-effect reconciliation excludes every `CLAIMED` or
  `SIDE_EFFECT_STARTED` parent bound to an executable composition plan, plus
  its child pipeline; only the dedicated durable adapter may resolve those
  heads from exact Fact evidence, so the generic watchdog cannot rewrite them;
- a sealed parent that is still `CLAIMED` with no Fact is proven not applied
  and closed, rather than re-signed or dispatched again;
- a crossed boundary without an exact Fact is `AMBIGUOUS` and requires
  reconciliation.

Plan registration and the parent Effect claim share an atomic cutoff: a plan
must exist before that request/run/generation obtains any execution Effect.
The worker reads the plan again immediately after claim, so either the plan is
observed or a late registration is rejected.

Before a durable parent-success resume performs projection or crosses P19,
Completion, Life or delivery, it re-reads and cross-binds the Request,
Execution and Delivery state snapshots. Only the forward-compatible shared-tail
matrix is accepted: request=`EXECUTING`, execution=`RUNNING`,
delivery=`NOT_PLANNED`; or execution=`SUCCEEDED` with an allowed forward
request/delivery state. A `DELIVERING` request cannot have `NOT_PLANNED`
delivery, and a `COMPLETED` request requires `CHANNEL_ACCEPTED` or `DELIVERED`.
A terminal conflict, identity mismatch or impossible backward combination
fails closed before any external authority is sampled.

## Result and dataflow authority

The verified source manifest, Action Registry and schema catalog compile as
one authority snapshot. Every action has an explicit result contract:
supported A0 actions have a concrete JSON result/value schema and every other
action is explicitly `OPAQUE`. `STEP_OUTPUT` materialization accepts only the
exact successful Effect, ticket, attempt, Fact tuple, content-addressed result
object and schema-bound JSON pointer declared by the immutable plan.

Every declared output is consumed by a downstream slot or a final output
alias. Final aliases are resolved only after every plan step has exact success
evidence; they form part of the terminal composition result rather than being
silently replaced by the earlier parent reply.

## P19 and Completion

Every persisted composition plan, including a one-step plan, is finalized.
Required completion Effects are the parent plus every DAG leaf. Lineage is the
parent plus every authorized child attempt, including a safely superseded
pre-start attempt. Duplicate, overlapping or substituted Effect and Fact
identities fail closed.

The P19 phase begins only after the all-step barrier. Composition uses one
stable completion clock derived from the latest exact parent/leaf Fact
observation, every leaf result finish time and the persisted execution
`SUCCEEDED` transition. On durable restart, an exact persisted readiness is
reused without invoking the executor. If the crash
occurred earlier, the executor reuses exact entry records at that clock and
only evaluates the missing suffix; an
exact persisted disposition prefix is likewise reused without consuming the
generation budget again. Identity, plan, registry, predicate, subject, hash or
clock drift fails closed. Composition uses the plural, exact-current
readiness/disposition authority even when every check passes and both
disposition sets are empty. Non-PASS evidence is persisted for every plan
entry, but this limited A0 stage never dispatches a repair. The established
non-composition repair path is unchanged.

Only `CompletionGate` may accept the desktop result. Its Store transaction
re-derives the current readiness, seals the decision and fences later
verification writes before crossing Life; this binds the complete required
Effect/Fact set before the request, Life terminal record and authenticated
desktop-pull result can become complete. A composition request cannot cross an
external delivery channel. Life is committed before desktop acceptance. The
composition Life payload uses the stable terminal core; repository evidence
remains under P19 and is not sampled as mutable Life enrichment. A durable
retry first recovers and validates the exact existing Life record and commit
hash. Unavailable, malformed or mismatched recovery fails closed and cannot
fall back to provider sampling. An unknown commit outcome leaves the Gateway
tail nonterminal so lease recovery can retry without producing a contradictory
FAILED request.

Once a composition CompletionDecision is sealed, restart may bypass the
generic generation revision cap and a disabled request-reexecution setting
only to recover the Life/desktop terminal tail; it never invokes a handler
again. If the request became terminal before its session queue head left
`ACTIVE`, worker startup idempotently retires that projection and then promotes
the next queued request.

## Bounded first batch

The immutable plan and continuation authority retain the frozen 60-second
window. The implementation does not silently extend it to the parent
watchdog's larger runtime budget. A frontier that cannot obtain its next
pre-start authorization inside that window fails closed. This is an explicit
P7D.2 first-batch limit, not a long-running DAG service-level claim.

P7D.2 supports at most 128 steps, at most two authorization attempts per step,
and therefore at most 257 Completion lineage Effects including the parent.
The final evidence matrix is tracked in `P7C_P7D_PROGRESS.md`; exact-head
GitHub checks will be recorded in immutable PR evidence after the candidate is
pushed.
