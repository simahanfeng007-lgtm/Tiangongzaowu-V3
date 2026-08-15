# P18-M3 Adaptive Control + Governance Closeout

Date: 2026-08-15
Branch: `agent/p18-m3-adaptive-control-governance`
Status: **CLOSED — M4 ADMISSION PASS**

## Frozen evidence

- M2 base: `8d99aa78b9d0b84b335284287aff811d3dec92f9`
- M3 production code freeze: `99c67e1f7935032486aa21dc338abfa470a57da7`
- Verified closeout candidate: `bb055c7874ec0293ccea98fe7135078b49e7cc23`
- Final four-gate workflow: `.github/workflows/p18-m3-closeout-validation.yml`
- Final four-gate Run: `31869087008`
- Run conclusion: **SUCCESS (4/4)**

`99c67e1... -> bb055c7...` changes only this closeout record; no production source changed after the production freeze used by the final validation candidate.

## M3 production scope sealed

M3.1-M3.7: adaptive horizon, EWMA/hysteresis, execution potential, frontier progress, strategy exhaustion, resource governor.

M3.8-M3.12: Known Fact freshness/revalidation, ToolResult authority poisoning defense, Memory/Learning poisoning defense, semantic drift control, checkpoint version-drift resume guard.

Production observation is fed only from existing authoritative runtime facts into the existing `TurnLoopState`. The final architecture gate confirms no second Runtime, Scheduler, GatewayStateStore, continuity authority, tool dispatcher, or persistence path was introduced.

## 15/15 hard acceptance

1. Stable read-only task grows Epoch — **PASS**.
2. High-failure task shrinks Epoch — **PASS**.
3. Risk jitter does not oscillate Epoch — **PASS**.
4. Context pressure requests early regeneration — **PASS**, including production observation wiring.
5. Test/edit/test progress is not falsely classified as stuck — **PASS**.
6. Repeated same frontier/strategy/failure exhausts strategy — **PASS**.
7. Fatal exhaustion requires multiple failed strategies — **PASS**.
8. Resource runaway / hard budget blocks execution — **PASS**.
9. Stale/volatile/source-version-changed Known Fact requires revalidation — **PASS**.
10. ToolResult prompt injection cannot change Authority — **PASS**.
11. Unverified/model-only fact cannot promote into long-term fact learning — **PASS**.
12. Explicit User Memory remains on Memory SSoT path — **PASS**.
13. Version mismatch cannot silently resume; `run.resumed` is gated — **PASS**.
14. Semantic drift triggers checkpoint/audit/frontier rebuild/replan and prevents horizon expansion — **PASS**.
15. All inherited repository tests pass on Ubuntu and Windows — **PASS**.

Result: **15/15 PASS**.

## Final four gates — Run 31869087008

### 1. Ubuntu focused M3 closeout — PASS

- `214 passed`
- `0 failed`
- `4 warnings`
- Source Authority: `PASS: 16 independent authorities, 1 aliases, 24 generated targets, 1 closed-world boundaries`
- Generated-source mirror check: PASS
- Python compile gate: PASS
- `P18-M3 FINAL architecture invariants: PASS`

### 2. Windows focused M3 closeout — PASS

- `214 passed`
- `0 failed`
- `4 warnings`
- Source Authority: PASS
- Generated-source mirror check: PASS
- Python compile gate: PASS
- `P18-M3 FINAL architecture invariants: PASS`

### 3. Ubuntu full repository regression — PASS

- `3006 passed`
- `35 skipped`
- `0 failed`
- `807 subtests passed`
- `4 warnings`
- Source Authority / generated mirrors: PASS before full regression

### 4. Windows full repository regression — PASS

- `3011 passed`
- `30 skipped`
- `0 failed`
- `804 subtests passed`
- `4 warnings`
- Source Authority / generated mirrors: PASS before full regression

## Architecture closure

Final closeout assertions prove:

- adaptive/governance/observation/freshness layers do not open a second `GatewayStateStore`;
- no direct `sqlite3.connect`, `subprocess.run`, or `_jineng_zhixing` execution bypass exists in those governance layers;
- production Runtime still binds `RegenerativeExecutionAuthority(runtime.store)` to the existing Store;
- `zongdiaodu` uses the existing tool execution path and existing `TurnLoopState`;
- Adaptive Control is explicitly activated in the production chain;
- `EpochRealityObservation` feeds real context pressure, frontier, tool, repeat, wall-clock/checkpoint observations into Adaptive Horizon, Semantic Drift and Resource Governor;
- Known Fact dependency reads fail closed when revalidation is required;
- ToolResult remains `UNTRUSTED_DATA / TOOL_RESULT_DATA` and cannot promote its own authority;
- checkpoint version compatibility is evaluated before `run.resumed`;
- incompatible resume enters `RECONCILE_REQUIRED` rather than silently resuming;
- temporary patch/applicator workflows used during engineering were removed from the production tree.

## Residual warnings / defect gate

The final runs contain only four inherited Pydantic schema-shadow warnings for world-understanding repository contract models. They are unchanged inherited warnings and did not produce test or architecture failures.

- P0: **0 open**
- M4-blocking P1: **0 known/open in M3 closeout scope**
- Production regression failures: **0**

## Admission decision

- [x] Ubuntu focused M3 closeout
- [x] Windows focused M3 closeout
- [x] Ubuntu full repository regression
- [x] Windows full repository regression
- [x] Source Authority / generated mirrors clean
- [x] 15/15 hard acceptance
- [x] P0 = 0
- [x] No known M4-blocking P1

**P18-M3 is CLOSED.**

**M4 ADMISSION: PASS.**

M4 may now begin from the M3 frozen production baseline. No M4 implementation is included in this closeout commit.