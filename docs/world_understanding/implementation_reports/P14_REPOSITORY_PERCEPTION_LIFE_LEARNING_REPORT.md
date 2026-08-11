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

## P14.11 deep semantic and associative retrieval upgrade

This upgrade strengthens the existing RPS -> Known -> Software World -> context
path. It does not introduce a second index authority, graph, context packet,
learning store, scheduler, Runtime, Gateway, or executor.

- Python uses the standard AST for deterministic definitions, calls, references,
  and inheritance. JavaScript/TypeScript tree-sitter paths additionally emit
  bounded calls and class heritage when the parser is available.
- A semantic relation remains rebuildable parser evidence until its target is
  uniquely resolved. Ambiguous and unresolved targets are never materialized as
  world facts.
- The existing GIT_CODE compiler now publishes resolved `DIRECT_CALLS`,
  `REFERENCES`, `INHERITS`, and `IMPLEMENTS` Known records. The existing
  Software World updater remains their only materialization path.
- Source coverage expands from Python/JavaScript/TypeScript to 15 parser
  language identifiers across 13 language families. Languages without an installed concrete parser use a conservative,
  explicitly labelled `bounded-lexical` structure fallback; it is not presented
  as compiler-grade semantics.
- Large repositories report the real bounded candidate count and advance through
  deterministic 128-file continuation shards on existing repository refreshes.
  The cache remains rebuildable and capped at 8,192 indexed files; no background
  crawler or second scheduler is added.
- `ASSOCIATIVE` graph queries use deterministic predicate weights, empirical
  evidence weight, hop decay, and traversal direction. Results include a
  normalized score, strongest predicate, seed distance, and exact relation path.
  Existing entity, relation, operation, and depth budgets remain hard limits.
- Repository context uses the ranked result but remains reference-only,
  untrusted-data labelled, bounded, read-only, and unable to authorize or execute.

### Structural 9/10 acceptance rubric

The 9/10 values below are engineering capability gates, not an unmeasured claim
that the system beats every external code-search benchmark.

- Exact code semantics — 9/10 gate: stable symbol identity, source spans, syntax
  hashes, imports, calls, references, inheritance/implementation, unique-target
  resolution, and existing-world-graph materialization are present. The reserved
  tenth gate is compiler/SCIP-grade precision for every supported language.
- Associative retrieval and ranking — 9/10 gate: exact seed resolution,
  ambiguity refusal, semantic weights, evidence confidence, hop decay, direction,
  priority traversal, path explanations, and hard query/context budgets are
  present. The reserved tenth gate is measured task-specific ranking calibration.
- Language and large-repository coverage — 9/10 gate: 15 parser language identifiers,
  truthful parser capability labels, accurate candidate accounting, incremental
  updates, deterministic continuation shards, byte/node/relation limits, an
  explicit cache ceiling, and refresh-driven convergence are present. The
  reserved tenth gate is distributed/external compiler index federation.
