# P10 R0：学习产出切换——基线与发布入口盘点

状态：**P10 已启动；R0 为静态审计及执行准备，不是运行时切换或阶段验收。**

## 基线与授权范围

- 仓库：`simahanfeng007-lgtm/Tiangongzaowu-V3`。
- 分支：`codex/capability-composition-p10-life-learning-cutover-v1`。
- 基线：`b1ea3e9d511aae9cabcc816e071ad3da645ae753`，P9 PR #74 正常合并后的 main。
- P9 最终候选：`28dd2bdacf86d093ba2c7e04e23b5d967aa72ec8`。
- 母版计划 SHA-256：`691e857e3a7f75d9107606bbffe2919bd484c03e25f1255cebd1bb069f89f895`。

用户明确要求先合并 P9，再进入 P10。P9 按工程检查点合并，实际模型任务、
生产签审与部署等验收债继续保留；不能把合并记录当作这些证据已存在。
P10 分支必须来自确认过父提交和源码树的新 main，不能从未合并旁支开工。

P10 的目标是将学习产出分成 Knowledge、Source Evolution 和 Composition
Experience 三条有证据约束的路径，停止旧的完整 Skill/Tool 学习产物直接
成为可激活能力的发布方式。不是删除学习能力、停止 Knowledge 导入，或新建
第二个 Runtime、Gateway、WorldState、Registry、Memory 或任务状态机。

## R0 实际交付及边界

新增 `scripts/audit-learning-publication.py`：以固定的待审查源范围进行 AST
静态读取，不导入、不执行产品模块，不修改注册表、产物、权限或当前指针。
脚本要求完整 Git SHA，并校验每份所读源码与该提交的 Git blob 完全一致。
输出文件 SHA-256、Git blob SHA、函数限定名、精确行段摘要、语法调用表达式
和 API 路由字符串。重复、缺失或有歧义的函数定位，以及路径/源码身份漂移
均报错，而不是用相近名称或最新文件替代。

本次清单包括 **17 份源文件、64 个重点函数、31 个去重路由字符串**。
这些数字不是穷尽的运行时调用图、31 个已上线接口或实际生产使用次数。
语法调用中包含嵌套表达式；动态分派和运行条件需要后续遥测及集成验证。
脚本的 `production_usage_verified`、`publication_freeze_applied`、
`may_authorize`、`may_execute` 均明确为 false。

复现命令（在对应基线的工作区执行，结果写到既有 output 目录）：

```text
python scripts/audit-learning-publication.py --repository . --baseline b1ea3e9d511aae9cabcc816e071ad3da645ae753
```

机器清单保存于本次交付证据包，文件名 `P10_PUBLICATION_INVENTORY_20260907.json`。
SHA-256：`080d1fda514a324359bc2addea7142f4f32f0840ce9bb2a99f0b1ccf1d50d116`。工具输出不包含运行时批准或新的发布权威。

新增 `P10 inventory focused gate` 只运行本审计工具的定向用例及当前提交的
静态盘点，覆盖 Windows/Ubuntu。它不导入产品模块、不取生产密钥、不推送仓库或改动产品源码，
也不替代阶段最终验收；现有 Architecture/P14/P19 工作流保持不变。

## 已核对的切换面

| 当前源码及重点位置 | 已看到的行为 | P10 处理要求 |
|---|---|---|
| `src/life_service/learning_workflow.py`：`build_draft` / `confirm_draft` / `publish_draft` | 草稿、确认及旧 Knowledge/Skill/Tool 发布语义 | 保留 Knowledge；Skill/Tool 先成为 Source 演化候选，不从确认直接获得能力注册结论 |
| `src/life_service/learning_executor.py`：`execute_learning_preview` | 获取材料、生成带依据的学习预览 | 保留材料来源、失败和质量信息；不把预览称为验证过的经验或已部署源码 |
| `src/life_service/artifact_executor.py`：编译、发布、落盘和 current pointer | 可产生 `SKILL.md`、skill-spec、证据及发布记录 | 新学习产物的旧式完整能力发布必须冻结；一般文档生成和 Knowledge 不受这一限制 |
| `src/life_service/embedded_runtime.py`：`_learning_confirm` / `_learning_publish` | 确认后发布，再落盘、映射和记录 capability/pointer | 从唯一编排入口切换三类产出；不能只改返回文案而保留旧副作用 |
| 同文件：`_recover_approved_learning_cards` | 维护时重试以前批准但未发布的学习卡 | 冻结必须覆盖旧队列及重启恢复，否则前台冻结后后台仍能重新发布 |
| 同文件：`_capability_patch_propose` / `_capability_patch_settle` | 补丁流程直接调用编译/持久化/发布，不全经过 `_learning_publish` | 独立覆盖该路径；只冻结 `_learning_publish` 不足以关闭旧发布入口 |
| 同文件：激活、重新激活、回滚、pointer 与 workspace 同步 | 可改变选中的版本或重新投影产物 | 禁止新旧格式混用绕过；保留已授权运行任务的固定历史引用，不提前删除旧文件 |
| `embedded_runtime_wiring.py` 与 `src/total_gateway/runtime.py` 的嵌套学习回调 | Knowledge 导入；非 Knowledge 的旧 Life overlay 和工作区映射；调用仍经 Gateway 授权 | 复用这些现有接线，不增加模型直调注册或同请求源码热导入；保留 Policy/Ticket/Grant |
| `src/total_gateway/desktop_api.py` 与 V3 `duihua_qiaojie.py` | 多组学习、能力及兼容接口，可进入相同底层操作 | 同时测试直接请求、确认、process-approved、activate、release 等别名；路由文字不等于生产可达性 |
| V3 `zongdiaodu.py`：`_ensure_xuexi_lian` | 已是返回 None 的兼容空入口 | 记录现状，不把它当成本次刚冻结的生产能力，也不据此断言其他路径全部停用 |
| V3 `jineng/jirou_ceng.py`：`_xuexi_liucheng` | 仍有延迟导入旧 XuexiLian 及发布参数的代码 | 记录为待查动态可达性/脱链兼容面；不能根据代码出现就声称生产已在调用 |
| V3 `zhili/nengli_zhuche.py` 与 `l0_ability_projection.py` | 旧能力注册、激活及投影面 | 对照实际调用和遥测处理；未取得零使用证据前，不擅自删除注册/兼容权威 |
| `src/life_service/store.py`、`capability_learning.py`、`life_learning_memory.py` | 现有候选、pointer、证据及能力学习存储 | 继续使用既有 Store/Memory，不能为三类产出另起数据库或任务状态权威 |
| `capability_experience_api.py` / `capability_experience_memory.py` | 已有经验意图及通过 MemoryCoordinator 提交的路径 | Composition Experience 必须沿此证据归因路径；LLM 总结不能伪装成成功任务经验 |

完整具体函数、限定名和行段以绑定基线的机器清单为准。部署安装器、动态插件、
历史产物与兼容端的实际入口还需运行时验证；本静态范围不声称覆盖所有可能
通过反射、插件或外部配置触达的代码。

## 后续工程执行顺序与退出条件

### R1：先冻结旧的“新完整能力发布”，保留知识与既有任务

一次性覆盖确认发布、直接请求、补丁发布、维护重试、兼容接口和 workspace
重建，不做只改一个函数的表面冻结。返回明确可识别的待迁移状态，原学习卡
和证据保留；后台不得无限重试被明确冻结的路径。新请求不能得到旧式
registered/active 结论，但 Knowledge 导入和已授权旧任务的固定引用仍可用。

退出条件：正向 Knowledge/历史读取不退化；所有已确认旧发布与激活重入面
被集成测试覆盖；产物、注册、pointer、投影均无被禁止的新写入。失败和
中断不丢原始学习证据。对尚未查清的动态入口继续标记待验证。

### R2：接通三类产出，不把学习结果变成授权

Knowledge 使用原有知识/记忆路径；Source Evolution 只产出绑定源版本的
提案，进入现有 P8/P9 审核、构建和发布边界；Composition Experience 使用
实际任务的归因、验证、Effect/Fact/P19/Completion 证据，再进入既有
MemoryCoordinator。任何模型输出、经验匹配或世界上下文都不签发权限。

退出条件：三类产出有明确结构、证据和生命周期；错误类型/缺证据/过期源
不会被重新解释为可执行能力。当前请求不能生成源码后直接导入运行。

### R3：旧记录迁移、恢复及真实使用遥测

覆盖草稿、已批准未发布、失败待重试、pending patch、已激活历史记录和
重启恢复。迁移可核对、可重入且保持唯一存储；保持在途任务源码锁定。
记录真实入口、观察区间、工作负载覆盖和调用计数，不拿 AST 计数代替
实际零使用证据。对归属不明或中断残留保守保留并报告，不猜测结束。

### R4：最终候选验证、正常 PR 与主干核对

先定向合同和故障回归，再在固定候选上运行阶段全量 Python/Node、
Windows/Ubuntu、P14/P19 等实际适用门禁。变更 P19 权威面时明确版本和
映射，使用原生成器更新冻结清单及诊断指纹；正常验证时更新环境变量
必须关闭，并运行完整 Golden 目录，避免重复 P9 漏同步指纹的问题。

P11 的正式 Shadow、P12 的 Static Skill Planner/上下文/完整 Skill 退役、
P13 的旧注册/发布兼容权威移除不在 R0 提前执行。冻结在前、移除在后；
迁移成功和零使用必须由后续证据证明。

## 本次验证

- 审计工具定向用例：`27` 项通过；不是产品任务验收。
- 在上述新 main 基线上两次执行审计，输出字节一致；读到的 17 份产品源与
  原生 Git 对象一致。运行前后源码无变更。
- 本次提交只包含开发审计脚本、测试、本文、当前阶段导航和一个新增的只读定向 CI；
  原有最终验收工作流与保护规则不变，没有修改产品学习发布行为、P19 权威
  文件、生成镜像、权限、数据库或用户任务。
- P10 整个学习切换尚未实现；真实生产遥测、冻结效果、模型任务与最终
  跨平台门禁均不由本工具测试替代。

## 持续保留的验收债

P9 的真实配置 worker/model 连续执行、重启/replan、正式签审、生产密钥、
归档/崩溃残留处理和 World index v2 的备份回退要求仍保留。P8 打包补丁
仍未应用；未批准的风险变化、真实 Tool Source 发布、运行中 X/X+1 和
任务矩阵也未被批准或补证。母版其余未验证任务不因阶段检查点合并而消失。

工程检查点进度：P9 合并后为 10/18（55.6%）；P10 为第 11/18 阶段。
该口径不是验收完成率，也不是本轮 R0 的工时比例。
