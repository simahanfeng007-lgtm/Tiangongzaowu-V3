# P10 R2-B：机器经验准入、Memory 写回及原任务恢复尾部

状态：R2-B 范围内的经验提交闭环实现；定向验收以本记录后附的实际结果和精确提交为准。
不进入 R3/R4，不合并 main，不部署生产，不批准 P8/P9 Source 或消除历史验收债。

## 基线

仓库：simahanfeng007-lgtm/Tiangongzaowu-V3。
工作分支：codex/capability-composition-p10-life-learning-cutover-v1。
远端父提交：cb9cc8387367f96bc9eb76890b7e9025fdeea95f。
父树：81e9aa7b31a8eeb8a7cd67b8b3891c156ef9b30c。
main 工程基线：b1ea3e9d511aae9cabcc816e071ad3da645ae753。

上次工作只到机器审计，本次不删除审计的 may_write_memory=false。
该审计仍是零授权 DATA，必须经过新接入的 P5/Memory 边界才能产生独立提交回执。

## 同一条生产链

原 Life terminal commit / 已有 worker strict recovery tail
→ 从签名 Life journal 读取已提交执行与机器审计事件
→ 重新读取 Gateway 登记计划、Effect/Fact/Object、sealed Completion、权威 P19
→ 从同一个 WorldStateStore 复核当前源、描述符、依赖及 registry/Manifest
→ 原 P5 Attribution / Admission / Aggregate / Memory intent
→ 原 MemoryCoordinator 的 L1 观察 + L3 CAPABILITY_KNOWLEDGE DATA
→ 原 Memory SQLite 事务内比较前态、检查父记录并提交
→ 同一 Life journal 的 learning.experience_committed 幂等回执。

新模块 learning_experience_writeback 是 Gateway 内部适配器，不是 Runtime、
Memory writer、Registry、观察者公钥来源或模型接口。产品入口只接收既有执行身份；
没有接收模型自报 observation、expected hash、Memory parent 列表或 aggregate。
Knowledge、Source 准备和 R1 旧完整 Skill/Tool 发布冻结保持不变。

## 来源与正经验范围

从原登记计划还原 request/run/generation、主体、工作区、原 WorldState 与源码引用。
复用原 finalizer 重新校验 Fact/Object 字节，不再次执行 Tool。正经验要求原系统
sealed Completion 和对应完整、非模型生成 P19 PASS 记录；原“已完成”字符串不够。

该生产采集器的正经验范围是当前 P7 已准入的闭合 A0 read/verify 链：已登记
执行及授权 lineage 必须与实际账本完全一致，所有效果成功，不允许 shell/python
或未观察的写作用。其他风险/不完整/多余链路/未知来源明确延后，不能填虚构 PASS。
trace 的 unknown/human/alternate 字段描述这个受约束机器账本，不是对恶意宿主机
或所有外部人工行为无所不知的声明。quality_milli 是必需机器谓词的覆盖率，
不是语义质量、用户满意度或大模型能力评分。

来源重新验证使用独立的当前 canonical WorldState，而非把原计划的同一组 hash
再次当作 expected 值。核对当前 Tool/Method 实体、描述符、版本、可用性、精确
source/registry 依赖及 stale；新的 World cut 只要这些来源不变仍可准入。
原生 Method 额外重开已有 P9 签名归档。旧任务始终保持其原运行绑定，不能换新版。
这是当前权威世界/manifest 的一致性检查，不新增连续宿主文件扫描或绕过原 Source
Authority。实际文件变化必须经既有现实回流进入 World，不能声称本模块独自防住
同用户的所有并发文件篡改。

首次正观察仍是原 P5 的 PROBATION。成功/失败分池、质量阈值、独立性统计、置信
下界、衰减规则均未更改。原 P5 negative 观察经同一 Memory 提交器实际可写入失败池，
不会增加成功数；不能据此宣称每一种真实失败/历史/兼容 worker 都已自动采集。
缺少闭合证据的失败保留原审计并延后，不把未知状态计成正经验。

## Memory 唯一写入口、CAS 与 lineage

先通过原 MemoryCoordinator 保存 L1 机器观察。其根事件来自已核验签名 journal
事件摘要；观察内容仅包含系统 IR、引用、hash 和机器合同，不复制用户回复正文。
L1 可以在 L3 失败后保留为事实证据，不能当成成功次数。

L3 使用原 P5 claim/source family 和实际 active head，读取并解密当前 aggregate，
复核 hash、主体/隐私、原观察集合及父派生链。未知、失效、删除、过期或内容漂移的
父记录不替换为虚构父记录。重复观察必须能关联其原 L1 和根事件；重用 ID 换材料
不能计第二次成功。早于聚合前态的未计入观察不能把时间倒退。

在原 put_live_memory_assertion 的 BEGIN IMMEDIATE 内加入可选 exact-head guard。
先比较 claim/layer 的实际 head，再遍历检查父链仍然有效，之后才执行原 payload、
assertion、derivation、head、change_log、outbox 写入与 COMMIT。不增加任何数据库
表、schema 版本或第二个 writer；没有 guard 的旧调用保持原合同。

另一连接先提交时，最多三次重读/重算/重试，不能覆盖已获胜更新。超过重试上限
明确延后。父记录在准备与 COMMIT 之间失效会被事务内再次拦住。

一个 Memory SQLite 事务的回滚是原子边界。Life journal、Gateway、World 和 Memory
不是分布式事务：L1 已提交、L3 已提交但回执未写，均是显式可恢复阶段；不能假称
所有库一起回滚。Memory COMMIT 后异常返回 UNCONFIRMED / null，而不是声称零写入。
重新处理同一终态会读取既有头与观察，回补审计而不重复累计。

锁序沿 Life → Gateway → WorldStore → 原 Memory transaction，不在 Life 锁内等待
World Runtime 锁。Gateway 原 SQLite 写事务和 WorldStore 既有锁覆盖读证据到 CAS；
这不增加多进程 World writer 协议。Memory 两个 SQLite 连接的竞争已纳入测试。

## 原 worker 恢复路径的实际补线

检查发现：原 worker strict tail 会先从 Life recover 已完成记录，并立即返回。
仅包装新 commit 回调不能处理“执行已经存在、经验尚未写入”的重启窗口。

本轮在原 worker 构造/from_runtime_config 增加可选 learning recovery callback，
由同一个 Gateway.start 注入。它在原 recover 分支中调用，不创建后台扫描器、
定时任务、第二 worker 或新 HTTP/Tool 路由。绑定器重读 Life 已提交记录，再执行
同一审计/准入/写回代码，不重提 Life execution、不重新执行 Tool。

恢复结果还需通过原 terminal 的精确 payload/hash 校验；回调不能替换完成身份。
学习失败保留原任务完成，并明确返回延后及未知写入状态。无新回调的原调用保持
原有返回，原 P7 终态 cutpoint 测试继续执行。这里验证的是已有 worker 恢复尾部，
不是声称对所有历史漏采记录已经自动扫描与迁移。

## 权限版本与回退

Verification Plane 1.10 正式声明。95 条原冻结覆盖全保留，新增7条成为102条，
包含新 Gateway 适配器、原 Memory coordinator/repository 和4个未改的 P5模块。
原13份 Golden baseline、P5/P15阈值、CompletionGate、verifier、权限及数据库
schema 保持不变。冻结和独立 fingerprint 通过原生成程序一起更新；正常测试
清除 UPDATE_FREEZE/UPDATE_FINGERPRINT/UPDATE_GOLDEN。

Life mirror 只通过原官方生成器产生。新审计事件登记在原 journal replay 表，
属于 AUDIT_ONLY，不产生第二套投影权威。旧二进制需要兼容重放器或经审查的备份
恢复；启用前备份 Life/World 数据。本轮仅操作临时测试库，无用户生产数据迁移。

## 实际证据范围

本地从上次已下载 native Git bundle 及最终文件恢复出与 cb9cc838 完全相同的
81e9aa7b 源码树。本地 metadata 是显式 reconstructed baseline，不冒称远端提交
对象；后续远端提交基于真实父提交构建。原 bundle fsck、外层 hash 和原文件已核验。

测试使用真实临时 SQLite、签名 journal、P6 World 和 P7登记/授权/Fact/Object/P19/
Completion/Memory。后端响应、旧 Method snapshot provider 和观察夹具不是模型
产品验收，未使用生产密钥。实际 Gateway.start 和 strict worker recover 调用点
都被测试，不只检查新模块能 import。独立 Python 子进程验证 Memory 重开后不重复。

保留初期失败：新增夹具的 invalidation reason、World cut、Registry读取接口、
CompletionGate参数、临时数据库路径，以及误用 EVENT_CLASSIFICATION 名称；
还修正了新产品代码中 capability_manifest 字段和 WorldValue读取字段。初轮的
post-COMMIT异常错误地声称零写入，故障用例暴露后改为明确 UNCONFIRMED。
无旧断言被削弱、无失败日志被覆盖为成功。中途修改过新测试的那次63通过运行只
作开发回归，不作为最终不可变输入证据；最终结果另行记录。

原冻结守卫先对未声明的源码变化返回失败，日志保留。所有验证统计须基于原
JUnit/完整日志和输入清单；平台/子集不能相加为独立任务，既有跳过不算已通过。

旧 World 已被既有保留策略回收、父记录已撤回、operator 未配置或没有完整机器
来源时，本模块明确延后而非换新版或伪造证据。现有恢复尾部的接线不等于提供
无限期限保留或对已退出队列的所有历史任务自动扫描；这些不能被称为已完成。
本轮只实现并验收上述符合准入前提的经验提交闭环，不以此进入 R3/R4。

## 最终本地定向结果

- 103 个选择项（含原完整 P19 Golden 目录），保留上一轮全部范围并增加原 Memory、
  P15、worker 恢复 cutpoint：1363 passed / 10 existing skips / 11 subtests passed。
- 39 个新增写回/故障/实际调用点案例全部通过，已包含在总数中，不能再相加。
- 0 failed/errors，5 条既有 Pydantic warnings。Linux/Python 3.13.5，pytest 392.41秒。
- 1091 个 Python 源码/测试输入在完整运行前后和再次核验时完全一致。
- 原 Source Authority：17 authorities / 1 alias / 24 generated targets / 1 closed-world PASS。
- 原 --check-committed 镜像检查 PASS；13份原 Golden baseline 字节不变。
- normal freeze 在正式声明/生成前失败，正式声明1.10后用原生成器刷新并正常通过；
  95→102覆盖无删减，UPDATE 标志未带入最终回归。

JUnit SHA-256：`09639fa1604a0bd89b2e43a9d4a305f56052b5dc87b4d04f3d17e5f441b49d1b`。
完整日志 SHA-256：`5a3f46351a5499c122f865cfd4c6c04f60216837ed9f15f8ff078e090790b80c`。
输入清单 SHA-256：`15a4be3b8891213dec7cbb73fd09f0d3fd6d4edb62f042b0782326e914537fb3`。

只读定向 CI 增加 Memory/P15/原 worker recovery 选择项，保留此前全部选择；
扩大的回归时限为45分钟，不变更任何原 Architecture/P14/P19 最终门禁。
远端结果以本轮最终提交的原始 CI 产物为准，不能沿用 cb9cc838 的绿灯。
