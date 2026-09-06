# P8-R3 native acceptance continuation — 2026-09-06

Status: LOCAL NATIVE LIFECYCLE AND STARTUP REGRESSION PASSED; remote acceptance
pending. This record continues the protected-key and isolated
startup work order. It does not close P8 or authorize publication, a main
merge, A1+ admission, or production-key changes.

## Inputs and protected workspace

- Remote main observed: `9a3344de9fe468fa845d2ff501166484439b8ec4`.
- Remote P8 / PR #73: `50feb1684012370c15366716f010ac57a7c979f4`,
  Draft / unmerged; its nine completed checks succeeded before this repair.
- Original base: `b37204ee4d94e4857aaf477b810a21f19345d584`.
- Independent policy base: `4bfa94ef918a18e33a939dc01d90b969ccf64a7f`.
- Imported handoff tree: `d24c70a881c3369980910127c49f4f957fdbec3c`.
  The cumulative patch was applied to the exact remote tree and `git
  write-tree` reproduced this value before continuation edits.
- Original P8 worktree remains at the original base, with no local changes.
  Implementation uses `codex/p8-r3-native-acceptance-20260906` in the separate
  `C:\Users\77571\Documents\天工造物v3-p8-r3` worktree.
- Recoverable Git bundle, original workspace inventory, extracted handoff,
  and local evidence live under `D:\TiangongP8R3-20260906`.
- A dedicated Python 3.12.10 runtime uses the repository's unchanged
  `requirements-source.lock`. The existing AppContainer launcher grants RX
  to its executable directory; this task uses its own runtime directory,
  rather than altering the shared `D:\Python` installation. The existing
  `TiangongV3.ToolSandbox` profile was observed before native execution.

## Review corrections and prerequisite matrix

The imported native negative test caught `PermissionError` across fixture
creation and the attempted read. A fixture-creation failure could therefore
be reported as successful read denial. Preparation and the observed denied
operation must be independently evidenced, with a regression for failed
preparation.

The shared token observer also returned ordinary-host status immediately
when `TokenIsAppContainer` was zero. Identification-level impersonation must
be rejected before ordinary-host file handling. Microsoft documents this
requirement in [GetTokenInformation](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation).
Unknown identity, query failure and invalid evidence remain failures.

| Prerequisite / exit | Required evidence | Current result |
|---|---|---|
| Source identity and ownership | Distinct original/policy bases, exact candidate and immutable preflight | PASS: candidate 7b2ab2f, original and policy bases unchanged |
| Effective token | Host/container observation; unusable impersonation and query failures rejected | PASS: controlled token cases and actual host/container observations |
| Actual protected keys | Windows host + AppContainer create/reload/sign/version change/tamper/cleanup | PASS: corrected-native-02, no skips |
| Negative fixture | Confirmed fixture preparation, separately observed access refusal | PASS: OS access denied; host ACL/content rechecked |
| Persisted authority | Reload persisted envelopes and authority record; stable signing identities | PASS: both host and container disk reopen |
| Isolated source build | Exact candidate, actual AppContainer, network denied | PASS: build-01 on 7b2ab2f |
| Complete Gateway lifecycle | READY, successful close, child and independent parent source agreement | PASS: startup-01, READY 200 / ALIVE, 1,967 files before and after shutdown |
| Regression and freeze | Affected tests, official mirrors/source authority, ordinary freeze guard | PASS: 387 cases + 6 subtests, no skips; source/mirrors and ordinary guard passed |
| Remote acceptance | Final candidate required Ubuntu/Windows and P19/P14 checks | Not run for this candidate |

Tests and native reports must identify the exact files or commits they used.
Portable assertions and ordinary host results cannot substitute for container
observations. Disk reopen in one worker is labelled disk reopen, not a claim
of complete cross-process Gateway restart acceptance.

## Local observations before the immutable build candidate

- `evidence/native-01`: 34 passed, no skips. Actual host and container key
  observations succeeded. An isolated-import subprocess emitted a reader-thread
  decoding warning because `-I` ignored inherited UTF-8 settings. The smoke
  test now sets `-X utf8` and rejects that warning; host capture also specifies
  UTF-8 explicitly.
- `evidence/affected-01`: 209 passed, 1 failed, 6 passing subtests. The failure
  was test setup: the deliberately overlong layout fixture hit Windows'
  directory-length limit before exercising GatewayConfig. Only fixture mkdir
  now uses an extended Windows path; the tested path and the unchanged
  240-character rejection remain explicit. Its initial failure is retained.
- A second failure-evidence gap was corrected before final native acceptance:
  failed-write cleanup now requires an observed call to the injected
  `FlushFileBuffers`, rather than accepting any earlier `OSError`. Tests reject
  early creation/write errors, missing cleanup and swallowed flush errors.
- `evidence/corrected-native-02`: 49 passed, no skips, one existing Pydantic
  field-name warning, exit 0. This includes all 40 current native/report tests,
  the eight layout cases and the isolated preflight CLI smoke. Both actual
  workers reported `KEY_LIFECYCLE_OBSERVED`; the container independently
  observed the injected flush and OS refusal of the foreign-package file.
- These groups overlap and must not be summed. Command JSON files retain
  exact argv, timestamps, source hashes before/after, exit codes and log hashes.
  The original imported candidate has no claim on these changed-source results.
  The corrected native run predates product commit creation and records HEAD
  `50feb168`; all 110 recorded input files match the later product's Git blobs.
  Its association with the product is by file hashes, not a rewritten HEAD.

## Immutable native build and startup

The actual product and trusted observer commit is
`7b2ab2fbbe0584f6ffb3f266424c6d986a1c25b4`, tree
`1dd3e1032804bb8c5fb5185445c85d98192ae2dd`.

- `evidence/preflight-01`: `SOURCE_CANDIDATE_OBSERVED`, exit 0. All changed
  paths retain their existing authority classification. The separate original
  and policy bases were checked without modifying the classifier or policy.
- `evidence/build-01`: `ISOLATED_BUILD_OBSERVED`, exit 0; actual
  `windows-appcontainer`, network `denied`. The bundle digest is recorded in
  `candidate-bundle-sha256.txt` and checked by the startup command.
- `evidence/startup-01`: `ISOLATED_STARTUP_OBSERVED`, exit 0; real
  `GatewayRuntime.start`, ALIVE health, READY with status 200 and successful
  `runtime.close`. The child verified 1,967 source entries before startup
  and again after shutdown against unchanged source/Manifest digests. The
  parent's independently reconstructed staged-source report equals its
  initial staged report. No error/cleanup/failure field accompanies success.
- The workflow's trusted-parent and candidate pins now both name this real
  product commit. The binding/documentation follow-up changes no product
  source, tests, original base or policy base. It must be separately identified
  when remotely scheduled; the old `7b73217` pins are not reused as acceptance.
- `evidence/startup-regression-01`: 387 ordinary cases and six subtests passed,
  no failures/errors/skips, exit 0. All 21 original startup test files remain;
  three private-file/ACL/token files and the actual key lifecycle file extend
  the matrix. Three existing Pydantic field-name warnings remain. Test inputs
  were unchanged during the run and the recorded HEAD is the product commit.
- The source dependency lock's 19 direct requirements match the dedicated
  runtime, and all installed distribution versions are retained in
  `runtime-verified.json`. The shared `D:\Python` directory's security
  descriptor was independently checked unchanged after native execution.
- `independent-native-audit.json`: all 74 evidence checks passed. The audit
  verifies all 2,900 bundle entries, the commit-to-bundle-to-startup chain,
  and 24 original artifact hashes; the main agent independently rechecked
  those artifact hashes. Startup regression's 129 recorded input files match
  the product commit's Git blobs before and after execution.

This is local Windows 3.12.10 evidence using the unchanged source dependency
lock, not a remote CI result or production approval. Source publication and
the remaining P8 Manifest, run-lock and task/model obligations are still open.

## Delivery boundary and next action

The source candidate and a separate workflow-binding/documentation commit are
local only. A normal push to the existing P8 branch and current-candidate
Ubuntu/Windows Architecture, P19/P14 and native workflow results are still
required before remote R3 acceptance. No main merge or Source publication was
performed. Earlier remote green results remain attached to `50feb168` only.

The next owner should inspect this record, the isolated worktree's HEAD and
`D:\TiangongP8R3-20260906\independent-native-audit.json`, then reread the remote P8 branch before
any authorized normal push. Preserve the product/observer commit and the
independent original/policy bases. Do not repeat old-pinned native CI.

## Stage position

P8 remains the ninth of eighteen stages, remaining package one of six. The
historical stage-merge metric remains 8/18 = 44.4%; it is neither an effort
estimate nor production readiness. R3 acceptance still precedes the remaining
Manifest review/publication, running X/X+1 lock and real-model/task obligations.
