# v1.2 P8–P17 implementation and acceptance ledger

Status: **USER HANDOFF — P8 IN PROGRESS; stop after online synchronization (2026-09-05)**.

The user requested that this checkpoint be synchronized online and work then
stop for a task update in chat. This is not phase completion. Do not resume
implementation, repair CI, merge, or start P9 without the user's new direction.
See `P8_HANDOFF_2026-09-05.md` and `P8_RESUME_PROMPT_2026-09-05.txt`.

## Scope and baseline

The accepted objective is the complete v1.2 P8–P17 continuation, including
outstanding product evaluations from earlier stages. Closing a component or
passing CI does not close the total objective.

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`.
- Master plan: `天工造物V3_世界理解驱动ToolSkillSource组合执行体系_详细工程实施计划_v1.2_全链源码审计版.txt`.
- Master plan SHA-256: `691e857e3a7f75d9107606bbffe2919bd484c03e25f1255cebd1bb069f89f895`.
- Starting main, fetched on 2026-09-05:
  `9a3344de9fe468fa845d2ff501166484439b8ec4`.
- P7D.2 candidate: `5f0601a5a1a75729362f1cb11b6a5ad9fb63186d`;
  [PR #72](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/pull/72)
  merged at the starting main above.
- P7D.2 [acceptance evidence](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/pull/72#issuecomment-5543123890)
  binds all nine successful PR checks to that candidate. The earlier
  `P7C_P7D_PROGRESS.md` is a pre-merge snapshot, not current remote status.
- Starting main post-merge gates: Architecture
  [33892621500](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33892621500)
  and P19
  [33892621577](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33892621577)
  both completed successfully on the starting main SHA.
- P8 worktree: `C:\Users\77571\Documents\天工造物v3-p8`.
- P8 branch: `codex/capability-composition-p8-source-evolution-v1`.

## Stage register

| Stage | Required result | Status | Closing evidence still required |
|---|---|---|---|
| P8 | Source candidate → build/static/sandbox/risk/evidence/review → existing manifest compiler → published source revision; running manifest lock | IN PROGRESS | Production path, adversarial and execution evaluation, local/remote gates, PR and merge |
| P9 | Method Source add/update/remove lifecycle | NOT STARTED | Production lifecycle, invalidation, tests, PR and merge |
| P10 | Knowledge / Source Evolution / Composition Experience learning cutover across every old publication entry | NOT STARTED | Frozen old publication, actual usage telemetry, replacement wiring, tests, PR and merge |
| P11 | Static/dynamic formal Shadow differential with only one execution path per task | NOT STARTED | Approximately 200 cases, model/fault matrices and all cutover metrics |
| P12 | Retire Static Skill Planner, context injection and full Skill publication | NOT STARTED | Stable new path, no lost capability, telemetry and removal evidence |
| P13 | Retire old learning / registry / release compatibility authority | NOT STARTED | Zero production usage evidence before removal |
| P14 | Dynamic Capability default with explicit, audited migration fallback only | NOT STARTED | Default wiring, fail-closed context, tests and cutover evidence |
| P15 | Source Revision → World invalidation + experience staleness/revalidation | NOT STARTED | Integrated existing invalidation DAG and regression evidence |
| P16 | 150-round long-horizon/restart/reconnect/replan/compaction/source-update/repair certification | NOT STARTED | Real execution trace and identity/authority checks at every boundary |
| P17 | Production cleanup and full source/world/memory/authority audit | NOT STARTED | No obsolete production paths, all master requirements mapped to evidence, final main green |

## Acceptance obligations carried across stages

These must remain pending until actual evidence is inspected. Test counts alone
are not product evaluations.

| Obligation | Required evidence | State |
|---|---|---|
| P4 evaluation | 24 tasks × 3 models, parser/compiler/validator and one repair statistics per model | UNVERIFIED |
| P6 evaluation | 50–60 tasks, retrieval/precision/context token/experience recall/cross-model variance | UNVERIFIED |
| P7/P8 evaluation | 80–100 tasks through Gateway, Runtime, P19, source locks and resumption | PENDING |
| Formal model matrix | Core 80 × 4 models; long-tail 120 × primary and weak model; fault 40 × models | PENDING |
| Formal Shadow | Approximately 200 task cases, only one path executing per task | PENDING |
| Cutover metrics | Verified success ≥ old path; false completion, A5 bypass, unauthorized action and drift misuse all zero; bounded stale reuse/parse failures; weak-model results; lower context cost; stable restart identity | PENDING |
| Fault injection | Source drift, unavailable tools and misleading experience first; permission/provider/schema/manifest/workspace/ambiguous Effect/verifier/context/interruption next | PENDING |
| Old-path telemetry | Actual origin, observation interval, workload coverage and zero-production-use evidence | PENDING |
| Long horizon | 150 rounds with all specified interruption and revision transitions | PENDING |

## Execution and release rules

1. Fetch current main before each stage; use a separate worktree and `codex/`
   branch. Later stages start only after the previous stage is merged and its
   main ancestry is verified. A stage may need smaller reviewable PRs; that
   does not remove any stage requirement.
2. Read the full authoritative sources to be changed and inspect their callers
   before editing. Preserve user changes. Generated copies come only from
   official sync.
3. Preserve the existing single Gateway, Runtime, WorldState, Memory SSoT,
   Registry, Policy/Ticket/Grant, Effect/Fact, P19 and CompletionGate.
4. Source proposals, embeddings, World context, experience and hypotheses
   never authorize actions. Candidate source cannot be imported by the current
   request. Source publication does not change a running manifest.
5. Preserve the current A0 composition limit. If later requirements need A1+
   admission, first produce a concrete independent risk/permission audit and
   seek the explicit decision required by the accepted objective.
6. Freeze old publication before removal. Remove old planners/registries only
   after Shadow, stability and actual zero-use gates. PR #66 stays retired.
7. Per-stage evidence includes focused contracts, adversarial/fault tests,
   generated mirrors, Source Authority, P14, P19 Golden, Python and Node full
   regressions, and Ubuntu/Windows CI. Inspect workflow coverage rather than
   relying on GitHub's smaller required-check subset.
8. Local, remote branch, PR and workflow heads must agree. Any candidate edit
   invalidates the old gates. Record evidence in an append-only PR comment,
   merge with a head guard, and verify main ancestry. Record subsequent main
   results separately. Never admin-merge or weaken a test to obtain PASS.
9. Missing models, access or telemetry stay explicitly pending; never convert
   simulation, a skipped test or a unit-test count into production evidence.
10. The objective closes only after all stages and all requirements above have
    current, adequate evidence, final main gates pass, and no unexplained
    pending obligations remain. Then stop.
11. User execution-cadence clarification (2026-09-05): intermediate edits and
    checkpoints run only relevant focused tests. Run full Python/Node and
    Ubuntu/Windows final gates when a major stage (P8, P9, etc.) is complete,
    not after every checkpoint. Existing live runs may finish; do not restart
    them or push repeatedly just to trigger another full regression. If a final
    gate reveals a failure, first diagnose and repair with focused tests, then
    validate the final merge candidate. Exact-head final acceptance is unchanged.

## P8 initial audit findings

- `scripts/sync_omni_capability_manifest.py` currently refreshes a previously
  stored manifest and schemas. It does not rebuild all routes from current
  source.
- Existing `v3.fact_kernel.compile_manifest` is the runtime compiler and must
  remain the compiler. Its runtime projection and the Gateway's published
  projection currently use different source-hash payloads.
- Baseline diagnostic using the current source compiler found 791 actions / 291
  executable, compared with 790 / 290 in the published manifest. Published-only
  IDs are `browser.image_search` and `web.image_search`; source-only IDs are
  `mcp.servers.list`, `mcp.tools.list`, `mcp.tool.call`.
- There are also effect-class differences between source metadata and the
  published manifest. Automatic publication must expose these differences for
  risk review, not silently lower existing permission floors.
- P8 must bind source bytes, compiler inputs, compiled output, validation and
  review to the same candidate. Self-consistent hashes are not reviewer or
  sandbox evidence. Git remains the source revision authority.

## Evidence log

Local P8 artifacts are now under `output/p8-evidence/`, the repository's
existing artifact-output location. Historical `out/p8-evidence/` references
below identify the original location; all ten existing files were moved
without changing a byte and each SHA-256 was checked before and after.
No source gate was changed or weakened to relocate these non-source reports.

- 2026-09-05: fresh main/remote/worktree inspection completed; no interrupted
  Python or GitHub watch process found. P8 isolated worktree created at the
  successful main baseline. Implementation and acceptance are still pending.
- P8 foundation IMPLEMENTED (not phase-closed): immutable Git source candidate
  observation, native object verification, private exact-byte materialization,
  committed-manifest review CLI, existing `compile_manifest` Gateway projection,
  direct and alias-inherited permission differential, and an isolated build CLI.
  No source publication, runtime admission expansion or manifest replacement
  has been performed.
- LOCAL PASS, component observations before the first checkpoint: source
  candidate suite 22 passed; manifest evolution + prior schema suite 32 passed;
  strict sandbox suite 9 passed, including the actual Windows AppContainer
  host-file/parent-secret isolation test. These overlap with later regressions
  and are not product task/model evaluations.
- LOCAL PASS: official generated-source write/check and Source Authority
  check (16 independent authorities, 1 alias, 24 generated targets, 1
  closed-world boundary).
- Preserved first-checkpoint test failure: the expanded candidate suite stopped
  at 23 passed / 1 failed. `test_corrupted_native_tree_identity_is_rejected`
  correctly rejected the damaged Git tree, but expected the application-layer
  `object bytes` message. This Git build rejected it earlier during `rev-parse`
  with `Git object is absent or incompatible`. The test must accept both
  rejection boundaries and retain independent coverage of native hash checking.
- PENDING: isolated compilation against a committed P8 candidate, evidence
  contract execution, source publication/review integration, production running
  lock validation, product/model evaluation, full local and remote gates, PR
  merge and phase closure.
- Failure checkpoint retained in Git:
  `f4fd846744a9f956dcf77d9a87006645ec87cb12`. The corrupted-object test now
  accepts Git's earlier rejection, with three additional direct native hash
  substitution tests. Expanded local component run: 72 passed. Its CLI test
  revealed a Windows stderr decoding-thread warning; the subprocess now
  explicitly selects UTF-8, with clean rerun pending.
- First real isolated-build attempt on that checkpoint was rejected before
  candidate execution: `source build archive exceeds its size limit`, captured
  in local `out/p8-evidence/foundation-isolated-build-1.json`. Native Git input
  inventory is 2,461 files, 1,530 unique blobs, 52,434,364 bytes. The archive
  route was removed: it can apply attributes, EOL and LFS/custom filters and
  is unsuitable for exact source-object materialization. The replacement reads
  native blobs through `cat-file --batch`, verifies each object's hash and
  writes only the private snapshot. LFS pointers stay pointers; native-asset
  hydration is not claimed by this source-compilation step.
- Native-object export and UTF-8 correction LOCAL PASS: 72 component/schema
  tests, with only the pre-existing Pydantic `schema` shadowing warning.
  Parent build guards: 8 additional tests passed. Those eight use simulated
  child results and are explicitly not isolation or product-evaluation evidence.
- Second checkpoint: `3f9a8d8932f2adc0eefa98107cb0b560536ad97a`.
  Full Python stopped at 33 passed / 1 failed on the expected P19 authority
  freeze change in `v3/fact_kernel/__init__.py`; JUnit evidence is retained in
  `out/p8-evidence/full-python-3f9a8d8.xml`. Verification Plane 1.6 is now
  explicitly declared with the old freeze coverage retained and P8 source
  review/build boundaries added. Generator refresh and clean guards are required.
- Node on that checkpoint initially failed six modules because this new
  worktree had no `app/node_modules/three`; no product code was changed for
  that environment failure. Locked `npm ci --ignore-scripts --no-audit
  --no-fund` installed 301 packages. Exact-head rerun: 224 passed, 2 skipped,
  zero failed; log `out/p8-evidence/full-node-3f9a8d8.log`.
- Second isolated-build rejection is retained in
  `out/p8-evidence/foundation-isolated-build-2.json`. Diagnosis reached the
  precise failing call: `SandboxRunner._copy_workspace -> shutil.copy2 ->
  _winapi.CopyFile2`, destination length 261; source and destination parent
  both existed and host `LongPathsEnabled` was 0. Strict source builds now
  use the extended Windows path namespace, including worker imports and
  private-tree cleanup. No host registry setting or permission was changed.
  Sandbox + parent guards: 18 passed, including real deep-path AppContainer
  copy/read and cleanup. Full source compilation still requires a clean rerun.
- Third checkpoint: `78c8a86d06bb5556e3858abdcb59f726ac6a1164`.
  Real immutable-source build reached `ISOLATED_BUILD_OBSERVED` inside Windows
  AppContainer (9.344 s child execution), with 1,279 Python files parsed by the
  trusted parent and a healthy source topology. Compiler output: 791 actions /
  291 executable. Artifact SHA-256:
  `1e00cfcebfebcde62507e570aa1ce5b4fb35e7638658054c8a0c4058002d9729`;
  local report `out/p8-evidence/foundation-isolated-build-3.json`.
  Its published-manifest differential exposes 99 effective-risk downgrades and
  42 newly-A0 candidates. These are NOT accepted permissions. The committed
  Manifest does not match the new build; `may_publish` remains false.
- Node on the third checkpoint: 224 passed / 2 skipped / 0 failed. Full Python
  passed the new P19 freeze guard, then stopped at 372 passed / 1 failed / 73
  passed subtests because the historical P7B.2 compatibility test still expected
  plane 1.5. That assertion now explicitly expects declared 1.6 while retaining
  exact Store v33 and all activation-eligibility tests. Full rerun pending;
  failed JUnit retained in `out/p8-evidence/full-python-78c8a86.xml`.
- Fourth checkpoint: `add2e1f12920a836230c948f772eb50a612c816f`.
  Isolated build 4 reached `ISOLATED_BUILD_OBSERVED`; publication remained
  forbidden. Node: 224 passed / 2 skipped / 0 failed. Full Python first stopped
  at 815 passed / 1 failed / 108 passed subtests because earlier JUnit reports
  in `out/` contained Windows CRLF. These generated reports are now preserved
  in `output/p8-evidence/`; no test assertion or source verifier was changed.
- The same-head rerun passed that source gate, then stopped at 2,412 passed /
  16 skipped / 1 failed / 413 passed subtests. Exact cause: P8 manifest review
  in World Understanding imported `total_gateway.action_registry`, violating
  the existing P17-M4 dependency direction. Evidence:
  `output/p8-evidence/full-python-add2e1f-rerun.xml`. Review is now moved into
  `src/total_gateway/tool_manifest_evolution.py`; there is no World re-export
  and the architecture guard is unchanged.
- Source input revision binding IMPLEMENTED: trusted parent observation of
  editable/frozen source bytes and dependency inputs, exact child comparison,
  and an optional source-input binding on the existing compiler projection.
  Handler-only source changes now produce a different whole-document Gateway
  authority identity without changing permission semantics. Generated Manifest
  output and generated mirrors are excluded from their own input digest.
  This is not proof of source publication or production running-lock behavior.
- LOCAL component observation before the next checkpoint: 73 passed / 2
  skipped across source-input, build-parent, manifest and architecture tests.
  The two skips are host-denied symlink creation, not successful isolation
  evidence. New source-input cases and all final gates still require reruns.
- Model resource preflight: the application-defined `api_keys.json` contains
  provider/endpoint/model configuration metadata only; no credentials were
  printed or changed. Availability through the existing desktop credential
  vault has not been verified. Model selection/weak-model control and batch
  call budget have been requested before paid batch evaluation begins.
- Expanded local pre-checkpoint run: 171 passed / 2 skipped, covering all P8
  source/build/sandbox/review suites, the unchanged P17-M4 architecture guards,
  and the full P19-R2 Golden directory with UPDATE_FREEZE absent. Official
  generated-source and Source Authority checks passed. This is component/local
  evidence only; the new committed candidate still needs isolated-build and
  full exact-head regression evidence.
- Fifth checkpoint: `51dc3e778e84497644db8b6c9525ea1268d6d38c`.
  Isolated build 5 observed Windows AppContainer with 677 source input files;
  source-input digest `28d989fa6f01d911690de86ffa893e517543c06cc02d6bef28379df537e47afd`.
  Node: 224 passed / 2 skipped / 0 failed. Full Python stopped at 3,791 passed /
  19 skipped / 1 failed / 847 passed subtests in 936.58 seconds. Evidence:
  `output/p8-evidence/full-python-51dc3e7.xml`. Exact remaining failure was
  World's existing no-process import guard: P8 candidate observation imported
  subprocess. Candidate and input-evidence modules now move into Gateway
  lifecycle tooling; no guard or ownership-policy exception was added.
- The new compiler projection also exposed a real loader integration failure:
  its eighth root field was rejected by the production loader. Reproduction
  retained in `output/p8-evidence/loader-pre-fix-51dc3e7.xml` (new test, original
  production source; 1 passed / 1 failed). The loader now explicitly validates
  the optional source-input digest while preserving exact release bytes,
  strict unknown-field rejection and single-read model/registry projection.
  New tests cover replacement, mixed source authorities, tampering and malformed
  extensions. Their results and all affected final gates remain pending.
- Source publication remains disabled. The 99 risk downgrades and 42 newly-A0
  candidates from build 5 are not approved. P8 still requires real publication,
  running-task locks, evidence-contract execution and product/model evaluation.
- Local pre-checkpoint validation after the moves and loader integration:
  62 loader/World ingress/architecture tests passed; the expanded P8 source,
  sandbox, loader, P7D.1 execution-manifest and P19 Golden run passed 196 tests
  with 2 host-denied symlink skips. Source Authority and official generated
  mirrors passed. These are pre-checkpoint observations, not final-head gates.
  The first loader rerun (31 passed / 1 failed) exposed a test assertion that
  incorrectly expected source-bound permission hashes to stay unchanged. The
  corrected test requires changed permission identity and unchanged policy
  semantics. Both JUnit results are retained under output/p8-evidence/.
- Sixth checkpoint: `9476b0af1a27a530f89da9ee402d5d5e22c43b95`, pushed as
  [draft PR #73](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/pull/73).
  Exact-head full Python completed: 4,210 passed, 19 skipped, 847 passed
  subtests, zero failures; JUnit `output/p8-evidence/full-python-9476b0a.xml`,
  SHA-256 `a9c86120170eb6c589c094c7f5ff29418aad96a25cd9e09f0bb578f9a32a9c3a`.
  Node: 224 passed / 2 skipped / 0 failed. P14 and prior World boundary tests:
  260 passed. Isolated build 6 observed actual AppContainer containment and
  exact source inputs; no publication or risk downgrade was approved.
  Remote P19 completed successfully on both platforms. Eight of nine PR checks
  are observed successful; Architecture Windows full regression remains live
  in `Run full repository pytest` in run `33940906572`. This is not REMOTE PASS
  for the whole candidate, and the draft is not ready to merge.
- Source-to-Tool-World bridge IMPLEMENTED: the trusted build parent now derives
  Action SourceRevision records from measured input closure and the verified
  ACTIONS entry-module binding. It reuses existing Registry/schema and P2 World
  compilers; P4 consumes their existing SourceRevision contract. Input records
  reject malformed types/paths/sizes, collisions, missing ownership identity
  and self-including generated Manifest records. Conservative closure identity
  changes on helper-only edits; implementation references are module-level,
  not claimed leaf-handler dependency analysis.
  Pre-checkpoint component run: 124 passed / 2 host-denied symlink skips,
  `output/p8-evidence/source-world-first.xml`. The output remains review data:
  `tool_world_ingested=false`, no Source approval or production publication.
  Real build and final candidate gates remain required.
- The source-to-World change passed the existing World ingress/P17 architecture
  and P19 freeze/uniqueness guards: 33 passed, 16.19 seconds, with UPDATE_FREEZE
  absent (`output/p8-evidence/source-world-boundaries.xml`). The first expanded
  command referenced two nonexistent test filenames and collected no tests;
  it is not PASS evidence. Corrected checks used the repository's actual files.
  Per the user's new cadence, no intermediate full regression is started.
- Seventh checkpoint: `3cba1d7bb3cdb5f30a6428896798d5a87ad75156`, local only.
  Actual isolated build 7 produced 291 Tool World primitives / 1,823 relations
  from 678 measured input files. World snapshot SHA-256:
  `80e08c955db966a7fbedcbae5d6fc2d90cadefd550d4f11f90e428157652ce82`.
  Report `output/p8-evidence/foundation-isolated-build-7.json`, SHA-256
  `ae1b4c5068b524ccb3ba11d4dda67b16c4d413601927f60bb9f8ec305a5bbb52`.
  Windows AppContainer / network denied were observed. World ingestion and
  Source publication remain false. No intermediate full gates were launched.
- Subsequent remote observation for the older published checkpoint `9476b0a`:
  Architecture run `33940906572` completed SUCCESS, including Windows full
  regression. Together with P14 `33940906555` and P19 `33940906569`, all nine
  checks now passed on that exact older head. This does not validate later
  local commits or close P8.
- Source metadata compiler defect reproduced on `3cba1d7`: explicit
  `effect="exeucte"` was silently normalized to `read` instead of rejected.
  Pre-fix failure retained in `source-metadata-pre-fix-3cba1d7.xml`.
  The existing compiler now rejects malformed explicit risk/effect/implemented/
  alias fields and invalid/undeclared dynamic route identities. Omitted legacy
  defaults are unchanged; this is not a resolution of the 99 published-risk
  differences. Focused compiler/schema/build/World checks: 151 passed, 5.74 s,
  `output/p8-evidence/source-metadata-post-fix.xml`. Official mirrors and Source
  Authority passed; the declared 1.6 freeze was regenerated and checked with
  UPDATE_FREEZE absent. Real-catalog isolated validation remains required.
- Eighth checkpoint: `c4a0635435019f6ec972059ee879bb75b07edf2b`, local only.
  The real 791-action catalog passed isolated build 8 after strict metadata
  validation. It produced 291 Tool World primitives from 678 measured inputs.
  Report `output/p8-evidence/foundation-isolated-build-8.json`, SHA-256
  `776f3f34ee6d4c4164dbfcc489cbc7f6bba1a0712002a154c05aa5229dfb9c46`.
  Publication remains false. No intermediate full regression was launched.
- A real issuer/consumer seam defect was reproduced at that checkpoint:
  Gateway's durable P7D.2 continuation issuer emitted the signed attempt,
  delegation and dependency fields, but the Omni consumer rejected them as
  unknown. The original single-step grant passed. Evidence:
  `output/p8-evidence/omni-continuation-pre-fix-c4a0635.xml` (1 passed / 1 failed).
  Consumer validation now accepts only the existing complete continuation and
  predecessor shapes, with exact runtime JSON types, unchanged signature,
  A0 ceiling and nonce checks. Gateway/Store still own retry eligibility/CAS.
  The first rerun (2 passed / 1 failed) exposed a new-test expiry assumption:
  shared runtime_security uses an inclusive expires_at_ms. The test now checks
  rejection one millisecond after expiry; no production expiry rule changed.
  Both reruns remain retained, and malformed bindings are cross-checked against
  the existing CompositionExecutionBindingV1 contract rather than invented.
  Focused real issuer/consumer + existing Omni guards/integration + durable
  continuation tests and freeze guard: 25 passed / 13 passed subtests, 26.40 s,
  `output/p8-evidence/omni-consumer-focused.xml`. Plan inputs/clock remain a
  controlled test harness; this is not real model or product-task evaluation.
  Official mirror/Source Authority checks passed. The consumer was added to
  the explicit 1.6 authority freeze; final phase gates remain pending.
- Running-source-lock audit remains open. The inspected runtime API resolves
  the skill root at invocation and caches imports by path, while constructing a
  fresh BodyRuntime per call. Signed Manifest/Registry identity comparisons do
  not by themselves prove installed source-byte immutability, including lazy
  helper imports. The next production task is to bind the existing release /
  runtime source installation to the pinned run identity and prove X remains
  X after separate publication of X+1; no claim of a completed lock is made.
- Release-source binding defects reproduced against `ce9da53`: a same-size
  helper edit with restored mtime kept the old tree digest; a forged persistent
  cache supplied accepted digests; generation wrote cache state; and the
  existing Gateway tree omitted Tool/backend/frozen/dependency source roots.
  Four failures retained in `release-source-pre-fix-ce9da53.xml`. Release
  generation now hashes fresh bytes and leaves legacy cache files untouched.
  The existing four-tree contract is preserved; gateway-source derives its
  additional roots from validated Source Authority plus policy/dependency
  inputs. This is release provenance, not a second compiler or Source approval.
  Release, source-change adversarial, runtime-authority, readiness and P19
  focused checks: 45 passed in 49.90 s (`release-source-focused.xml`, SHA-256
  `c3ed31f01e6106aad6d7cd9e707d1ac4fccfb44634e93c94eb515d9b097cf32d`).
- Embedded startup's explicit-root defect was also reproduced: a caller-selected
  installation lost to the current checkout; incomplete roots fell back; cached
  foreign backend modules were not rejected. Untouched-backend reproduction:
  8 failed / 1 passed (`embedded-source-pre-fix-ce9da53.xml`). An explicit root
  now selects only itself and validates backend module/namespace origins before
  imports and sys.path mutation. Uncached namespace aggregation from another
  checkout is rejected too. Module deletion/hot reload and a second Runtime are
  not introduced; unspecified-root discovery remains unchanged. The initial
  loader fixture was isolated from unrelated pytest backend search paths, with
  an explicit adversarial test for that namespace case. Focused loader tests:
  10 passed. Existing real single-process startup/owner lifecycle, composition
  transport, P17 architecture and P19 guards: 74 passed in 27.47 s together with
  the new loader tests (`embedded-source-focused.xml`). These are startup and
  component contracts, not 80–100 product tasks or a completed live-source lock.
  Official generated mirrors and Source Authority passed. Both edited runtime
  authorities join the explicit 1.6 freeze; old freeze coverage is retained.
- No full Python/Node or new remote workflow was launched for these changes.
  Final exact-head gates remain pending until P8 is ready. Immutable source
  installation/publication, shared-module/source-byte pinning across a live Run,
  the unapproved 99 risk downgrades/42 newly-A0 candidates, evidence-contract
  execution and real task/model evaluation are still open. P9 has not started.
- Source revision packaging IMPLEMENTED in the existing isolated build command
  (`--bundle`): retain the exact private-source revision, compiled Gateway
  Manifest, official generated mirrors and build report in one content-bound
  ZIP. The official mirror generator now accepts an explicit private workspace
  without retargeting module globals or running candidate-supplied scripts.
  Input closure is checked before and after generation; the package verifier
  binds every entry to its inventory and source/Manifest to the compiler report.
  Whole-file pins, portable paths, size limits, duplicate/link rejection and
  no-overwrite behavior are enforced. New versions leave old package bytes
  unchanged. No Runtime imports, publication approval or activation is implied.
  This is the artifact prerequisite for immutable installation, not proof that
  a live Run continues X after production publication of X+1.
- Package test evidence: the initial run had 22 passed / 1 failed because a new
  assertion compared an in-memory dataclass tuple to its JSON array. It now
  compares exact canonical report bytes; `source-bundle-first.xml` is retained.
  The first expanded run reached 109 passed / 2 host-denied symlink skips / 22
  passed subtests and one failed mirror subtest: the new Gateway module was not
  yet in the local generated marker (101 versus 100 files). Official sync fixed
  the mirror; no source or mirror assertion was weakened. After sync, the P8
  package/build/inputs/World, official-source, P17 architecture and P19 guard
  focused run passed 131 tests / 23 subtests with those same 2 skips in 18.99 s
  (`source-bundle-focused-2.xml`). Real committed-candidate packaged build and
  final phase gates remain pending. No intermediate full regression launched.
- Packager checkpoint `8dc2637380cbdc5d47d47d883be9618ac7f13476` was committed
  locally and real isolated build 9 was attempted with `--bundle`. The process
  exited 1 during package report serialization: SandboxRunner's finite
  `elapsed_seconds` was rejected by the signed-contract canonical serializer.
  The synthetic child had omitted this real report field. No report or ZIP
  was produced; the failed commit is retained and the observed traceback is
  recorded explicitly as a manual failure record in
  `output/p8-evidence/foundation-isolated-build-9.failure.txt`, not PASS evidence.
  Ordinary report JSON now preserves finite observations separately from signed
  Gateway contracts; nonfinite values remain rejected and the shared contract
  serializer is unchanged. Packaging failure now returns BUNDLE_FAILED while
  retaining the actual contained-build result. Focused regression: 34 passed in
  2.04 s (`source-bundle-float-fix.xml`). Real packaged build retry pending.
- Real packaged build 10 SUCCESS at exact source candidate
  `c361f6dd22cfa466fd8d99831257fe8b5e1a2862`: Windows AppContainer, network
  denied, 10.016 s child execution, 679 measured input files, 791 Actions /
  291 executable descriptors. The retained ZIP contains 2,857 indexed file
  entries (source files plus the build report), 61,235,408 indexed payload
  bytes. It was independently verified again after the build process and its
  temporary source snapshot had terminated. No candidate code was imported by
  that verification, and publication/authorization/execution flags stay false.
  Evidence:
  - `output/p8-evidence/foundation-isolated-build-10.json`, SHA-256
    `94bf5f54c2b50e15249e0ae6b920ed0c676cfbd5f694a681a02c8c9c991d2bf5`;
  - `output/p8-evidence/source-revision-c361f6d.zip`, SHA-256
    `2d772f2b68d5e3af34e411b1deeb97afafe41cff8d0571516868800260229eaa`;
  - source-input closure
    `4e8b21942233e794dbb67a9127c77311cc81c468de2c86eb3b89301e73e890c8`;
  - packaged Capability Manifest file
    `705ca21fe5d32438081637594088fa96f250f24d41303ad9f031a9f0c1d9842e`.
  The package is retained review material, not an installed/approved production
  release. Independent version-directory installation, live X/X+1 source locks,
  permission review, evidence-contract execution and product/model evaluation
  remain open. P8 stays IN PROGRESS; later stages remain NOT STARTED. No full
  regression or remote workflow was launched for this intermediate checkpoint.
- Separate source-directory staging IMPLEMENTED: the trusted staging command
  verifies the original ZIP pin and creates only a new destination, retaining
  existing/partial versions unchanged. Exact file/directory inventories, bytes,
  source-input closure and read-only file flags are verified; a changed local
  index cannot substitute for the original bundle. No activation pointer is
  changed and no candidate code is imported. Read-only flags do not provide
  isolation from the host owner/admin; launch-time byte admission remains open.
- Real retained build-10 package `source-revision-c361f6d.zip` was staged to
  `output/p8-evidence/staged-c361f6d` and independently reverified in a separate
  CLI process. Both exited 0 with `STAGED_VERIFIED_UNAPPROVED`, 2,857 indexed
  entries and the same bundle/input/Capability Manifest hashes recorded above.
  All three may_publish/may_authorize/may_execute flags remain false. The staged
  source is the original c361f6d candidate, not this later staging implementation.
  Neither verification launched it as an approved production installation.
- The existing Omni API wrapper had a reproduced source-selection defect:
  changing TIANGONG_OMNI_BODY_ROOT could move an already-loaded wrapper and its
  verifier from X to Y. All five new offline reproductions failed before the
  repair (`omni-source-pin-before.xml`), including a foreign cached verifier.
  The wrapper now prefers its own enclosing skill package, pins its initial
  root and fails if that version disappears. Standalone wrappers retain one-time
  host-root discovery; changing the environment cannot hot-reload them. Cached
  capability-verifier origin is checked just like the existing runtime origin.
  These are synthetic offline loader fixtures, not approved live-Run evaluation.
- Staging/package/loader tests first passed 36 tests. After official mirror sync,
  the focused staging/package/loader, signed capability, real single-process
  application, explicit backend-root and P19 architecture/freeze checks passed
  69 tests / 13 subtests in 20.82 s (`source-staging-runtime-focused.xml`, SHA-256
  `706d618c8d89645c18256b46cc55fc7694d3f4f380b88bf83eb528aa47292387`).
  Generated-source and Source Authority checks passed. Plane revision entries
  16/17 declare the staging and wrapper boundary changes; existing freeze
  coverage and assertions remain intact. No full regression, candidate rebuild,
  remote workflow, publication or merge was launched for this checkpoint.
  Launch admission, shared/lazy-module byte pins, approved live X/X+1 evidence,
  the 99/42 permission review, evidence-contract and product/model evaluations
  remain open. P8 stays IN PROGRESS; P9–P17 remain NOT STARTED.
- P8 source-pinned startup consistency IMPLEMENTED in the existing
  `GatewayRuntime.start`, before its epoch lease, stores and embedded services.
  The preflight requires an explicit existing release, source root, embedded
  mode and launcher-disabled bytecode writes (`-B`). The existing release
  selector and release-pinned Action loader remain authoritative; the preflight
  does not generate a release, choose a fallback or approve publication. It
  remeasures the input closure, binds the Capability Manifest, compares generated
  mirrors to measured authority bytes, rejects writable/hard-linked files and
  bytecode caches, and checks selected Skill roots, loaded module origins and
  available namespace import routes. Final orchestration assembly must retain
  the same release before its worker starts. Legacy manifests return no source
  observation and are not falsely upgraded to P8 source-attested status.
- Source-startup evidence: initial source/preflight plus existing real
  single-process startup tests passed 29 tests (`source-launch-first.xml`).
  A fresh `-B` subprocess verified the read-only synthetic package without
  importing its deliberately raising package initializer; this is an offline
  consistency fixture, not a production activation. Controlled assembly drift
  was rejected before worker start and resource/lease cleanup was verified.
  After bytecode/read-only cases and official sync, focused source launch,
  staging, bootstrap, embedded backend, wrapper pin, existing single-process
  application, Source Authority, P17 architecture and P19 freeze/uniqueness
  checks passed 104 tests / 29 subtests in 30.65 s. Evidence:
  `output/p8-evidence/source-launch-runtime-focused.xml`, SHA-256
  `7dc3c54a10f03ff06e635a33ee1883c1fecc7e791e6ea4c1de734f5c46f8de72`.
- Real build-10 installation cross-directory observation: a fresh trusted
  `-B` verifier read `output/p8-evidence/staged-c361f6d/source` against its
  original input and Capability Manifest digests. Source/mirror checks reached
  the import-origin phase, then the process exited 1 as expected with
  `source_launch.path_outside_installation`: the already-loaded Gateway came
  from this P8 checkout, not the staged c361f6d source. No candidate code was
  imported and the staged source was unchanged. This is a negative consistency
  observation, not a successful Gateway launch from that unapproved version.
- This preflight is startup consistency, not proof against host-owner tampering
  or all future dynamic imports, and it does not complete running-source locks
  or Source publication review. The legacy mutable startup paths remain for
  their separately gated cutover. Actual approved source-pinned startup,
  live X/X+1/replan evidence, the 99/42 permission review, evidence-contract
  execution and real product/model evaluation remain open. Plane entry 18 and
  the unchanged freeze generator cover the new preflight and Runtime entry.
  No full regression, new isolated build, push, remote workflow or merge was
  launched for this intermediate checkpoint. P8 remains IN PROGRESS.
- Real packaged build 11 SUCCESS at candidate
  `ea575ed6d8fd69a9b7fbd6f697aeb8236e8417f3`: Windows AppContainer, network
  denied, 9.812 s child execution, 680 measured input files, 2,863 indexed
  package entries and 61,319,698 indexed bytes. Build report
  `foundation-isolated-build-11.json` SHA-256
  `3ed67ed2bff5283fb442af2312df2de1bbd52431f3a3bcf11633eaf18cf6eab6`;
  `source-revision-ea575ed.zip` SHA-256
  `347f24d396d68cd48db0c0b5946107dd51f9050868384408f24840bd8e370212`;
  input revision `45810d2e37731b488506effae45508e15b9c0d55014d816dbe06045621793b5d`;
  Capability Manifest `a8d5bd797ae31829760ef7896a21c851d7bb71df3f862ffc84f59844e889bfbc`.
  This remains unapproved review material, not a published release.
- A trusted offline startup probe now stages a pinned package in a private
  workspace and uses the existing required AppContainer runner for a fresh
  -I/-B child. It observes source consistency, existing release generation,
  actual Gateway boot/READY/shutdown and post-shutdown source consistency.
  Parent verification still checks original ZIP/staged bytes and refuses
  incomplete, not-ready, mismatched or approval-claiming observations. Child
  code is never imported by the parent; process/child evidence survives later
  verification or cleanup failures. All permission/publication flags stay false.
- Real probe failures are retained, not reported as successful startup:
  - Attempt 1 failed in the new parent's installed-module bootstrap because
    only src, not the existing backend root, was added to sys.path. No candidate
    process or JSON report was produced. Exact failed script hash and native
    traceback are preserved in `foundation-isolated-startup-1.bootstrap-failure.txt`.
    The parent now uses the same two trusted import roots as the build command.
  - Attempts 2/3 ran in actual AppContainer with network denied and failed at
    `source_consistency`, before Gateway service startup. Path.resolve(strict=True)
    raised WinError 5. The retained package/staged bytes still verified afterward.
    Reports `foundation-isolated-startup-2.json` and `-3.json` have SHA-256
    `c9849544262932d246298c6d8247f0e676aba82fa148d0bc24471971ca76b8dc` and
    `4ce094c3abdb22f275dd0f159954511dd1be24262af2524edab6ebb1d899cccc`.
- A separate small native AppContainer diagnostic, containing no candidate code,
  established the exact failing API operation: file handles open successfully;
  DOS/GUID final-name queries return WinError 5, while normalized NT final-name
  queries on the same handles succeed. The diagnostic script/report are retained
  as `appcontainer-path-diagnostic.py` / `appcontainer-path-diagnostic-1.json`;
  the report SHA-256 is `c3e92d3c0b4e5a2bdb3eed4a18109073b0c4abb7d13a7e994d7f29f167f39ed7`.
  Windows source checks now use metadata-read handles and compare normalized
  physical NT volume/root/relative identities, with no extra ACL grant, path
  cache or non-strict fallback. Reparse/traversal/mismatched-volume cases and
  unavailable identity queries remain rejected. POSIX strict checks are retained.
- Focused source/probe/real single-process/P17/P19 tests passed 73 in 30.40 s
  (`source-launch-probe-focused.xml`). An earlier 9-test probe run had a stderr
  decode-thread warning in the fresh -I help test; explicit -X utf8 fixed the
  test invocation without changing assertions. The probe uses a compact private
  staging directory `r` instead of `revision`: measured AppContainer Skill paths
  otherwise reach 243 characters, beyond Gateway's unchanged 240-character
  configuration limit. The compact-path parent suite passed 12 in 3.15 s
  (`source-launch-probe-compact.xml`). Official mirrors, Source Authority and
  regenerated/normal freeze guards passed; plane entries 19/20 declare this
  boundary revision. A rebuilt candidate and real startup retry are required
  before claiming the native-path repair works through the complete boot chain.
  No full Python/Node regression, push, remote workflow or merge was launched.
- Real packaged build 12 SUCCESS at candidate
  `1858ce9c16ebde034dc7cb04e16d07d96d34d58b`: actual AppContainer/network
  denied, child 9.797 s, 2,866 indexed entries / 61,351,200 indexed bytes.
  Report `foundation-isolated-build-12.json` SHA-256
  `b6447be028f8fc143c317c6f71240a6d56293d7f12d42366ce605b8392cc0ae5`;
  bundle `source-revision-1858ce9.zip` SHA-256
  `2b10c2ed352385a23f4ad409d9ed9ff18c31e3d40b7cfb11001f136e99e2f1b0`;
  source inputs `4e1ed273cc90fd56067d775eb8f9784e1ac980d16a015865d3efe524c2329f64`;
  Capability Manifest `1981efbf4bf6d00ba5ab12517abef1ea409e4c743000c893dd521e42192c5638`.
- Real startup attempt 4 FAILED, retained as `foundation-isolated-startup-4.json`,
  SHA-256 `45506a5d0f8296b6eb982951d6007529c0974bb8936b6ad8d2d5c547a3148ddc`.
  The normalized NT identity check passed far enough to measure inputs; the
  child then rejected `source_launch.bytecode_cache_present`, before Gateway
  startup. AppContainer/network denial remained effective (5.985 s child),
  and the parent reverified the staged source against its original bundle.
  The eight observed .pyc inputs are committed artifacts under the existing
  `_internal/frozen_modules`, not caches produced by this -B build. Git diff
  confirmed that frozen root and source-ownership.json are unchanged between
  the stage baseline and candidate 1858ce9.
- Startup inventory now retains measured frozen .pyc artifacts as data while
  preserving the existing Source Authority and candidate frozen-edit rejection.
  It still rejects ordinary bytecode, optimized .pyo and __pycache__, including
  caches under frozen roots. Bytecode import origins inside the installation
  fail even when a custom loader uses an unrelated alias. No frozen file,
  generated mirror, authority policy, permission or production path is deleted
  or enabled by this correction. Plane entry 21 records the distinction.
- Eight new regression cases first failed (`source-launch-frozen-red.xml`).
  The first focused correction run passed 48 and failed a new subprocess
  assertion that demanded no imports whatsoever. Its diagnostic identified
  only lazy stdlib ctypes/UTF-16 imports used by native identity checks. The
  fixture now initializes those trusted stdlib bindings before taking its
  snapshot and retains the exact no-module-change assertion; candidate/frozen
  bytes are not imported. Source startup/probe plus immutable Git candidate
  tests then passed 80 in 41.44 s, `source-launch-frozen-candidate-focused.xml`,
  SHA-256 `7b593f38c45a30d0190a58b5d49fa831ff8a349ef31b4bd2ca232a1b028a6d12`.
  This is focused local evidence, not a successful real Gateway startup or
  product evaluation. P8 remains IN PROGRESS; P9–P17 remain NOT STARTED.
- Real packaged build 13 SUCCESS at candidate
  `633339988f00f50692238143aac9bdcc569c1744`: AppContainer/network denied,
  9.83 s child, 2,866 entries / 61,361,078 indexed bytes. Build report
  `foundation-isolated-build-13.json` SHA-256
  `d56bfcd2f1dc8d30fcefac287ddab86500fec610b387cba12f4c6b3f3e7f3531`;
  bundle `source-revision-6333399.zip` SHA-256
  `f239d388642ccae174165627f4ac268a4fbbf0b3e91b8cb442c99471f1879f01`.
  Source inputs `b6e6c84281127517357223e04972b82eba0f05c1cd84b5289248a703302329d5`;
  Capability Manifest `658a3d88f8a7923ef539a404f28fa9a7827b2ee483d8e1653d036f28c6504ba1`.
- Real startup attempt 5 passed source consistency for 1,964 authority/mirror
  files and retained eight frozen bytecode artifacts as data. It then FAILED
  at `release_generation`: the existing release_manifest._safe_workspace still
  called Path.resolve(strict=True), which returned WinError 5. Gateway services
  had not started. The parent independently reverified the staged package.
  Report `foundation-isolated-startup-5.json` SHA-256
  `23f44dcce014b410339e49d8327d84a61d01ddad32787b30884dadb304fd4954`;
  actual AppContainer/network denied, 7.75 s child. This is a successful source
  consistency observation inside an unsuccessful complete startup, not READY.
- Two small trusted diagnostics ran without candidate imports. Ancestor metadata
  checks remain denied outside the private AppContainer area; the native drive
  anchor and complete source-root path can both be queried and their physical
  volume/suffix comparison succeeds. Reports
  `appcontainer-ancestor-diagnostic-1.json` SHA-256
  `b4ba12bfd2d98aa6c1eaf0da1142bb53df740dfe79756441acaa31d02d9264ac` and
  `appcontainer-volume-diagnostic-1.json` SHA-256
  `8ff9a33838245308d8b0bdad729f29508d066a324daf346bbe90e5915431ea13`.
  Original scripts and failure reports are retained; no broad ACL was granted.
- The existing Windows release generator now reuses the source-path verifier
  for workspaces, input files and source-tree files. The verifier also binds
  the complete root suffix to its native drive/share anchor, rejecting ancestor
  redirection as well as root-relative drift. Native query failures do not
  fall back; POSIX strict behavior and release/hash/permission authorities stay
  unchanged. Three new tests failed before the repair (`source-release-native-red.xml`).
  Source/release/startup-probe focused tests then passed 71 in 9.85 s,
  `source-release-native-focused.xml` SHA-256
  `63dc411f498fc250a5271e6497d7d9bed0c1aa391bbacd0d0b9fd99219963c73`.
  The Windows-specific native tests do not claim equivalent POSIX observations.
  Plane entry 22 records this correction; a fresh exact-candidate build and
  isolated release/startup retry are still required. No full regression, push,
  remote workflow, publication or merge is implied.
- After official mirror synchronization, the existing release-manifest and
  single-process application suites passed 21 tests in 36.04 s:
  `source-release-native-integration.xml` SHA-256
  `2d3fae2b639b7518dc448ff36316412aeaa9577deab70e6084c497f4072e4102`.
  Source Authority, committed mirror checks, regenerated freeze and the normal
  freeze guard also passed. These integration tests are existing local runtime
  evidence, not the still-pending rebuilt AppContainer startup or a full-stage
  regression. This intermediate checkpoint was not pushed or merged.
- Real packaged build 14 SUCCESS at candidate
  `5882fb4af7ed4337f7d508ccbce0ab2b9e075964`: actual AppContainer/network
  denied, 9.843 s child, 2,866 indexed entries / 61,369,888 indexed bytes.
  Report `foundation-isolated-build-14.json` SHA-256
  `ebe4a27d0b12f3dbbfb12ff715ff6683aa9636fdb5be33919587930ad99d0c18`;
  bundle `source-revision-5882fb4.zip` SHA-256
  `7121e7e5946ba8b5cf5556ba386904fffb058108b32419d03e9f63d7db82391c`.
  Source inputs `7aef424186e5f023a3f2d7433c92bb0e08312dd2a7e15cce399b1ce420e3454e`;
  Capability Manifest `1fd89a0c3dd18e66f4aecf53f10319c403b121dcf35b7887935890cc51c2cc2c`.
- Actual startup attempt 6 FAILED at release generation in Skill catalog root
  strict DOS resolution (WinError 5); source consistency passed for 1,964 files
  with eight frozen bytecode artifacts retained as data. Gateway did not start.
  Report `foundation-isolated-startup-6.json` SHA-256
  `2907ceac81a6c5197c836699a5ff902ee8b8328308c9fc164c83c1d5402bb5f9`.
  AppContainer/network denial remained effective (7.875 s child); parent
  independently reverified the staged source against the pinned original ZIP.
- The native verifier now lives in runtime_security, reused by source/release,
  Skill catalog, Life store setup, Communication raw/staging setup, embedded
  backend, workspace adapters, desktop attachment setup and protected-key reads.
  Full modified authoritative sources and callers were audited; authority/ACL/
  DPAPI/ticket/effect policy is not replaced or relaxed. Source-pinned development
  readiness may use its explicit source root outside the manifest directory,
  with no wrong-root fallback and unchanged exact binary/hash checks. Plane
  entries 23/24 and an expanded freeze surface declare these boundaries.
- Focused local results on this checkpoint's code snapshot (not full-stage gates):
  - Key path/cryptography/runtime authority: 14 passed, 14.00 s;
    `startup-key-path-focused.xml`, SHA-256
    `cedfda5797348026dfbf54d0b172d77f2baf6dc550a2872bf19ccb364a70b992`.
  - Source-root/readiness: 9 passed, 22.14 s;
    `readiness-source-root-focused.xml`, SHA-256
    `92e24b93872951eb1ba560b19ce1b057541c388c51f694279ea7a0d5a85502c9`.
  - Channel/staging/desktop: 31 passed, 4 subtests, 2.65 s;
    `service-startup-path-focused.xml`, SHA-256
    `af6e9565108d6b5106d520d79f1cd5f473503c337d66a623bd4d131b2594f563`.
  - Consolidated native/source/probe/key/service/readiness/compatibility/P7D.1:
    143 passed, 3 subtests, 146.07 s; `startup-path-checkpoint-focused.xml`,
    SHA-256 `d37bcef8bb5fb93e88b4f4fb6a1974d57dcc26a5c8f635ba465ce43d3fdba182`.
  - Official generated sync/check and Source Authority passed (16 authorities,
    1 alias, 24 targets, 1 closed-world boundary). Normal freeze guard passed
    after declared refresh; no Golden corpus or assertions were weakened.
- The Windows controlled DOS-denial full-startup diagnostic remains FAILED:
  `tests/test_source_startup_paths_p8.py` reaches `orchestration.py:457`, where
  the unchanged worker constructor still calls workspace.resolve(strict=True).
  `startup-dos-denial-3.xml` SHA-256
  `1576dde9b97104e44e2a524d95239789db5485394aec0f590f6420915eba09f6`.
  This is deliberately preserved, not skipped/xfail/deleted. It is local fault
  injection, not real OS containment evidence. The later edit only restores
  test workspace environment variables; this diagnostic must be rerun on the
  exact handoff head. Full orchestration/Omni-authority audit and subsequent
  isolated rebuild/startup are still pending. No build 15 or startup 7 exists.
- User-requested handoff: synchronize the current WIP branch/PR, retain the
  known failing startup acceptance boundary, do not merge or claim P8 complete,
  and stop implementation. Any automatically triggered GitHub run after the
  push is not a completed remote PASS or permission to continue repairing.
