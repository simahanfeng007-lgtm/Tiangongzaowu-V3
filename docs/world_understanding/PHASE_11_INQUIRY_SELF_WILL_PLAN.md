# PHASE 11 PLAN — Inquiry Output → Self-Will → Existing Total Gateway

## Goal
Open the second and only other World Understanding semantic output without giving World Understanding execution authority.

Frozen chain:

`KnowledgeGap -> WorldCuriosity -> Lambda admission -> WorldInquiry -> existing Self-Will / Autonomy candidate intake -> SelfWillDecision -> AutonomousIntent(origin=SELF_WILL, principal=life:self, authority_refs=[]) -> existing life_scheduler ActionIntent adapter -> Existing Total Gateway -> Ticket / Policy / Grant -> Omni Body -> Tool / Runtime -> Fact / ToolResult -> SAME WorldUnderstandingFacade.accept() / WorldUnderstandingIngress -> InquiryOutcome`

## Existing modules reused
- P1 `KnowledgeGap`, `WorldCuriosity`, `WorldInquiry`, `InquiryOutcome` contracts.
- P5 `BudgetLedger` / `WorkCost` resource reserve semantics.
- existing Life Self-Will / agency score and risk machinery in `life_service.agency`.
- existing Life proposal-only `LifeActionIntentEmitter`; its transport already points to Gateway.
- existing `ActionIntent(source=life_scheduler)` contract and machine-computed Gateway risk.
- existing `total_gateway.policy_engine.PolicyEngine`; no risk or A5 behavior changes.
- existing one `WorldUnderstandingFacade.accept()` / `WorldUnderstandingIngress`.
- existing post-commit source envelope builder.

## New files
`src/world_understanding/inquiry/`
- `__init__.py`: P11 internal surface; does not create a new public WU facade.
- `knowledge_gap.py`: deterministic reference-only gaps from coherent P9 stale/conflict/uncertainty state.
- `curiosity.py`: deterministic curiosity and WorldInquiry construction; observation modalities only, never command/tool call syntax.
- `admission.py`: bounded Lambda inquiry admission using VOI/relevance/impact/novelty/actionability minus cost/risk/duplicate/privacy/runtime-pressure/uncertainty plus count/time/budget gates.
- `self_will_integration.py`: typed SelfWillDecisionRecord, zero-authority AutonomousIntent, existing Self-Will callback adapter, and bridge into the existing Life ActionIntent emitter.
- `inquiry_outcome.py`: close inquiry lineage only from actual post-execution source/observation/evidence references.

## Existing files modified
- `src/life_service/action_intents.py`
  - add a strict `submit_self_will(...)` validation path over the existing emitter/transport;
  - requires an exact non-authorizing `EXTERNAL_DATA` SourceRef bound to the WorldInquiry id/hash;
  - rejects representing the Inquiry as `CURRENT_USER_INSTRUCTION`, `PREAUTHORIZED_USER_FACT`, or `AUTHENTICATED_DIRECTORY`;
  - then reuses the existing `submit()` path.
- `src/world_understanding/source_adapters.py`
  - add an autonomous execution feedback envelope helper for `FACT_EXECUTION`, `TOOL_RESULT`, `EXECUTION_INTEGRITY`, or `CHAIN_EVENT`;
  - the helper carries inquiry/AutonomousIntent/Gateway-intent lineage but still returns an ordinary source envelope that must re-enter the SAME WU ingress.

## Explicitly not modified or replaced
- no second Self-Will scheduler or autonomy queue;
- no new Runtime;
- no new Gateway;
- no new Tool executor;
- no `WorldUnderstandingExecutor`;
- no `AutonomousGateway`;
- no direct ToolCall from WorldInquiry;
- no changes to `PolicyEngine` machine risk computation;
- no changes to A5 rejection behavior;
- no changes to Ticket / Policy / Grant / Omni Body execution authority;
- no fake UserMessage / USER source identity.

## Source identity adaptation
The current Gateway `ActionIntent` contract intentionally exposes `source=life_scheduler`, not a SELF_WILL enum. P11 therefore keeps a separate canonical `AutonomousIntent` with:

- `origin=SELF_WILL`
- `principal=life:self`
- `source_inquiry_id=<WorldInquiry>`
- `authority_refs=[]`
- `authorization=NONE`
- `may_execute_directly=false`
- `requires_gateway_evaluation=true`
- `empirical_evidence_weight_milli=0`

Only after the existing Self-Will accepts the Inquiry may an existing Life action-intent factory map that goal to a real `ActionIntent(source=life_scheduler)`. The WorldInquiry is attached to that ActionIntent only as an `EXTERNAL_DATA` provenance ref, never as authorization provenance.

## Lambda admission
Admission is synchronous and bounded; it does not create a WU daemon. The deterministic score uses integer milli inputs:

`VOI + UserRelevance + WorldImpact + Novelty + Actionability - Cost - Risk - Duplicate - PrivacyCost - RuntimePressure - Uncertainty`

Hard gates precede score admission:
- expired / zero inquiry-count budget;
- privacy forbidden;
- insufficient remaining time;
- P5 budget reserve unavailable;
- exact duplicate already admitted.

Admission does not authorize execution.

## Feedback / failure semantics
Successful and failed autonomous attempts both return through ordinary source envelopes and the SAME WU ingress.

A failed autonomous run is a valid observation that the run/action failed or was blocked. It is never compiled as a successful world mutation. `InquiryOutcome.resolved` stays false unless independent reality results actually satisfy the gap.

## P11 tests
At minimum:
- Inquiry remains `authorization=NONE`, `may_execute=false`, `may_call_tools=false`, empirical=0.
- suggested modalities cannot contain executable command syntax.
- Lambda count/time/cost/privacy/runtime/duplicate gates.
- ACCEPT creates only zero-authority AutonomousIntent, not Evidence/Grant.
- DEFER/DISMISS/EXPIRE create no AutonomousIntent and do not touch Gateway.
- AutonomousIntent keeps `origin=SELF_WILL`, `principal=life:self`, empty authority refs.
- Gateway bridge rejects fake USER provenance and missing/mismatched inquiry provenance.
- existing `LifeActionIntentEmitter` transport remains the only submission path.
- existing PolicyEngine still computes risk and A5 remains rejected.
- success/failure feedback envelopes re-enter SAME facade/ingress.
- failure observation does not become success evidence.
- InquiryOutcome closes exact lineage and cannot mark accepted inquiry resolved without independent reality refs.
- package creates no scheduler/thread/store/Tool/Runtime/Gateway.

## Gate
P11 passes only when all frozen P11 gate items are demonstrated by executed tests or explicitly recorded as not executable in the current environment. P12 must not start automatically.