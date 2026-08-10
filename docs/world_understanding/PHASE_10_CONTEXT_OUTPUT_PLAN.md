# PHASE 10 PLAN — L8 WorldContextPacket + WORLD_CONTEXT_SLOT

## Goal
Attach the frozen L8 context projection to the existing V3 LLM context path without creating a second input, Runtime, Gateway, authorization path, model transport, or execution path.

Frozen order:

`trusted user/system context -> source/authorization identity resolved -> CONTEXT_REQUEST -> SAME WorldUnderstandingFacade.accept -> WorldQuery -> L8 projection -> WorldContextPacket -> WORLD_CONTEXT_SLOT -> existing final context assembly -> LLM`

## Existing modules to reuse
- `contracts.world_understanding.query.WorldQuery`
- `contracts.world_understanding.context_packet.{WorldContextPacket,WorldContextItem,ExpansionHandle}`
- `contracts.world_understanding.ingress.WorldIngressEnvelope`
- `world_understanding.facade.WorldUnderstandingFacade`
- the one `WorldUnderstandingIngress`
- P9 `WorldStateStore` and reference-only `MaterializedWorldSnapshot`
- V3 `RunContext` isolation
- V3 `goujian_shenti_tishi()` dynamic-context seam
- V3 `context_compactor.estimate_tokens`

## New implementation
`src/world_understanding/context_output/`
- `request.py`: compile/build CONTEXT_REQUEST and expansion queries.
- `output_port.py`: bounded one-shot output sink keyed by correlation ID; no input/accept API.
- `handler.py`: CONTEXT_REQUEST handler reached only through the existing ingress.
- `mandatory.py`: mandatory current-state/conflict/stale/uncertainty items.
- `projection_support.py`: deterministic policy, diversity preselection, item/handle/packet factories.
- `projection.py`: mandatory-first, budgeted L8 projector with L0/L1/L2 disclosure.
- `slot.py`: typed non-authorizing WORLD_CONTEXT_SLOT renderer.

Backend:
- `v3/world_context_integration.py`: read-only V3 consumer over P9 store and SAME WU ingress.

## Existing files modified
- `src/world_understanding/facade.py`: optional internal context-request handler; public surface remains `accept()` only.
- `src/world_understanding/ingress/__init__.py`: pass optional handler to router.
- `src/world_understanding/ingress/router.py`: CONTEXT_REQUEST invokes handler when configured; legacy generic ACK remains when absent.
- `src/world_understanding/world_state/store.py`: add read-only `current_candidates()`; no persistence semantics changed.
- `app/backend/tiangong-backend/v3/run_context.py`: carry exact current user text as ContextVar prompt input, excluded from audit identity.
- `app/backend/tiangong-backend/v3/gutong/shangxiawen.py`: OFF-gated lazy append of typed WORLD_CONTEXT_SLOT to dynamic body/context projection.

## Explicitly not replaced or modified
- `zongdiaodu.py`
- `duihua_qiaojie.py`
- `permission_settings.py` / authorization scanner
- Total Gateway / Ticket / Policy / Grant
- Tool / Omni Body / Runtime execution
- P4 Known mathematics
- P5 Γ / Λ
- P6 graph
- P7 Cognition store/consolidator/math
- P8 semantic pipeline/model adapter
- P9 materializer semantics
- existing LLM transport

## Authority boundary
`WORLD_CONTEXT_SLOT` is a typed context surface only:
- `source_kind=WORLD_UNDERSTANDING`
- `context_only=true`
- `authorization_source=false`
- `authorizes=false`
- `confirms=false`
- `changes_risk=false`
- packet `may_execute=false`
- packet empirical weight 0.

The raw user-message builder remains unchanged (`return xiaoxi`). P10 does not import or call `check_tool_permission`, Life compile-and-authorize, Total Gateway, Tool, Runtime, or Cognition mutation APIs.

## Query and expansion
- Context requests use `envelope_kind=CONTEXT_REQUEST`, `source_kind=CONTEXT_REQUEST`, no native authority.
- Every expansion creates a new `WorldQuery`, then a new CONTEXT_REQUEST through the SAME `WorldUnderstandingFacade.accept()`.
- L0 -> L1 -> L2. L2 creates no further expansion handle.
- Expansion target refs become mandatory in the next packet.

## Selection and token budget
1. Build mandatory frame/task/constraints/current-state items.
2. Preserve current delta, unresolved conflicts, stale refs, and critical uncertainty as mandatory when present.
3. If mandatory alone exceeds budget, return explicit `MANDATORY_OVERFLOW`; do not squeeze mandatory material.
4. Preselect optional refs cheaply and diversity-aware across entity/relation/cognition/hypothesis/prediction classes.
5. Build at most the policy's bounded candidate set and admit optional items only while the rendered packet stays within budget.
6. Recheck the final rendered form after the `BUDGET_TRUNCATED` label is applied; if needed remove only the last optional item(s).
7. `evidence_digest` includes only refs actually present in the returned packet.

## Epistemic rendering
- `[CONFLICTED]` remains unresolved.
- `[STALE]` explicitly means stale, never FALSE.
- `[UNCERTAINTY]` is preserved.
- hypotheses/predictions remain non-empirical proposals.
- L2 exposes hashes/evidence-root references, not copied raw lower-layer payloads.

## OFF behavior
`TIANGONG_WORLD_UNDERSTANDING_ENABLED` defaults OFF.
When OFF, `goujian_shenti_tishi()` returns the exact legacy body text and does not import `v3.world_context_integration`; therefore no WU state-store construction, prompt slot, worker, LLM, Tool, or directory creation occurs from this attachment.

## Gate tests
P10 focused coverage must include:
- ACK-only ingress and semantic output separation;
- expansion via SAME ingress;
- budget enforcement and mandatory overflow;
- critical mandatory preservation;
- stale/conflict/uncertainty labels;
- evidence digest excludes omitted candidates;
- progressive L0/L1/L2 disclosure;
- required expansion refs mandatory;
- output-port correlation isolation;
- adversarial task text cannot change authority flags;
- raw user message remains raw;
- authorization/Tool/Runtime/Cognition-write static exclusion;
- RunContext isolation;
- ambiguous P9 current frame fails open to no slot;
- OFF prompt equivalence and lazy import;
- 1000-ref interactive projection latency benchmark.
