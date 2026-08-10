# WORLD UNDERSTANDING BASELINE MANIFEST V0.1

- Status: **P0 Baseline Lock**
- Generated: 2026-08-09
- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Default branch: `main`
- Current authoritative `main` HEAD: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`
- Design-freeze source-review SHA: `f65d1aa34d22964eb666d80f49899adfa5165f82`
- Delta from freeze-review SHA to current main: **22 commits ahead**
- Implementation branch: `agent/world-understanding-v0.1`
- Implementation parent SHA: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`

## 1. Baseline rule

All World Understanding implementation work MUST be based on the current authoritative V3 mainline and its existing execution/life/context modules. This manifest does not authorize a second Runtime, second Total Gateway, second execution entrypoint, a World Understanding executor, or direct reality Tool access from World Understanding.

The implementation constitution remains:

- one physical World Understanding ingress: `WorldUnderstandingIngress` / `WorldUnderstandingFacade.accept(...)`;
- two semantic outputs only: `WorldContextPacket` and `WorldInquiry`;
- `WorldContextPacket` is context-only and non-authorizing;
- `WorldInquiry` is non-executable and must be considered by existing Self-Will/Life autonomy before any action intent is emitted;
- accepted autonomous action re-enters the **existing Total Gateway** with SELF_WILL/life origin semantics;
- native execution/evidence producers remain authoritative for reality;
- World Understanding consumes native committed records; it does not replace their commit paths.

## 2. Current branch/source reality

### 2.1 Implementation branch

`agent/world-understanding-v0.1` was created directly from current main HEAD `a918b360...` before any P0 artifact was written.

### 2.2 Existing related branches

The repository currently contains related work branches including:

- `agent/world-cognition-contracts-v0.1`
- `agent/world-cognition-core-v0.1`
- `agent/world-cognition-core-v0.1-verify`
- `agent/world-data-model-v0.1`
- `agent/world-understanding-rhythm-admission-v0.1`

The existing World Cognition branches are **not merged into current main**. Their merge base predates current main and they are materially behind it. Therefore Phase 7 must absorb/reconcile Cognition against the current implementation branch rather than blindly merge or cherry-pick an old branch wholesale.

Observed World Cognition branch assets include contracts for cognition evidence/prior/revision/statement and an internal `world_cognition` core with consolidator/evidence/facade/priors/retrieval/stability/store plus cognition tests. These assets are implementation inputs for later L5 absorption, not a second long-term public cognition system.

## 3. BASELINE / ENTRYPOINT MAP

| Concern | Current authoritative source | Current symbol / boundary | World Understanding attachment decision |
|---|---|---|---|
| Total Gateway execution ingress | `src/total_gateway/desktop_api.py`, `src/total_gateway/orchestration.py` | Gateway inbound + `GatewayOrchestrationWorker`; existing Policy/Ticket/Grant chain | **Do not replace.** Autonomous intents and user intents must converge here with different origin/principal semantics. |
| Execution contract | `src/contracts/execution.py` | `ExecutionTicketPayload`, `ExecutionTicket`, `ExecutionResult`, `FactRecord` | Reuse existing ticket/fact contracts; do not mint authority inside WU. |
| Omni capability authority | `src/total_gateway/omni_grant_authority.py` | one-time Omni grant bound to active execution ticket | WU never writes/grants this authority. |
| User source partition | `app/backend/tiangong-backend/v3/duihua_qiaojie.py` | `_source_partition_wrap`, `_extract_source_partitions`; `CURRENT_USER_INSTRUCTION` vs `EXTERNAL_DATA` | First-wave `UserConversationCompiler` must preserve these source semantics; user claims must not be laundered into objective facts. |
| Life context compile + authorization extraction | `src/life_service/context_api.py`, `src/life_service/production_api.py`, `src/total_gateway/life_client.py` | `LifeContextCompileAuthorizeApi.compile_and_authorize(...)`; `/api/v1/v3/life/context/compile-and-authorize`; Gateway `compile_and_authorize_snapshot(...)` | `WORLD_CONTEXT_SLOT` must be attached **after this authority/source boundary is resolved**. It must never be an input to this authorization extraction path. |
| Final LLM context seam | `app/backend/tiangong-backend/v3/zongdiaodu.py` | current system/context build; `dynamic_context_parts` -> `dynamic_context` -> `_huanxing_simple_chain(...)` | Safest integration seam: after existing trusted/Life authorization resolution and immediately before final dynamic context handoff. Add a dedicated typed/rendered `WORLD_CONTEXT_SLOT`; do not reuse generic authorizing external items. |
| Run identity/isolation | `app/backend/tiangong-backend/v3/run_context.py` | immutable `RunContext`; ContextVar bind/reset/get; request/run/life/session/conversation/principal/workspace IDs | First-wave `RunContextCompiler`; preserve ContextVar/run isolation. |
| Self-Will/autonomy candidate intake | `src/life_service/agency.py`, `src/contracts/agency.py` | `decide_autonomy(...)`; `ActionCandidate` | `WorldInquiry` should be adapted into this proposal/decision boundary. Decision itself remains empirical-weight 0 and non-authorizing. |
| Self-Will -> existing Gateway re-entry | `src/life_service/action_intents.py` | `LifeActionIntentEmitter`; proposal-only transport to Gateway; returns `REJECTED` / `CONFIRMATION_REQUIRED` / `AUTHORIZED` | This is the preferred P11 re-entry path. Extend/reuse intent metadata to preserve `origin=SELF_WILL` and `source_inquiry_id`; do not fake a user message. |
| Fact execution commit | `app/backend/tiangong-backend/v3/fact_kernel/__init__.py` | `FactExecutionKernel`; atomic operation record + append event containing `fact_transaction` | Emit WU SourceEnvelope **only after native fact commit/append succeeds**. Fact kernel remains owner. |
| ToolResult finalization | `app/backend/tiangong-backend/v3/tool_result_contract.py` | `normalize_tool_result(...)`; `tiangong.v3.tool_result.v1`; observed write evidence `tiangong.v3.write_evidence.v1` | Emit after normalization/finalization. Preserve distinction between declared effects and observed/readback-verified effects. |
| Runtime environment producer | `app/backend/tiangong-backend/v3/runtime_environment.py` | `collect_runtime_environment(...)`; factual `tiangong.v3.runtime_environment.v1` snapshot | First-wave deterministic source compiler; environment observation must not become permission. |
| Execution Integrity | `app/backend/tiangong-backend/v3/execution_integrity.py` | existing obligation/evidence/completion gate | Consume its committed decisions as Source; do not replace it or infer completion independently. File changed after the freeze-review SHA and must be treated as current authority. |
| Memory commit | `src/life_service/store.py` | source-owned `LifeShadowStore`; `put_memory_assertion(...)` | Emit only after native memory commit succeeds. Memory does not upgrade original provenance/authority. |
| Knowledge commit/index | `app/backend/tiangong-backend/v3/knowledge_store.py` | document import/extract/chunk; save context + save index before successful return | Emit only after native knowledge/context/index commit completes. Knowledge content remains document claims unless independently verified. |
| L0 primitives | `app/backend/tiangong-backend/tiangong_kernel/l0_primitives/` | existing identity/context/event/autonomy/decision/health/learning primitives | P1 must reuse compatible general primitives; do not duplicate generic identity/event/context concepts merely for WU. |
| Git/code observation | current governed V3 Git/app tooling, including `app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill/tools/pro_apps_v34.py` | governed status/diff/log/read-style observations available in current codebase; additional Git capabilities may exist only on side branches | WU consumes completed Git/code observations. It must not own Git execution. Parser-derived structure is deterministic derived knowledge, semantic role remains hypothesis/cognition. |
| Existing World Cognition | related `agent/world-cognition-*` branches | cognition contracts/core/store/facade/tests exist off-main | Phase 7 absorbs as L5; no second persistent public `WorldCognitionFacade` after migration. |

## 4. Best actual attachment decisions

### 4.1 Single World Understanding input

The least invasive and authority-safe attachment pattern is **thin post-commit adapters** from current native producers into one `WorldUnderstandingFacade.accept(WorldIngressEnvelope)` path.

Initial no-intrusion source order:

1. RunContext
2. User/source-partitioned conversation input
3. RuntimeEnvironment
4. FactExecution
5. normalized ToolResult
6. filesystem/readback evidence carried by ToolResult/fact paths
7. simple-chain/run lifecycle events
8. Execution Integrity

Later sources can include Git/code, governance/config, authorization results, memory, knowledge, autonomy decisions, metrics, migration/audit, external web/network, desktop/UI, and model output.

The adapter rule is always:

`native commit/finalization -> thin SourceEnvelope adapter -> SAME WU ingress`

Never:

`WU -> rescan/recommit native truth store`.

### 4.2 Context Output insertion

The current safe seam is:

`existing source partition -> Life/trusted context + authorization extraction -> build WU CONTEXT_REQUEST -> SAME WU ingress -> WorldContextPacket -> dedicated WORLD_CONTEXT_SLOT -> current final context/dynamic_context assembly -> existing LLM/simple-chain call`

Hard rule: authorization extraction/scanners MUST ignore `WORLD_CONTEXT_SLOT`.

### 4.3 Inquiry Output insertion

`WorldInquiry` should enter the source-owned Life/Self-Will proposal boundary represented by `decide_autonomy(...)` / `ActionCandidate`, not a tool executor.

On ACCEPT, a Self-Will-origin action intent should use/reuse `LifeActionIntentEmitter` transport into the existing Gateway. The WU inquiry itself never calls a tool and never receives user authority by construction.

### 4.4 Self-Will re-entry

Preferred chain:

`WorldInquiry -> existing Life/Self-Will decision -> SELF_WILL-origin intent -> LifeActionIntentEmitter -> existing Total Gateway -> existing Policy/Ticket/Grant/Omni chain -> Tool/Runtime -> Fact/ToolResult -> SAME WU ingress`

## 5. Native modules explicitly forbidden to duplicate or replace

P0 freezes the following as existing owners/boundaries:

- Total Gateway inbound/orchestration
- ExecutionTicket / Policy / Grant / Omni grant chain
- current Runtime/simple-chain execution path
- RunContext / ContextVar isolation
- FactExecutionKernel
- ToolResult normalization and write-evidence semantics
- Execution Integrity
- Life context compile-and-authorize
- source-owned Life autonomy/agency/action-intent transport
- RuntimeEnvironment factual observer
- native Memory store
- native Knowledge store/index
- governed Git/code execution paths
- `tiangong_kernel/l0_primitives`
- existing World Cognition contracts/core logic to be absorbed later rather than reimplemented

## 6. Reality deviations and cautions found in P0

### D-01 — Freeze-review SHA is stale

The design freeze cites `f65d1aa...`; current main is `a918b360...`, **22 commits ahead**. Current main is therefore the only valid implementation baseline.

### D-02 — Execution Integrity changed after the freeze-review baseline

Current `execution_integrity.py` and its tests/behavior must be treated as authoritative for implementation and regression protection. WU may consume integrity results but may not replace the gate.

### D-03 — World Cognition exists off-main and is behind current main

The Cognition contracts/core branch lineage is materially behind current main. Phase 7 requires explicit reconciliation/migration, not blind branch merging.

### D-04 — Legacy autonomous/direct-tool code exists in `zongdiaodu.py`

The current orchestration file contains legacy `huanxing_zizhu` / direct `_jineng_zhixing(...)` style autonomous behavior. **This is not the P11 target.** WorldInquiry must use the source-owned proposal-only Life autonomy/action-intent path and then the existing Total Gateway.

### D-05 — Generic context containers are unsafe for WU authorization semantics

`WorldContextPacket` must not be inserted into a generic Life/external-items structure that later participates in signing or authorization extraction. A dedicated non-authorizing world-context slot is required.

### D-06 — Side-branch Git/data/rhythm capabilities are not automatically current-main reality

Capabilities found only on side branches must not be treated as merged/current. P3/P5/P6 must re-evaluate them against the then-current implementation branch.

## 7. Current regression/test assets and verification status

P0 source reconnaissance located the repository test surfaces and current execution/context/runtime code, including regression coverage added in the mainline delta for Execution Integrity and Windows UTF-8. Exact Phase 1+ test commands must be resolved against the implementation checkout/tooling before claiming execution.

### Actually performed in P0

- GitHub repository metadata read: **performed**
- current default branch and main HEAD verification: **performed**
- compare design-freeze SHA -> current main: **performed**
- related branch enumeration/comparison: **performed**
- current source reads for Gateway/context/RunContext/Life autonomy/Fact/ToolResult/RuntimeEnvironment/Memory/Knowledge/Execution Integrity: **performed**
- implementation branch creation from exact current main SHA: **performed**
- GitHub Actions lookup for exact `a918b360...`: **performed; no workflow runs found for that exact HEAD**

### Not performed

- current-main local `pytest`: **NOT RUN** — current repository checkout could not be established in the available local execution environment; clone access returned HTTP 403.
- Windows runtime smoke execution: **NOT RUN** in this environment.
- Runtime regression suite: **NOT RUN** in this environment.

No unexecuted test is classified as PASS in this manifest.

## 8. P0 Gate

- [x] Current main HEAD confirmed.
- [x] Implementation branch parent confirmed.
- [x] Implementation branch created from exact current main before P0 artifacts.
- [x] Source commit/finalization points identified for the first implementation wave.
- [x] Current final LLM context assembly seam identified.
- [x] Current authorization/source extraction boundary identified.
- [x] Self-Will/Life proposal -> existing Gateway re-entry path identified.
- [x] Fact/ToolResult reality feedback points identified.
- [x] World Cognition ownership/location/divergence identified.
- [x] No second Runtime/Gateway/execution path is required by the frozen design.
- [x] No source symbol in this manifest is intentionally filled from design-memory alone; named symbols were checked against current GitHub source.
- [ ] Runtime regression execution on current implementation checkout — **NOT RUN; environment limitation, not a P0 architecture failure.**

## 9. P0 conclusion

No blocking architectural conflict was found between the frozen World Understanding design and current main `a918b360...`.

The implementation may proceed to **P1 Contracts First** after user approval, with one required caution: all P1/P2 contracts and later P10/P11 bridges must target the current source-owned Life/Gateway boundaries documented above, not historical/legacy autonomous shortcuts.

P0 rollback is documentation/branch-only: delete `agent/world-understanding-v0.1` (or reset it to parent `a918b360...`) and no runtime behavior is affected.
