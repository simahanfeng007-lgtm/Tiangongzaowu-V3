# P18-M3 Adaptive Control + Governance Closeout

Date: 2026-08-15
Branch: `agent/p18-m3-adaptive-control-governance`
Status: **FINAL FOUR-GATE VALIDATION PENDING**

## Production freeze candidate

- Production code freeze: `99c67e1` (includes verified task-contract hash seam fix cleanup; no temporary applicator remains)
- M2 base: `8d99aa78b9d0b84b335284287aff811d3dec92f9`
- Final closeout workflow: `.github/workflows/p18-m3-closeout-validation.yml`

## M3 scope sealed for acceptance

M3.1-M3.7: adaptive horizon, EWMA/hysteresis, execution potential, frontier progress, strategy exhaustion, resource governor.

M3.8-M3.12: Known Fact freshness/revalidation, ToolResult authority poisoning defense, Memory/Learning poisoning defense, semantic drift control, checkpoint version-drift resume guard.

Production observation is fed only from existing authoritative runtime facts into the existing `TurnLoopState`; no second Runtime, Scheduler, GatewayStateStore, continuity authority, tool dispatcher, or persistence path is introduced.

## Hard acceptance matrix

1. Stable read-only task grows Epoch — covered.
2. High-failure task shrinks Epoch — covered.
3. Risk jitter does not oscillate Epoch — covered.
4. Context pressure requests early regeneration — covered, including production observation wiring.
5. Test/edit/test progress is not falsely classified as stuck — covered.
6. Repeated same frontier/strategy/failure exhausts strategy — covered.
7. Fatal exhaustion requires multiple failed strategies — covered.
8. Resource runaway / hard budget blocks execution — covered.
9. Stale/volatile/source-version-changed Known Fact requires revalidation — covered.
10. ToolResult prompt injection cannot change Authority — covered.
11. Unverified/model-only fact cannot promote into long-term fact learning — covered.
12. Explicit User Memory remains on Memory SSoT path — covered.
13. Version mismatch cannot silently resume; `run.resumed` is gated — covered.
14. Semantic drift triggers checkpoint/audit/frontier rebuild/replan and prevents horizon expansion — covered.
15. All inherited repository tests — pending final Ubuntu/Windows full regressions.

## Final gates

Required before M4 admission:

- [ ] Ubuntu focused M3 closeout
- [ ] Windows focused M3 closeout
- [ ] Ubuntu full repository regression
- [ ] Windows full repository regression
- [ ] Source Authority / generated mirrors clean
- [ ] 15/15 hard acceptance
- [ ] P0 = 0
- [ ] No M4-blocking P1

M4 admission remains **BLOCKED** until all boxes above are supported by one final four-gate run on the closeout candidate tree.
