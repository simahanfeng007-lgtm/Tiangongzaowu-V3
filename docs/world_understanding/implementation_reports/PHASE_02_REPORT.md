# WORLD UNDERSTANDING PHASE 02 REPORT

- Phase: **P2 — One Ingress**
- Date: 2026-08-09
- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Implementation branch: `agent/world-understanding-v0.1`
- Current authoritative main at P2 start/end: `ae3404b3300c09a0de0f7782e7d739fe24c93c05`
- P1 report HEAD before main sync: `f9074df1849941a15a866f5d06fed2f9675256db`
- Main-sync merge commit: `62268de5b63d83a53dcb69a141d22fb0cf399803`
- P2 code commit: `d00597bc64b690221baa04df698fda4ae60f37b1`

## 1. Mainline revalidation and sync

Before P2 implementation, `main` was re-read and found to have advanced from the P0 baseline `a918b360...` to `ae3404b3300c09a0de0f7782e7d739fe24c93c05` with commit message `fix: enforce context and execution integrity`.

Because this change touches boundaries that World Understanding later depends on, P2 did not continue from the stale parent. The implementation branch was synchronized through a non-force merge commit with two parents:

- previous implementation HEAD: `f9074df...`
- current main: `ae3404b3...`

The merge result `62268de5...` was verified against current main: implementation branch was `behind=0`, and its remaining diff consisted only of P0/P1 World Understanding additions. No current-main context or Execution Integrity file was overwritten by the sync.

## 2. P2 objective

P2 implements exactly one physical attachment surface:

```text
WorldUnderstandingFacade.accept(WorldIngressEnvelope) -> IngressReceipt
```

`IngressReceipt` is ACK/control only. It is not a third semantic World Understanding output.

The only semantic outputs remain:

1. `WorldContextPacket`
2. `WorldInquiry`

P2 does not yet connect native V3 reality producers. Concrete source compilers are deferred to P3.

## 3. Files added

- `src/world_understanding/__init__.py`
- `src/world_understanding/facade.py`
- `src/world_understanding/ingress/__init__.py`
- `src/world_understanding/ingress/compiler_registry.py`
- `src/world_understanding/ingress/dedup.py`
- `src/world_understanding/ingress/receipt.py`
- `src/world_understanding/ingress/router.py`
- `src/world_understanding/ingress/validation.py`
- `tests/test_world_understanding_ingress.py`

## 4. Files modified

- `pyproject.toml`
  - package discovery now includes `world_understanding*` so the new source-owned package is installable through the existing Python packaging path.

No Runtime, Gateway, backend orchestration, FactKernel, ToolResult, Execution Integrity, Life autonomy, Memory, Knowledge, or model-call file was modified by the P2 code commit.

## 5. Modules explicitly not replaced

P2 leaves the following current V3 owners untouched:

- Total Gateway inbound/orchestration
- Policy / ExecutionTicket / Grant / Omni authority chain
- Runtime/simple-chain execution path
- `zongdiaodu.py`
- RunContext / ContextVar isolation
- FactExecutionKernel
- ToolResult normalization and write-evidence semantics
- Execution Integrity
- Life context compile-and-authorize
- source-owned Life Self-Will/autonomy/action-intent transport
- RuntimeEnvironment
- Memory store
- Knowledge store/index
- governed Git/code execution paths

## 6. P2 implementation details

### 6.1 Facade

`WorldUnderstandingFacade` has one non-private physical ingress method:

```text
accept(envelope)
```

There is no public `ingest_source`, `project`, `submit_feedback`, `trigger_revalidation`, Tool call, Runtime call, or Gateway call.

### 6.2 Validation

Every enabled ingress re-validates the P1 `WorldIngressEnvelope` from its dumped payload. This prevents `model_copy(update=...)` or equivalent unchecked mutations from bypassing the P1 model validators.

Malformed input is fail-closed and returns a rejected ACK receipt.

### 6.3 Canonical dedup / idempotency

P2 uses an in-memory synchronous `DedupGate` keyed by the P1 canonical ingress `dedup_key`.

Properties:

- completed duplicate input returns the cached receipt;
- the compiler is not re-run for the same completed canonical input;
- concurrent duplicates wait on the same in-flight key;
- no worker thread is spawned;
- compiler exceptions release the in-flight reservation;
- transient compiler failure is retryable rather than being permanently cached as success.

P2 intentionally does not create a database or persistent dedup store.

### 6.4 Routing

Routing behavior is:

```text
CONTEXT_REQUEST
  -> ACK ACCEPTED
  -> never source-compiled in P2

SOURCE_RECORD + UNCLASSIFIED_SOURCE
  -> QUARANTINED

SOURCE_RECORD + known source + no registered compiler
  -> QUARANTINED / NO_COMPILER_REGISTERED

SOURCE_RECORD + registered compiler
  -> compiler(envelope)
  -> ACK ACCEPTED on success
```

P2 ships no concrete native source compilers. P3 owns those adapters/compilers.

### 6.5 Compiler registry

The registry is internal infrastructure for P3 source compilers. In P2 it contains no production compiler registrations.

Registration does not grant authority and does not change the one-ingress rule: all compiler invocation still occurs only inside the single ingress pipeline after envelope validation and dedup admission.

### 6.6 IngressReceipt

Receipt properties are frozen as ACK/control semantics:

- `ack_only = true`
- `semantic_output = false`
- `may_authorize = false`
- `may_execute = false`
- `empirical_evidence_weight_milli = 0`

Receipt ID and receipt hash are deterministic from canonical receipt payload material.

### 6.7 OFF mode

Default construction is `enabled=False`.

OFF construction does not instantiate:

- compiler registry when no registry is supplied;
- ingress pipeline;
- dedup gate;
- condition/lock owned by the ingress;
- database/store;
- directory;
- worker/daemon/background thread;
- LLM client;
- Tool client;
- Runtime client.

`accept(...)` in OFF mode returns an `OFF_NOOP` ACK and performs no world processing.

P2 is not yet wired into current V3 runtime paths, so existing V3 behavior remains unchanged.

## 7. Forbidden capabilities audit

The P2 package contains no imports or calls to:

- `subprocess`
- HTTP/network clients
- Total Gateway
- `zongdiaodu`
- ToolResult execution paths
- OpenAI/Anthropic/model clients

It contains no filesystem persistence or database implementation.

## 8. Tests actually executed

### 8.1 Syntax compilation

Command:

```text
python -m compileall -q /mnt/data/wu_p2_local/src/world_understanding /mnt/data/wu_p2_local/tests/test_world_understanding_ingress.py
```

Result: **PASS**.

### 8.2 Isolated P2 behavioral suite

Command:

```text
PYTHONPATH=/mnt/data/wu_p2_local/stubs:/mnt/data/wu_p2_local/src \
pytest -q /mnt/data/wu_p2_local/tests/test_world_understanding_ingress.py
```

Result:

```text
11 passed in 0.04s
```

The same suite was run once before final GitHub commit and once again after final local test shaping; both runs passed.

Covered gates:

1. facade has one public physical ingress method;
2. sequential duplicate processes once and returns cached receipt;
3. unclassified source is quarantined without compiler execution;
4. known source with no compiler is quarantined;
5. ContextRequest never enters source compiler and remains ACK/control-only;
6. tampered envelope is revalidated and rejected fail-closed;
7. OFF mode creates no files/threads and does not run compiler;
8. compiler exception is rejected and reservation is retryable;
9. correlation ID is preserved;
10. eight concurrent duplicates compile exactly once and receive the same cached receipt object in-process;
11. P2 source has no Runtime/Tool/network/LLM imports.

### Important test-environment limitation

The behavioral test was executed in an isolated local P2 harness using contract-compatible stubs for the already-tested P1 contract surface because this environment does not have an authenticated local checkout of the private/current GitHub repository.

Therefore this report does **not** classify an exact full-repository checkout test as PASS.

## 9. GitHub post-commit verification actually performed

- P2 commit created atomically: **performed**.
- branch fast-forwarded without force: **performed**.
- exact P2 commit diff inspected: **performed**.
- final `facade.py` re-read from GitHub: **performed**.
- final `router.py` re-read from GitHub: **performed**.
- current main re-read after P2 implementation: **performed**, still `ae3404b3...`.
- GitHub Actions runs for exact P2 commit queried: **performed; none found**.

## 10. Tests not performed / not claimed as PASS

- full repository `pytest`: **NOT RUN**.
- exact checkout import test against the complete current repository: **NOT RUN**.
- Windows runtime smoke: **NOT RUN**.
- Linux production runtime smoke: **NOT RUN**.
- GitHub Actions CI for P2 commit: **NO RUN EXISTS**.
- native Source adapter integration: **NOT APPLICABLE IN P2 / deferred to P3**.
- Runtime/Gateway integration: **NOT APPLICABLE IN P2 / forbidden in this phase**.

## 11. P2 Gate

- [x] Exactly one facade physical ingress method: `accept(...)`.
- [x] Input kinds remain P1 `SOURCE_RECORD` / `CONTEXT_REQUEST` only.
- [x] Enabled input is revalidated fail-closed.
- [x] Canonical dedup key drives idempotency.
- [x] Repeated completed source processes once.
- [x] Concurrent duplicate source processes once in P2 in-memory scope.
- [x] Unclassified source quarantines/rejects.
- [x] Missing source compiler quarantines.
- [x] ContextRequest does not enter source compiler.
- [x] IngressReceipt is ACK-only and non-authorizing.
- [x] Correlation ID propagates to receipt.
- [x] Compiler failure does not become successful dedup completion.
- [x] OFF mode does not create world DB/files/worker/thread/LLM/Tool/Runtime.
- [x] P2 changes do not alter Runtime/Gateway/execution behavior.
- [x] implementation branch is based on latest observed main `ae3404b3...`.
- [ ] full-repository regression on an authenticated checkout: **NOT RUN; environment limitation recorded**.

## 12. Gate conclusion

**P2 Gate: PASS WITH FULL-REPOSITORY TEST-EXECUTION LIMITATION RECORDED.**

No architectural blocker was found for P3.

P3 may now implement the first native Source Compiler wave behind the same `WorldUnderstandingFacade.accept(...)` path. It must not create another public ingress or move native reality ownership into World Understanding.

## 13. P3 boundary carried forward

First source wave remains:

1. RunContext
2. User/source-partitioned conversation
3. RuntimeEnvironment
4. FactExecution
5. normalized ToolResult
6. filesystem/readback evidence already carried by native result/fact paths
7. Chain lifecycle events
8. Execution Integrity

Each adapter must attach after its native producer commit/finalization point and emit through the same ingress.

## 14. Rollback

P2 runtime/code rollback point is:

`62268de5b63d83a53dcb69a141d22fb0cf399803`

Resetting the implementation branch to that commit removes P2 ingress/package changes while preserving:

- latest-main synchronization;
- P0 baseline documentation;
- P1 contracts and tests.

Main branch itself was not modified by P2.