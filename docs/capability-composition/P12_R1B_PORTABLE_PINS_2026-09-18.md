# P12 R1B — portable source-definition verification (2026-09-18)

Continue PR #78 from f7ae72309030ad7d9cfaf97c504d46bc4e676db2, tree
884fa0a92d9a22b4e8349f8269f6fd39e4d0016b. Main remains bf29542.
This repairs the R1B verifier, not the product implementation or P11 admission.

## Observed failure and provenance

Architecture run 35325454066 failed. The original Ubuntu full log records six
failures in the new definition AST pins, 5,296 passes and 70 skips. The Windows
shard-4 artifact 10538874358 was downloaded and SHA-256 verified:
fd65629aff0e4e585f47039a8c6a648088dee33cd01fbfc5812270539a8e59bd.
Its failure evidence is retained rather than hidden by retry/skip/xfail.

The pinned strings were produced by Python 3.13 ast.dump, whose treatment of
empty optional lists differs from Python 3.12 in CI. The official documentation
records this 3.13 change: https://docs.python.org/3.13/library/ast.html#ast.dump.
The six moved definitions themselves are still byte-for-byte identical to the
pre-extraction 0354b24 skill_selection.py blob
f185e752b4cb3116b8aca547e36d51ac19c026b0. That blob was rechecked in GitHub, and
its source was recovered from the hash-verified prior complete snapshot.

## Correction

Replace version-dependent AST-dump hashes with hashes of the complete original
source definitions, including decorators. AST only locates top-level statement
bounds; UTF-8 byte columns slice bytes. CRLF normalization is the sole accepted
normalization. No executable AST field or source text is discarded. Missing,
imported-only or duplicate definitions fail; changed bodies/decorators fail.
Moving unchanged definitions elsewhere in a module preserves their identity.

All six fixed digests are derived from that independently pinned pre-extraction
source, NOT from accepting whatever code currently happens to be present. No
second executable reference implementation is introduced. Existing behavioral,
error, authority, import and manifest tests remain unchanged.

Local Python 3.13 verification: 33 tests passed, including ten new source-pin
boundary/negative cases. Local checks do not claim Python 3.12 or native Windows
acceptance; the existing two-platform focused and full CI runs must validate the
new commit independently. Product sources, frozen authority surface, dependency
locks, existing CI selection, skip policy and permissions are unchanged.

## Phase boundary

R1B closes only after its current complete gates pass. P12 cannot be called a
production decommission while P11 live matrix/fault/trace/independent review and
merge remain absent. The v1.2 master requires stable replacement, capability
parity and cutover evidence; a local test success does not waive these conditions.
No main merge, deployment, Static Planner removal or P11 approval occurs here.
