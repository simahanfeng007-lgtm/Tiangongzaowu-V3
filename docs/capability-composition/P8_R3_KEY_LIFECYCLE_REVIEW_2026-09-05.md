# P8-R3: repair the deployment contract, not the last stack frame

## Position, authority and immutable baseline

Same OPEN P8-R3 work order. P8 is stage 9/18, remaining package 1/6.
Engineering-stage merge progress is 8/18 = 44.4%, not production readiness.
Main reread at 9a3344de9fe468fa845d2ff501166484439b8ec4; product baseline
85558aaae5fa5add11f21d42df64607b3c7bbc06, tree4a1802ad93c0fb7cdfe2fdbdc2ea479b58886763.
The v1.2 master plan remains authoritative. No P9, main merge, Source publication,
A1+ admission, second Runtime/Gateway/Registry or alternative completion rule.

## Corrected diagnosis and why previous rounds did not close

The previous checkpoint's TicketKeyStore/_restrict/user+SYSTEM description was
incorrect. Reading the actual immutable source and native startup.json gives:
GatewayRuntime.start -> GatewayOrchestrationWorker.from_runtime_config ->
RuntimeTicketAuthority.open -> ProtectedKeyStore.create_key -> _protect_key_file
-> _restrict_file_to_sid -> os.chmod(...,0o600), tickets.py line82 at baseline.
The preceding command is icacls /inheritance:r /grant:r *CURRENT_USER:(F).
Primary source/trace outrank the prose checkpoint. There is only one relevant
existing ProtectedKeyStore; no new ticket authority is needed.

The repeated failures belong to a shared deployment-contract gap: ordinary
Windows/checkout assumptions were carried into a restricted AppContainer and
source-pinned private installation. Import roots, immutable source ownership,
physical path identity, 8.3 spelling, maximum configuration path length,
private profile, constructor cleanup and dual-principal file access are
prerequisites, not isolated business features. A fail-fast sequential Gateway
startup reveals the first broken prerequisite and hides later ones. Repeatedly
repairing just that stack frame creates serial rediscovery. Host/fixture CI
success is useful regression evidence but does not establish this deployment
contract. Previous completion wording was bounded, but the prerequisite audit
and attribution to original traces were not sufficiently complete.

## First-principles acceptance contract

The existing Gateway must use one exact source/release and operate with only
its explicitly supplied private resources. Its keys remain DPAPI ciphertext;
its authorized OS principal must be able to create, reload, use and retire them.
An unrelated principal must not gain access. A failed attempt must not destroy
an existing object, silently change an ACL, invent evidence or leave ambiguous
runtime ownership. This is a conjunction of identity, isolation, lifecycle,
source consistency and readiness, not a test-count target.

Fastest safe route: measure the prerequisite matrix in the actual restricted
environment before product edits, repair the shared cause, test the entire
resource lifecycle, then run the unchanged end-to-end gate. Do not grant more
ambient access or substitute ordinary startup for the restricted gate.

## Measured native experiment before product submission

Run33972335240 failed before the worker: its diagnostic parent omitted the
backend import root, so importing SandboxRunner could not find v3. This was
our diagnostic plumbing defect, not a new product regression. It is retained,
not reclassified as useful native evidence. The corrected runner records an
identity before imports and uses both exact trusted source roots.

Run33972666119, workflowc4198cf4de6e7f547dbcba73ee18907b32041686,
product85558aa, actual Windows/Python3.12.10 and requirements-source.lock:
ordinary host old protection/read/rename passed; actual AppContainer old
protection, read and rename all returned permission denial. DPAPI itself
round-tripped under both tokens. Creating a new encrypted fixture with an
explicit protected DACL for exactly the user AND actual package made chmod,
DPAPI readback, atomic replacement and cleanup pass. Different-package access
was denied. Containment was windows-appcontainer, network denied.
Artifact9971418235 SHA256:
a0d53f97f5a6ae92c686c0a76b524ebc1aa6b86d4ac2b6c0ec57b3cb9a0807d7.
This is design evidence, not acceptance of the revised product or Gateway READY.

The raw experiment also exposed that Windows renders a local administrator
trustee as LA in SDDL. Therefore the final observer reads binary ACE SIDs rather
than guessing identities from localized/abbreviated SDDL text. This was caught
before submitting the product change, not after another full startup run.

## Explicit Verification Plane1.6 addendum: narrow shared file lifecycle

The existing release-staging effective-token observation is moved, not copied,
into total_gateway/windows_private_files.py. Release staging keeps precisely
its existing descriptor and behavior. The helper is part of the existing
Gateway source authority, not a new store, policy, registry or runtime. No
source-ownership amendment is needed; original/policy-only base ancestry stays.

For an OS-observed AppContainer only, ProtectedKeyStore creates NEW ciphertext
and metadata with a protected two-principal DACL at CREATE_NEW time. No inherited
ACE, broad Users/Everyone/All Application Packages grant, environment-supplied
identity or existing-file ACL repair is accepted. The actual DACL is read from
the same exclusive handle used for I/O; binary ACE type, flags, rights, SID
bounds, identity and protection are checked. Native read/write/flush/close
avoid CRT handle-ownership ambiguity. A failed new-file operation deletes only
its own handle-bound object; cleanup failure is retained with the original
error. An existing filename is never overwritten or deleted by that cleanup.

Readback rechecks the exact current OS principal and actual ACL before returning
ciphertext for the existing byte/hash/entropy/DPAPI verification. Blob and
metadata must retain the same user/package evidence. Non-AppContainer Windows
and explicit portable-test protection retain their original behavior; token
observation failure never becomes a fallback. Key envelope schema, Ed25519,
DPAPI, ticket signatures, Registry, P19 and completion semantics are unchanged.

Temporary-file ownership tracking also corrects an existing finally-block bug:
create_key previously could unlink a preexisting .dtmp after CREATE_NEW failed.
Only temporaries created by this call are now cleanup candidates. A blob already
promoted before later metadata failure remains fail-closed as before; this work
does not claim pair-atomic transactional publication or change key rotation policy.

Limits: OS user/admin ownership and another process with the SAME package
identity are not distinct isolation principals. Path observations do not prove
immunity to future host-owner mutation. Existing source/byte/scope checks remain
mandatory. This changes only newly-created AppContainer-scoped files, not the
permissions of existing desktop keys. The new shared helper joins the existing
freeze surface; existing entries and Golden corpus remain intact. Regenerate
only through UPDATE_FREEZE, followed by the normal guard without that flag.

## Test and workflow changes

Portable tests cover exact/missing/broad/changed/inherited ACLs, principal drift,
denied identity before writes/decryption, exclusive creation, original-error
preservation, handle-owned cleanup, and preservation of preexisting temporaries.
The old Windows artifact reproduces the actual product failure; initial new
module tests also failed before implementation due to the absent new API and
are not misrepresented as14 independent original behavioral defects.

The new native lifecycle test uses the actual revised ProtectedKeyStore and
real DPAPI on host and AppContainer: create -> reload -> sign/verify -> distinct
new key -> reload -> duplicate rejection -> atomic ciphertext replacement ->
readback -> failed-flush cleanup -> different-package denial -> corrupted-key
rejection -> cleanup. It is separate from full Gateway boot; neither replaces
the other. A portable fresh isolated interpreter validates both trusted import
roots and compiles the worker before the native job, preventing this round's
initial import plumbing mistake from reappearing unnoticed.

All previous supplemental startup tests must remain. The new native tests are
implemented, but the existing workflow still pins the PREVIOUS product. After
an authorized product submission, add the new test files before build/startup
and pin BOTH the actual product and observer commits. This integration is NOT
claimed complete by the local patch. No ci_fragile marker is used for the new
native case. Local Linux skips are not evidence of native PASS.

## Recurrence prevention within the master framework

Before each bounded batch, write a prerequisite/consumer/negative/exit matrix
and classify all proposed paths with the actual candidate authority. Distinguish
static source ownership, ordinary-host compatibility, restricted-environment
compatibility, evidence-parser rejection and genuine end-to-end behavior.
Use the smallest real environment diagnostic to test uncertain OS assumptions;
validate that diagnostic's own imports, embedded code and evidence preservation
before sending it to CI. Do not repeatedly use a complete application boot as
the only means to discover elementary environment requirements.

Repair shared primitives in their existing owner. Reuse rather than duplicate
native identity/ACL implementations. Test creation, subsequent use, restart or
reload, replacement and failure cleanup together. For partial startup, assert
which resources were acquired and who releases them. Keep absolute identity
and permissions distinct from path spelling and environment labels.

Bind each acceptance report to source commit/tree, dependency lock, test set,
OS/token environment and trusted observer. Preserve raw failing reports. A
passing mock, many unit tests, successful build or prior commit does not close
the current native gate. Independent checks should collect their own outcomes;
a downstream check whose prerequisite failed is BLOCKED, not PASS or OMITTED.

Stop adding new scope while this exit gate is open. R3 closes only after the
same candidate has actual AppContainer build -> Gateway READY -> clean close
-> child and parent source consistency, with final required regressions. Any
new startup blocker remains this work order, not a nominally completed next
phase. After R3 closes, return to P8 Manifest risk/alias/schema review, evidence
contracts, approved publication, live X/X+1 locks and real-model/task evidence.
The full P8 stage remains open until all master-plan obligations are met.

Primary platform references:
https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo
https://docs.python.org/3/library/os.html#os.chmod

## Delivery checkpoint: implementation is LOCAL, not submitted

The source-blob write for the proposed helper was blocked by the connector's
safety check. No alternate route, encoded upload or workflow transport was used
to bypass it. The temporary successful diagnosis workflow was removed normally;
application bytes and existing gates remain at the prior85558aa product tree.
The native evidence concerns the diagnostic design, NOT this exact new helper.

Local affected suite:221 ordinary cases passed,6 subtests passed,11 platform
skips,zero failures. The exact tested Git tree was
b84de809527869317511b996fdb864a2d8388cf9. The delivery differs only in this
clarifying document; product/test bytes and the frozen authority surface are
identical. LocalPython3.13.5 dependencies are NOT the Windows release lock.
Official source authority and committed mirror checks passed; freeze was
regenerated by the existing command and then passed without UPDATE_FREEZE.

No READY or clean full Gateway lifecycle acceptance is claimed. Resume the
normal reviewed source submission and exact-candidate native gates; do not
spend another round rediscovering the already-measured dual-principal failure.
