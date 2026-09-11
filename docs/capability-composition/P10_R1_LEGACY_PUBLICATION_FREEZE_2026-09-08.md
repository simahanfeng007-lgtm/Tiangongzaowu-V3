# P10 R1：冻结旧式新完整 Skill／Tool 能力发布

状态：**R1 代码及本地定向验收已完成；跨平台结果按最终提交另行记录。P10 尚未阶段验收或合并。**
本记录不代表生产部署、真实模型评估、Source 审批或 P8/P9 验收债已消除。

## 基线与交付身份

- 仓库：`simahanfeng007-lgtm/Tiangongzaowu-V3`。
- 分支：`codex/capability-composition-p10-life-learning-cutover-v1`。
- R0 基线：`1510f174745d6446729e4ab488a1a0276b3ed8c1`。
- main：`b1ea3e9d511aae9cabcc816e071ad3da645ae753`，本轮未修改。
- 本地验证的 33 文件补丁 SHA-256：`3a7242182c58842a961f1d8c8d045986aa8afb9756219aa2b6a0f17077640ba5`。
- 仅应用该补丁的完整源码树：`eeff80b6248b48b82fffc7af892013cde017cdf2`。
- 临时传输提交：`dc237ffe109633543ed797a7b8dc810fa3df06c4`。
- 实际源码提交：`ed0b48dd230cfd8268ac78f652a7b7fce1238717`。

最终收尾仅删除临时传输工作流，加入本记录、阶段导航和只读定向 CI；不再修改
上述已测源码／测试。原有 Architecture、P14、P19 及 R0 工作流均保持不变。

## 冻结的是旧发布方式，不是学习、知识或历史任务

原有 `learning_workflow` 现在统一判断旧式完整能力发布。Skill、Tool、未知类型、
可执行结构元数据和非规范发布产物不能因改名 Knowledge 或 `kb` 而获得注册结论。
普通知识正文包含 “Skill／Tool／SKILL.md” 等文字不会被误分类。

冻结返回 `migration_required` 和 `life.learning.legacy_publication_frozen`，
`ok/registered/retryable/may_publish/may_authorize/may_execute` 均为 false。
这表示保留材料、等待新路径迁移，不是发布成功，也不是一个额外任务状态机。

| 既有入口／写入点 | 本轮行为 |
|---|---|
| 草稿确认、直接请求、`publish_draft`、Life 编排发布 | 在编译完整发布包、注册、工作区映射之前冻结；不能以确认或 user_direct 绕过 |
| 学习预览／研究材料 | 保留来源、预览、原失败和证据；不再为 Skill／Tool 编译持久发布包，Knowledge 继续原路径 |
| `artifact_executor` 发布及 bundle 持久化 | 在创建目录／文件前拒绝旧式完整能力；纯编译及普通文档生成不作为新授权 |
| CURRENT 指针、回滚、重新激活 | 拒绝新建、换版、恢复 active；仅允许已有同一能力的严格身份匹配停用／降级 |
| approved 维护队列、失败重试 | 先持久记录冻结处置，再停止旧发布重试；不把永久冻结记成暂时预算不足，不消耗能力修复轮次 |
| 补丁提议、补丁结算、健康恢复 | 拦截独立于普通学习发布的旧入口；不再把补丁成功自动变成可激活能力 |
| Gateway 发布回调、Life overlay、workspace 重建 | 拒绝旧完整能力注册和重新投影；只接受真正的规范 Knowledge 产物进入原导入路径 |
| 可选旧 V3 学习引擎回调及肌肉层延迟学习入口 | 在导入／调用旧引擎之前返回冻结，不启动第二条发布通路 |
| 旧 Registry 原始写入、注册、新激活 | 对照实际磁盘记录，而非调用方自报 root；禁止新增、重写或重新激活，保留严格的停用／删除 |
| LifeShadowStore 旧 G5 完整 Skill CURRENT 写入 | 直接写入和候选提升也拒绝；单独冻结上层调用不足以覆盖此入口 |

Knowledge 的既有确认、风险处理和导入不变。既有 active 能力的只读重复激活、
引用读取、经原 Gateway 授权的调用及结果记账继续可用。原始学习卡、来源、
证据、失败、历史能力文件和未结算记录不被删除；已发布／discarded 历史卡
不会被本轮改写为“刚刚冻结”。普通 Memory/Knowledge 存储没有新建替代实现。

## 原 journal 内的可重入处置

冻结将新增 `learning.publication_frozen` 事件写入原 journal，沿原 reducer/registry
恢复投影。事件在本轮登记到原重放器；事件名虽为新增，存储仍是同一个 journal。
幂等键绑定原学习卡，冻结内容不加入每次变化的时间戳。日志／投影写入失败时
回滚内存状态；原卡可以再次处置，不丢原失败证据。重启重放、重复确认、维护
重试和 discard 都有定向用例。

原数据库 schema 没有升级。但旧二进制不认识新增 journal 事件，故启用新版本
前应备份 Life/World 数据目录。需要降级时使用兼容重放器或经过审查的备份恢复，
不能直接修改事件或版本字符串。本轮只操作临时测试库，没有迁移用户生产数据。

## Verification Plane 1.8

在原版本权威中声明 1.8，并在 `AUTHORITY_MAP.txt` 记录发布权限的收紧。
冻结覆盖继承原 85 条文件，增加 7 条直接／兼容发布源码覆盖，总计 92 条；
这不是新增 7 个独立运行时权威。原 Source Authority 仍为 17 个独立归属。

通过原生成流程同时刷新 `VERIFICATION_PLANE_FREEZE.json` 和独立诊断
`VERIFICATION_PLANE_FINGERPRINT.json`。正常测试关闭 UPDATE_FREEZE、
UPDATE_FINGERPRINT、UPDATE_GOLDEN。13 份 Golden baseline 字节、verifier、
CompletionGate、Effect/Fact、RepairPolicy、ActionPermission 和 SQLite schema33
均未变；没有为了冻结而放松 P8/P9 审核、任务绑定或现有权限检查。

Life runtime314 的 5 个跟随源码文件及生成标记由原官方生成器同步并提交，
不是另写一份运行时实现。`source-ownership.json` 未修改。

## 实际测试、原失败与测试合同迁移

| 观察 | 结果与边界 |
|---|---|
| 同一组使用旧 public API 的新增回归，在原 R0 基线 | 6 项失败：旧行为允许完整能力发布，未触发本次要求的拒绝 |
| 同一组回归在修复后 | 6 项通过 |
| 新冻结／重放／原 API 回归总计 | 63 项，全部包含在最终扩大回归内，没有新增跳过 |
| 未修改的 Knowledge、Memory、学习禁止自确认、poisoning、journal 等回归 | 74 项通过；不是额外独立任务数量 |
| 最终扩大定向回归，62 个相关模块加完整 Golden 目录 | 821 passed，10 skipped，0 failed/errors，11 subtests passed |
| 原有完整 P19 Golden 目录单独执行 | 55 passed，已包含在扩大组中，不相加 |
| 最终运行前后源码／测试输入 | 1,085 个 Python 文件哈希未变化；再次回读仍一致 |
| 源码归属与官方生成镜像检查 | 17 authorities／1 alias／24 generated targets／1 closed-world：PASS；mirrors --check：PASS |

最终本地环境为 Linux／Python 3.13.5，pytest 9.0.2，耗时 229.12 秒；5 条
原有 Pydantic 字段警告仍在。10 项跳过属于既有环境限制，不能称为这些分支
已验证；没有增加生产测试跳过或修改环境策略。远端 Python3.12/Windows
结果只能根据新提交实际 CI 判断，不能由本地结果推断。

本次要求有意改变旧完整能力的发布合同。9 个旧测试文件中原先断言“新 Skill
发布／激活成功”的部分改为断言拒绝和无写入；需要旧能力以测试历史执行的
部分改用显式 `tests/legacy_learning_fixtures.py`。该工具仅构造测试用的历史
记录，不被产品导入。保留旧授权调用、结果记账、停用和删除的实际测试，不拿
模拟当前发布成功替代历史数据。原测试产生的 27 项失败记录保留，并未隐瞒。

开发中还纠正了新测试夹具缺少字段／使用错误临时路径的问题，以及一个原
scheduler 用例误把永久冻结当成预算消耗失败的旧预期。它应在预算计数前结束，
而不是进入重试。一次早期 Golden 执行被外部超时中断，保留为 partial，之后
完整重跑通过；不会把中断日志当作通过。冻结事件重放表和 Knowledge 别名
回调分支在最终回归前均补齐了测试和实现。

## 源码与提交证据

本地由校验过的 native Git bundle 和已提交补丁恢复完整工作树，并重新构建
出与 R0 完全相同的树和提交身份。起始仓库标为 shallow，不冒充完整历史克隆。
本轮补丁在另一个干净的 R0 worktree 执行 `git apply --check --index` 和实际
应用，所有 33 份结果 blob 及完整树均一致。

直连 GitHub DNS 不可用时，临时开发传输只在 P10 分支读取 hash-pinned blob，
校验压缩／补丁摘要、全部前后 blob 和最终产品树，再正常非强推提交。
运行 `34169418431` 成功；它不运行产品测试或 Source 发布。下载原始产物
`10035209768` 后再核对 native bundle、父提交和每份已测源码；外层 ZIP
SHA-256 为 `324fc9f42e2cafe95a6f7a1b803636cf0fec564259248ecd082fa7347d94dc38`。
临时工作流在收尾删除，不保留可持续写入旁路。

新增只读 `P10 learning freeze focused gate` 在 Windows/Ubuntu 运行同一扩大
定向范围、源码归属和镜像检查，保存 JUnit／日志／精确 HEAD 与输入摘要。
它不是完整仓库、Node、真实模型任务或生产使用遥测，也不替代 R4 最终门禁。
最终提交和两端结果以 GitHub 回读、原始产物及交付执行记录为准。

## 下一步及未覆盖项

R1 是冻结，不是宣布所有历史发布代码已删除。R2 继续接通 Knowledge、Source
Evolution、Composition Experience 三类产出，分别沿既有 Knowledge/Memory、
P8/P9 审核发布及真实任务证据归因路径。新候选不能自行证明自己可执行。

R3 仍需完整旧记录迁移、重启恢复与真实入口使用遥测。已识别路径的源码／集成
用例不等于所有反射、外部插件、部署安装器和用户工作负载均已证明零使用。
在途任务的 Source pin 仍沿 P9；不删除不明归属引用或猜测任务结束。

P10 尚未合并，未开始 P11 Shadow、P12 Planner 退役或 P13 兼容权威移除。
P8 未应用的打包补丁、风险审批、真实 Tool Source 发布／X-X+1／任务矩阵，
P9 的实际 worker/model 连续运行、生产签审及运维回退要求仍独立保留。
工程检查点进度仍为 10/18＝55.6%，不是产品验收率。
