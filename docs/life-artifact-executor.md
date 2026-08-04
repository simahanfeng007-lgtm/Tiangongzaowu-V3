# 生命学习产物执行器

本文件是学习产物执行器的跨上下文实施契约。实现、测试和后续讨论以此为准；它不替代用户对学习等级和发布权的决定。

## 不可违反的边界

1. `src/life_service` 是生命运行时的唯一源码；修改后必须运行 `python scripts/sync-generated-sources.py --write` 和 `--check`。
2. 不修改发布时固定的 Skill Catalog、`deliverable_skills`、Capability Manifest 或 Action Registry 来安装运行中学习产物。它们是版本、哈希和执行授权的只读根。
3. 学习 Skill/Tool 通过生命动态叠加层发布。叠加层仅引用现有 Action Registry 中可用的 action，不能降低 action 的风险、效果或参数约束。
4. A0--A2 知识完成编译和验证后自动发布；Skill/Tool 始终按 A3+ 草案、验证证据、用户确认、原子发布的顺序处理。用户直接学习使用同一执行器但跳过学习卡确认。
5. 动态 `tool` 第一阶段是复合工具：由既有 action 的受验证 DAG 组成。不得把模型生成的任意 Python/Node 文件热加载到生命主进程。
6. 每次构建都产生不可变 artifact/version/hash/evidence；更新创建版本而不是覆盖，回滚只移动当前版本指针。
7. 模型可以提出内容或审阅，但不能成为唯一的验证器；发布必须有机器可重放的 schema/绑定/dry-run 证据。

## 目标形态

```text
学习决定 -> ArtifactSpec 编译 -> 构建/验证证据 -> 发布适配器
                                                |- KnowledgePublisher -> 现有 knowledge_store
                                                |- LifeSkillOverlay -> 模型/前端可见 Skill
                                                `- CompositeToolRegistry -> 预置 Gateway 入口
```

## 产物布局

每个生命拥有一份受状态文件和日志引用的不可变产物版本：

```text
artifact.json      身份、版本、哈希、状态和依赖
SKILL.md           面向模型和用户的说明
skill-spec.json    输入输出、action 绑定、步骤和验收条件
evidence.json      编译、绑定验证、试运行、发布和回滚证据
```

`ArtifactSpec` 的 Skill/Composite Tool 必须使用 `required_actions` 及 `steps[].action_id` 绑定既有 action；发布时快照 `action_catalog_sha256`。当 action 不存在、不可用或其声明的风险高于草案绑定时，验证失败且不发布。

### 当前 Action 适配事实

当前发布态模型工具面只有 `omni_body`。`web.search`、文件读写等是它的内部 `action`，而不是可直接绑定的顶层 Action。因此学习产物的步骤必须以 `action_id: "omni_body"` 绑定现有 Action；其 `arguments_template` 使用已经存在的 `{action, target, args}` 形状。执行器只验证并调用 `omni_body`，不会为内部动作另建未经发布的 Action。

## 迭代顺序

1. `artifact_executor.py`：严格产物编译、action 绑定验证、不可变版本和回滚指针。
2. `EmbeddedLifeRuntime`：草案先编译，发布时调用发布适配器，日志和投影持久化结果。
3. `GatewayRuntime`：注入现有 Tool/知识适配器；知识写入现有 `knowledge_store`。
4. `LifeSkillOverlay`：为生命面板、模型上下文和前端 Skill 页输出动态已发布 Skill。
5. `life.composite.invoke`：作为固定发布 action 执行动态复合工具的既有 action DAG。

## 关键测试

- 不能引用未知、不可用或风险被降低的 action。
- 草案、验证失败、已发布、回滚四种状态均可重放。
- A0 知识会进入既有知识库并返回其 document id。
- A3 Skill/Tool 在用户确认前不出现在动态 overlay。
- 已发布 Skill 使用固定 action 调用；回滚后不再选择新版本。
- 固定发布的 Skill Catalog 和 Capability Manifest 哈希完全不变。

## 实施检查点（2026-07-22）

已完成：

1. `artifact_executor.py` 会编译、校验、持久化 `artifact.json` / `SKILL.md` / `skill-spec.json` / `evidence.json`，并把发布记录单独写入 `publication.json`；构建哈希不会因发布状态而变化。
2. 学习产物采用稳定 lineage + 当前版本指针；更新保留旧版本，回滚和用户删除只移动/禁用指针，不删除审计材料。
3. Knowledge 发布适配器写入既有 `knowledge_store`；Skill/Tool 进入生命 overlay，不写发布态 Catalog、Manifest 或 Registry。
4. overlay 同时提供前端完整投影与精简模型上下文；普通聊天读取它失败时降级，不影响聊天本身。
5. 固定的 `/api/v1/v3/life/capability/invoke` 会重放已发布的步骤模板，并只经过 Gateway 的内部 `omni_body` 入口。它不会导入模型生成的代码。
6. 旧 `xuexi_lian` 的有效学习过程已迁为新的 `learning_executor.py`：收集用户/记忆材料、按需只读研究、证据筛选和提示注入剔除、模型提炼预览。它不导入旧模块，不写旧能力注册表，也不生成旧候选工具；结果仍由新 artifact executor 编译、版本化和发布。

后续仍需明确完成的工作：

1. 为真实发布环境补一条不含外部副作用的 `omni_body` 合约探针，验证内部 Action 目录和学习研究快照的长期一致性。
2. 把学习完成报告从待投递的 proactive 消息接入正常聊天历史，确保用户一定看见学习结果。
3. 视前端体验决定是否在生命的“能力”子页加入 overlay 的版本历史、禁用、回滚和执行记录视图；API 映射已经存在。
