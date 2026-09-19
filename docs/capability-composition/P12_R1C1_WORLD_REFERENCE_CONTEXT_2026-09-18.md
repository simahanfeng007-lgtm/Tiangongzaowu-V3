# P12 R1C1 — source references reach the existing model-input path

## Frozen baseline and recovered interruption

Continue original PR #78 and its existing P12 branch from exact HEAD
`6f805d0615cdfd8166bd1d3a4eaaf15d08163e8e`, tree
`3d1393b0bfb05709d7d5480a8b2bb3c5489c7d72`.
Main was reread at `bf29542b3048c8d1806add8063b0db7c72be055b`.
The parent R1B Architecture, P14, P19 and focused engineering checks are closed.
Those old outcomes are not acceptance of the new candidate.

The preceding interrupted turn created unreferenced R1C1 blobs/trees but no
commit/ref update. This batch restores the supplied 2,602-file native-object
snapshot, verifies its complete Git tree against GitHub, and recovers the
uncommitted code. Six recovered file blobs (including the original 34-test
module) match the prior supplied content objects. This is not a July baseline,
an alternate product branch, or evidence that the interrupted work was published.

## Actual connected path

Existing Tool/Method compiler -> ordinary Domain Contribution -> existing single
WorldState materializer/store -> existing CONTEXT_REQUEST ingress/handler ->
existing one-shot output port -> V3 World context bridge -> existing body prompt
assembler -> existing HTTP client/protocol mapper -> model request payload.

No new listener, planner, Source catalog, Runtime, registry or database is added.
The new `world_reference_context.py` is a pure read-only projection imported by
the existing handler, not an unused alternative hook. Production singleton wiring
already uses that handler and output port and therefore receives the integration.

Tool/Method compilers retain the original full SourceRevisionRef and FrameBinding
as canonical metadata alongside descriptor refs in ordinary entities. The existing
binder checks each new address against the typed contribution's source references
and attaches its digest only to that entity. Inherited invalidation keys remain.

The projection checks committed snapshot/head/dependency/delta hashes, entity and
attribute hashes, exact source/descriptor/frame/cut/workspace bindings, lifecycle,
and stale/conflicted refs. It never reopens Source files or promotes a summary
into an executable candidate. Missing addresses in old snapshots require normal
source re-observation; they are not fabricated from the old Skill registry.

The same request carries one capability packet with protected query/state/workspace
identities. Output and V3 readback reject scope/task/state/query substitutions;
concurrent request/run/generation identities cannot share a pending emission.

Identity space is reserved before optional base-context selection. Whole display
records can be omitted with counts; identity strings are never cut off. Default
2,400-token rendering is checked including the outer slot delimiters. Small
budgets can make typed display unavailable; the legacy base mandatory-overflow
semantics are retained rather than hiding missing context as a successful cutover.
Descriptions are reversibly JSON-escaped, including Unicode line separators.
This prevents tested delimiter/line spoofing, not every possible model injection.

## Explicit display and authority boundary

The model-visible ABI declares WORLD_STATE_SOURCE_REFERENCES, DISPLAY_ONLY,
not_a_composition_candidate_snapshot=true, source_resolution_required_for_planning,
model_authority=false and may_execute=false. Action/Method display IDs and their
projection digest must not be used as a P4 executable candidate snapshot.
No Grant/Ticket/Activation/Completion is constructed. Procedural experience and
negative evidence are empty here; those sections are not claimed fully integrated.

Typed rendering uses the existing SHADOW result mode. Here that means a staged,
non-authorizing context integration with the old execution path retained; it is
NOT a live two-model shadow comparison or a guarantee that model reasoning is
unaffected. Unavailable reference projections emit bounded reason codes, never
source bodies or raw parser diagnostics. Existing base context remains the active
pre-cutover path; the failure is not counted as successful typed replacement.

## Defects found while completing the interrupted code

The recovered 34 tests passed. Four additional negative cases then failed:
full source-ref digests were broadcast to all entities/relations of a contribution,
and U+0085/U+2028/U+2029 could split a descriptive DATA line. The fixes keep each
new address dependency local to its owning entity and reversibly escape those
separators. The original failed log is retained. No test was skipped or weakened.
A new negative test also rejects rehashed addresses absent from the contribution.

An installed-catalog scale test compiles the actual manifest using explicit synthetic
source-reference fixtures: 790 catalog entries produced 290 eligible Tool primitives
and 1,818 relations. Exactly 290 new address keys were retained, not a cross-product;
6 action references fit the tested 2,400-token half-reservation and 284 omissions
were disclosed. These are fixture/scaling observations, not production Source
approval, execution parity, model calls, or 790 successfully executed tools.

## Validation and immutable boundaries

The new suite contains 40 cases: Source/Frame/cut/path mutation rejection, actual
single-ingress rendering, 12 concurrent requests, persisted-state restart,
signed Method publication with synthetic test keys, exact prompt assembly, bounded
rendering, linear address metadata, and final HTTP wire payload. In the HTTP test,
only endpoint settings/DNS/socket transport are fixtures; no external model is called.
The wire payload contains one typed World slot, unchanged user text, and an explicit
marker proving that the old learned-Skill injection still remains enabled.

The focused workflow adds the new World-context/P6/P9/retention group and retains
all previous groups and exact old test-input checks. Architecture's eight-shard
Windows union and serial Ubuntu full gate are unchanged. Remote outcomes must be
read from the new commit; local Linux skips are not native Windows acceptance.

Verification Plane 1.18 explicitly adds eight context/provenance files to the freeze
without removing old members. Official mirror/freeze generation precedes ordinary
checks. Store v33, schema bodies, registry, old HTTP injection and orchestration,
static router actions, sandbox, permissions, source compiler execution semantics,
Golden corpus and independent fingerprint remain unchanged.

Local completed results (Linux/Python 3.13.5, pytest 9.0.2, Pydantic 2.13.4):
227 passed in the expanded World/P6/P9/retention/shared/version/Golden selection;
147 passed and 1 Windows-only skip in the five preserved product modules;
37 passed and 256 subtests in the same-process fixture/epoch/continuity selection;
54 unchanged R0/R1A diagnostic contracts passed. Normal source and committed-mirror
checks passed. Groups overlap and must not be summed as independent totals.
No local full-repository or native Windows acceptance is claimed.

## Remaining P12 work and rollback

R1C1 is a model-input integration checkpoint, not complete P12 decommission.
Still required: exact retained Source resolution into real P4 candidate/Plan flow,
capability and experience parity, replacement of legacy HTTP injection and static
startup/planning/query consumers, current-task/in-flight source identity evidence,
and approved live/fault/restart/zero-old-use results. Preserve explicit user intent
and machine-fact step/progress operations. Do not delete mixed modules wholesale.

P11's real observations, controlled faults, single-path traces, independent review
and merge, and P8/P9 obligations remain. No main merge or deployment is performed.
Stage-merge progress remains 11/18 = 61.1%, not product readiness.

Rollback is an explicit revert of this unmerged source batch, regenerating normal
mirrors and a declared freeze version. Previously published snapshots are immutable;
extra address attributes do not require a database migration. They must not be
rewritten or relabeled as old/new production acceptance.
