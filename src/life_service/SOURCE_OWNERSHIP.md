# Life service source ownership (P0-P11)

This package is the source-owned replacement path for the frozen Python 3.14
life authority on port 7175.

P0 remains the default and is intentionally status-only:

- no network listener;
- no writer lease;
- no scheduler;
- no access to real life data;
- no execution proxy;
- no fallback that can become a second writable authority.

P2 adds an optional authenticated loopback shadow listener. It accepts only a
manifest-bound offline `snapshot_copy` and hard-rejects production port 7175,
writer lease acquisition, scheduling, mutation, and execution bridging.

The snapshot adapter verifies the legacy Ed25519 identity, Soul, and event
chain; the writer epoch; immutable snapshot tree; memory SQLite schema; and
AES-256-GCM bindings for memory/context before emitting comparison anchors.
No live user-data path is discovered or accepted implicitly.

Every accepted snapshot contains `life_snapshot_manifest.json` with:

- `schema = tiangong.life.legacy-snapshot.v1`;
- `source_kind = snapshot_copy` and `immutable = true`;
- `capture_consistency = atomic`;
- `capture_method` equal to `stopped_process_copy`, `volume_shadow_copy`, or
  `sqlite_backup`;
- UTC `captured_at`, relative `life_roots`, and the computed `tree_sha256`.

SQLite WAL/SHM sidecars are rejected. This prevents an immutable SQLite reader
from silently comparing a stale main database while newer rows remain only in
WAL. The shadow process exposes only a bearer-authenticated `127.0.0.1`
listener on a non-7175 port.

The packaged mirror lives at app/life-service/runtime314/life_service and must
match this directory byte-for-byte. Later phases do not weaken the default
single-writer gates.

P11 adds the only production-writable source mode. It is unavailable unless an
operator supplies all of the following mutually bound artifacts:

- an immutable, signature-verified final legacy snapshot;
- `cow_final.json`, whose final journal is prefix-bound to the initial import;
- a healthy copy-on-write SQLite overlay;
- zero-pending/zero-inflight drain evidence that attests the old writer stopped;
- an Ed25519-signed handoff permit for exactly `legacy_epoch + 1` and port 7175;
- the pinned cutover public key and desktop loopback token.

The first state install enrolls that public-key hash in root-level `trust.json`.
Release-local keys are never self-trusted: upgrade, overwrite, recovery, desktop
startup, and rollback all have to match the enrolled root hash. The mutable
overlay is identity-checked rather than frozen to a release file hash, so normal
context writes do not invalidate the software bundle.

The service dual-reads the immutable legacy base and the source overlay, but
writes only the overlay. The scheduler, external side effects, and arbitrary
mutation routes remain disabled. An absent, partial, expired, tampered, or
cross-bound handoff cannot activate the source writer; startup instead keeps the
frozen compatibility runtime. Rollback first stops the new writer, promotes the
epoch again, and requires the compatible replay hash list to equal the complete
post-cutover event list, so incompatible events cannot be silently dropped.

State installation uses content-hashed release directories plus one atomic
`active.json` pointer. Upgrades retain the prior release, rollback only moves the
pointer after copying the drained current overlay forward and installing an
epoch-promoted compatibility permit. Recovery requires an operator-supplied
overlay hash and refuses an older writer epoch, so it cannot guess which of two
divergent state copies is newer.
