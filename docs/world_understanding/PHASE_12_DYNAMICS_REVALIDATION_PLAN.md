# PHASE 12 PLAN — L7 Dynamics / Revalidation / Adaptive Mathematics

## Goal

Add telemetry-driven dynamics only after P0-P11 reality/source/authority/context/inquiry boundaries are in place.

Frozen P12 scope:

- prediction score / outcome / error;
- stale change hazard;
- adaptive revalidation priority;
- queue rho / adaptive debounce;
- transform cost and quality feedback;
- curiosity/inquiry budget and zero-gain backoff;
- projection feedback;
- anti-oscillation damping for contradictory cognition;
- calibration gate before any prediction value may be called a probability.

P12 is internal mathematics and admission control. It does not create another Runtime, Gateway, Tool path, World Understanding input, semantic output, scheduler, daemon, database, or authority layer.

## Frozen formulas

Stale hazard:

`P_stale = 1 - exp(-lambda * delta_t)`

`lambda` is derived only from actually observed change counts and observation windows. No learned or guessed change rate is injected when telemetry is absent.

Revalidation priority:

`P_stale * Impact * Need * Uncertainty * Dirty / (ValidationCost + epsilon)`

Inquiry efficiency:

`Expected Gap Reduction / Expected Cost`

Queue:

`rho = lambda_eff / mu`

Adaptive queue behavior is allowed only inside a bounded capacity region and must preserve P5 interactive reserve semantics.

Uncalibrated prediction values remain `prediction_score_milli`. The word/field `probability` is exposed only by the explicit calibration gate after minimum real outcome sample, coverage and calibration-error conditions are satisfied.

## Existing modules to reuse

- `contracts.world_understanding.prediction.{WorldPrediction,PredictionOutcome}`
- `contracts.world_understanding.transform_metrics.{TransformCostObservation,TransformQualityProfile}`
- P5 `common.rhythm.{RhythmPlane,QueueTelemetry,adaptive_debounce_ms}`
- P5 `common.budgets.{BudgetLedger,WorkCost}`
- P9 `world_state.invalidation` dirty/source-watermark semantics
- P7 `cognition.stability.StabilityReport`
- P8 semantic admission as the downstream LLM gate
- P10 context projection output metadata/token accounting
- P11 `InquiryOutcome` and `InquiryAdmission`

## New internal package

`src/world_understanding/dynamics/`

- `__init__.py`
- `hazard.py` — deterministic observed change-rate window and stale hazard.
- `revalidation.py` — cost-aware revalidation priority / plan, optional existing REVALIDATION queue submission.
- `prediction.py` — prediction resolution/error and calibration profile/gate; prediction never becomes Evidence.
- `queue_control.py` — queue rho/capacity assessment and telemetry-derived adaptive debounce plan.
- `transform_feedback.py` — bounded cost/quality aggregation from real Transform telemetry only.
- `inquiry_backoff.py` — repeated zero-information-gain exponential backoff derived from real InquiryOutcome history.
- `projection_feedback.py` — token utilization/truncation/expansion feedback profile; telemetry only.
- `cognition_damping.py` — deterministic dwell/confirmation hysteresis over existing Cognition stability results; does not change C0-C4 evidence math.
- `semantic_throttle.py` — pre-LLM throttle using real queue/change/quality telemetry; wraps P8 admission instead of replacing it.

## Existing files modified

### `src/world_understanding/common/event.py`

Add a bounded setter for the existing EventCoalescer debounce window. This changes only the window used by the same coalescer; it does not create a second coalescer or scheduler.

### `src/world_understanding/common/rhythm.py`

Add a bounded method that applies a debounce value computed by P12 queue control to the existing coalescer. Existing default behavior remains unchanged until explicitly called.

### `src/world_understanding/inquiry/admission.py`

Add optional backoff signals with zero defaults. When a caller supplies a positive backoff remainder derived from real InquiryOutcome history, admission returns DEFERRED before spending budget. Existing P11 callers that do not supply P12 signals retain identical behavior.

## Modules explicitly not replaced or modified

P12 does not modify or replace:

- Total Gateway / PolicyEngine / Ticket / Grant / Omni Body;
- Runtime / RunContext / execution integrity;
- Tool execution;
- source authority compilation;
- P4 Known closure mathematics;
- P6 graph identity/relations;
- P7 Cognition evidence aggregation thresholds or C0-C4 semantics;
- P8 model transport or WorldHypothesis authority;
- P9 WorldState/WorldCut coherence;
- P10 WORLD_CONTEXT_SLOT authorization isolation;
- P11 Self-Will / AutonomousIntent / Gateway provenance boundary.

## Telemetry discipline

Dynamic values may be derived from:

- `TransformCostObservation`;
- `TransformQualityProfile`;
- `QueueTelemetry`;
- real PredictionOutcome history;
- real InquiryOutcome history;
- real projection token/overflow/expansion observations;
- real world change counts/time windows;
- existing Cognition stability reports.

Missing telemetry must not be silently fabricated. When a required measurement is absent, the dynamic controller must return an explicit unavailable/conservative state rather than inventing a parameter.

## P12 Gate tests

1. stable-world observed change rate falls and background revalidation work declines;
2. high-churn telemetry increases debounce and same-boundary events coalesce;
3. noisy/overloaded semantic work is deferred before any LLM callback;
4. alternating contradictory Cognition reports do not oscillate levels;
5. query burst/background overload does not consume interactive reserve;
6. repeated zero-gain InquiryOutcome history produces bounded exponential backoff;
7. WorldPrediction and PredictionOutcome remain empirical weight zero and non-authorizing;
8. a calibrated probability view is unavailable below sample/coverage/calibration thresholds and opens only after those real-outcome conditions pass;
9. cross-life/cross-scope telemetry is rejected where identity is carried;
10. adaptive controls are bounded and deterministic for identical telemetry;
11. P11 behavior is unchanged when P12 backoff signals are omitted;
12. no new Runtime/Gateway/Tool/direct execution import path exists.

## Test limitations to record

P12 report must separately state whether the following were actually run:

- full authoritative repository pytest;
- exact fresh P0-P12 checkout regression;
- production queue load;
- production model/provider semantic throttle E2E;
- Windows/Linux runtime smoke;
- long-duration real telemetry calibration.

No unexecuted item may be reported PASS.

## Rollback

Rollback point for P12 is P11 final:

`3b1f3373abeed9f66d87509da395bb08398d6300`

Do not start P13 until the P12 Gate is explicitly closed.
