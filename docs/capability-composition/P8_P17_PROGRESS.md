# v1.2 P8–P17 implementation and acceptance ledger

Status: **ACTIVE — P8 source audit and implementation**.

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
