# P13.2 Active World Cognition Production Closure

## Result

P13.2 connects the active cognition path to the already existing production
components without adding a Runtime, Gateway, World Understanding ingress,
tool executor, scheduler thread, or parallel world-state database:

`committed WorldState -> KnowledgeGap -> WorldInquiry -> existing Self-Will model bridge -> existing Total Gateway worker -> Policy / outer Ticket -> existing Omni Grant -> existing Runtime / Tool -> ToolResult post-commit -> same WorldUnderstandingFacade.accept() -> next WorldState -> InquiryOutcome`

## Production boundaries

- `WorldInquiry` and `AutonomousIntent` remain zero-authority proposals and are
  never represented as a user instruction.
- Only a fixed read-only observation set is eligible. Writes, deletion, shell,
  messaging, login and arbitrary execution are rejected before authorization.
- Total Gateway independently derives impact, applies Policy including the A5
  hard block, signs the existing outer execution ticket, and registers the
  existing inner Omni grant.
- Active inquiry work is consumed only when the existing Gateway worker has no
  user request to claim. No new thread or task loop is created.
- Tool reality returns through the native post-commit observer and the single
  World Understanding ingress. A reality transaction closes its originating
  inquiry and cannot synchronously generate a successor.
- Inquiry lineage and outcomes are stored as bounded, hashed records inside the
  existing `WorldStateStore` index. This provides restart idempotence without a
  second state system. Repeated zero-gain outcomes use the existing P12
  exponential backoff policy.

## Verification performed in the implementation workspace

- P13.2 focused tests cover admission, restart persistence, idempotence,
  zero-gain backoff, existing-worker dispatch, read-only enforcement, causal ID
  propagation, success Reality closure and anti-self-excitation.
- The complete world-understanding selection passed after the implementation.
- Generated-source synchronization/check and the source verifier quick gate
  passed.
- The repository-wide pytest run had one unrelated release-afterPack fixture
  failure because `app/runtime/python312/python.exe` is absent in this source
  checkout; all other tests and subtests passed. This report does not relabel
  that environment-dependent release test as green.

## Operational note

The active path requires a configured Self-Will model and a live embedded Total
Gateway execution assembly. Missing model, unavailable Gateway, exhausted
queue, Policy rejection, A5, timeout, and tool failure all fail closed into a
non-resolving InquiryOutcome and backoff; the passive perception/context path
remains fail-open and continues operating.
