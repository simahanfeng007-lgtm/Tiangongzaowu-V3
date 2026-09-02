# P2 — Tool Capability World

Base: `main @ c0d9b5b8fede060fde2d379444c8ae56931b58dc`

## Purpose

P2 adds a deterministic, read-only Tool Capability World projection. It does not add a Tool Registry or any execution authority.

Authority remains:

`ACTIONS / BodyRuntime route reality`
→ `fact_kernel.compile_manifest`
→ `capability_manifest.generated.json`
→ `total_gateway.action_registry`
→ `ActionPermission`

P2 only projects the already-authoritative manifest/registry/source facts into `ToolSourcePrimitiveV1` records and structural relations for later WorldState integration in P6.

## Deterministic inputs

- existing capability manifest;
- existing `ActionRegistrySnapshot`;
- action-scoped `SourceRevisionRefV1` supplied from source/software-world observation;
- deterministic argument/result schema hashes.

Missing or drifting inputs fail closed.

## Produced relations

P2 permits only mechanically derived structural relations:

- `COMPILES_TO`
- `IMPLEMENTED_BY`
- `PROVIDED_BY`
- `AVAILABLE_IN`
- `ALIASES`
- `READS`
- `WRITES`
- `PRODUCES`

P2 deliberately does **not** produce mature semantic relations such as:

- `SUITABLE_FOR`
- `COMPOSES_WITH`
- `PREFERRED_WHEN`
- `CONFLICTS_WITH`

Those may only grow later from shadow hypotheses and Reality-verified experience under the v1.2 plan.

## Invariants preserved

- no second Runtime;
- no second Gateway;
- no second Action Registry;
- no permission minting;
- no new executable action can be introduced by the projection;
- no WorldState store is introduced in P2;
- no Memory write occurs;
- no generated mirror is edited manually.

## P2 gate

The branch must pass the repository's protected source-authority/full-regression checks. P3 starts only after this implementation is green and merged.
