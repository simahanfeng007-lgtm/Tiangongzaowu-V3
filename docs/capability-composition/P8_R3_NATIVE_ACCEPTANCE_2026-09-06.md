# P8-R3 native acceptance continuation — 2026-09-06

Status: IN PROGRESS. This record continues the protected-key and isolated
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
| Source identity and ownership | Distinct original/policy bases, exact candidate and immutable preflight | Pending final candidate |
| Effective token | Host/container observation; unusable impersonation and query failures rejected | PASS: controlled token cases and actual host/container observations |
| Actual protected keys | Windows host + AppContainer create/reload/sign/version change/tamper/cleanup | PASS: corrected-native-02, no skips |
| Negative fixture | Confirmed fixture preparation, separately observed access refusal | PASS: OS access denied; host ACL/content rechecked |
| Persisted authority | Reload persisted envelopes and authority record; stable signing identities | PASS: both host and container disk reopen |
| Isolated source build | Exact candidate, actual AppContainer, network denied | Pending candidate commit |
| Complete Gateway lifecycle | READY, successful close, child and independent parent source agreement | Pending build |
| Regression and freeze | Affected tests, official mirrors/source authority, ordinary freeze guard | Final startup regression pending; source/mirrors and ordinary guard passed |
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

## Stage position

P8 remains the ninth of eighteen stages, remaining package one of six. The
historical stage-merge metric remains 8/18 = 44.4%; it is neither an effort
estimate nor production readiness. R3 acceptance still precedes the remaining
Manifest review/publication, running X/X+1 lock and real-model/task obligations.
