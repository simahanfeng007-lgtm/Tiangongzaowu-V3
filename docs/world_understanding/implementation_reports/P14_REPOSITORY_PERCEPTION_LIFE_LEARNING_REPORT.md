# P14 Repository Perception and Life Learning Closure

## Decision

P14 is implemented as an extension of the existing canonical chain and is suitable
for core integration. It adds no Runtime, Gateway, ingress, scheduler, worker,
authoritative store, server port, or parallel context packet.

The authority chain remains:

`repository/Life source owner -> native post-commit ingress -> World Understanding -> bounded context/evidence -> Life reflection/learning -> Total Gateway for any action`

Repository perception is a read-only, rebuildable sensor. World Understanding owns
materialized world state. Life owns durable learning and capability lifecycle state.
Total Gateway remains the only execution authority.

## Core-integration repairs

- Hardened Git sensing against inherited Git configuration, hooks/diff helpers,
  interactive credential prompts, unbounded pipe capture, output flooding, and
  command timeouts.
- Rejected secret, binary, and oversized source candidates before opening them;
  bounded baseline traversal, file bytes, nodes, imports, and total projection.
- Added a dual ingress budget: 256 KiB canonical payload and at most 64 projected
  structure facts. Projection keeps whole files, prioritizes retirement facts, is
  deterministic, and marks partial views as truncated.
- Replaced host paths and nested repository paths in WU identities with stable
  opaque subjects; removed exact duplicate Known rows with collision detection.
- Added a bounded, reference-only repository evidence snapshot for the exact
  Life/principal/workspace scope. No source text, host path, credential, or raw
  arbitrary payload crosses into Life.
- Added the dedicated `LIFE_LEARNING` source contract/compiler and post-commit
  projection for publish, activation, rollback, disable, patch, degradation, and
  reactivation transitions.
- Routed active repository inquiries to existing read-only Gateway actions. No
  repository action bypasses Total Gateway.
- Added pull-request CI coverage and made the release-after-pack test portable to
  source-only and bundled-Python environments.

## Production evidence

Evidence was collected against this repository through production composition
roots, not a fake compiler or isolated unit-test facade.

- Repository observation: `IngressReceipt`, disposition `ACCEPTED`, reason
  `SOURCE_MATERIALIZED`; exact-scope evidence schema
  `tiangong.life.repository-evidence.v1`; bounded to 32 entity references.
- Repository frame: `swf_c01de9c8ec109f9d9620c0610b3279a06d3f9d5a9baed244c831ecb6f988b407`;
  revision hash `718da2d3a48fb251d34925b656720add9f485d49265393b107fdeeb9dd992404`.
- Real Life flow: user learning draft -> confirmation -> artifact persistence ->
  authoritative journal commit -> `LIFE_LEARNING` notification -> WU
  `ACCEPTED / SOURCE_MATERIALIZED`.
- Life artifact: `art_22cd8f307be301aabea6582896188ce19f819ee5`;
  observation hash `4ad1f13c8617575f8add47e002b8c3aeadbea8e91a8c9a83ef72903d3ef71a47`.

The evidence references are run evidence, not durable protocol identifiers and are
not used by production logic.

## Merge gates

- Focused P14 security, repository structure/incremental, Life bridge, and
  reflection tests.
- P3-P13 World Understanding regression.
- Life health and single-process regression.
- Full repository test suite.
- Generated-source synchronization and committed-mirror check.
- Python compile check and `git diff --check`.

