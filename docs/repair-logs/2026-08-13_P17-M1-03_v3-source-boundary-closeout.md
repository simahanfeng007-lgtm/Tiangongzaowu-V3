# 天工造物 V3 修复日志

## P17-M1-03：V3 Source Authority Boundary Closeout / V3 主树权威边界收口

- 日期：2026-08-13
- 状态：已完成，待合并
- 分支：`agent/p17-m1-source-authority-closure`
- 前置阶段：`P17-M1-01`、`P17-M1-02`
- 本步基线：`46a733170eef2e0398d2f63a3ed8964f25ac161f`
- 实现提交：`4642de5f9f3083f87c883a0a32de15e54f907612`
- 实现提交说明：`refactor(p17-m1): close v3 source authority boundary`
- 变更性质：源码权威边界收口 / generated 副本治理 / compatibility shim 守门
- 产品功能变更：无
- Runtime 行为变更：无

## 1. 本步目标

P17-M1-01 已建立 Source Authority Guard，P17-M1-02 已将 `readable-python-source/` 中仍承担人工权威的生产源码收敛到 `src/`。

M1 最后一项遗留问题是：

`app/backend/tiangong-backend/v3/` 仍被整体声明为一个可编辑权威树，但树内实际同时存在四种不同性质的内容：

1. 真正由 V3 主树拥有的生产实现；
2. 从其他权威源生成进 V3 的镜像；
3. 为旧 import 保留、但不拥有实现和状态的 compatibility adapter；
4. 开发日志、设计文档等非 Runtime 文件。

如果继续把它们全部笼统视为 V3 自有实现，后续进入 M2 拆 `zongdiaodu.py` / God Module 时，AI 或工程师仍可能把 generated 副本、兼容 shim 当成第二套源码继续修改。

因此 M1-03 的目标是将 V3 主树升级为 **closed-world source boundary**：

> V3 顶层每一个路径都必须被明确分类。任何新增但未分类的顶层内容，Architecture Gate 必须直接失败。

## 2. 修复前发现的真实问题

### 2.1 V3 Novel Skill 是未受管副本

发现路径：

`app/backend/tiangong-backend/v3/bundled_skills/novel-creation/`

此前真正权威源已经是：

`src/bundled_skills/novel-creation/`

并且 `_internal/v3/bundled_skills/novel-creation/` 已经是 generated target。

但 V3 主树中的 `bundled_skills/novel-creation/` 没有进入 `source-ownership.json`，没有 generated marker，因此它在仓库结构上仍然看起来像一份可独立修改的生产源码。

此外，该副本比真正权威源多一个文件：

`references/source-map.md`

该文件记录历史 desktop archive 来源和整合设计决策，属于来源追踪文档，不属于 Novel Runtime/Skill 本体。

### 2.2 `world_cognition/` 实际是 compatibility shim

检查：

`app/backend/tiangong-backend/v3/world_cognition/`

其中 `__init__.py`、`facade.py`、`consolidator.py`、`evidence.py`、`priors.py`、`retrieval.py`、`stability.py`、`store.py` 均为 legacy compatibility re-export。

真实实现位于：

`src/world_understanding/world_understanding/cognition/` 对应的 `world_understanding.cognition.*` 模块。

因此 `v3/world_cognition` 不应被理解为第二套世界认知实现，也不应允许在其中继续增长业务状态、类、函数或执行逻辑。

### 2.3 `hotfix_20260727.py` 不是可清理历史文件

本轮专门检查了：

`app/backend/tiangong-backend/v3/hotfix_20260727.py`

虽然文件名带日期并看起来像历史补丁，但其模块说明明确指出仍由 `v3/peizhi.py` 启动链触发 import，用于对冻结 Gateway/Life 模块进行运行时兼容修补。

因此本轮没有删除、迁移或降级它，而是继续将其归入 V3 production implementation。

M1 只做真实权威边界治理，不以文件名推测运行身份。

## 3. V3 closed-world 分类

`source-ownership.json` 中 `v3-backend-main` 现在新增 `boundary_policy`：

`mode = closed_world`

V3 顶层分成四类。

### 3.1 Production implementation

`implementation_roots` 明确列出当前由 V3 主树真正拥有的生产实现，包括：

- `zongdiaodu.py`
- `peizhi.py`
- `run_context.py`
- `novel_system.py`
- `hotfix_20260727.py`
- `context_compactor.py`
- `confirmation_store.py`
- `execution_integrity.py`
- `fact_kernel/`
- `gutong/`
- `jineng/`
- `jinhua/`
- `zhili/`
- `ziyu/`
- 以及当前其余明确的 V3 生产模块

本轮只是明确 ownership，不移动或改写这些模块。

### 3.2 Generated-owned

`generated_exclusions` 收口为：

- `bundled_skills`
- `endpoint_security.py`

这意味着整个 V3 `bundled_skills/` 容器现在是 generated-owned，不再只对其中的 Omni Body 做例外声明。

其中：

- Omni Body 权威：`src/omni_body_skill`
- Novel Skill 权威：`src/bundled_skills/novel-creation`
- Endpoint Security 权威：`src/runtime_security/model_endpoint.py`

### 3.3 Compatibility-only

`world_cognition` 被定义为：

- `implementation_authority = world-understanding-embedded-python`
- `contract = python_reexport_only`
- `import_prefix = world_understanding.cognition`

即该目录可以继续服务旧 import，但不能拥有独立实现。

### 3.4 Non-runtime artifacts

以下 V3 顶层文件被明确归类为非 Runtime 资产：

- `KAI FA_RIZHI.txt`
- `KAI_FA_RIZHI.txt`
- `成长路径设计.md`

Guard 会阻止把 Python 源码伪装进 non-runtime 分类。

## 4. Novel 副本治理

`managed-novel-skill-runtime` 新增 target：

`app/backend/tiangong-backend/v3/bundled_skills/novel-creation`

同时：

1. 新增 `.tiangong-generated-source.json`；
2. marker 指向真正权威源 `src/bundled_skills/novel-creation`；
3. 原 V3 副本中多出来的 `references/source-map.md` 从 generated target 移除；
4. 该文档原 blob 不丢失，迁移到：
   `docs/source-provenance/novel-creation-source-map.md`。

因此既消除了第二可编辑副本，也保留了历史来源信息。

## 5. Source Authority Guard 扩展

`scripts/check-source-authority.py` 现在除原有 ownership topology 检查外，增加 closed-world boundary 校验。

对于设置 `boundary_policy.mode = closed_world` 的 authority，Guard 会检查：

1. implementation / generated / compatibility / non-runtime 四类不得重复归属；
2. 分类项必须是 authority 的直接子项；
3. 实际顶层目录必须和分类全集完全一致；
4. 新增未分类顶层路径直接失败；
5. 配置中已经不存在的 stale 分类直接失败；
6. non-runtime artifact 不得包含 Python 实现；
7. compatibility adapter 必须指向另一条合法 independent authority；
8. compatibility adapter 不得把自身 authority 指回自己；
9. `python_reexport_only` adapter 必须通过 AST 校验；
10. re-export 只允许来自声明的 canonical import prefix；
11. adapter 中出现函数、类或其他 owned implementation node 时直接失败。

这使 `world_cognition/` 从“靠注释说明它只是兼容层”升级为机器强制约束。

## 6. 回归测试

保留原：

`tests/test_source_authority_p17_m1.py`

全部 13 组测试不删、不覆盖。

新增独立：

`tests/test_source_authority_p17_m1_03.py`

新增 5 组针对 M1-03 的测试：

1. V3 boundary 必须处于 closed-world 模式；
2. 从 implementation 清单移除真实文件时必须产生 unclassified failure；
3. V3 Novel 必须是 generated mirror，不能重新出现独立 `source-map.md`；
4. `world_cognition` 必须保持 re-export-only；
5. compatibility adapter 一旦出现 FunctionDef 等业务实现节点必须被拒绝。

Architecture Gate 新增独立步骤：

`Run P17 M1-03 V3 boundary regression`

因此旧 M1 测试和新 M1-03 测试分别执行，防止新规则覆盖旧回归面。

## 7. GitHub Actions 验证

### Architecture Gate Run #6

- Run ID：`31702618295`
- Head：`4642de5f9f3083f87c883a0a32de15e54f907612`
- 结果：`success`

### Ubuntu latest

全部通过：

- Validate source-authority topology：PASS
- Verify generated-source mirrors：PASS
- 原 P17 M1 regression：PASS
- P17 M1-03 V3 boundary regression：PASS

### Windows latest

全部通过：

- Validate source-authority topology：PASS
- Verify generated-source mirrors：PASS
- 原 P17 M1 regression：PASS
- P17 M1-03 V3 boundary regression：PASS

说明 closed-world 分类、AST re-export 守门、Novel generated mirror 在 Linux / Windows 两端表现一致。

## 8. 运行影响评估

本轮没有修改：

- `zongdiaodu.py` 内容
- `peizhi.py` 内容
- `hotfix_20260727.py` 内容
- Gateway Runtime
- Life Runtime
- Memory SSoT
- World Understanding canonical implementation
- Tool execution
- A5 gate
- 数据库 schema
- 前端协议

Novel Skill 的 V3 bundled copy 仍保留在原运行位置，只是从未受管副本变成由 canonical source 生成的 mirror，因此运行路径不变。

`world_cognition` 文件内容没有修改，只是其兼容身份被机器锁定。

## 9. M1 收口结果

P17-M1 现在完成 3/3：

1. `P17-M1-01 Source Authority Guard`：建立 ownership 拓扑和永久 Architecture Gate；
2. `P17-M1-02 Physical Source Authority Convergence`：将 readable 人工权威迁入 `src/`；
3. `P17-M1-03 V3 Source Authority Boundary Closeout`：对 V3 主树建立 closed-world ownership 边界。

至此，M1 不再继续扩展。

后续进入 M2 时，God Module 拆分必须在这套已锁定的源码权威体系内完成，禁止新增第二 Runtime、第二入口、外挂实现或新的未分类源码根。

## 10. 下一步

下一阶段：`P17-M2 God Module 拆分`。

建议顺序：

1. 先拆 `zongdiaodu.py` 的 Composition / Bootstrap 生命周期副作用；
2. 再拆 Turn orchestration / tool loop / proactive expression 等职责；
3. 再处理 `src/life_service/embedded_runtime.py` 的 identity / policy / scheduler / proactive wiring；
4. 全程保持现有 Runtime authority、现有启动入口和现有 Gateway 主链不变。

M2 的重点不再是移动源码目录，而是降低单文件职责耦合，并通过 Contract / Port / Event 保持单一生产链。
