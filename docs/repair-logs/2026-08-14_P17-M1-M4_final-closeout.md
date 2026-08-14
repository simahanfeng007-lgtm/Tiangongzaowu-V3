# P17 M1-M4 Final Closeout

Date: 2026-08-14 (Asia/Shanghai)

## Verdict

P17 M1-M4 结构性收口正式封板。本阶段不是失败的重构：从源码权威、Runtime
职责、Store authority、Memory SSoT 与事务边界看，原"大泥球"结构已被明显
压下去；剩余工作属于发布周期与后续规划，不在 M1-M4 范围内扩大。

## Stage scope (frozen, 4/4)

1. P17-M1 — Source Authority 收口 — complete
2. P17-M2 — God Module 拆分 (5/5) — complete
3. P17-M3 — Store / Transaction 拆分 (5/5) — complete
4. P17-M4 — Architecture Gate + 全链回归 — complete

Per-milestone evidence lives in the repair logs:

- `docs/repair-logs/2026-08-13_P17-M1-*.md`
- `docs/repair-logs/2026-08-13..14_P17-M2-*.md`
- `docs/repair-logs/2026-08-14_P17-M3-*.md`
- `docs/repair-logs/2026-08-14_P17-M4_architecture-gate-full-regression.md`

## Post-M4 hardening (before this closeout)

Beyond the milestone work, three first-principles repairs were applied to the
same "derived value written back as authority" bug class found while
hardening the settings surface:

1. Provider identity / routing fallback separation (`peizhi.py`,
   `duihua_qiaojie.py`): the L4 routing fallback can no longer overwrite the
   persisted provider identity; empty/unknown providers persist as
   `custom`; legacy corrupted records self-heal on read.
   Locked by `tests/test_provider_identity_persistence.py` (7 contracts).
2. Workspace settings are field-scoped (`workspace_settings.py`): a
   mode-only save no longer overwrites the configured workspace path with the
   default, and an empty value means "no new value".
   Locked by `tests/test_settings_authority_contract.py` (4 contracts).
3. Workspace tests no longer write the developer's real
   `~/.tiangong/v3/workspace_settings.json` (module-level Path.home() binding
   replaced by attribute patching + a hard untouched-file assertion).

The M4 AST guards were also hardened
(`tests/test_p17_m4_architecture_guards.py`, now 16 checks):

- import-time side effects are scanned on bare statements AND assignment
  values with an extended marker set (install/observer/register/init/
  configure/apply/mount/listen/bind/connect/attach/watch/hook/patch);
- forbidden-legacy and layer scans resolve multi-name imports (all names,
  not just the first), aliased imports, and relative `from . / from ..`
  imports to their absolute module, so none of these shapes can hide a
  forbidden or cross-layer target.

## Permanent Architecture Gate

Two jobs, Ubuntu + Windows, triggered on every `main` / `agent/p17-*` push,
PR and manual dispatch:

- `source-authority`: source authority topology, generated mirror drift,
  P17 M1-M4 regressions, single-writer/cutover guards, provider identity
  contract, settings authority contract, seam compile.
- `full-regression`: complete repository pytest with pinned
  `requirements-source.lock`; Windows leg skips only the 17
  `ci_fragile`-marked runner-environment tests.

### main branch protection (non-bypassable)

`main` is now protected:

- required status checks: `source-authority-ubuntu-latest`,
  `source-authority-windows-latest`, `full-regression-ubuntu-latest`,
  `full-regression-windows-latest` (strict)
- enforce_admins: enabled (direct pushes to main are rejected; changes reach
  main only through a pull request whose four checks pass)
- required approving review count: 0 (solo workflow; PR still required)

## Final gate evidence (final HEAD)

- Branch: `agent/p17-m4-architecture-gate`
- Final implementation HEAD: `ffc5aad`
- Gate run: `31780750126` (the closeout document commit re-runs the same gate and must stay green)
- source-authority: Ubuntu success / Windows success
- full-regression: Ubuntu success / Windows success

## Explicit non-changes

P17 M1-M4 does not change:

- product runtime behavior beyond the provider/workspace authority repairs
- Life / Gateway / World authority semantics
- A0-A5 gate behavior
- store schemas or transaction semantics
- `main` (still at `3d5f13b`; integration is a release-cycle decision)

## Frozen pre-release step (task list)

Before any formal Windows release, run a real Windows release acceptance on
the merged integration state:

```powershell
cd app
npm run release:win
powershell -ExecutionPolicy Bypass -File ..\scripts\verify-windows-artifacts.ps1
```

Acceptance checklist:

- release binding re-verified from the unpacked installer (one-byte tamper
  of app.asar must fail the binding)
- app.asar VRM module closure verified
- frozen total gateway passes the Runtime/Life/Communication/Policy contract
  probes (life API contract failure blocks the build)
- generated-mirror hashes byte-identical to the authoritative source tree
- unsigned candidate is explicitly marked "未签名候选包" until a real
  signing certificate is configured

## Recommendation

M1-M4 is sealed here. Do not expand the M1-M4 scope further; remaining work
(main-line integration, signing, release acceptance) belongs to the release
cycle and any future P-packages.