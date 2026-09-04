# P7D.1 Single-worker A0 Composition Execution

Status: implementation candidate. P7D.1 is intentionally not a production
completion claim; P7D.2 must still close DAG, `STEP_OUTPUT`, P19 and all-leaf
completion.

## Authority path

There is no second scheduler, Gateway, Runtime, Store, Policy engine, Fact
ledger or Completion authority. The only production path is:

```text
GatewayOrchestrationWorker
  -> CompositionStepExecutionCoordinator
  -> BackendClient
  -> CompositionBackendExecutionTransport
  -> EmbeddedBackendRuntime private composition route
  -> v3.jineng.jirou_ceng._run_omni_body_tool
  -> run_omni_body
  -> BodyRuntime
```

The coordinator is an adapter owned by the existing worker. It does not run an
idle/global scan and is invoked only for the exact request/run/generation that
the worker currently owns. `ExecutionEngine` is not used as a competing
scheduler or result authority.

## Dispatch sequence

P7D.1 supports exactly one static root step. It rejects dependencies and
`STEP_OUTPUT`; those belong to P7D.2.

1. The normal parent backend call returns and its Omni authority is
   unregistered, releasing the embedded core lock.
2. The Gateway records the exact parent execution Fact batch.
3. The Gateway commits the parent `SUCCEEDED` Effect.
4. The worker resolves the immutable executable-plan obligation for the exact
   request/run/generation. This lookup does not hide expired plans.
5. The coordinator reloads and verifies the receipt, plan, current Registry,
   schema, permission, current object bytes, target snapshot, parent
   Effect/Fact, current trust bundle, grant and deterministic execution
   binding.
6. The child Effect claim is committed.
7. One Store transaction verifies the still-current generation owner/lease,
   active generation fence, open global action fence and exact successful
   parent Effect, then commits `DISPATCH_PERMIT` and `STARTED`.
8. `BackendClient` consumes the ticket nonce and calls only the private
   composition transport.
9. Immediately before Omni Body, the private embedded route calls the pinned
   Gateway authorizer. The Store rechecks the immutable P7C receipt, exact
   `STARTED` attempt and ticket nonce consumer, then consumes the grant nonce
   in the same transaction. The route cannot self-describe its own authority.
10. The transport invokes Omni Body directly; it does not enter
    chat/simple-chain/model routing and does not mint a grant.
11. The Gateway commits the child Fact batch before the child terminal Effect.
12. Only a successful child outcome permits the legacy parent path to continue.
    Failed or ambiguous composition outcomes terminalize the aggregate request
    before artifact, Life, outbox or delivery commits.

## Fail-closed rules

- No executable plan means the request has no composition obligation. A plan
  that exists but has a missing, expired, corrupt or inactive receipt is an
  execution failure, not the absence of work.
- An unavailable embedded executor blocks both new receipt issuance and any
  persisted plan obligation.
- A pre-dispatch rejection calls no handler and consumes no ticket nonce. A
  direct call to the private embedded route without the worker-installed
  authorizer also calls no handler, even if the caller supplies structurally
  valid signed-looking inputs.
- Handler admission is two-stage and one-shot: `BackendClient` must have
  consumed the exact ticket nonce for this dispatch, then the pinned route
  authorizer consumes the exact grant nonce. A replay fails before the handler.
- A non-empty opaque target is legal. A raw host path remains forbidden by the
  P7C object-grant-only path policy and the live schema validator.
- Only A0 permissions with `read`/`verify` effect, no shell, no Python, no
  confirmation and `none`/`read` side effects are accepted.
- The persisted runtime receipt retains `fact_kernel_enabled=true`. P7D derives
  a detached execution copy with `fact_kernel_enabled=false`, leaving the
  Gateway FactLedger as the sole machine-fact writer.
- The timeout wrapper begins only after the durable permit and ticket nonce
  boundary. Its process-wide bounded watchdog slot rejects saturation before
  another handler can start. A timeout is `AMBIGUOUS`; both the watchdog slot
  and the Store inflight permit remain occupied until the in-process call
  actually exits, and a late result is discarded without racing a Fact or
  terminal Effect. On process restart, startup recovery may release an orphaned
  terminal composition permit because no handler from the exited process can
  still be running.
- `STARTED` is never replayed. Restart recovery accepts an exact immutable Fact
  for the same effect/ticket/action/attempt/scope or closes the Effect
  `AMBIGUOUS` when no Fact exists.
- An already-terminal child is returned as an outcome. `FAILED_FINAL` and
  `AMBIGUOUS` are never collapsed into `None`.
- A stale `CLAIMED` child has not crossed the dispatch permit. If its current
  preflight or receipt window is no longer valid, it is atomically closed
  `FAILED_FINAL` rather than left as an orphan.

## Current execution manifest

The model-facing capability manifest and Action Registry use different action
version projections. `compile_composition_execution_manifest` verifies the
model manifest, Registry and schema catalog as one source-bound set, then emits
a real `CapabilityManifest` whose versions and argument schemas match current
Registry permissions while retaining the model manifest's provider, result
schema, availability and resource limits. Policy, child Ticket, Omni grant and
`BackendClient` all bind to that compiled manifest hash; the raw source-file
hash is drift evidence only.

## Restart boundary

The worker first recovers composition `STARTED` Effects through the exact Fact
adapter, then recovers parent Fact-before-Effect crash windows, and only then
runs the generic conservative recovery with the composition pipeline excluded.
Any composition/parent/generic recovery corruption prevents the worker from
starting. Wall-clock rollback cannot create a terminal observation timestamp
earlier than the durable start boundary.

P7D.1 proves the child handler is never called twice across its single-step
`STARTED` crash windows. Full request continuation after process restart,
multi-step frontier replay and reauthorization are P7D.2 obligations.

## Activation status and limits

P7C.1 exposes the current registration-to-authorization adapter, but this
repository still has no automatic production planner/issuer that creates a
composition registration from ordinary chat. Therefore P7D.1 consumes an
explicitly registered and authorized obligation; it does not claim that normal
chat is already routed through composition by default.

P7D.1 does not implement:

- multi-step topological scheduling;
- dynamic `STEP_OUTPUT` resolution;
- P19 readiness execution for the composition leaf set;
- repair dispatch;
- authoritative all-leaf CompletionGate closeout;
- production enablement of A1+, external writes or external sends.

These remain hard gates for P7D.2.
