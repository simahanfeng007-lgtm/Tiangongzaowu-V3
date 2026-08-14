# 天工造物 V3 修复日志

## P17-M1-01：Source Authority Guard / 源码权威拓扑守门

- 日期：2026-08-13
- 状态：已完成，待合并
- 分支：`agent/p17-m1-source-authority-closure`
- 基线：`3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- 实现提交：
  - `30368dc938b2a8a74095760ed6c20d398cfbdb25` — `refactor(p17-m1): enforce source authority topology`
  - `bf8fb04796f72121d10b5d27fbd6facbee57a6d6` — `fix(p17-m1): make generated tree hashes cross-platform`
- 变更性质：架构守门 / 工程卫生 / 跨平台一致性
- 产品功能变更：无

## 1. 修复目标

P17-M1 的第一步不搬迁现有运行代码，也不新增生命能力。先把当前仓库中“谁是人工可编辑权威源、谁是冻结兼容权威、谁只是别名、谁只能由生成器产生”变成机器可验证的硬约束。

修复前已有 `source-ownership.json` 与 `scripts/sync-generated-sources.py`，能够检查生成镜像内容是否漂移，但仍存在以下结构性缺口：

1. 无法阻止两个嵌套目录同时被声明为独立权威源。
2. 无法区分真正权威源与仅用于检索/文档的重复别名。
3. 生成目录可以嵌入人工可编辑源码树，但配置层没有强制声明例外。
4. 一个源码路径如果落入某个 generated target，旧校验不会在 ownership 拓扑层直接拒绝。
5. ownership 配置本身缺乏永久 CI 守门，只依赖阶段性 workflow。

## 2. 本轮修改

### 2.1 `source-ownership.json` 升级到 v2

新增显式 `source_role`：

- `authoritative`：当前人工可编辑权威源。
- `frozen_authoritative`：冻结兼容层权威源，只用于既有兼容链维护。
- `authoritative_alias`：不是第二权威源，仅用于文档/检索定位，必须继承 `authority_parent`。

当前 `backend-context-compactor` 被明确降为 `v3-backend-main` 的 `authoritative_alias`，避免同一文件既属于 `v3-backend-main`，又被误理解为另一套独立权威源。

对 `v3-backend-main` 新增 `generated_exclusions`：

- `bundled_skills/omni_body_skill`
- `endpoint_security.py`

这两个路径虽然物理上位于 V3 后端树内，但实际由其他权威源生成，现已被显式标记为 generated-owned 例外。

### 2.2 新增 `scripts/check-source-authority.py`

新增 dependency-free 架构校验器，负责 ownership 图本身的硬约束。

当前拒绝以下情况：

1. schema 不匹配。
2. mapping ID 重复。
3. 非法或非规范仓库路径、`..`、反斜杠配置。
4. source/target 重复声明。
5. 两个独立权威源发生父子嵌套。
6. generated target 互相嵌套。
7. 人工 source 位于 generated target 内。
8. generated target 位于人工 source 内但没有 `generated_exclusions` 明确豁免。
9. `authoritative_alias` 没有合法父权威、拥有 target、或越出父权威范围。
10. stale `generated_exclusions`：声明了例外但实际上没有对应 generated target。

该校验器只负责“谁拥有源码”的拓扑；生成文件字节/marker 一致性仍由 `sync-generated-sources.py` 负责，两者职责不重叠。

### 2.3 新增永久 `.github/workflows/architecture-gate.yml`

Architecture Gate 不再绑定 P13/P14 等阶段编号，而是作为仓库长期守门器存在。

当前在以下环境执行：

- Ubuntu latest
- Windows latest

当前步骤：

1. Validate source-authority topology
2. Verify generated-source mirrors
3. Run P17 M1 source-authority regression

触发范围：

- `main` push
- `agent/p17-*` push
- 指向 `main` 的 pull request
- 手工 workflow dispatch

## 3. CI 首轮发现的额外缺陷

首个实现提交 `30368dc...` 上线后，新 Architecture Gate 立即发现一个旧的跨平台缺陷。

现象：

- Windows：ownership 校验通过，generated mirror 校验通过，回归测试通过。
- Ubuntu：ownership 校验通过，但 `sync-generated-sources.py --check-committed` 报多个 `marker_drift`。

受影响映射包括：

- `life-service-runtime`
- `omni-body-runtime`
- `managed-novel-skill-runtime`

根因不是源码内容漂移，而是旧 `tree_hash()` 直接依赖 `Path` 的宿主平台排序规则：

- Windows/NTFS 语义下路径排序大小写不敏感。
- POSIX/Linux 路径排序大小写敏感。

当源码树包含例如 `SOURCE_OWNERSHIP.md` 与普通小写 Python 文件时，同一组文件在 Windows 与 Ubuntu 的遍历顺序不同，导致 logical tree hash 不同，最终形成假 `marker_drift`。

## 4. 跨平台修复

提交 `bf8fb047...` 修改 `scripts/sync-generated-sources.py`：

新增 `logical_tree_path_sort_key()`，统一使用历史 Windows/NTFS 兼容的逻辑排序：

- 路径分隔符归一到 `\\`
- 使用 `casefold()` 统一大小写语义
- `tree_hash()` 在哈希前显式按该 key 排序，不再依赖宿主 `Path` 默认比较行为

该策略同时满足两个要求：

1. Linux/Windows 对同一源码树计算相同 logical tree hash。
2. 保持与仓库现有 Windows 生成 marker 的历史顺序兼容，不要求无意义地重写所有 marker。

## 5. 回归测试

新增 `tests/test_source_authority_p17_m1.py`，当前共 8 组：

1. 当前仓库 ownership 图必须合法。
2. 嵌套独立权威必须拒绝。
3. 合法 alias 必须允许。
4. generated target 嵌入人工 source 且未声明 exclusion 必须拒绝。
5. 明确 generated exclusion 后必须允许。
6. source 位于 generated target 内必须拒绝。
7. marker tree hash 必须使用跨平台 Windows/NTFS 逻辑顺序，并与输入遍历顺序无关。
8. alias 不得拥有 generated target。

本地合成测试结果：8/8 通过。

## 6. GitHub Actions 验证

### Run #1

- Workflow：Architecture gate
- Run ID：`31696579363`
- 结果：失败（有效失败，未绕过）
- Windows：核心校验全部成功
- Ubuntu：在 generated mirror marker 校验处失败
- 作用：暴露并定位原有跨平台 tree hash 排序缺陷

### Run #2

- Workflow：Architecture gate
- Run ID：`31696841828`
- Head：`bf8fb04796f72121d10b5d27fbd6facbee57a6d6`
- 结果：成功
- Ubuntu：全部成功
- Windows：全部成功

已验证：

- Source Authority topology：PASS
- Generated mirror：PASS
- P17-M1 regression：PASS
- Ubuntu / Windows：双端一致

## 7. 修改文件

- `source-ownership.json`
- `scripts/check-source-authority.py`
- `scripts/sync-generated-sources.py`
- `tests/test_source_authority_p17_m1.py`
- `.github/workflows/architecture-gate.yml`
- `docs/repair-logs/2026-08-13_P17-M1-01_source-authority-guard.md`

## 8. 风险与边界

本轮已经把“当前源码权威关系”从文档约定升级为自动化硬约束，但**没有宣称物理源码目录已经完全收敛**。

当前权威源仍真实分布在：

- `src/`
- `readable-python-source/`
- `app/backend/tiangong-backend/v3/`
- `app/backend/tiangong-backend/tiangong_kernel/`
- `_internal/frozen_modules/` 的少量冻结兼容源

本轮的价值是先把这张真实地图锁住，避免下一阶段迁移过程中再次出现“改错镜像、生成目录被人工编辑、同一能力出现第二权威源”。

## 9. 回滚方式

若需要回滚本轮：

1. 回退 `bf8fb04796f72121d10b5d27fbd6facbee57a6d6`。
2. 回退 `30368dc938b2a8a74095760ed6c20d398cfbdb25`。

本轮没有修改业务 Runtime、Memory、World Understanding、Life、Gateway 执行路径，因此回滚不涉及数据迁移。

## 10. 下一步

P17-M1-02 建议进入“权威源码物理收敛设计与第一批迁移”，优先处理最容易产生误编辑的 `app/` 可编辑源与 generated 子树边界；迁移必须保持单主链、现有启动入口和现有运行行为不变，并继续逐步写独立修复日志。
