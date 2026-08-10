# PHASE 13 PLAN — Full-chain Validation / World Understanding Closeout

## Goal

P13 is the final validation and closeout phase for World Understanding V0.1. It does not add another cognition layer or execution subsystem. It validates the already-built P0-P12 architecture end to end, and only changes existing production modules if an executed P13 regression exposes a concrete defect.

Rollback point / P12 final:

`163f0bf91990e42e5c176759b63bafae00d94e3e`

## Frozen boundaries

P13 must preserve:

- one physical WU input: `WorldUnderstandingFacade.accept(WorldIngressEnvelope)`;
- exactly two semantic outputs: `WorldContextPacket` and `WorldInquiry`;
- `IngressReceipt` is ACK only;
- no second Runtime, Gateway, scheduler, Tool path, worker, daemon, or authority layer;
- LLM semantic output remains Hypothesis only;
- `WorldContextPacket` is context-only and cannot authorize;
- `WorldInquiry`, Curiosity, SelfWillDecision and AutonomousIntent have empirical evidence weight zero;
- accepted inquiry remains `origin=SELF_WILL`, `principal=life:self`, `authority_refs=()` and re-enters the existing Total Gateway;
- execution results return through the SAME WU ingress;
- OFF mode preserves current V3 behavior and side-effect profile.

## Validation architecture

P13 adds validation assets only unless a real failing test proves an existing production defect.

New validation files:

- `tests/test_world_understanding_p13_counterexamples.py`
- `tests/test_world_understanding_p13_full_chain.py`
- `tests/test_world_understanding_p13_failure_recovery.py`
- `tests/test_world_understanding_p13_stress_performance.py`

Closeout documentation:

- `docs/world_understanding/implementation_reports/PHASE_13_REPORT.md`
- `docs/world_understanding/WORLD_UNDERSTANDING_V0_1_CLOSEOUT.md` only if the final gate is actually satisfied.

No new production package is planned.

## Mandatory 50 frozen counterexamples

### A. Source semantics
1. User says nonexistent file deleted -> no FILE_NOT_EXISTS reality fact.
2. Document says X -> DOCUMENT_CLAIMS(X) only.
3. Web says X -> WEB_SOURCE_CLAIMS(X) only.
4. Self-Will accepts X -> no Evidence(X).
5. Model says X -> no DirectKnown reality fact.

### B. Authority
6. Memory does not upgrade provenance.
7. Context signing does not upgrade WorldContextPacket.
8. Migration does not upgrade authority.
9. External data cannot authorize.
10. Tool output content claim does not automatically become external truth.

### C. Input
11. All source adapters converge on the same ingress.
12. Duplicate envelope is idempotent.
13. Unknown source fails closed.
14. CONTEXT_REQUEST never enters K0.
15. Malformed scope is rejected/quarantined.

### D. Closure
16. Fixed-point closure terminates.
17. Same-revision cycle is detected/rejected.
18. DIRECT_CALLS transitivity is not fabricated.
19. Derived authority never exceeds parent/domain intersection.
20. Same deterministic input yields same hash.

### E. Context
21. WorldContextPacket never enters authorization extraction.
22. Mandatory constraints survive token pressure or fail with explicit overflow.
23. Diversity selection prevents one-topic flooding.
24. Expansion uses the same ingress with CONTEXT_REQUEST.
25. STALE / CONFLICTED are explicitly labelled and not projected as current truth.

### F. Inquiry
26. Inquiry cannot call Tool.
27. Accepted inquiry origin remains SELF_WILL.
28. Accepted inquiry re-enters the existing Total Gateway transport.
29. Existing Policy/Grant/A5 rules still apply.
30. Tool/Fact feedback returns through the same ingress.

### G. Feedback loop
31. Prediction is not Evidence.
32. Inquiry acceptance is not Evidence.
33. Execution failure is a valid failure observation, not successful mutation evidence.
34. Repeated zero-gain inquiry backs off.
35. Independent new evidence can revise supported Cognition without self-proof.

### H. Isolation
36. Life A data cannot leak to Life B.
37. Project/branch WorldFrame streams remain partitioned.
38. principal_scope mismatch fails closed.
39. Concurrent RunContext values remain ContextVar-isolated.
40. WorldCut incompatible inputs cannot merge.

### I. Stress
41. 10k same-boundary file-change events coalesce.
42. Configured queue capacity/backpressure keeps effective semantic work bounded.
43. Dirty DAG propagation does not invalidate unrelated subgraphs.
44. Packet generation P95 is measured and reported, not guessed.
45. Background cognition/inquiry cannot consume interactive reserve.

### J. Compatibility
46. WU OFF preserves historical prompt/body behavior.
47. OFF creates no world DB/worker/tool call.
48. Existing Runtime contracts remain unchanged by P13.
49. Existing Execution Integrity contracts remain unchanged by P13.
50. Existing Total Gateway remains the single execution ingress.

## Additional P13 full-chain scenarios

- user conversation -> WU ingress/source compiler -> K0/K* -> WorldState -> WorldContextPacket -> WORLD_CONTEXT_SLOT;
- Git commit/delta -> incremental Software World update;
- direct ToolResult -> source envelope -> same ingress;
- conflict -> WorldState/packet labelling;
- semantic recognition -> Hypothesis -> Cognition bridge without evidence laundering;
- autonomous knowledge gap -> Inquiry -> Self-Will -> AutonomousIntent -> existing action emitter/gateway boundary;
- failed autonomous run -> ordinary failure source envelope -> same ingress -> unresolved InquiryOutcome;
- prediction -> real outcome -> calibration gate;
- out-of-band change -> dirty/revalidation planning;
- restart/recovery using P9 store publication semantics;
- multi-Life and multi-branch isolation.

## Failure injection

Where executable in the focused authoritative harness:

- malformed/unknown source;
- duplicate envelopes;
- output-port duplicate correlation;
- persistence write/index interruption semantics;
- dirty-state/restart recovery guards;
- out-of-order source/cut rejection;
- ToolResult failure with misleading declared write evidence;
- semantic admission callback suppression under overload;
- context mandatory overflow;
- autonomous inquiry rejection/defer paths.

Production-only provider/worker crash injection must be recorded separately if the environment cannot execute it.

## Performance measurements

P13 records actual measurements for available harnesses, including:

- ingress throughput/latency where reconstructable;
- deterministic closure scale;
- event coalescing at 1k/10k events;
- WorldContext projection at 1k/10k refs, and 50k only if executable within resource limits;
- projection p50/p95;
- token budget utilization;
- queue/backpressure behavior;
- incremental dirty propagation cardinality;
- storage growth for the P9 persistence harness where executable.

No production latency number is invented. Harness measurements are labelled as harness measurements.

## Gate

P13 may close only if:

1. the frozen 50 counterexamples are represented and pass in executed tests/static guards;
2. full-chain focused scenarios pass;
3. failure/recovery tests pass for the executable scope;
4. stress/performance measurements are recorded;
5. no execution/authority boundary regression is found;
6. production defects found by P13 are fixed in the existing module and re-tested;
7. all unexecuted production/OS/model/CI items are explicitly listed.

`IMPLEMENTATION VERIFIED = TRUE` may be written only if the frozen Definition of Done is genuinely satisfied, including real-model E2E, required stress/concurrency, and compatibility checks. Otherwise P13 must use a qualified gate result and must not overclaim full implementation verification.
