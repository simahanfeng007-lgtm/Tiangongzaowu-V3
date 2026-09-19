# P12 R1A repair checkpoint — two Windows defects fixed, one remains

Date: 2026-09-17. Continue PR #78 on its existing branch. This is unfinished R1A
repair, not R1B shared-manifest separation, P11 exit or P12 retirement approval.
The preparation observer is `c1782e58952c9f907788d647a2339cad99099ac8`.
Main remains `bf29542b3048c8d1806add8063b0db7c72be055b`.

## Committed product correction

The product now receives the exact 16 files generated and tested in preparation
run 35211496448, including all five official mirrors/stamps, the sole Verification
Plane version declaration (1.15), its existing freeze and two regression modules.
Only two existing frozen authority entries change: sandbox_runtime.py and the
version source. Surface membership, independent fingerprint, Golden corpus,
permission floors, schemas, timeout limits and the AppContainer launcher do not.
The mirror and freeze files were produced by existing official generators, then
checked normally with UPDATE_FREEZE/FINGERPRINT/GOLDEN absent.

The workspace rewrite retains both the supplied and resolved source spelling,
rejects changed alias identity before execution, and preserves the explicitly
supplied private destination spelling. Replacement remains one-pass and bounded;
it does not recursively substitute paths introduced by the replacement itself.

The marked PowerShell wrapper explicitly imports the installed Management and
Utility module manifests from PSHOME before creating its existing private PSDrive.
Module autoload is disabled only during that bootstrap and restored in finally.
Bootstrap errors exit 125. User command bytes, original exit codes, private cwd,
secret removal, parent-file isolation, permissions and 20-second tests remain.
No parent module/profile settings are inherited to make the shell work.

The original five product test modules, all original 54 diagnostic tests and both
old read-only map scripts remain byte-identical to the R1A baseline. Ten new alias
regressions plus six new shell regressions accompany the repair. One newly written
alias fixture changes its expected destination spelling, not its non-recursive
rewrite assertion. No original foundation assertion is changed or skipped.

## Actual prepared-source results

Original R1A Windows result was 3 failed / 145 passed. The tested repair result is:

| Group | Ubuntu | Windows |
|---|---|---|
| Original diagnostic contracts | 54 passed, no skips | 54 passed, no skips |
| Alias + shell bootstrap | 15 passed, 1 native-only skip | 16 passed, no skips |
| Existing five product modules | 147 passed, 1 Windows-only skip, 3 subtests | 147 passed, 1 failed, 3 subtests |
| Normal Source Authority / committed mirrors | PASS | PASS |
| Normal freeze and architecture guards | 6 passed | 6 passed |
| Normal independent fingerprint | 1 passed | 1 passed |

These counts overlap repeated runs and are not summed as unique tasks. Windows
native readback, preserved code-7 failure, host-secret removal, parent-file read
rejection and run-directory cleanup all passed in the new tests. The unchanged
legacy-terminal test also passed, including its original A5 non-overridable check.

The sole remaining original failure is:
`SandboxCloseoutTests.test_absolute_unicode_workspace_path_in_cmd_runs_inside_private_copy`.
It returns native code 1, `Access is denied.`, with no requested output. It remains
a real failing case, not xfail, skip, compatibility fallback or an accepted defect.
Therefore Windows closeout, R1A phase completion and R1B entry remain blocked.

## Independently retrieved bytes

Both original artifact ZIPs were actually downloaded and extracted in this turn:

- Ubuntu artifact 10492327187, ZIP SHA-256
  `f061eff9441e2ae7d5a2eeaf42a98e12ae1d4d240fe4caf63baca0028fe88441`.
- Windows artifact 10491784845, ZIP SHA-256
  `84fdca1c0acce3a6818cc3a45b0cc571acaa36cbd594ff0b345735d8e11e98fc`.
- The extracted tracked patch is byte-identical on both platforms, SHA-256
  `46af8af844405d0a9ca2e4d3f115d5bf3d9698429cc7e60d620ab6f4d66c3d88`.
- Parsed prepared-entries.json contents and all 16 file hashes match. The raw
  receipt bytes differ by platform newline encoding; no byte equality is claimed.
- The authoritative sandbox runtime and every generated copy have SHA-256
  `246231859f8e18bc9985213b0bcc65c6d1e3cc463de9cabc25dd0610ae14ad17`.

The earlier local-source access limitation was partially resolved: native source
artifact 10489996070 was downloaded, its ZIP and bundle digests verified, and its
complete Git history passed bundle verification and git fsck. It identifies the
export commit `8f444df479f5d32a22294c7f3be7ef7a0de06821`, not current remote HEAD.
Applying the downloaded tracked patch and the two new test files reproduced all
16 tested file identities locally. LFS assets were not hydrated. Local Linux
alias/bootstrap rerun: 15 passed / 1 native-only skip. This does not claim local
Windows execution or a final-current-HEAD full regression.

## Temporary transport cleanup and current regression gate

Remove the three temporary export/preparation/probe workflows, the temporary
preparation/uploader script and its three staging patch/test inputs. Their original
commits and evidence remain in Git history. The product uses only the existing
SandboxRunner; no developer patch or secondary Runtime is loaded at execution.

The existing P12 focused workflow now tests COMMITTED_SOURCE_REGRESSION with
read-only contents permissions, exact checkout, unchanged original-test byte checks,
input hashes before/after, normal Source Authority/mirrors, original diagnostic
contracts, complete P19 Golden directory, new repair tests and all five original
product modules. A remaining failed test fails the job. No write token, source
mutation, generation switch, hidden exclusions or approval option remains there.

This is an explicit transition out of the earlier developer-only delta. R0/R1A
map scope rules are unchanged; they must not be reinterpreted as approving later
product changes. Historical inventory is not current production acceptance.
Final committed-candidate CI is separate from the prepared-worktree results above;
its status must be recorded from the new exact-head run, never inherited.

## Next bounded work

Resolve the remaining CMD absolute-path failure under unchanged native containment.
Inspect the failing path operation rather than broadening parent ACLs, increasing
limits, suppressing errors or changing user-command semantics to obtain PASS.
Re-run the complete original Windows test class and shared consumers. Only after
those failures close should R1B separate shared manifest logic from static selection.
P11 live-provider/single-path/independent-review obligations and P8/P9 debts remain.
No old planner/registry removal, default switch, merge or deployment occurs here.
Engineering-stage merge progress remains P0-P10, 11/18 = 61.1%.
