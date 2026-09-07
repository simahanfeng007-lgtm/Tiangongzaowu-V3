# P9 R2 - Reviewed Method Source revision boundary - 2026-09-07

Status: **R2 implementation and focused protocol validation complete; P9 IN PROGRESS.**
No production Method World publication, current-revision switch, P9 merge, or P10 work.

## Baseline and scope

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`.
- Branch: `codex/capability-composition-p9-method-source-evolution-v1`.
- R1 code baseline: `0fd327ffbbd99172008f9d186b744d24e89968b7`.
- Commit parent: `cfacd15c33f0789bda6cbd454bb80d7f163a9ed8` (document-only operator mistake; recorded below).
- Main observed: `34668e03b9f5097adde92a32e6dc8c2d7e541753` (P8 checkpoint merge).
- Master plan SHA-256: `691e857e3a7f75d9107606bbffe2919bd484c03e25f1255cebd1bb069f89f895`.

R2 turns an R1 lifecycle proposal into a separately reviewed, immutable semantic
revision using the SAME Skill Method World compiler and snapshot type:

`externally pinned base + candidates + exact JSON source bytes`
-> `independent signed simulation report + every raw case observation`
-> `separate signed review bound to reconstructed plan/candidates/base/time`
-> `existing Method World compiler with legacy/native provenance`
-> `METHOD_WORLD_REVISION_PREPARED`.

The result keeps `may_publish=false`, `may_authorize=false`, `may_execute=false`
and `current_world_changed=false`. It is prepared material for R3, not a
published WorldState, an execution permission, or proof of production acceptance.

## Implemented boundaries

The existing compiler now accepts strict, canonical, bounded semantic JSON.
Raw bytes must match the supplied SHA-256; the descriptor additionally binds
logical source path and semantics. Unknown execution fields, duplicate JSON
keys, non-finite numbers, malformed contracts, traversal/drive/ADS paths and
non-portable device names are rejected. No candidate code is imported or run.
Native versions are positive `vN`; reviewed UPDATE rejects numeric rollback.

`ReviewedMethodSourceBindingV1` preserves exact source, descriptor, simulation
and review identities. Existing legacy methods retain their many-to-one
migration evidence. Native methods do not fabricate legacy Skill parents.
Every method has exactly one provenance kind, and native path/step collisions
are rejected before graph materialization. Unmodified provenance survives
other updates. Mixed and native-only revision sequences are covered.

The SAME `SkillMethodWorldSnapshotV1` supports explicit wire schema v2 for
native/mixed provenance. The existing all-legacy v1 payload omits the new field;
the original five production seeds and their exact v1 canonical payload and
snapshot hash were independently compared against the preserved original
compiler/models, with identical results. P1 contracts and P4/P6 callers were
not rewritten; focused compatibility tests consume the revised snapshot.

`src/total_gateway/method_source_review.py` owns verification. It requires two
distinct externally configured Ed25519 public keys and separates simulation
and review signature domains. A candidate cannot nominate a new trust key in
its document. The simulation subject excludes only its evidence digest to
avoid a circular candidate/evidence hash dependency. The review binds the
complete internally reconstructed lifecycle plan, candidate hashes, base and
validity window. Reopening repeats source/evidence/signature verification
against an external revision pin, not a receipt's self-asserted flags.

Every changed method, including REMOVE, needs both positive and negative cases.
Every referenced raw observation must be present and match its signed hash,
subject, case ID and expected/observed outcome. A signed aggregate PASS does
not override a contradictory raw failure. Extra/missing evidence, stale bases,
rehashed malformed base graphs, expired reviews and signature-domain confusion
fail closed.

## Trust and integration limits

This boundary verifies authenticated evidence and exact semantic bytes. It
cannot prove that a dishonest trusted observer actually ran the experiment or
that a trusted reviewer is correct. Key provisioning and observer execution
remain external. The test suite generates temporary TEST-ONLY keys and
synthetic case observations; these are NOT independent production approvals,
real-model task results, or actual Method Source publication.

The logical path validator does not read Git or enforce filesystem ACLs. The
R3 caller must obtain bytes from the trusted repository/source authority, bind
repository/worktree/WorldFrame scope, archive the exact evidence and source
revision, and perform an atomic current-head comparison through the existing
World ingress/store. It must not admit an arbitrary rehashed result object as
trusted current state. Existing legacy corpus context is still required by
the cold-start compiler; native methods themselves need no legacy derivation.

No second Gateway/Runtime/WorldState/Method registry/Memory authority was added.
There is no current pointer, filesystem write, permanent SKILL.md, runtime hot
import, permission expansion or Life learning cutover in this change. P19,
CompletionGate, the freeze manifest, source-ownership rules, workflow settings,
Action Manifest and P8 packaging kernel remain unchanged.

## Local evidence

The test workspace was reconstructed from the original C5 source-revision ZIP:
all 2,911 indexed entries passed size/hash verification. The committed Manifest,
main's sealing checkpoint and R1 sources were restored and checked separately.
This is an artifact-derived test workspace, NOT a claimed native clone or full
exact-parent checkout. Native Git clone failed because github.com DNS resolution
was unavailable. Remote commit construction retains untouched files from the
actual parent Git tree, rather than uploading this reconstructed workspace.

The three modified pre-existing files match these parent blobs:

- `skill_method_world/__init__.py`: `9078c8aa8bc397c5d445758c2420f6b3367c7d7f`.
- `skill_method_world/compiler.py`: `860f6adaf782eff348f8c144c8f1292fb9be8731`.
- `skill_method_world/models.py`: `59e8331a21c5c34ea09baed5c5b10e84cecc0f61`.

Observed local results, all with original logs retained:

| Observation | Result |
|---|---|
| R1 baseline reproduced | 58 passed |
| First new R2 suite | 68 passed, 1 failed: new test used the wrong existing P6 field name |
| Corrected final R2 suite | 75 passed, no failures/skips |
| Final expanded focused regression | 199 passed, 0 failures/errors/skips; 5 existing Pydantic warnings |
| Source Authority | 17 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary: PASS |
| Official generated mirrors | Original generator --write then --check: PASS |
| Exact input inventory | 887 source/test Python files unchanged during the final completed run and rechecked afterward |
| Original/current five-seed v1 payload | Byte-identical; snapshot SHA-256 `43397b514e1b785bb83398e9b16eda71059a49a38b976e8b2660f7c7a31160ff` |

These groups overlap and must not be added as independent tasks. A preliminary
expanded invocation was externally stopped at the container's 100-second limit;
its `final-focused.log` is PARTIAL, not PASS. The supervised rerun completed with
exit 0 in 48.98 seconds (pytest: 46.28 seconds). The original test field mistake
was corrected to `source_revision_refs`; no old test assertion was weakened.

Environment: Linux / Python 3.13.5, NOT locked native Windows/Python 3.12.
The completed focused command covers new R2 and R1, P3/P4/P6, World semantic and
integration guards, P8 publication/preparation/sealing regression and the P19
freeze/architecture guard. UPDATE_FREEZE was absent and no freeze refresh was
performed. This is not the entire P19 Golden corpus or full repository CI.

Final JUnit SHA-256: `5fc5ba21688a87b4bebd2d841393cbf73faf22404ceadfd02d62414815ce7daf`.
Final complete log SHA-256: `8b0c0140d38fc785711e7a4ff09546c4adf2fe7ecc4d8a63938b77d042ba3ae4`.
Final input inventory SHA-256: `75b11abb04167c22aa875f2c49d981ba13dc5c9367f1fa027ca5f0b438e26e0d`.
First failed R2 JUnit SHA-256: `71f9a0dfd6fef4863eec4c004b7ecfce4e97233dbf6651a5cdec1dac4300df87`.

## Preserved submission error

After uploading verified code blobs, an erroneous empty `update_file` call
created commit `cfacd15c33f0789bda6cbd454bb80d7f163a9ed8` on the P9 branch.
Remote comparison confirms its only change is this newly added, empty report;
no production code changed. The intended commit object `0fcdaefb` had not been
attached to the branch. Recovery uses a normal child of `cfacd15c`, preserving
history and installing the already-tested code plus this complete report.
No forced ref update, branch reset, test edit or production merge is involved.
The source/test blobs and completed regression identities remain unchanged.

## Remaining work

R3: trusted source/evidence archival and WorldFrame binding; integration with
the existing single ingress and atomic current revision swap; actual add/update/
remove invalidation and retrieval; restart/replan and retained running-source
identity. A prepared R2 object alone cannot satisfy those gates.

R4: final adversarial coverage and exact-final-candidate full Python/Node,
Ubuntu/Windows, P14/P19 gates, normal PR review/merge and main verification.
Intermediate checkpoints use focused tests under the user's recorded cadence;
no skipped or absent CI run is counted as a passed check.

P8 acceptance debts remain separate and unapproved, including risk changes,
actual reviewed Tool Source publication, live X/X+1 observations and remaining
real task evidence. Its packaging patch is still unapplied. P10 is not started.
Merged engineering checkpoint count remains 9/18 (50%); P9 is stage 10/18.
This report does not turn that checkpoint metric into product readiness.
