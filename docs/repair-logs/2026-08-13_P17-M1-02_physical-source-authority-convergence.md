# 天工造物 V3 修复日志

## P17-M1-02：Physical Source Authority Convergence / 物理源码权威收敛

- 日期：2026-08-13
- 状态：已完成，待合并
- 分支：`agent/p17-m1-source-authority-closure`
- 前置阶段：`P17-M1-01 Source Authority Guard`
- 本步基线：`4a4c1c4e43a06942395b0ba87677fbb67a942205`
- 实现提交：`60530e24229cadf8d168dd7d430b69ecb742e69a`
- 实现提交说明：`refactor(p17-m1): converge readable authorities into src`
- 变更性质：源码权威物理收敛 / 兼容镜像治理 / 架构卫生
- 产品功能变更：无
- Runtime 行为变更：无

## 1. 本步目标

P17-M1-01 已经把现有源码 ownership 从文档约定变成机器可检查的拓扑，但仓库里仍存在一个明显的物理分散点：`readable-python-source/` 仍承载多个可人工修改的生产权威源。

这会造成长期维护歧义：

- 工程师看到 `src/` 与 `readable-python-source/` 都可能认为是源码入口；
- AI 在做自动修复时可能修改旧 readable 路径而不是当前权威源；
- 同一能力在 `src/`、readable、app/runtime 三类位置之间缺乏单一修改入口；
- 后续 God Module 拆分或 Runtime 收口时，源码位置判断仍然存在额外分支。

本步目标不是删除旧路径，也不是修改运行结构，而是先完成第一批低风险物理收敛：

> 把 `readable-python-source/` 中仍属于生产权威的内容原样迁移到 `src/`，旧路径降为 deterministic generated compatibility mirror。

## 2. 设计约束

本轮严格遵守以下边界：

1. 不新建第二 Runtime。
2. 不改变现有启动入口。
3. 不改变 Gateway、Life、Memory、World、Tool 执行路径。
4. 不删除历史 readable 路径，避免旧脚本、检索或维护工具立即失效。
5. 不重写业务内容，迁移文件按原 blob 原样复用。
6. 所有旧路径必须进入 `source-ownership.json` targets，由现有同步器管理。
7. Linux / Windows 必须同时通过 Architecture Gate。

## 3. 第一批物理迁移

### 3.1 Life bootstrap

迁移前权威：

- `readable-python-source/life-bootstrap/tiangong_life_bootstrap.py`
- `readable-python-source/life-bootstrap/tiangong_life_runtime_fixes.py`

迁移后权威：

- `src/life_bootstrap/tiangong_life_bootstrap.py`
- `src/life_bootstrap/tiangong_life_runtime_fixes.py`

原 readable 文件继续保留，但现在是 generated targets；Runtime314 目标文件也继续由同一 mapping 生成。

### 3.2 Omni Body

迁移前权威：

- `readable-python-source/omni_body_skill/`

迁移后权威：

- `src/omni_body_skill/`

原目录和以下运行镜像全部变成同一权威源的 generated targets：

- `readable-python-source/omni_body_skill/`
- `app/backend/tiangong-backend/omni_body_skill/`
- `app/backend/tiangong-backend/_internal/omni_body_skill/`
- `app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill/`
- `app/backend/tiangong-backend/_internal/v3/bundled_skills/omni_body_skill/`

源码内容没有改写。Git tree 直接复用原有 blob/tree，因此本次迁移是位置权威变化，不是业务代码重写。

### 3.3 Managed Novel Skill

迁移前权威：

- `readable-python-source/bundled-skills/novel-creation/`

迁移后权威：

- `src/bundled_skills/novel-creation/`

原 readable 路径与运行内置 Skill 路径均成为 generated targets。

## 4. `source-ownership.json` 收敛结果

本轮新增 `authority_policy`，明确当前允许的物理权威根：

### 正常人工权威根

- `src/`
- `app/backend/tiangong-backend/v3/`
- `app/backend/tiangong-backend/tiangong_kernel/`

### 冻结兼容权威根

- `app/backend/tiangong-backend/_internal/frozen_modules/`

### 兼容镜像根

- `readable-python-source/`

本轮之后，`readable-python-source/` 不再允许承载 independent authority。

当前 Source Authority Guard 输出：

- independent authorities：16
- authoritative aliases：1
- generated targets：23

生成目标从 M1-01 的 19 个增加到 23 个，新增的 4 个 target 正是本轮降级后的旧 readable 路径。

## 5. 兼容策略

没有直接删除 `readable-python-source/`。

原因：

- 仓库历史工具、人工检索、旧文档或本地脚本可能仍使用这些路径；
- 当前阶段目标是消灭“第二可编辑权威”，不是强制一次性删除兼容路径；
- 继续保留 generated mirror 可以在不破坏旧路径读取能力的前提下完成 SSoT 收口。

新增：

- `readable-python-source/README.md`
- 更新 `readable-python-source/bundled-skills/README.md`

README 已明确声明：该目录现在是 compatibility mirror，不得直接修改受管源码。

## 6. Generated marker 更新

由于 Omni Body 与 Novel 的 authority source path 已改变，所有对应目录 marker 已同步更新。

Omni Body marker 现在统一记录：

- `mapping_id = omni-body-runtime`
- `source = src/omni_body_skill`
- `file_count = 148`
- `tree_sha256 = 61bf1cad81cf21062df863b600ab24d5a78dbe6e53572a4ba2d98ad22c8f2f09`

Novel marker 现在统一记录：

- `mapping_id = managed-novel-skill-runtime`
- `source = src/bundled_skills/novel-creation`
- `file_count = 9`
- `tree_sha256 = 5f0ca06c4524cff7f677628fb7250c73c15ab430a840ad879fa66610641d5ff4`

旧 readable 目录也新增 marker，因此从仓库视觉和自动化两方面都能识别其 generated-mirror 身份。

## 7. 回归测试扩展

`tests/test_source_authority_p17_m1.py` 从 8 组扩展到 13 组。

新增重点校验：

1. `readable-python-source/` 下不得再有 independent authority。
2. Life bootstrap / Omni Body / Novel 的权威路径必须位于新 `src/` 位置。
3. 旧 readable 路径必须存在于对应 mapping 的 generated targets 中。
4. authority root policy 必须保持当前收敛结构。
5. 所有已提交目录镜像 marker 必须指向当前真正 authority source，并校验 file count 与 tree hash。

原 M1-01 的双权威、alias、generated exclusion、cross-platform tree hash 等测试继续保留。

## 8. GitHub Actions 验证

### Architecture Gate Run #4

- Run ID：`31697741859`
- Head：`60530e24229cadf8d168dd7d430b69ecb742e69a`
- 结果：`success`

### Ubuntu 24.04

全部通过：

- Source Authority topology：PASS
- Generated mirror check：PASS
- P17-M1 regression：13/13 PASS

Guard 输出：

`[source-authority] PASS: 16 independent authorities, 1 aliases, 23 generated targets`

测试输出：

`Ran 13 tests ... OK`

### Windows latest

全部通过：

- Source Authority topology：PASS
- Generated mirror check：PASS
- P17-M1 regression：PASS

说明此次物理权威迁移没有重新引入 M1-01 已修复的跨平台 mirror/hash 问题。

## 9. 运行影响评估

本轮没有修改：

- `zongdiaodu.py`
- Gateway Runtime
- Life Runtime
- Memory SSoT
- World Understanding
- RunContext
- Tool execution / A5 gate
- 前端协议
- 数据库 schema

因此不存在运行数据迁移，也不存在模型行为变化。

旧路径仍然可读，且字节内容继续与新权威源一致；变化只在“今后应该在哪里修改源码”。

## 10. 当前源码物理结构

完成 M1-02 后，普通可编辑生产源码已经进一步收敛：

```text
src/
  contracts/
  life_service/
  life_bootstrap/
  total_gateway/
  world_understanding/
  communication_service/
  runtime_security/
  omni_body_skill/
  bundled_skills/novel-creation/

app/backend/tiangong-backend/v3/
  现有生产主树（暂保持原位）

app/backend/tiangong-backend/tiangong_kernel/
  现有 kernel 权威树（暂保持原位）

app/backend/tiangong-backend/_internal/frozen_modules/
  仅冻结兼容 authority

readable-python-source/
  compatibility mirrors only
```

## 11. 剩余风险与下一步

本轮已经清除了 `readable-python-source/` 作为人工生产权威根的角色，但物理收敛还没有完全结束。

仍存在两个故意保留的主要非 `src/` 正常权威：

1. `app/backend/tiangong-backend/v3/`
2. `app/backend/tiangong-backend/tiangong_kernel/`

其中 `v3/` 是当前主生产树且包含 `zongdiaodu.py` 等高耦合入口，不适合在 M1-02 直接搬迁；应在后续 God Module / Composition Root 收口中逐步拆出明确模块，再决定是否迁入 `src/`。

`tiangong_kernel/` 同样暂不强搬，避免在缺乏完整调用边界验证时制造 import/path 风险。

下一步建议：

**P17-M1-03：V3 主树 generated boundary 与 editable boundary 精细化**。

目标不是搬整个 v3，而是进一步识别其中哪些目录是真正生产权威、哪些只是桥接/兼容/生成内容，把 `v3` 这个大粒度 authority 拆成可验证的边界，为后续 M2 God Module 拆分做准备。

## 12. 回滚方式

若需回滚本步：

- 回退实现提交 `60530e24229cadf8d168dd7d430b69ecb742e69a`。
- 本日志提交可单独回退。

由于本步没有数据 schema 或 Runtime 行为迁移，回滚不需要数据库恢复。
