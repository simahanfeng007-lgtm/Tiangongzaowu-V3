# P17-M2-03 ToolResult / Continuation Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M2-03 — ToolResult / Continuation Boundary Extraction

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Working branch: `agent/p17-m2-god-module-decomposition`
- M2-02 final log baseline: `c5682695635139e4fea05fe4f6136f66891f1c66`
- `main` baseline remained `3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- No merge to `main` was performed.

## Objective

Reduce `zongdiaodu.py` responsibility without creating a second ToolResult protocol, second Tool Executor, second completion engine, second Runtime, or side startup path.

The intended authority chain after this stage is:

```text
existing Tool Executor (_jineng_zhixing)
    -> raw tool result
    -> canonical tool_result_contract.py
    -> runtime_tool_result_boundary.py
       - UI dispatch projection
       - contract envelope / World post-commit
       - observed-write verification
       - evidence-to-completion delegation
    -> existing execution_integrity.decide_task_contract_completion
    -> existing Turn continuation / closeout path
```

## Audit finding

`app/backend/tiangong-backend/v3/tool_result_contract.py` was already the real canonical contract authority. It owns schema `tiangong.v3.tool_result.v1`, status normalization, write evidence, paths, media, attachments, artifacts and result success/failure normalization.

The defect was architectural coupling rather than missing protocol functionality: `zongdiaodu.py` consumed the low-level contract directly in several separate helpers and also owned dispatch projection, post-commit projection, write-evidence verification and evidence-to-terminal-decision wiring.

Therefore this stage deliberately did **not** create another ToolResult schema.

## Implementation

### 1. Added `runtime_tool_result_boundary.py`

New authoritative application-boundary module:

`app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py`

Responsibilities:

- `canonical_tool_result()`
  - thin delegation to the existing `normalize_tool_result()`;
  - owns no schema and no alternate normalization rules.
- `project_tool_dispatch()`
  - preserves the historical dispatch/UI result shape: `status`, `resultStatus`, `resultContract`, and failure `resultSummary`.
- `attach_tool_result_contract()`
  - preserves canonical contract attachment;
  - preserves deterministic native event id behavior;
  - keeps World Understanding `TOOL_RESULT` post-commit downstream of the canonical result;
  - preserves causal fields from `RunContext`.
- `contract_observed_write()`
  - requires authoritative observed write evidence for new contracts;
  - keeps the historical `write_effect` fallback only for durable legacy checkpoints.
- `tool_write_verified()`
  - preserves authoritative write evidence checks, readback/evidence behavior and the historical B4 existing-file fallback.
- `decide_simple_chain_completion()`
  - connects the existing evidence-check port to the existing terminal authority;
  - semantic terminal authority remains `execution_integrity.decide_task_contract_completion()`.

### 2. Converted `zongdiaodu.py` helpers into façades

The following public/internal helper names remain in their original module so current call sites do not need a broad migration:

- `_tool_dispatch_with_result`
- `_tool_result_with_contract`
- `_contract_observed_write`
- `_tool_write_verified`
- `_simple_chain_life_completion_gate`

Their implementation now delegates to the M2-03 boundary.

The quality-gate contract read also goes through `canonical_tool_result()` rather than directly calling the low-level normalization function.

### 3. Preserved single execution authority

`self._jineng_zhixing(...)` remains in `zongdiaodu.py` and is not imported or implemented by `runtime_tool_result_boundary.py`.

This stage did not move:

- permission checks;
- Authority Gate / A0-A5 decisions;
- tool execution;
- tool side effects;
- Runtime ownership;
- Memory authority;
- World Understanding authority;
- Life Runtime authority.

### 4. Preserved single terminal authority

`runtime_tool_result_boundary.py` does not classify task semantics or invent a second terminal state machine.

It accepts the existing evidence-check callable as an `EvidenceCheckPort` and delegates exactly once to:

`execution_integrity.decide_task_contract_completion()`

The direct terminal-decision call was removed from `zongdiaodu.py`.

### 5. Closed-world ownership

`runtime_tool_result_boundary.py` was added to the existing V3 closed-world `implementation_roots` in `source-ownership.json`.

No new source authority, runtime root, compatibility root or generated mirror was introduced.

### 6. Permanent Architecture Gate

`.github/workflows/architecture-gate.yml` now permanently runs:

`python tests/test_zongdiaodu_p17_m2_03.py -v`

and compiles `runtime_tool_result_boundary.py` together with all prior M2 V3 seams.

## Permanent regression tests

Added:

`tests/test_zongdiaodu_p17_m2_03.py`

Seven regression cases lock the following properties:

1. the boundary consumes canonical `normalize_tool_result` but owns no ToolResult schema or Tool Executor;
2. dispatch projection preserves historical output fields and status behavior;
3. contract envelope keeps World post-commit downstream of the canonical contract;
4. observed writes require authoritative evidence while preserving legacy resume fallback;
5. completion boundary delegates to the existing single terminal authority;
6. `zongdiaodu.py` façades delegate to the new boundary while `_jineng_zhixing` stays local;
7. V3 closed-world ownership includes the new implementation module.

## Candidate safety process

`zongdiaodu.py` is a very large production module, so it was not manually re-uploaded as a hand-edited 400+ KB file.

The candidate was produced with an AST/name-based migration script:

- every target top-level function had to exist exactly once;
- import anchors had to match exactly once;
- direct `normalize_tool_result(...)` and `decide_task_contract_completion(...)` calls were required to disappear from `zongdiaodu.py`;
- `self._jineng_zhixing(...)` was required to remain present;
- the resulting file had to parse successfully before validation.

The first candidate run stopped before touching product code because the construction script itself had an invalid multiline-string quoting form. That was corrected and locally compiled before rerun.

A second quality issue was also corrected before finalization: the first candidate serialized `source-ownership.json` with pretty formatting, creating a noisy `+227/-31` diff for a one-entry semantic change. The final candidate restores the M2-02 byte layout and performs only the intended insertion, reducing the ownership diff to `+1/-1`.

Neither construction issue represented a production/runtime regression.

## Verified candidate identity

Read-only candidate artifact digest:

`sha256:0bfbaecb5ae4b7af0fc69f4bb91da4f7ef67b83de8e3c605c7fa572191b99c57`

The final materialization job created only unreferenced Git blobs and did not create commits, update refs or push product changes.

Final verified blob identities:

- `.github/workflows/architecture-gate.yml` — `4089878cc541d9ccd018392c67725ae167003300`
- `runtime_tool_result_boundary.py` — `c7e680e40307b231d978e5391e553a6cbac4ce07`
- `zongdiaodu.py` — `3b88637ece4aa601fa6253d53c7075da625fe76d`
- `source-ownership.json` — `e9efa3db0a733ac93ec320afad3b51b44d95745e`
- `tests/test_zongdiaodu_p17_m2_03.py` — `40f11e70e9f0b2e944f9a555438b6db3f0ce436c`

## Clean implementation

Clean implementation commit:

`34205726ebb8e0a10eec8927b49aa6ffebca7327`

Construction-lineage closure commit:

`25d51de9c557a84bb2c21d3e10981f501a38a3bf`

The closure commit uses:

- first parent: clean M2-03 implementation;
- second parent: auditable construction lineage;
- Tree: exactly the clean implementation Tree.

This was used because the connector did not permit force-ref cleanup. Construction scripts and temporary workflows are therefore reachable only through Git history and are absent from the final runtime Tree.

## Final net diff from M2-02

Relative to `c5682695635139e4fea05fe4f6136f66891f1c66`, the final M2-03 Tree changes exactly five product/repository files:

1. `.github/workflows/architecture-gate.yml`
2. `app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py`
3. `app/backend/tiangong-backend/v3/zongdiaodu.py`
4. `source-ownership.json`
5. `tests/test_zongdiaodu_p17_m2_03.py`

`zongdiaodu.py` net change in this stage: `+24 / -109`.

No candidate script, candidate workflow, materialization workflow or formatting helper exists in the final Tree.

## Final Architecture Gate

Implementation/closure verification:

- Run: `31727290358`
- Head: `25d51de9c557a84bb2c21d3e10981f501a38a3bf`
- Conclusion: `success`
- Ubuntu: success
- Windows: success

Both OS jobs passed:

- Source Authority topology;
- generated-source mirror verification;
- P17 M1 regression (13 tests);
- P17 M1-03 regression (5 tests);
- P17 M2-01 regression (6 tests);
- P17 M2-02 regression (6 tests);
- P17 M2-03 regression (7 tests);
- M2 V3 seam compilation.

## Explicit non-changes

This stage did not modify the canonical implementation rules in `tool_result_contract.py`.

It also did not modify:

- `src/life_service/embedded_runtime.py`;
- Total Gateway authority;
- A0-A5 gate semantics;
- tool permission policy;
- Memory SSoT;
- World Understanding canonical implementation;
- Life state machine semantics;
- Store/SQLite authority;
- `main`.

## Result

P17-M2-03 is complete.

The V3 execution chain now has an explicit ToolResult/Continuation application seam while retaining one canonical ToolResult protocol, one Tool Executor and one semantic terminal authority.

Next planned stage: **P17-M2-04 — Life Embedded Runtime God Module Decomposition**.
