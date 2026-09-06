# P8 continuation: publication, running-source locks and execution evaluation

Status: IN PROGRESS. P8 is not closed and P9 has not started.

## Current authorization and prerequisites

On 2026-09-06 the user requested pushing the prepared P8 commits, completing
P8 and its CI, then entering and completing P9. The existing P8 branch was
normally advanced from `50feb168` to `a4c1dcaab4ee26897261794ebfe0754b57d45c31`.
The remote was reread immediately before the update. No main merge occurred.
The isolated worktree and recoverable backup remain as described in
`P8_R3_NATIVE_ACCEPTANCE_2026-09-06.md`.

The user subsequently selected **MiniMax M3 Token Plan, primary model only**
for this batch's evaluation. Cross-model coverage is excluded from this batch
and remains unverified; a single-model result must not be described as the
master plan's multi-model matrix. The existing plan key is used only in memory
for the named provider, without repository, configuration or artifact storage.
The domestic endpoint returned HTTP 200, response model `MiniMax-M3`, READY,
and 208 total tokens in the connectivity smoke probe. This is connection
evidence, not a task evaluation. No pay-as-you-go fallback is permitted.

## Exact remote R3 evidence

- Workflow `34013426125` completed SUCCESS on workflow commit `a4c1dca` with
  product and trusted observer both `7b2ab2f`. Windows regression, actual key
  lifecycle, source build and complete Gateway startup passed. Its 17 original
  artifact hashes were independently checked after download to
  `D:\TiangongP8R3-20260906\evidence\remote-native-a4c1dca`.
- P19 workflow `34013428454` completed SUCCESS on `a4c1dca`.
- P14 workflow `34013428431` completed FAILURE. Its Ubuntu full regression
  reported 4,621 passed, 67 skipped, 842 passing subtests and one failure:
  `test_invalid_impersonation_evidence_rejects_fallback[level_size-8]`.
  The portable fixture uses host `ctypes.wintypes.DWORD`, which is eight bytes
  on LP64 Linux, while the emulated Windows ABI requires four bytes. Locally
  injecting a 64-bit DWORD reproduced the same missing rejection. Production
  Windows token behavior and the eight-byte rejection assertion must remain.
  Original log: `evidence/remote-p14-a4c1dca-failed.log` under the same task root.
- Architecture workflow `34013428475` completed FAILURE: Windows full regression
  and both source-authority jobs passed; Ubuntu reported the same single DWORD
  fixture failure (4,621 passed, 67 skipped, 842 subtests, 809.66 seconds).
  The current source/publication work is uncommitted and is not covered by any
  of these preceding-commit CI outcomes.

## Publication ownership and exit matrix

Git remains Source revision authority. `v3.fact_kernel.compile_manifest` and
the Gateway Action compiler remain the only Manifest/Registry/permission
compilers. Source publication is an offline operator lifecycle operation;
there is no model-facing publication route or current-request import.

| Boundary | Required positive evidence | Required rejection |
|---|---|---|
| Candidate and bundle | Reconstruct immutable Git inputs and rederive the full Manifest differential | Rehashed foreign source, substituted candidate/scope, forged review, unsafe bundle |
| Core/Human review | Separately configured review key signs the exact proposal and evidence digests | Unknown key, unsigned build, missing collateral review, implicit risk downgrade/A0 admission |
| Behavioral contracts | Both positive and negative observations for each required Action, bound to exact source | Missing, failed, duplicate, cross-source or changed evidence |
| Publication | New immutable version and completion receipt, with independent source verification | Existing/partial destination, changed bytes, failed staging; no current pointer or Registry switch |
| Running X/X+1 | Actual old Run continues X, next Run uses X+1, resumption remains pinned | In-place source mutation, cross-generation activation or stale source use |
| Evaluation | 80–100 actual Gateway/Runtime/P19 tasks using the selected M3 provider | Treating synthetic clocks/plans, parser-only calls or test counts as real execution |
| Phase closure | Full Python/Node, P14/P19, Ubuntu/Windows and exact candidate native gates; merge ancestry | Old-head green, skipped required native evidence, pending reviews or unfinished exits |

The read-only preparation API verifies Git bytes independently of archive
claims. On the actual `7b2ab2f` bundle, it produced a pending proposal with
793 Action deltas, 99 effective-risk decreases and 42 newly-A0 candidates.
Those lists remain unapproved. A successful build cannot supply its own
review, evidence-contract or running-lock authorization.

The operator publication API requires a domain-separated detached Ed25519
review signature from an independently configured trust key. Its decision
names the exact proposal, full Action review scope, individual risk changes,
generated Manifest adoption and referenced behavioral evidence. Runtime
Ticket/Grant keys and semantics are not reused or changed. Synthetic signed
test receipts validate the protocol only; they cannot approve this project's
actual pending differential. The implementation and real acceptance remain
under validation before any phase-completion claim.

## Retained focused observations

- `publication-preparation-01`: 15 failed / 1 passed. The new fixture wrongly
  supplied a Git checkout including `.git` to the existing bundle writer,
  which correctly rejected it. The fixture now supplies a private source
  copy, preserving the production bundle guard.
- `publication-preparation-02`: 16 passed, no skips, one existing Pydantic
  warning, exit 0. Recorded input hashes were unchanged during execution.
- `publication-real-candidate-01`: expected exit 2,
  `SOURCE_PUBLICATION_REVIEW_REQUIRED`; this is not a successful publication.
  The retained proposal identifies each unresolved review/evidence obligation.
- `publication-review-02`: 54 passed, no skips, one existing warning, exit 0,
  input hashes unchanged. Includes signature/risk/evidence validation, immutable
  publication, fresh external-pin verification, CLI roundtrip and token checks.
- LP64 injection with the corrected Win32 fixture: 15 passed. The test fixture
  uses `ctypes.c_uint32` for the mocked DWORD; production token code is unchanged.
- `gateway-model-probe-01`: no model call; the harness omitted Content-Type.
- `gateway-model-probe-02`: the model ran, but the harness lacked the callback
  listener/internal token and also queried Effect records without their scope.
- `gateway-model-probe-03`: the callback worked, but the harness lacked the
  nonce state root supplied by the actual desktop launcher. Source validation
  after the model turn also rejected a backend namespace mismatch.
- `gateway-model-probe-04`: M3 correctly read inventory.txt and answered 29;
  parent, regenerative child and grant Effects all succeeded. Post-run source
  verification still rejected `v3.__path__` containing the Skill's v3 data
  directory. This is a failed source-lock acceptance, not a passed task case.
- The exact producer was `_model_adapter_core` adding the whole Skill root to
  sys.path. The fix uses a source-bound module alias without that insertion.
  The pre-fix test reproduced the path mutation; 22 focused adapter/protocol
  tests passed after repair (`model-adapter-source-fixed-02.xml`). Real staged
  source acceptance still needs a newly built candidate containing the fix.
- `publication-and-adapter-regression-01`: 170 passed, no skips, five existing
  warnings, exit 0, all recorded inputs unchanged. This includes the normal
  P19 freeze guard, publication protocols, model adapter, source launch and
  private-token regressions. Official generated-source check also passed.

Engineering-stage merge progress remains **8/18 = 44.4%**. Current position:
P8, stage 9/18, remaining execution package 1/6. Later P9 work starts after
P8's accepted exit and verified merge; no pending obligation is silently
converted into a completed stage.
