# P12 R1B — shared capability manifest separated from static selection

## Verified entry and exact source

Continue PR #78 on the same existing branch from HEAD
`0354b24fce746427d1931fad718e1bd5d596904c`, tree
`b83dd9e355d9d107c394dccd6a4347b26d6657ef`. Main and P11 PR #77 are not modified.
The local checkout was reconstructed from the earlier complete tracked-source
snapshot plus the four actual 0354b24 changes. Native Git tree equality was checked
before editing; a local checkpoint commit is not claimed as a GitHub commit.

At entry all four current-baseline workflows had terminal success:
Architecture 35320950460, P14 35320950449, P19 35320950516, and the exact-HEAD
P12 native/fixture focused workflow 35320945912. The Windows union artifact
10537437465 was downloaded and verified (ZIP SHA-256
`0d0a231dddf65bb2833a09c3845e9afda62ebd8a4c585037b75709a81201ddc0`).
It records a complete disjoint eight-shard union: 5,349 collected, 5,317 passed,
32 skipped, run 35320950460 / attempt 1. Architecture used PR merge b28cf147,
whose tree matches the baseline tree; this is not relabeled as a branch checkout.
The union summary was inspected; this batch did not independently download and
recompute all eight underlying reports. Skips are not production acceptance.

This closes the previous engineering CI collection for R1A and permits this
bounded structural R1B batch. It does NOT close P11 or authorize production
retirement. Historical red runs and their fixes remain in earlier records.

## Production changes and consumers

`src/total_gateway/capability_manifest.py` becomes the only implementation of:

- `SkillSelectionError` and `LoadedModelCapabilityManifest`;
- `_strict_json_pairs` and `_routing_side_effects`;
- `load_model_capability_manifest` and `compile_composition_execution_manifest`.

The six definitions are moved verbatim; normalized AST hashes are pinned to the
pre-extraction code in the new regression module. Every remaining static-selection
definition was compared before/after and is unchanged. The old file re-exports the
same shared objects: no forwarding wrappers, second compiler, second registry or
alternate executor. Static catalog/query functionality remains available.

`tool_source_launch.preflight_source_revision` now imports the shared loader
directly. Its body, source/release checks and failure behavior otherwise stay the
same. Existing `orchestration.py` is deliberately byte-identical: its old imports
resolve to the shared objects through the compatibility exports. This preserves
the production startup/catalog requirement while separating implementation ownership.
It does not assert that orchestration no longer needs static selection or that the
old import surface has already retired. `skill_authority.py` keeps catching the
same exported exception object. The shared module can import, load and compile a
real manifest in an isolated interpreter without importing skill_selection or
reading deliverable_skills / skill_router_index.

All public exception names/messages, loaded-value fields/frozen behavior, exact
release-byte versus parsed-document hash domains, one-read authority binding,
Registry/schema/risk/effect joins, limits and returned contracts are retained.
Defining-module/traceback provenance changes to the shared file; old trusted pickle
globals resolve through aliases. No claim of unchanged __module__ strings is made.

## Tests and versioned authority coverage

The existing P8 byte-pinning test monkeypatched compile_action_authority on the old
module. Leaving that target in place after extraction would make the interception
ineffective. Only its module-alias import changes to the defining shared module;
all test functions/assertions and legacy public imports remain unchanged. A new
positive sentinel test proves the patch actually intercepts the loader, while the
negative test proves unpinned bytes never reach it. The existing focused workflow
allows precisely this one import-line transformation when checking original test
bytes; it does not exempt the file or relax the assertions.

New tests cover same-object compatibility, six AST identities, no duplicate bodies,
both isolated import orders, shared-only real-manifest loading/compilation, immutable
loaded values, legacy pickle names, identical outputs, strict JSON error identity,
active P8 interception, exact direct-consumer import and frozen authority membership.
Existing Source launch, action/result schema, Skill selection/authority and complete
composition/Grant/step/native/fixture regressions remain selected in the workflow.

Verification Plane advances explicitly from 1.16 to 1.17. The new shared source
path is ADDED to the inherited frozen authority surface; no existing member is
removed. The original source mirror generator and freeze generator run normally.
Store v33, Golden corpus, independent fingerprint, schema versions, registry,
permissions, sandbox implementation, product timeouts and pytest sharding rules
are unchanged. Source ownership needs no new editable root or mapping.

## Local evidence and retained errors

Local environment: Linux/Python 3.13.5, pytest 9.0.2, Pydantic 2.13.4 (not the CI lock).
Initial preserved loader/compiler/selection suite: 51 passed.
The first new test run had 22 passed / 1 failed: the test used canonical JSON bytes
while asserting the raw-byte and canonical-document hashes differed. Those hashes
can legitimately coincide. The fixture was corrected to deliberately non-canonical
JSON, preserving the hash-domain assertion and all product code; original red log
is retained. The shared/Source/schema/selection regression then passed 94 tests.
Final combined product/Golden/diagnostic outcomes are recorded in the delivery logs
and PR checkpoint against the delivered source, not inferred from those first runs.

This executor cannot directly resolve github.com for cloning; connector reads are
available. Complete tree identity, not a July ZIP or remembered SHA, establishes
local provenance. The single VRM LFS asset remains a pointer. No local native Windows
or hydrated installer is claimed.

## Exit, next work and rollback

R1B code and local tests do not inherit baseline green checks. Read new exact-HEAD
focused and full PR workflow outcomes before calling this batch accepted. The
read-only workflow preserves native tests, fixture-isolation tests and full source
identity, adds the shared consumer group, and fails if any selected group fails.

After R1B validation, review static-context consumers individually: preserve explicit
user intent, replace learned static-Skill context through the existing World context
slot, and keep fact-derived router progress. Actual disabling/removal still requires
P11 live matrix, controlled faults, single-path traces, independent review and merge.
P8/P9 retained debts remain; P13 registry/release removal and P14 dynamic-default
switch are not performed. Do not equate this source split with full behavioral
migration of the 34 indexed Skills or static production zero use.

Rollback is a normal revert of this bounded commit including the shared file,
compatibility exports, Source consumer import, test seam and version/freeze records.
No database migration, new runtime, deployment switch or change to main is involved.
Stage-merge progress remains 11/18 = 61.1%; P12 remains unmerged.
