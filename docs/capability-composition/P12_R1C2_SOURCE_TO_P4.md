# P12 R1C2 — exact Source references to real P4 candidates and Plans

## Baseline, ownership and scope

Continue original PR #78 on `codex/capability-composition-p12-retirement-preflight-v1`
from `aa9fe3f7da7d3280cd4010cf17e6371aa575f2f5`, exact native Git tree
`b05b94e86e40ce04cfde72567e94495428e84e62`. The supplied 2,607-file snapshot was
verified file-by-file against its native blobs and reconstructed that exact tree.
Local checkpoint commit IDs are not the remote commit ID. Main is not merged.

The predecessor was NOT finally accepted: Architecture 35360252754 and P14
35360252735 failed, while P19 35360252742 succeeded. This batch repairs the
reproduced exit failures as part of the same regression obligation. No previous
partial pass, failed union or fixture result is relabeled as complete acceptance.

## Two concrete predecessor defects, reproduced before correction

The new R1C1 handler always sent `capability_packet=None` as a keyword. Existing
base-only two-argument output sinks therefore failed, including P14's unchanged
fail-open enrichment tests. When no capability exists, the handler now uses the
original two-argument emit call. Nonempty capability packets still take the typed
path; there is no broad TypeError catch or hidden packet discard.

The P8 parent-claim test genuinely starts a Gateway, which installs process-global
continuity/regenerative callbacks. It closed the Gateway without restoring the
previous surrounding callbacks; a later original epoch test correctly refused
that closed runtime. Only this fixture's teardown now restores the exact old
objects through monkeypatch cleanup. Real startup, original authority checks and
all original assertions stay active. A nested regression verifies restoration.

Unchanged focused selection: 3 failed / 11 passed before; 14 passed after repair.
Both original failing Windows ZIPs were downloaded, hash-checked and extracted:
- shard 1 artifact 10553459831: SHA-256
  c73e9dd2f9a31b41ca468963056488d99a32f6976726bd053565c219ae1284ef;
  674 passed / 2 failed / 2 skipped, all 678 allocated nodes finished.
- shard 7 artifact 10554915283: SHA-256
  0a720e936041428b57af80c885e11d0169c0d129b13bd38f412f79ac3c1bdb39;
  671 passed / 1 failed / 5 skipped, all 677 allocated nodes finished.
These are failed historical runs, not current-candidate evidence.

## Connected system entry, not another planner

`WorldContextIntegration.prepare_composition_for_turn` obtains the same normal
CONTEXT_REQUEST emission, retaining existing scope/task/state/query guards.
It calls the already-installed V3 Gateway/World reader, which exposes a pre-Plan
method on MethodRunSourceResolver. The existing sealed-Plan `.read()` API is not
misused, relaxed or replaced. Both methods still use the same existing Stores.

The new helper performs integrity preparation, not execution or publication:
1. Bind the query to the persisted inbound text, active request/run/generation,
   native correlation identity, exact principal/workspace and current WorldState.
2. Recheck the display references against the same entity/descriptor/source/frame/
   cut/dependency identities. Never treat a display candidate number as a P4 ID.
3. Reuse P8 native-Git/bundle/source-input checks to reconstruct Tool primitives;
   use the existing P9 reader to revalidate the state's signed Method archive.
4. Match complete SourceRevisionRefs and original Tool/Method world fingerprints.
   Call the original `build_candidate_snapshot`, not a duplicate candidate builder.
5. Derive CompositionCompileContext from system-owned persisted identities and
   return an immutable preparation plus a typed prompt with REAL P4 candidate
   IDs, candidate digest, goal and small Proposal ABI. Display-to-P4 mapping is
   explicit because task-priority display order can differ from canonical order.
6. After model I/O, `compile_composition_for_turn` revalidates the exact sources
   and preparation, then calls original one-repair parsing, Plan compilation and
   tri-state validation. Recheck current generation/head before returning.

P8 Tool coordinates (bundle, commits, measured entry, repository/worktree IDs)
and the separately supplied system Action registry are operator/runtime inputs.
They are never extracted from a model's Proposal or narrative. Matching them
proves source identity, not active installed-release registration. Existing P8
build/review/running-manifest blockers are retained and NOT approved by this seam.
The selected Method world must have its normal configured signed archive reader.

The methods are explicit system-invoked planning APIs on the existing bridge.
This batch does NOT automatically route every legacy conversation through them,
add a model-call loop, configure a provider, or switch the default Planner. The
existing HTTP client can perform I/O between preparation and reply compilation,
outside Gateway/World locks. The wire-level regression uses precisely that client
and protocol mapper; only endpoint settings, DNS and socket I/O use test fixtures.

## Output and failure contracts

Returns the existing real PlanIR and original validation result. The result's
`may_authorize` and `may_execute` are false. The principal source-backed fixture
correctly returns UNKNOWN because its observed Tool evidence is incomplete; this
is not upgraded. An original invalid-time finding returns PROVED_INVALID unchanged.
Even PROVED_VALID is bounded mechanical validation, not natural-language success
or permission. Existing activation/registration, Policy/Ticket/Grant/A5 and P19
completion must independently admit any subsequent execution.

No source code is imported during preparation. Changed mutable checkout bytes do
not change a pinned native Git revision. Altered bundle/archive bytes, source or
registry drift, changed request/generation/workspace, rehashed preparation changes,
old queries replayed to another same-text request, or advanced World heads fail
closed. No `latest` fallback is invented. Registered historical Plans retain their
separate existing Source retention and lifecycle semantics.

Preparation is not a durable registration or new retention owner. A state evicted
before a reply is rejected, not rebuilt. It may be prepared again for a new current
request/query; no persisted resume/automatic retry is claimed by this batch.

## Tests, guards and evidence boundary

Tests use real temporary native Git, the existing P8 build-evidence bundle and
P9 signed Method archives with synthetic test keys. They traverse the actual
production source ingress, existing one WorldState materializer, persisted Gateway
request/lease, installed reader, V3 context bridge, existing HTTP mapper, and the
original P4 parser/compiler/validator. No external model, actual tool execution,
production key, P11 live comparison or Source-review approval is claimed.

The initial new positive test used the wrong Proposal fixture shape. The original
strict parser rejected it; the test now uses its existing canonical fixture helper.
No parser/schema change was made. Original red evidence and an interrupted local
test invocation are retained; only terminal final runs count as passes.

Verification Plane advances explicitly to 1.19 with the new helper added to the
inherited frozen surface. No old frozen member is removed. Store v33, original
contract/schema implementations, Action registry, sandbox, risks/permissions,
product timeouts, Golden corpus, independent fingerprint, Architecture's eight
Windows shards/full union and full Ubuntu selection remain unchanged.

The existing focused workflow keeps all old groups and input pins and adds
Source-to-P4 plus same-process parent/epoch/base-output regressions. New candidate
remote results must be recorded separately from local Linux results and prior
commits. Test groups overlap and must not be added as unique executions.

## Completed local checkpoint before publication

Linux/Python 3.13.5, pytest 9.0.2, Pydantic 2.13.4, not the remote locked runtime:
- New source-planning suite: **33 passed**, 58.49 seconds, no skips.
- Expanded P4/World/P6/P9/P8/context/version/Golden group: **303 passed**,
  199.04 seconds, no skips.
- Preserved five product modules: **147 passed / 1 Windows-only skip**,
  3 subtests, 218.46 seconds.
- Same-process Gateway predecessor/epoch/P14/continuity/shadow group:
  **58 passed**, 256 subtests, 28.79 seconds.
- Original R0/R1A diagnostic contracts: **54 passed**, unchanged.
- Normal Source Authority, committed mirrors and structural assertions pass.

The source-backed fixture returns UNKNOWN, and an invalid-time fixture returns
PROVED_INVALID, identically to the original validator. Neither is authorized.
Groups overlap and are not additive. Full local/remote acceptance, native Windows
results and current-candidate artifacts must be reported separately once complete.

## Next gate and remaining P12 work

Accept this batch only on its own final source-bound regression evidence. Then
connect accepted planning results to the existing controlled admission flow,
complete capability/experience coverage and replace the legacy HTTP injection,
static startup/selector/query consumers while preserving explicit intent and
machine-fact progress. None of those consumers is deleted by this change.

P11 live observations/faults/single-path traces/metrics/independent review/merge,
and retained P8/P9 obligations remain. Stage-merge progress remains 11/18 = 61.1%
unless a separate actual merge changes it. This is not product readiness or
P12 phase completion. No main merge, deployment or automatic default cutover.

Rollback: revert this source batch explicitly, restore its interface additions
and regenerate mirrors/freeze under the declared version procedure. No Gateway
schema migration, new Store or runtime registration is involved.
