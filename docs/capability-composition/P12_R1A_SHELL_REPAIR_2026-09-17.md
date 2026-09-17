# P12 R1A — native shell repair, R1B remains blocked

This batch continues the existing PR #78. The final Windows run of the original
R1A candidate had three failures; the earlier in-progress summary was not a pass.
No P11 exit, P12 retirement, deployment or main merge is authorized here.

## Evidence-driven corrections

Native run 35210244618 at a81ddad tested bounded environment changes: drive state,
system module path, private cache and private profile. They did not fix CMD absolute
path access or PowerShell automatic module discovery. An explicit Management module
import returned promptly, but its C:\direct.txt write was denied. That return code
zero is NOT successful write evidence.

Native run 35210856228 at db719e9 tested explicit Management and Utility startup.
The existing marked PowerShell wrapper then produced legacy.txt with exact content
sandboxed via the existing broker, under windows-appcontainer. Restoring the prior
module-autoload preference still worked. Explicit failure returned 7 with stderr.
CMD absolute/native-short/extended path cases still failed. The marked CMD example
returned zero without the requested marker file and produced cache byproducts;
it is not a successful delivery or justification for weakening a test.

This prepared source candidate extends the existing alias patch only by retaining
the caller's private destination spelling, and explicitly loading the two existing
PowerShell system modules before creating the private PSDrive. The original autoload
preference is restored in finally. Bootstrap failures exit 125, not success. No
parent profile/module paths or secrets are propagated; no environment allowlist,
AppContainer capabilities, ACLs, permissions, timeouts or native launcher changes.

The original foundation tests stay byte-identical. The new alias fixture's nested
destination assertion is adjusted to preserve the supplied destination spelling;
the no-recursive-rewrite assertion remains. Six bootstrap regressions are added,
including actual Windows native readback, code-7 failure, host-secret non-disclosure,
parent-file access rejection and run-directory cleanup. Non-Windows explicitly skips
the one native case; it is never counted as native acceptance.

## Preparation and acceptance boundary

The existing temporary preparation script applies this bounded candidate, runs
the official mirror generator and existing Verification Plane freeze procedure,
and declares 1.15. Only the existing sandbox runtime and version authority hashes
may change within the frozen authority surface; member paths, independent fingerprint,
Golden corpus, schemas and permissions remain unchanged. All generated source copies
come from the official generator. Normal freeze/Source Authority/mirror guards run
with update switches absent. The unchanged five product regression modules are run.

Preparation results bind to a worktree produced from a precise observer commit,
not to a committed production HEAD. Final outcomes and generated object hashes must
be read before committing that exact source. No local full-repository validation is
claimed: the local executor cannot directly clone the current repository. Local
checks for this batch cover preparation/test-script syntax only, not product tests.

CMD absolute-path behavior and any subsequent failed original assertion remain R1A
unfinished work. R1B shared-manifest separation is deferred until those original
failures close; this document does not waive or transfer them to another phase.
Main engineering merge progress remains P0-P10, 11/18 = 61.1%.
