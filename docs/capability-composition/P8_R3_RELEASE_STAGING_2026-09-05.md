# P8-R3 continued — private release staging in an actual AppContainer

## Scope, original observation and source review

Same unfinished R3 work order; P8 remains open, PR #73 Draft, main unchanged.
Master-plan phase 9/18, remaining package 1/6; merged-stage progress 44.4%.
Starting product tree cc2ec326052baad8e018790ea1a3cbcb06ccbaae at 385e7ea,
workflow db18513c3b2b22d105624bde627db4f231021534. The local checkout was
reconstructed from native-Git blobs and verified to this exact 2,500-blob tree.

Native Windows run 33964681792, artifact 9969134433, observed successful
immutable core-policy/candidate preflight, 224 passed/0 skipped/0 failed,
real AppContainer build and actual source consistency (1,965 observed files).
Startup then failed in release_generation: mkdtemp created a directory that
the container could not write; cleanup also failed and obscured the first error.
Artifact ZIP SHA256 bc3b2c0120bcf3a75c9a4b6e61d9ebf1c79f7926b0751cd06dcdec4dc6d5d90a.
Original reports and all eleven artifact checksums were independently verified.
These results are NOT Gateway READY or shutdown acceptance.

The symptom matches CPython issue 134587: Windows mkdir(0700), used by mkdtemp,
applies a protected owner/admin ACL excluding the creating AppContainer SID.
Read the complete existing release generator, both source/production writers,
source launch parent/worker, sandbox creation and relevant release/freeze tests.
No candidate classifier, Source policy, Runtime or sandbox grant is changed.

## Explicit Verification Plane 1.6 addendum and bounded correction

Only the existing covered src/total_gateway/release_manifest.py production
source changes. It still generates the same canonical manifest using the same
registry/source hashes, atomically renames a private staging directory, and
independently verifies the final output. Development and production writers
share the same staging lifetime; neither gains a publication approval path.

Ordinary Windows host and POSIX retain tempfile.mkdtemp. For a Windows token
actually marked AppContainer by GetTokenInformation, a new directory is
created with a protected DACL for system, administrators, the effective token's
user, and that exact container SID. Identity comes from the effective thread
token, falling back to the process token ONLY for ERROR_NO_TOKEN. An environment
flag cannot choose the compatibility branch. Missing/malformed/denied evidence
fails before creation. A SID is never obtained from candidate/model text.

This is a security descriptor for a NEW private directory, not a modification
of any existing host/source/workspace ACL. No Everyone, Users, all-packages,
null-DACL, inherited-DACL fallback, token change, new capability or privilege
is granted. The kernel still enforces the existing parent's creation rights.
Creation is exclusive, random (128-bit name), bounded at 16 collisions, and
never reuses an existing path. No host LongPathsEnabled or sandbox policy changes.
Only the current container receives its intended private-object access; OS
containment and all Gateway Policy/Ticket/Grant/P19 boundaries remain unchanged.

Both writers now clean a stage after generation failures as well as write or
rename failures. If cleanup also fails, the original exception remains primary
and the cleanup exception is added to its traceback notes. A failure cannot
become successful publication or startup. Existing output guards and final
source/manifest verification remain mandatory.

## Regression and acceptance matrix

Red-first existing behavior: both writers leak a stage after generation error;
both replace an original write exception with a cleanup exception. Four new
selection-contract tests also fail before the helper exists. After correction,
local release/staging/source-binding tests: 60 passed, 3 Windows-only skips.
These local Python 3.13.5 tests are NOT native AppContainer acceptance or the
locked dependency environment. Added ABI fixtures cover token selection,
access denial, malformed SID/size/flag, broad SID rejection, descriptor failure,
exclusive collisions and no permission-relaxing fallback. A real ordinary
Windows-token test must run without a skip on the Windows acceptance runner.

Refresh only the existing release_manifest.py authority hash using the official
UPDATE_FREEZE=1 generator, then check without UPDATE_FREEZE. Golden corpus,
plane semantics and existing required workflows are not weakened. Generate
mirrors only through the official sync command; no mirror is hand-edited.
Re-run native source preflight and expanded no-skip Windows tests on the exact
new candidate, then actual AppContainer build -> Gateway READY -> clean shutdown
-> child and parent source consistency. Any new failure remains unfinished R3.

## References (not substitutes for this repository's actual OS evidence)

- https://github.com/python/cpython/issues/134587
- https://docs.python.org/3/library/os.html#os.mkdir
- https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-token_information_class
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openthreadtoken
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw

P8 Source publication, permission-delta review, live X/X+1 run locks and real
model/task evaluation remain open. No main merge, P9 work or A1+ admission.
