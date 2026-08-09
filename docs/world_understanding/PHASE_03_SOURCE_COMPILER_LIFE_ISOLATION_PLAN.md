# WORLD UNDERSTANDING PHASE 03 — SOURCE COMPILER + LIFE ISOLATION HARDENING

Status: implementation plan frozen for `agent/world-understanding-v0.1`.

## Scope

P3 now combines the original Source Compiler phase with the mandatory Life Isolation Hardening requirements. P0-P2 remain intact. P4 deterministic closure is explicitly out of scope until this gate passes.

## Invariants

1. Engine shared: one `WorldUnderstandingFacade`, one ingress implementation, one shared compiler registry/algorithm set. No per-life engine/runtime/gateway.
2. State life-scoped: World state-bearing records use `WorldScope` as the single source of truth for life identity.
3. Ingress life explicit: `WorldIngressEnvelope.life_id` is required for both SOURCE_RECORD and CONTEXT_REQUEST.
4. Ingress dual consistency: `envelope.life_id == envelope.scope_hint.life_id`.
5. Principal consistency: when top-level principal scope is present it must equal `scope_hint.principal_scope_hash`.
6. Compilers never choose or rewrite scope. A uniform post-compiler boundary validates every DirectKnown result against the input envelope.
7. Compiler registry stores compiler configuration only; no `current_life`, `current_world`, or `last_life_state`.
8. DirectKnown identity remains scope-sensitive because `known_id` includes `world_scope_hash`, which includes life identity.
9. P4 precondition is frozen now: `K*_l = Closure(K0_l)`. Mixed-life parents fail `SCOPE_MISMATCH`; no derivation is emitted.
10. Derivation objects are explicitly scope-bound; the Derivation DAG may not cross life/world scope by default.
11. Future World Graph and persistence queries must be keyed by both `life_id` and `world_scope_hash`; global-load-then-filter is prohibited.
12. Context and Inquiry outputs retain `scope: WorldScope` as their single life identity source.
13. Future WorldContextPacket insertion must match `RunContext.life_id`; mismatch fails closed.
14. Future WorldInquiry dispatch must target Self-Will for exactly the same life.
15. Execution reality feedback must inherit life from RunContext/execution lineage, never guess from path/workspace.
16. Cross-life cognition is prohibited by default. Future transfer must be explicit claim transfer followed by destination-life evidence validation.

## P3 source compiler set

P3-A strong sources:
- RUN_CONTEXT
- USER_CONVERSATION
- RUNTIME_ENVIRONMENT
- FACT_EXECUTION
- TOOL_RESULT
- FILESYSTEM
- CHAIN_EVENT
- EXECUTION_INTEGRITY

P3-B compiler vocabulary is also registered for:
- GIT_CODE
- SYSTEM_GOVERNANCE
- AUTHORIZATION
- MEMORY
- KNOWLEDGE
- CONTEXT_CONTINUITY
- AUTONOMY
- METRICS
- MIGRATION_AUDIT
- WEB_EXTERNAL
- DESKTOP_UI
- MODEL_OUTPUT

All compilers are deterministic and stateless with respect to life identity.

## Source semantics

- User claim -> `USER_SAID`, never direct reality truth.
- Web -> `WEB_SOURCE_CLAIMS`, empirical reality weight 0.
- Model -> `MODEL_PROPOSED`, empirical reality weight 0.
- Memory -> `MEMORY_RECORDED`, no authority upgrade.
- Autonomy -> decision record, empirical reality weight 0.
- Authorization -> authorization decision record, not execution completion.
- Chain completed -> chain lifecycle fact, not real-world goal completion.
- ToolResult observed write -> observed filesystem proposition only when authoritative `write_evidence` exists.
- ToolResult declared write -> `TOOL_WRITE_DECLARED`, empirical reality weight 0.
- Filesystem existence/hash -> emitted only from explicit observed filesystem payload.

## Adapter rule

The P3 adapter is a post-commit envelope builder. It requires an already-resolved `WorldScope` and copies life/principal scope from it. It has no API to infer life from workspace/path.

Native producer ownership is not moved into World Understanding. Runtime/Gateway behavior is not modified in this phase.

## Gate

- 30+ source/life semantics test cases.
- 1000 Life A + 1000 Life B concurrency using one Facade/Registry with zero cross-life contamination.
- Existing P1/P2 tests updated only for required life identity contract and must remain semantically unchanged.
- OFF mode remains lazy/no-op.
- No Tool/Runtime/Gateway execution authority added.
- No P4 closure implementation, L4 semantic, WorldState DB, or Self-Will integration.
