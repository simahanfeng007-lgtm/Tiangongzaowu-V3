# WORLD UNDERSTANDING PHASE 00 REPORT

## Phase

- Phase: `P0 Baseline Lock / Source Reconnaissance`
- Date: 2026-08-09
- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Implementation branch: `agent/world-understanding-v0.1`

## Baseline

- Authoritative main SHA at phase start: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`
- Implementation parent SHA: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`
- Design-freeze review SHA: `f65d1aa34d22964eb666d80f49899adfa5165f82`
- Current main is 22 commits ahead of the design-freeze review SHA.

## P0 artifact commit

- Baseline manifest commit: `fc320d2545779309454579b4cf7ab798ff7428f4`
- This report is published by a subsequent documentation-only commit; its own commit SHA is intentionally not self-referenced inside the file. The final branch SHA is reported in the external P0 closeout.

## Changed files

P0 intentionally changes documentation only:

1. `docs/world_understanding/BASELINE_MANIFEST_V0.1.md`
2. `docs/world_understanding/implementation_reports/PHASE_00_REPORT.md`

No Runtime, Gateway, context, evidence, autonomy, memory, knowledge, or tool implementation file is modified in P0.

## Source areas actually inspected

Current GitHub source was inspected for:

- repository/default branch/current main metadata;
- branch topology and related World Cognition / data model / rhythm branches;
- `app/backend/tiangong-backend/v3/zongdiaodu.py`;
- `app/backend/tiangong-backend/v3/duihua_qiaojie.py`;
- `app/backend/tiangong-backend/v3/run_context.py`;
- `app/backend/tiangong-backend/v3/runtime_environment.py`;
- `app/backend/tiangong-backend/v3/execution_integrity.py`;
- `app/backend/tiangong-backend/v3/fact_kernel/__init__.py`;
- `app/backend/tiangong-backend/v3/tool_result_contract.py`;
- `app/backend/tiangong-backend/v3/knowledge_store.py`;
- `src/contracts/execution.py`;
- `src/contracts/agency.py`;
- `src/total_gateway/desktop_api.py`;
- `src/total_gateway/orchestration.py`;
- `src/total_gateway/life_client.py`;
- `src/total_gateway/omni_grant_authority.py`;
- `src/life_service/context_api.py`;
- `src/life_service/production_api.py`;
- `src/life_service/agency.py`;
- `src/life_service/action_intents.py`;
- `src/life_service/store.py`;
- `src/life_service/SOURCE_OWNERSHIP.md`;
- `app/backend/tiangong-backend/tiangong_kernel/l0_primitives/`;
- current governed Git/app tooling and existing World Cognition branch assets.

## Design mapping established

### One ingress

The implementation target is a single `WorldUnderstandingFacade.accept(WorldIngressEnvelope)` path. Existing native producers remain owners of reality; post-commit thin adapters will emit typed source envelopes only after native commit/finalization succeeds.

### Context output

The source/authorization boundary is owned by current Life compile-and-authorize and dialogue source partition logic. The selected P10 seam is after that boundary and before current final `dynamic_context` / `_huanxing_simple_chain(...)` handoff in `zongdiaodu.py`.

A dedicated `WORLD_CONTEXT_SLOT` is required. It must be `context_only=true`, `authorizes=false`, `confirms=false`, and `changes_risk=false`, and must never be scanned as user/system authorization.

### Inquiry output

The selected autonomy intake is the current source-owned Life proposal/decision boundary around `decide_autonomy(...)` and `ActionCandidate`.

On ACCEPT, the selected re-entry is the existing `LifeActionIntentEmitter` transport into the existing Total Gateway. This preserves the current policy/ticket/grant/Omni authority chain and avoids fake UserMessage semantics.

### Reality feedback

First-wave reality sources are anchored at native commit/finalization boundaries:

- RunContext;
- source-partitioned user conversation;
- RuntimeEnvironment;
- FactExecution commit;
- normalized ToolResult and observed filesystem/readback evidence;
- run/simple-chain lifecycle events;
- Execution Integrity.

Memory, knowledge, Git/code, authorization, autonomy, metrics and external/desktop/model sources are later adapters under the same ingress.

## Reality/source deviations found

1. The design-freeze source-review SHA is stale relative to current main by 22 commits.
2. Execution Integrity changed in that delta and must be treated as current authority, not the older design-review implementation.
3. Existing World Cognition contracts/core are off-main and behind current main; P7 requires reconciliation rather than blind merge/cherry-pick.
4. Legacy autonomous/direct-tool style code still exists in `zongdiaodu.py`; it is explicitly rejected as the future WorldInquiry execution path.
5. Generic context/external-item containers may participate in signing/authority handling, so WorldContext cannot be inserted there without a dedicated non-authorizing slot.

No blocking architectural conflict was found.

## Tests / verification actually run

### Performed

- GitHub repository metadata lookup: performed.
- Current `main` HEAD verification: performed.
- Freeze-review SHA -> current main commit comparison: performed.
- Related branch listing/comparison: performed.
- Current-source entrypoint inspection listed above: performed.
- Implementation branch creation from exact main SHA: performed.
- GitHub Actions query for exact current main SHA `a918b360...`: performed; no workflow runs were found for that exact SHA.

### Not run

- Local current-main `pytest`: **NOT RUN**. The available local execution environment could not obtain the current repository checkout; Git clone access returned HTTP 403.
- Runtime regression suite: **NOT RUN**.
- Windows UTF-8 smoke execution: **NOT RUN** in this environment.
- Real model E2E: **NOT RUN**; not a P0 activity.

No unexecuted test is reported as PASS.

## Gate result

P0 architecture/source-discovery gate: **PASS WITH TEST-EXECUTION LIMITATION RECORDED**.

Passed/confirmed:

- current main HEAD;
- implementation parent;
- implementation branch;
- source commit/finalization points;
- context assembly seam;
- authorization extraction boundary;
- Self-Will/Life -> existing Gateway path;
- Fact/ToolResult feedback points;
- World Cognition ownership/location;
- no need for a second Runtime/Gateway/execution entry.

Unverified due environment:

- current-main runtime regression execution.

This limitation does not change the P0 source map but remains open and must be closed before any claim of implementation/runtime compatibility.

## Rollback

Rollback point: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`.

P0 modifies documentation only. Reset/delete the implementation branch to the parent SHA to remove all P0 changes without affecting runtime behavior.

## Next gate — P1 Contracts First

P1 may start only after approval. The minimum P1 scope is:

- add typed World Understanding contracts under `src/contracts/world_understanding/`;
- freeze one-ingress/two-output semantics;
- freeze provenance/authority/time/scope/cut/epistemic fields;
- make context/inquiry/prediction non-evidence/non-authorizing by construction;
- reuse existing general L0 primitives where compatible;
- reference/adapt existing off-main Cognition contracts without yet absorbing or merging L5 implementation;
- add deterministic serialization/hash/import validation tests;
- no runtime/gateway/context wiring in P1.
