# P8 publication sealing checkpoint — 2026-09-06

Status: **P8 IN PROGRESS; not publication approval or phase acceptance.**
Parent branch/PR #73 head observed: `b1f2ee83fc314498683fd8e0c176ef09ccea4f72`.
Main remains `9a3344de9fe468fa845d2ff501166484439b8ec4`.

## Verification Plane 1.6 addendum: publication-directory sealing

The existing Gateway operator publisher writes and verifies its receipt, then
seals its new publication directory. Previously a failed final chmod left
well-formed receipt bytes in a writable directory. The independent reopen
path checked individual metadata files, but did not check the directory's
sealed mode. A no-op chmod could also let publication return success.

This checkpoint adds the same directory-mode rejection at publication return
and independent reopen. It changes only the existing
`src/total_gateway/tool_source_publication.py` authority. Its existing P19
freeze entry is refreshed through the normal generator. Freeze coverage,
Golden corpus, schema/version, signature/approval scope, A0 admission, P19 and
Completion semantics are unchanged. No runtime entry, fallback or permission
compiler is added. This checks the existing mode-bit invariant; it is not a
new Windows DACL guarantee or proof against concurrent same-user mutation.

Three newly executed fault cases failed before the correction and passed
afterward: writable directory at reopen; ignored sealing; and an injected
final sealing error followed by independent verification of its retained
receipt. Failed directories and original failure evidence remain retained.
The existence of publication.json alone is not a publication acceptance API.

## Observed evidence and its limits

- Existing signed publication suite before the fix: 23 passed.
- New fault regression before the fix: 3 failed (DID NOT RAISE), retained.
- After the fix, preparation + signed publication + new sealing suites:
  42 passed, no failures/skips; one existing Pydantic field-name warning.
- Final expanded publication, Manifest evolution, World binding and ordinary
  P19 freeze/architecture group: 98 passed, no failures/skips; inputs unchanged.
  This overlaps the 42-case group and must not be added as unique tasks.
- Local environment: Linux, Python 3.13.5 / pytest 9.0.2, **not** the locked
  Windows Python 3.12 runtime. Synthetic signed fixtures are protocol tests,
  not actual Source approvals or real-model task evaluations.
- Source Authority: 17 authorities, 1 alias, 24 generated targets, 1
  closed-world boundary. Official committed-mirror check passed.
- The ordinary freeze guard rejected the intended authority change before
  refresh. The official regeneration changed only this authority's digest;
  the ordinary guard then passed with UPDATE_FREEZE absent.

The local working source was reconstructed from the downloaded C5 native
artifact and independently checked against its 2,911 indexed entry hashes.
C5 product `36627e2c` and the observed parent differ only in the native-workflow
pins and an appended evidence document; their product source is identical.
The build package's generated Manifest was not mistaken for the committed
Manifest: the latter was restored using independently connector-checked blob
`f443737de830ad600c41792c8afbe30d9a7c4618`, SHA-256
`0971fd04f760d4b491361fa3526b17d092c561ce224fb7f9b10446e0bcd5999d`.
Generated mirrors were restored only with the existing official generator.
The downloaded native artifact itself was never modified.

## Packaging patch remains explicitly separate

The previous patch is preserved at
`patches/P8_packaging_prohibition_20260906.patch`, SHA-256
`34ba06a27e15bc4150202af048580b4be3a58627980e2c0c467c214f7b3ac6bd`.
Its five packaging/execution/delivery suites were independently rerun locally:
168 passed and 84 passing subtests, no failures/skips; three existing warnings.
Two earlier combined attempts were externally interrupted at 20 and 120
seconds; their partial logs are not successful test runs. The publication
suite was then completed separately, not skipped or weakened.

**The patch file is a handoff artifact, not an applied kernel change.**
This checkpoint's production kernel remains blob
`064ecadd26bd7c68700bbece601727151a3862d6`; it does not install the packaging
helper or its new test. The tests above used a separately patched local kernel
and cannot be reported as validation of an applied remote packaging fix.
Before application, reread the branch and worktree, check the original blob,
use git apply --check, and preserve other edits. No whole-kernel overwrite or
current-request import was performed.

## Remaining P8 exit requirements

C5 Architecture run `34021171617` is now completed SUCCESS. P14/P19 and native
C5 success remain historical evidence for that candidate, not for this change.
This is an intermediate checkpoint using focused local tests under the user's
recorded cadence. A checkpoint commit may use [skip ci]; that is NOT a passed
check. No workflow or required-check configuration is changed, and final P8
acceptance still requires fresh exact-candidate full and native gates.

Real independent risk review, each required Action's behavioral evidence,
reviewed Source publication, live X/X+1 and restart/replan observations,
final current-candidate task evidence and normal merge remain outstanding.
The 99 risk downgrades and 42 newly-A0 candidates are not approved here.
No model call, production-key access, main merge or P9 work occurred.
Engineering stage-merge progress remains 8/18 = 44.4%; P8 is stage 9/18.
