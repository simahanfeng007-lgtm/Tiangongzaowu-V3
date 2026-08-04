# 天工造物 v3 总网关改造计划

版本：1.0  
计划基线时间：2026-07-14  
产品行为基线：`D:\天工造物 v3.0 完整版`  
正式源码工作区：`C:\Users\77571\Documents\天工造物v3`  

## 0. 压缩恢复和执行纪律

1. 每次上下文压缩、任务恢复或重新进入工作区后，必须先完整读取本文件。
2. 每次只允许一个步骤标记为 `IN_PROGRESS`。
3. 开始步骤前先核对前置门禁；完成后记录证据、测试结果和实际修改文件。
4. 安装目录、安装包、临时解包目录、`.pyc` 和冻结 EXE 只作为行为或协议证据，不作为正式源码直接修改。
5. 不创建 `*.bak`、`*.old`、`*_fixed`、`*_patch` 或额外旁路源码树；回退依靠版本控制和可重复构建。
6. 构建、测试和解包临时文件只能进入明确的临时/构建目录，并在阶段结束时清理。
7. 未通过 P0 门禁前，不接入真实微信/飞书出站，不删除旧链路，不修改活动安装目录。
8. 所有“完成”必须有文件、哈希、QC、工具事实或渠道回执；模型文字不构成完成证据。
9. 自 2026-07-14 起采用大阶段节奏：同一 P 阶段内连续实现各小项，只运行能阻止错误扩散的语法、定向和边界快速检查；不再为每个小项重复执行全量回归、双构建和污染扫描。
10. 每个 P 阶段全部实现后统一执行阶段质检，至少包含全量测试、契约兼容、关键并发/崩溃注入、两次可重复构建、工件哈希和源码污染检查；阶段质检通过后才允许进入下一 P 阶段。
11. 小项可在定向检查通过后标记 `DONE`，但整个阶段在阶段质检通过前不得对外宣称已验收或可发布。

## 1. 当前基线事实

- 桌面产品：`3.0.0`，`app.asar` 内 `api_contract_version=tiangong.desktop.backend.v3`。
- 后端 Release Manifest：`3.7.0`，`api_contract_id=tiangong.desktop.backend.v3`。
- 当前安装组件：7174 后端、7175 生命服务、7176 通信服务；7184 尚未成为正式总网关。
- 前端 `app.asar` 可还原为 607 个目录/文件项，其中 534 个为实体文件。
- 后端 `_internal` 共 2,429 个文件，其中 23 个 `.py`、2,154 个 `.pyc`；关键 v3 入口主要是冻结模块。
- 7175 项目模块只存在于冻结 EXE：`life_affect`、`life_api`、`life_capability`、`life_context`、`life_contracts`、`life_core`、`life_execution`、`life_memory`、`life_projection`、`life_scheduler`。
- 7176 项目模块只存在于冻结 EXE：`communication.backend_adapter`、`communication.gateway_links`、`communication.gateway_manager`、`communication.json_guards`、`communication.reply_sanitizer`。
- 旧 3.3.2 包只作为微信 iLink/飞书协议样本；旧的一体化 `GatewayLinkManager` 不直接移植。

### 当前组件哈希

| 组件 | SHA-256 |
|---|---|
| 桌面 EXE | `1A3CD0A36A4FAD51566EBA4232905F6F0B20FCD8DAAC7A690E87669FF9018B16` |
| app.asar | `50B614BF802A4632D5544939E5FD8CD1B619CC32FFBB0BE8D1CBDB011E1C7A9C` |
| 7174 | `205B4838752BFA0A2353B14A87FCD5EF9C5849E956BD1CF67137E557BAAFDB37` |
| 7175 | `575DF6AAC4200BBFA9695BF6062808CD00A79971ABA15B3596B04C05F03F5BB0` |
| 7176 | `613F569EE889B1F365B4678F02A2F2DC12507A52858A91D6B8A553880E2D11F6` |

## 2. 对抗审计结论

准入结论：架构方向有条件通过；允许开发契约、可靠性内核、模拟器和无副作用服务，不允许立即切换正式渠道流量。

必须关闭的致命风险：

1. 单线状态无法表达文本、执行、产物和多附件投递的部分成功。
2. 微信外部网络无法承诺严格恰好一次，只能实现至少一次传递与业务效果幂等。
3. 旧 7174/7176 业务入口未关闭会形成双权威。
4. ExecutionTicket/DeliveryTicket 尚未绑定完整上下文和持久防重放状态。
5. 现有安装成品不是完整可构建源码，必须建立唯一源码树和构建基线。

### 必须始终成立的不变量

1. `AUTH-01`：每个业务 run 有且只有一个 7184 `request_id`。
2. `AUTH-02`：7174 最终必须拒绝无有效 ExecutionTicket 的业务执行。
3. `AUTH-03`：7176 必须拒绝无有效 DeliveryTicket 的业务发送。
4. `AUTH-04`：7176 入站不得直接调用 7174，也不得请求 7175 做业务授权。
5. `AUTH-05`：模型、Skill、7174、7175、7176 均不能自行设置任务完成。
6. `STATE-01`：状态只能由定义过的事实事件和合法迁移改变。
7. `STATE-02`：取消或被替代 generation 的迟到结果不得产生出站副作用。
8. `IDEM-01`：同一入站作用域键最多创建一个逻辑请求。
9. `IDEM-02`：同一 `delivery_id` 本地最多存在一个活动发送记录。
10. `FACT-01`：文件任务没有成功工具事实时不能完成。
11. `ART-01`：产物必须通过格式、大小、哈希和任务特定 QC。
12. `ART-02`：验收对象和最终发送对象的哈希必须一致。
13. `DEL-01`：文本成功不能替代附件发送成功。
14. `DEL-02`：没有平台送达证据时只能称 `CHANNEL_ACCEPTED`，不能称 `DELIVERED`。
15. `SKILL-01`：Skill 选择不得扩大 ExecutionTicket 权限。
16. `SKILL-02`：Skill 引用的 action 必须存在于本次固定 CapabilityManifest。
17. `VER-01`：契约、服务、Skill 或策略版本不兼容时必须 fail closed。
18. `ISO-01`：不同渠道账号、租户、会话和用户的附件与运行状态严格隔离。
19. `FILE-01`：模型只接收对象引用，不接收任意宿主机路径。
20. `RECOVERY-01`：在每个外部副作用前后崩溃重启，系统不得虚假完成。

### 2.1 ExecutionTicket / DeliveryTicket 威胁模型

#### 保护目标和信任边界

1. 7184 是两类 Ticket 的唯一签发者；模型、Skill、Electron renderer、7174、7175、7176 均不得持有签名私钥。
2. 7174 只验证并消费 ExecutionTicket；7176 只验证并消费 DeliveryTicket，两个 audience 不可互换。
3. Ticket 只授权一个已固定的 effect，不授予“在某目录自由执行”或“向某会话自由发送”的持续权限。
4. 防御对象包括提示词注入、恶意内容、renderer XSS、重复/乱序请求、旧进程、旧 generation、磁盘账本重放、错误服务调用和偶发组件失陷。
5. 管理员/内核级攻击者或能在同一 Windows 用户下任意执行代码的恶意程序不属于单进程 Ticket 能完全隔离的范围；正式高安全部署需再使用独立服务账户、ACL/Job Object/AppContainer 或服务化隔离。

#### 签名和规范化

1. 使用 Ed25519 非对称签名；7184 仅持有私钥，7174/7176 仅持有对应 audience 的公钥。
2. ExecutionTicket 与 DeliveryTicket 使用独立 key pair 和独立 `kid`，防止一个验证端被攻破后横向伪造另一类票据。
3. 待签名正文使用 RFC 8785/JCS 规范化 JSON；UTF-8、禁止 NaN/Infinity、禁止重复键、所有时间为 UTC epoch milliseconds。
4. 对 arguments、payload、LifeSnapshot、CapabilityManifest、PolicySnapshot 和 ArtifactManifest 保存 SHA-256；验证端以接收到的规范化对象重新计算，禁止信任调用方提交的摘要。
5. Ticket 采用 `header.payload.signature` 的结构化封装，但不接受算法协商；`alg` 必须精确等于 `EdDSA`，禁止 `none` 和算法降级。

#### 两类 Ticket 的公共绑定字段

- `schema_version`、`ticket_type`、`ticket_id`、`kid`、`issuer`、`audience`。
- `issued_at`、`not_before`、`expires_at`；验票允许的时钟偏差不超过 5 秒，签发到首次消费最长 60 秒。
- `gateway_epoch`、`request_id`、`run_id`、`generation`、`effect_id`。
- `channel`、`tenant_id`、`link_account_id`、`conversation_scope_hash`、`principal_scope_hash`。
- `capability_manifest_hash`、`policy_snapshot_hash`、`component_manifest_hash`。
- `life_snapshot_revision`、`life_snapshot_hash`；不内嵌可被下游修改的 Soul 文本。
- `confirmation_id` 与 `risk_class`；非 A5 必须显式为空，A5 必须绑定一次性确认事实。

#### ExecutionTicket 的额外绑定

- 精确的 `action_id`、`action_version`、`arguments_hash` 和参数 Schema 摘要。
- `workspace_id`、允许读取的 `object_refs[]`、允许写入的 `output_root_id`，禁止宿主机任意路径。
- `artifact_intent_id`、最大输出字节数、最大运行时、最大工具调用次数和允许的副作用类别。
- 每个输入对象绑定 `object_id + revision + sha256 + size + mime`；执行端不得换对象或跟随路径型引用。
- Skill 只作为审计字段记录 `skill_id/version/hash`；不能扩大 Ticket 已签发的 action 和资源范围。

#### DeliveryTicket 的额外绑定

- 精确的 `delivery_id`、`channel`、`tenant/account`、收件会话、`reply_to_message_id` 和发送 generation。
- `payload_manifest_hash`、`text_hash`、分段规则、最大消息段数、是否允许文本/附件。
- 每个附件绑定 `artifact_id + revision + sha256 + size + mime + filename`；禁止本地路径，7176 只能从内容寻址对象库取件。
- 绑定渠道策略摘要、单附件/总大小上限、上传和发送阶段超时；接收者、文本或文件任一变化都必须签发新 Ticket。

#### 持久防重放和崩溃语义

1. 7174/7176 分别维护本地 SQLite Ticket/Effect Ledger；对 `(issuer, kid, ticket_id)` 和 `effect_id` 建唯一约束。
2. 验签、检查 epoch/generation、登记 Ticket、登记 effect 和进入 `CLAIMED` 必须在一个事务内完成；未成功落盘不得产生副作用。
3. 同一 Ticket 重复到达不重复执行；返回第一次已知的事实结果。参数、对象或收件上下文不同但 effect_id 相同则硬失败并报警。
4. 状态至少包含 `CLAIMED`、`SIDE_EFFECT_STARTED`、`SUCCEEDED`、`FAILED_RETRYABLE`、`FAILED_FINAL`、`AMBIGUOUS`、`RECONCILED`。
5. 在副作用前崩溃可用同 effect_id 安全恢复；在外部副作用后、回执落盘前崩溃必须进入 `AMBIGUOUS`，禁止盲目重发。
6. 发送链路以 channel client message id/effect_id 做业务幂等；平台不支持查询时只能进入 `RECONCILE_REQUIRED`，不得声称已送达。
7. 7184 持久保存已撤销 request/generation 和当前 epoch；7174/7176 拒绝旧 epoch、低于已观察 generation 或已撤销 generation 的票据。
8. 系统时钟回退超过允许偏差、Ledger 损坏/不可写或 epoch 状态丢失时，服务 `/ready` 硬失败。

#### 密钥生成、保存和轮换

1. 首次启动由 7184 生成两套 Ed25519 key pair；私钥使用 Windows DPAPI CurrentUser 加密并绑定产品 app-id 的附加熵，不允许明文回退。
2. 私钥文件、公开信任清单和 Ledger 使用当前用户专属 ACL；日志、崩溃转储和 API 不输出私钥或完整 Ticket。
3. 验证端通过受信 ComponentManifest 获得 `kid/public_key/not_before/not_after/audience`，并固定其摘要；未知 kid、错误 audience 或摘要不一致一律 fail closed。
4. 每套 key 保留 `next/active/previous` 三阶段；新公钥先分发并通过 readiness，再切 active。previous 仅保留到最长 Ticket TTL、在途 effect 和回滚窗口结束。
5. 轮换声明由旧 active key 签名并绑定新 key、audience、release/component manifest 和生效时间；不能跨 audience 授权。
6. 私钥疑似泄露时立即提升 `gateway_epoch`、撤销 kid、停止签发并使相关服务不 ready；不尝试静默继续。

#### 验票失败矩阵

以下任一条件必须在任何副作用前拒绝并只记录脱敏原因码：签名错误、未知 schema/kid、algorithm 不匹配、issuer/audience 错误、未生效/过期、时钟异常、epoch/generation 过旧、Ticket/effect 重放冲突、Manifest/Policy/Life/arguments/payload/artifact 摘要不符、租户/账号/会话不符、A5 确认缺失、路径型对象、对象 revision/hash/size/MIME 不符、Ledger 不可写或组件版本不兼容。

### 2.2 冻结组件兼容边界

#### 7174 主后端：阶段性执行内核

- 保留现有模型、Skill、Tool、工作区、知识库和运行状态能力，7184 通过 BackendClient 调用。
- 迁移期由 7184 把旧请求转换为固定 CapabilityManifest 和内部 effect；前端及 7176 不再新增长期直连点。
- 在 7174 原始源码缺失期间，ExecutionTicket 先由 7184 本地强制门禁并记录 FactLedger；能够替换 7174 入口后，再把验票下沉到实际副作用之前。
- 最终允许 7174 公开健康、静态能力和受票据执行接口；旧 `/api/v1/gateway/internal/inbound` 及无票据业务执行必须关闭。
- 7174 的模型文字只作为候选回复，不能决定 Request/Artifact/Delivery 的完成状态。

#### 7175 生命服务：只读快照与显式生命事务

- 7184 只读取带 revision/hash 的 LifeSnapshot，并把固定快照绑定到 run；7176 不再自行编译生命上下文。
- 身份、头像、声音、称呼等设置属于显式控制面事务，可保留兼容 API，但最终经 7184 统一 readiness、审计和错误表达。
- `execution/prepare/commit` 等旧授权结果只作兼容输入，不再成为工具执行或任务完成的唯一权威。
- 7175 不接收渠道收件人、不发送消息、不持有 DeliveryTicket 私钥或渠道凭证。

#### 7176 通信服务：从冻结服务替换为纯适配层

- 新源码先保持 `tiangong.communication.api.v1` 健康契约和 `/api/v1/gateway/links/*` 管理兼容面，内部实现全部替换。
- 入站只负责渠道鉴权、游标、落盘、去重、下载/验收附件和生成 InboundEnvelope；不得直接调用 7174/7175。
- 出站只接受 7184 的 DeliveryTicket 和内容寻址对象；逐阶段记录取件、上传、渠道接受和歧义回执。
- 新服务环境只接受 7184 地址、服务认证、公钥/信任清单和渠道配置；移除当前直连 `TIANGONG_BACKEND_URL` 与 `TIANGONG_LIFE_URL` 的业务依赖。
- 微信/飞书共用 Inbox、Delivery Ledger、限流和安全内核，协议差异仅留在 channel adapter。

#### 7184 总网关：唯一业务权威

- 前端业务请求、7176 入站、7174 执行事实和 7175 固定快照都在 7184 汇合；每个逻辑请求只分配一个 request_id。
- 对旧前端提供有清单的兼容路由，禁止透明任意反向代理；所有业务路由逐项迁移并在清单中标注 owner。
- `/health` 仅表示进程存活；`/ready` 必须同时验证 Ledger、密钥、四组件 Manifest、Schema、动作、Skill 和磁盘写入能力。
- 7184 是状态机、Ticket、FactLedger、Artifact Gate、Completion Gate、Outbox 和聚合 UI 状态的唯一所有者。

### 2.3 唯一权威源、构建目标和发布边界

| 组件/事实 | 唯一可编辑权威 | 构建/生成目标 | 当前约束 |
|---|---|---|---|
| Electron 桌面 | `app/` | `app.asar` + Electron 产品目录 | 不再建立 `src/desktop`；`app/node_modules` 只能由 lock 重建 |
| 共享契约/Schema | `src/contracts/` | JSON Schema、OpenAPI、摘要清单 | 后续所有服务和前端类型从这里生成 |
| 7184 总网关 | `src/total_gateway/` | wheel，随后冻结为 7184 EXE | 当前 wheel 版本 `3.0.0.dev1` |
| 7176 通信服务 | `src/communication_service/` | wheel/EXE，最终替换同名冻结服务 | 旧 EXE 只作协议证据，不热补 |
| 7174 执行内核 | `app/backend/tiangong-backend/` 的正式运行时 + 可读 Skill 源 | 兼容期原样随包；后续逐边界替换 | EXE/PYC 不直接修改 |
| 7175 生命服务 | `app/life-service/` 正式运行时 | 兼容期原样随包 | EXE/PYC 不直接修改 |
| 旧动作/Skill 表 | `app/backend/tiangong-backend/_internal/omni_body_skill/` | P4 迁移为 `src/contracts` 生成物 | `readable-python-source/` 仅是审计导出，不是第二权威 |
| 安装器 | `installer/` 与后续正式构建脚本 | NSIS 安装包 | 不从临时目录或活动安装目录取输入 |
| 无密钥配置 | `config-templates/` | 首次启动模板 | AppData、凭证、日志和用户数据永不进源码/安装输入 |

当前旧注册表基线：

- `actions.json`：56 个动作，SHA-256 `3798276FEF6C4635BF644C8890EF6A16188AA9C27C93FA53C3F9F59123BEBB25`。
- `skill_router_index.json`：31 个索引项，SHA-256 `77C99B7191FB2AAC15394D4C45E8AD3330816602472845867EA3B4165667C30F`。
- `capability_manifest.generated.json`：310,913 字节，SHA-256 `A32B9312A183C3DCE03A82F7E2519B56524F4F45896BFD6215D9BF6A1F349367`。
- 7174 `release.json`：SHA-256 `C65E834C6D645548BEC273DAED1EE3CAE5B3F33EE1D1E9D821D9CE1D26A163B8`。
- 前端 `build-info.json`：SHA-256 `BF5FD7E5812871652B39747B1E788E88EDC10CDBFB6765AA0E811777BED9BC40`。

正式安装输入只允许：组装后的 Electron `app.asar`、锁定依赖、7174/7175 兼容运行时、新构建的 7184/7176、产品资产、Release/Component Manifest、必要配置模板和安装器资源。以下内容必须排除：`.git`、`.agents`、`.codex`、`GATEWAY_REFACTOR_PLAN.md`、`tests`、`readable-python-source`、`recovered-python-bytecode`、`maintenance-tools`、源码缓存、`node_modules` 开发副本、`out`、日志、数据库、临时目录、AppData、凭证和任何 `bak/old/tmp/fixed/patch` 旁路文件。构建脚本必须从显式清单取文件，禁止对工作区根目录做通配打包。

## 3. 详细执行计划

状态定义：`TODO`、`IN_PROGRESS`、`DONE`、`BLOCKED`。

### P0 对抗审计与设计准入

- [x] `P0.1 DONE` 逻辑审计：唯一权威、状态、幂等、Ticket、Skill、Artifact、微信文件链路。
- [x] `P0.2 DONE` 推理审计：安装、首次启动、渠道收发、断网重启、Word、升级回滚。
- [x] `P0.3 DONE` 汇总 F1-F5、20 条不变量和正式切换禁区。
- [x] `P0.4 DONE` 将状态模型修订为 RequestState、ExecutionState、ArtifactState、DeliveryState。
- [x] `P0.5 DONE` 将外部投递语义修订为至少一次传递与业务效果幂等。
- [x] `P0.6 DONE` 冻结事实所有者：7176 渠道、7184 编排、7174 工具、7175 生命快照。
- [x] `P0.7 DONE` 完成两类 Ticket 的正式威胁模型、字段绑定、持久防重放和密钥轮换设计。

### P1 唯一源码树和构建基线

- [x] `P1.1 DONE` 以 2026-07-14 恢复工程快照建立唯一工程树；原 ZIP 和安装目录保持只读。
- [x] `P1.2 DONE` 以现有 `app/` 作为唯一桌面源码根，不再复制到 `src/desktop`；已核对 app.asar 的 607 项、依赖重建和关键哈希。
- [x] `P1.3 DONE` 建立 `src/contracts`、`src/total_gateway`、`src/communication_service`、`tests`，不建旁路副本。
- [x] `P1.4 DONE` 明确 7174/7175 冻结组件的兼容边界和后续源码替换计划。
- [x] `P1.5 DONE` 建立依赖锁、统一构建/测试命令和 Git/LFS 基线。
- [x] `P1.6 DONE` 记录 Electron、7174、7175、7176、配置、Schema、动作表、Skill 表和构建产物映射。
- [x] `P1.7 DONE` 明确打包 include/exclude，计划、测试、缓存和临时文件不得进入安装包。

### P2 契约、状态机和安全边界

- [x] `P2.1 DONE` 定义 InboundEnvelope、AttachmentRef、LifeSnapshot、SkillSelectionRecord。
- [x] `P2.2 DONE` 定义 CapabilityManifest、ExecutionTicket、ExecutionResult、FactRecord。
- [x] `P2.3 DONE` 定义 ArtifactManifest、OutboundPlan、DeliveryTicket、DeliveryReceipt、ComponentManifest。
- [x] `P2.4 DONE` 为四套状态定义状态值、合法迁移、终态、取消、迟到事件和聚合显示。
- [x] `P2.5 DONE` 定义多账号隔离作用域键：channel+tenant+link_account+conversation+message_id。
- [x] `P2.6 DONE` 定义 effect_id、request_id、run_id、artifact_revision、delivery_id、generation fencing。
- [x] `P2.7 DONE` 定义终态/可重试/歧义错误、Retry-After、退避、动态超时和断路器。
- [x] `P2.8 DONE` 定义服务间认证、issuer/audience、DPAPI、密钥轮换和日志脱敏。
- [x] `P2.9 DONE` 生成 JSON Schema/OpenAPI 并建立契约兼容测试。
- [x] `P2.10 DONE` Schema/动作/Skill/组件摘要不一致时 `/ready` 必须硬失败。

### P3 可靠性内核

- [x] `P3.1 DONE` 实现 7184 无副作用入口、配置、health、ready、单实例 epoch、磁盘健康。
- [x] `P3.2 DONE` 实现持久事件/状态存储、事务、CAS、唯一约束、迁移和损坏检测。
- [x] `P3.3 DONE` 实现 7176 durable Inbox：先落盘再 ACK/推进微信游标。
- [x] `P3.4 DONE` 实现 7184 Request Journal、幂等账本和 Session Actor。
- [x] `P3.5 DONE` 实现事务 Outbox：状态和出站意图同一事务提交。
- [x] `P3.6 DONE` 实现 7176 Delivery Ledger、歧义回执和 `RECONCILE_REQUIRED`。
- [x] `P3.7 DONE` 为所有副作用实现稳定 effect_id；重复请求返回第一次事实结果。
- [x] `P3.8 DONE` 实现内容寻址、不可变 Attachment/Artifact/DeliveryPackage 和 revision。
- [x] `P3.9 DONE` 实现 Ticket 签发/验证及持久 nonce 消耗。
- [x] `P3.10 DONE` 实现租约、心跳、generation fencing、取消和迟到事件隔离。
- [x] `P3.QA DONE` P3 阶段质检：全量测试、契约兼容、关键故障/并发复核、双构建和污染检查。

### P4 生命、Skill、执行和事实门禁

- [x] `P4.1 DONE` 实现 7175 LifeClient，只读取固定 revision/hash 的 LifeSnapshot。
- [x] `P4.2 DONE` 实现 Skill 双通道：系统推荐和模型主动 route/list/get/read。
- [x] `P4.3 DONE` Skill 激活前固定来源/version/hash/required_actions 并校验 CapabilityManifest。
- [x] `P4.4 DONE` 使用 NEED_SKILL/NEED_CONFIRMATION 事件，禁止同步环形回调。
- [x] `P4.5 DONE` 实现 7174 BackendClient 和 ExecutionTicket 边界。
- [x] `P4.6 DONE` 实现 FactLedger，模型文字不能写入完成事实。
- [x] `P4.7 DONE` 实现 Artifact Gate：对象、哈希、大小、魔数、MIME、结构和写后回读。
- [x] `P4.8 DONE` 实现 DOCX QC：ZIP、Content Types、document.xml、关系、段落和真实字数。
- [x] `P4.9 DONE` 修复打包：新 `.zip`、非空 items、输入输出不同路径、临时写和原子提交。
- [x] `P4.10 DONE` 实现 Completion Gate，文本、执行、产物和每个附件投递分别判定。
- [x] `P4.QA DONE` P4 阶段质检：全量测试、契约兼容、关键故障/并发、双构建和污染检查。

### P5 微信和飞书通信适配层

- [x] `P5.1 DONE` 7176 收敛为纯通信适配层，删除生命编译、权限、模型和完成判断。
- [x] `P5.2 DONE` 微信文本入站：Inbox、去重、自消息、群策略、会话键、乱序跟进、context token。
- [x] `P5.3 DONE` 微信文件入站：CDN 白名单、禁跨域重定向、流式下载、双大小限制、AES 解密。
- [x] `P5.4 DONE` 微信文件安全：文件名、路径、MIME/扩展/魔数、ZIP bomb、OOXML、隔离、配额、TTL。
- [x] `P5.5 DONE` 微信文本出站：稳定 client/effect ID、限流、token 失效、分段和回执。
- [x] `P5.6 DONE` 微信文件出站：ArtifactRef→getuploadurl→AES→CDN→sendmessage→分阶段回执。
- [x] `P5.7 DONE` 微信大文件：产品上限、动态超时、带宽预算、进度和歧义对账。
- [x] `P5.8 DONE` 飞书入站：先落盘、去重、thread/root/mention/富文本和多租户隔离。
- [x] `P5.9 DONE` 飞书图片/文件入站：image_key/file_key、Scope、安全验收和 AttachmentRef。
- [x] `P5.10 DONE` 飞书文本/卡片/图片/文件出站：token 单飞刷新、429、线程回复和回执。
- [x] `P5.11 DONE` 7176 必须验证 DeliveryTicket，禁止路径型发送和换文件/换收件人。
- [x] `P5.QA DONE` P5 阶段质检：全量测试、契约兼容、关键并发/崩溃、双构建和污染检查。

### P6 测试和故障注入

- [x] `P6.1 DONE` 建立微信/飞书模拟器、脱敏协议样本和安全文件语料。
- [x] `P6.2 DONE` 单元/契约测试覆盖状态机、幂等、CAS、Ticket、Skill、FactLedger、Artifact/QC。
- [x] `P6.3 DONE` 故障注入覆盖 Inbox、Journal、7174 effect、Outbox、上传和 sendmessage 边界。
- [x] `P6.4 DONE` 重复、断网、歧义场景自动注入至少 100 轮。
- [x] `P6.5 DONE` 微信矩阵覆盖文本、图片、语音、视频、文件、伪 MIME、超大、半下载、SSRF。
- [x] `P6.6 DONE` 飞书矩阵覆盖重复/乱序、富文本、附件、缺 Scope、403/429/5xx、token 风暴。
- [x] `P6.7 DONE` Word E2E：1000 字 DOCX→QC→revision→打开→微信投递，拦截 246B/1KB 假产物。
- [x] `P6.8 DONE` 产品回归：工作区、身体/头像/声音/称呼、微信、Skill 主动申请、真实打开按钮。
- [x] `P6.QA DONE` P6 整阶段质检：全量测试、契约兼容、关键故障注入、双构建、工件哈希和源码污染检查。

### P7 Electron、前端和发布契约

- [x] `P7.1 DONE` Electron 监督四服务启动、ready、单实例、drain、重启和退出恢复。
- [x] `P7.2 DONE` 前端业务入口全部切到 7184；旧直连只允许健康和静态能力。
- [x] `P7.3 DONE` UI 分别显示执行、产物和投递：处理中、QC、渠道接受、歧义、失败。
- [x] `P7.4 DONE` 实现真实 Artifact Card/openPath，OS 返回决定成功。
- [x] `P7.5 DONE` 建立单一 Release Manifest，绑定组件哈希、契约、Schema、动作和 Skill 摘要。
- [x] `P7.QA DONE` P7 阶段质检：全量测试、契约兼容、关键并发/退出恢复、双构建和污染检查。

### P8 迁移、真实渠道和回滚

- [x] `P8.1 DONE` 影子模式只复制入站和比较决策，禁止真实工具与出站副作用。
- [x] `P8.2 DONE` 切换前排空旧链路，使用单一 epoch，证明无双轮询/双发送。
- [x] `P8.3 DONE` 关闭 7174 无票据执行、7176 直调 7174/7175、旧代理和业务旁路。
- [ ] `P8.4 IN_PROGRESS` 真实测试账号验证微信/飞书文本和文件双向闭环，对端实际可打开。
- [ ] `P8.5 TODO` 升级执行 drain→单写者切换→迁移→验证，失败整体回滚。
- [ ] `P8.6 TODO` 回滚后已处理消息和 Outbox 不重复回复、不丢投递。

### P9 新机、安装包和最终准入

- [ ] `P9.1 TODO` 新机安装检查注册表、快捷方式、目录、四端口、版本、ready 和日志。
- [ ] `P9.2 TODO` 混装或篡改任一组件时 ready/Manifest/签名必须硬失败。
- [ ] `P9.3 TODO` 构建正式安装包，验证清单、源映射、哈希、签名和无污染。
- [ ] `P9.4 TODO` 最终双路对抗复审；P0 用例 100% 通过、致命/高风险清零后交付。

## 4. 本轮进度日志

### 2026-07-14

- 完成 3.3.2 参考包只读审查，确认微信传输实现已大部分存在于当前 7176。
- 完成逻辑与推理两路对抗审计，结论为有条件准入。
- 用户指定以现有 `D:\天工造物 v3.0 完整版` 作为产品基线。
- 完成安装成品只读盘点，确认前端可还原、7175/7176 和关键后端主要为冻结模块。
- 收到并只读核验 `天工造物v3完整版源码与运行时_2026-07-14.zip`。ZIP 为完整工程恢复快照，不是完整原始 Python 仓库。
- 原 ZIP：201,599,156 字节，SHA-256 `07768792AD9701EA7BADA99C19C3F03AE2D2D2512E8D19786FEF5A408FAB8922`。
- ZIP 共 4,604 个条目：4,103 个文件、501 个目录；`checksums.sha256` 有 4,102 个条目，逐项验证全部通过。
- 5 个中文路径缺少正确 ZIP UTF-8 标志，落盘时按 GBK 文件名字节恢复为正确中文；无路径穿越、重复路径或大小写碰撞。
- 工程快照已直接落入本工作区，没有建立第二份源码副本；归档范围内 4,103 个文件，无缺失、无篡改、无额外文件。
- 静态基线：40 个 JavaScript/MJS 文件和 50 个 Python 文件语法检查均为 0 失败。
- Python 源码 50 个文件仅对应 22 份唯一内容；主后端、生命核心、通信服务仍只有 EXE/PYC/PYD。总网关和通信边界按新源码重写，冻结件只作行为与协议参考。
- 原 app.asar 映射为 73 个目录和 534 个文件：453 个依赖文件、81 个产品源码/资源。80 个产品文件逐哈希一致；`package.json` 仅增加恢复工程的启动、语法检查、Electron 开发依赖和快照说明。
- 在系统临时目录执行 `npm ci --ignore-scripts` 后，453 个发布依赖文件全部恢复；436 个逐字节一致，17 个差异均只在依赖 `package.json` 被发布过程裁掉的脚本/Git 元数据，包名、版本与运行代码一致。验证目录已清理。
- `P0.7` 完成：两类 Ticket 的非对称签名、字段绑定、DPAPI、持久防重放、歧义恢复和轮换规则已冻结。
- `P1.3` 完成：建立三个唯一源码包和边界测试；默认端口固定为 7184/7176，2 个最小测试通过。
- `P1.4` 完成：7174 定位为阶段性执行内核、7175 为固定生命快照、7176 重写为纯通信适配层、7184 为唯一业务权威。
- `P1.5` 完成：锁定已验证 Python 工具链；统一检查/构建脚本通过，连续两次 wheel SHA-256 均为 `344A1A22E7F493FE736B55AFAAC782E2D69C56DF455DA2ED62F9841990DFB6B1`，构建目录无残留。
- Git/LFS 根基线提交：`c836d7f84ad2585cd61083e84b46d171b509f3f1`，4114 个文件；Git 默认逐字节保存，90+ 个大二进制使用 LFS。
- `P1.6/P1.7` 完成：组件、旧注册表、构建目标和安装 include/exclude 已登记，恢复导出目录不作为第二权威。
- `P2.1` 完成：四类首批契约使用 strict/frozen/extra-forbid 模型；附件禁止路径并绑定租户/账号/会话，生命快照分离生命和用户身份，Skill 同时支持系统推荐与模型主动申请且必须经 get/read 才激活。
- 首批 Schema bundle SHA-256：`C81E60C48399F559EA4FCB49EE90159A2F7EB21D962574A152D7F8F3A94CB1F6`；14 个单元/边界测试全部通过，缓存为 0。
- `P2.2` 完成：CapabilityManifest 固定动作/Schema/风险/副作用/上限；ExecutionTicket 最长 60 秒并绑定 epoch、generation、上下文、对象和 Skill；ExecutionResult 禁止在副作用开始后盲重试；FactRecord 固定为非模型机器证据。
- 结构授权必须显式传入已验签事实，并校验 Manifest 摘要、有效期、epoch/generation、动作版本和全部资源上限；签名密码学实现仍在 `P3.9`。
- 当前 8 类 Schema bundle SHA-256：`CEDC75D84362D6C86FEE966FECF6B4646490C2C0A05E987110FD1E0570CE2BD1`；29 个测试全部通过，缓存为 0。
- `P2.3` 完成：ArtifactManifest 绑定内容对象/哈希/大小/MIME/安全文件名和 QC；OutboundPlan 把文本与每个附件拆成独立 part；DeliveryTicket 精确绑定会话、收件作用域、回复目标和每个 part；DeliveryReceipt 只凭平台证据区分 CHANNEL_ACCEPTED 与 DELIVERED。
- ComponentManifest 禁止绝对路径；`production_claim=true` 必须具备桌面、7184、7174、7175、7176 全组件且不能含开发版本。
- 当前 13 类 Schema bundle SHA-256：`D6BEAF9D487E64931548CAEC4686A59B9D20F1E18E1132A50192A09DC43FA557`；40 个测试全部通过，缓存为 0。
- `P2.4` 完成：Request/Execution/Artifact/Delivery 四套显式状态机、owner、CAS revision、generation fence、事件摘要、终态和事实证据门禁已实现。
- 副作用开始后禁止取消/fence/可重试，必须进入 AMBIGUOUS；迟到 generation 返回 LATE_IGNORED；聚合显示区分 CHANNEL_ACCEPTED 与 DELIVERED，并支持文本成功/附件失败的 PARTIAL。
- 当前 17 类 Schema bundle SHA-256：`506085262B561571BD1519426FFB6AA0F45211DB54F6688A7DB2C9359B1E021B`；54 个测试全部通过，缓存为 0。
- `P2.5` 完成：以 JCS+SHA-256 域分离方式固定 inbound conversation/principal/message/idempotency 和 outbound conversation/recipient 作用域键。
- InboundEnvelope 现在自带 conversation/principal/message 三个摘要；入站绑定同时核对渠道、租户、账号、会话、消息、主体、附件来源和幂等键，服务重启后不依赖进程内作用域缓存。
- OutboundPlan 绑定精确渠道、租户、账号、会话、回复目标和收件人摘要；任一跨租户、跨账号、跨会话或换收件人都会得到不同键并 fail closed。
- 当前 21 类 Schema bundle SHA-256：`AA53B631467A018205E867273FEBC49E2C40168E3713BF16B1E1D613BE60608B`；62 个测试全部通过，缓存为 0。
- 连续两次 wheel 均为 24,284 字节、SHA-256 `BD591729A1AF6DC5191C800944C04EF0AF8CEC6178CF71502A428E9067D78C72`；构建输出已清理。
- `P2.5` 修改范围：`src/contracts/models.py`、`scope.py`、`schema.py`、`__init__.py`、`tests/test_foundation_contracts.py`、`tests/test_scope_keys.py`；未修改 app、冻结 7174/7175/7176 和安装目录。
- `P2.6` 完成：使用 JCS+SHA-256 域分离生成 `req_`、`run_`、`eff_`、`art_`、`arv_`、`del_`、`fnc_` 全长稳定身份；旧式自由字符串不能进入新契约。
- request_id 固定绑定 P2.5 入站幂等键；run_id 绑定 request_id+run_sequence；effect_id 绑定 run/generation/effect kind/ordinal/完整意图摘要，因此相同 effect 重试保持不变而换参数或换代必然变化。
- artifact_id 在同一逻辑产物的 revision 间稳定，artifact_revision_id 绑定 run、generation、revision 和实际内容哈希；该 revision 身份已贯穿 ArtifactManifest、DeliveryTicket grant 和 DeliveryReceipt correlation，渠道回执不能换成另一版文件。
- delivery_id 绑定 request/run/generation、收件作用域、回复目标和不可变 payload；换账号/会话/收件人/回复线程/内容均产生新 delivery_id，同内容同目标重试保持不变。
- GenerationFence 绑定 gateway_epoch、request/run 从属关系、generation、lease、时窗和前序 fence；纯判定明确区分 digest/context/epoch/旧代/未来代/租约/未生效/过期，仅 CURRENT 可接受。签名和持久代际账本分别留在 `P2.8/P3.9` 与 `P3.2/P3.10`。
- 当前 28 类 Schema bundle SHA-256：`5A0F242CBA9A4E67C1EFE31358F2A9726EC2B092ED8B61E6E8D72CB46BA88DAF`；71 个测试全部通过，缓存为 0。
- 连续两次 wheel 均为 28,196 字节、SHA-256 `95D49519F70829754CA632F8F701962C67B147DF03AD88D776ED133FBD44E562`；构建输出已清理。
- `P2.6` 修改范围：`src/contracts/identities.py`、现有 model/execution/delivery/state/schema/authorization/export 契约和对应测试；未修改 app、冻结 7174/7175/7176、安装目录或用户运行数据。
- `P2.7` 完成：ErrorDescriptor 只接受可信组件事实并严格区分 TERMINAL/RETRYABLE/AMBIGUOUS/CANCELLED/FENCED；模型不能生成错误事实，副作用开始后不能标为可重试，未知外部结果必须进入对账。
- RetryPolicy/Decision 使用无浮点的整数指数退避、确定性 jitter、平台 Retry-After 下限、最大尝试、总重试预算、最小执行时间和绝对 deadline；任何一项越界即停止，不提前绕过渠道限流。
- DynamicTimeoutPolicy 依据阶段、字节数、实测吞吐、最低吞吐、安全系数、操作上限、空闲窗口和剩余 deadline 计算；deadline 小于最小安全窗口时拒绝启动，避免大文件沿用固定短超时。
- CircuitBreakerPolicy/Snapshot 使用摘要、revision 和纯迁移：失败阈值打开、精确 Retry-After、半开探测并发上限、成功闭合、失败重开；篡改、旧时间、探测风暴和迟到 probe 结果均 fail closed/忽略，不改变状态。
- 当前 37 类 Schema bundle SHA-256：`14A6BA7D9F1F9EB538690F8DA725147139E6ED251C71AC1672C5286BEA9CB5D6`；82 个测试全部通过，缓存为 0。
- 连续两次 wheel 均为 34,241 字节、SHA-256 `45BFFBEEE093701AC455ED0B1B799D3329AA6AD28251659A3FC3EA3682CE9588`；构建输出已清理。
- `P2.7` 修改范围：`src/contracts/reliability.py`、契约导出/Schema 和 `tests/test_reliability.py`/Schema 清单；未修改 app、冻结服务、安装目录或用户数据。
- `P2.8` 完成：服务认证断言固定 EdDSA/kid、issuer/audience、实例、30 秒时窗、最大 5 秒时钟偏差、gateway epoch、一次性 nonce、HTTP 方法、规范化 API 路径、正文摘要、请求/effect 和组件清单摘要；授权必须显式具备验签与 nonce 持久登记事实，任何换主体、换路径、换正文、旧 epoch、未知/非活动 key 均 fail closed。
- TrustBundle 固定 audience/purpose 信任作用域、next/active/previous/revoked 生命周期、唯一 kid、生产 active key 和自摘要；正常轮换分 PREPARE/ACTIVATE/RETIRE，旧 active key 签名且只能改变目标旧/新密钥，禁止夹带其他密钥变更。
- 应急吊销使用独立离线恢复 signer，强制 epoch 精确加一、旧 key 在生效时刻转 REVOKED、替代 key 转 ACTIVE、信任包 revision 加一且 scope 不变；离线恢复 key ID、签名事实、前后包摘要、组件摘要和生效时间全部绑定。
- DPAPI 私钥信封只保存 `Windows-DPAPI/CurrentUser` 加密元数据、app-id 附加熵摘要、密文摘要/大小、用户 SID/ACL 摘要和 `keys/*.dpapi` 规范化相对路径；契约明确禁止明文回退。
- 结构化日志使用固定默认策略，对授权头、token、cookie、私钥/密文、票据、签名和用户/工作区路径做不可逆摘要标记，同时扫描 Bearer/JWT 形态、拒绝浮点并限制长字符串；脱敏前后载荷分别绑定摘要，防止日志对象被篡改。
- 当前 45 类 Schema bundle SHA-256：`9BE303575EB4C19F73437C285ED675201AF5C984909D58382698C35E810D1802`；95 个测试全部通过，40 个 JavaScript 文件与全部 Python 源码语法检查通过，缓存为 0。
- 连续两次 wheel 均为 41,801 字节、SHA-256 `4A8D6F535BCFCA639EDF8522A3D480E31F6E468E99409402AE4C6937B05AF866`；构建输出已清理。
- `P2.8` 修改范围：`src/contracts/security.py`、契约导出/Schema、`tests/test_security.py` 和 Schema 清单测试；未修改 app、冻结 7174/7175/7176、安装目录、原 ZIP 或用户运行数据。密码学验签、DPAPI 调用和持久 nonce 消耗留在 `P3.9` 实现。
- `P2.9` 完成：从 `src/contracts` 唯一模型源确定性生成且只生成 3 个契约构建工件：`schema-bundle.json`、OpenAPI 3.1.1 组件目录 `openapi.json` 和自摘要 `contract-artifacts.manifest.json`；不拆成 45 个零散 Schema 文件，不在源码树保存生成副本。
- OpenAPI 目录包含 45 个根契约及其全部嵌套组件，所有 `$ref` 自动验证可解析；`paths` 明确为空并标记具体路由归 `P3.1`，避免把尚未实现的端点伪装成产品能力。
- 生成过程在同一父目录暂存并原子提交，目标非空时 fail closed 且不会删除原文件；验证器要求目录恰好 3 个文件，并逐字节对照当前源码重生成结果、校验 manifest 自摘要。
- 契约兼容门禁同时检查 backward 与 strict-consumer forward 方向；可拦截删除根契约/字段、增加必填、类型/引用/常量变化、枚举缩窄、范围收紧、禁止额外字段和联合分支删除。新增独立根契约允许作为显式增量；当前 P2.8 bundle 摘要被锁为审查基线。
- 102 个测试全部通过；两次完整构建的 4 个文件逐大小和 SHA-256 一致：manifest 1,551 字节/`CB9EAB13487A8CF23C62D1E87B24748546D127FA7C612308E3160C8D21CFC7FF`，OpenAPI 98,193 字节/`0CCF29A31F76DB13F4C948A8EDBCE1E43A551BB0C54A5DD184DD83D383BA2070`，Schema bundle 110,217 字节/`740A21B6C6E14B771D34194F0AC19888BDECD71CD1E61FFE85B70EBA5E1E498B`，wheel 46,510 字节/`A9B470123E257E81CA368E03EF1C8AE7618C349986DC8A7DC9A0CC11023B3B60`。
- 契约 manifest 内部自摘要为 `7FF9CA6859FCF39068B567BA95AC76ACEF33541A1A25F86B5EC453F2C75417E1`，Schema 语义 bundle 摘要仍为 `9BE303575EB4C19F73437C285ED675201AF5C984909D58382698C35E810D1802`；`out`、源码缓存和系统临时构建目录均已清零。
- `P2.9` 修改范围：`src/contracts/artifacts.py`、`compatibility.py`、`tests/test_contract_artifacts.py` 和 `scripts/build.ps1`；未修改 app、冻结服务、安装目录、原 ZIP 或用户运行数据。
- `P2.10` 完成：ReadinessExpectation 从已通过自摘要验证且声明 production 的 ComponentManifest 派生，只接受角色正确的 7184/7174/7175/7176 四个服务；每个服务固定 version、build、可执行文件摘要和同一 Schema bundle。
- 每份 ComponentReadinessEvidence 绑定实例、gateway epoch、组件清单、Schema、CapabilityManifest、Skill index、ReleasePolicy、契约工件 manifest 和二进制摘要，并带机器证据自摘要、观测时间和 `model_generated=false`；组件自己的 `health_check_passed=true` 不能替代外部传入的服务认证与二进制验证事实。
- 纯判定器对缺失/重复/意外组件、超过 64 份证据、未认证、二进制未验证、证据篡改/过期/来自未来、错误角色/version/build/epoch 和任一摘要漂移全部 fail closed；只有四个组件逐项完全一致时返回 `READY + HTTP 200`，其余固定 `NOT_READY + HTTP 503` 并给出脱敏原因码。
- P2.10 仅新增 3 个根契约，已证明删去这 3 个根后其余 P2.8 bundle 仍精确等于历史 SHA-256 `9BE303575EB4C19F73437C285ED675201AF5C984909D58382698C35E810D1802`，因此是经兼容门禁确认的增量；当前 48 类 Schema bundle SHA-256 为 `6B08C98B9BA6F0020E6F3549E4D93AEA439080EADE63E51E125F092544915F1A`。
- 106 个测试和 40 个 JavaScript/全部 Python 语法检查通过；两次构建逐文件一致：manifest 1,624 字节/`27D582B899B4E56A1EE6EDFEAE75D6E6ADCF41B0F82E4FAE139ED3E515FB587C`，OpenAPI 105,719 字节/`59141852E129CD33B246E0F90C18DF54128928E95B76445ACDB845990145D61F`，Schema bundle 117,665 字节/`8A6504B33912BEEE2B5C1145C943EC67E9406ABE1E0A6973AD2AFE58DEB5799E`，wheel 50,172 字节/`AC172A13201DF388F612F84EE4D522876FC8B56AF9306D507ACDA8A1DC1B8EF1`。
- 契约工件 manifest 内部自摘要为 `70C4C386343BA83D8A87CD79EDFD74A13275F6F6479D3038112BDDEFA5AD1893`；`out`、源码缓存和系统临时构建目录均已清零。
- `P2.10` 修改范围：`src/contracts/readiness.py`、契约导出/Schema、兼容基线和对应 readiness/工件/Schema 测试；未修改 app、冻结服务、安装目录、原 ZIP 或用户运行数据。
- `P3.1` 完成：7184 配置仅允许显式白名单环境变量、绝对非根状态目录和 `127.0.0.1`；production 端口强制 7184，未知变量、疑似误写的密钥变量、非十进制数值、相对路径或 production 改端口均启动失败，不把凭证混入普通配置。
- 单实例使用状态根中的 OS 独占文件锁；第二实例在写任何新 epoch 前被拒绝。`gateway.epoch.json` 使用严格/规范 JSON 和自摘要，正常重启单调加一并记录前一实例；已初始化目录中 epoch 丢失、损坏、非规范或摘要错误均 fail closed，不静默归零。
- 磁盘健康探测执行独占临时文件创建、写入、flush/fsync、完整回读、SHA-256 和清理，并校验可用空间；结果短时缓存以防 `/ready` 请求风暴频繁写盘，任一步失败都会令 readiness 503，探测文件不残留。
- 7184 当前 HTTP 仅绑定 loopback，只允许 GET `/health` 和 GET `/ready`；未知业务路由返回 404，POST/PUT/PATCH/DELETE 等返回 405，不存在提前开放的聊天、执行或发送入口。响应固定 JSON、`no-store`、`nosniff` 和拒绝 frame/default source，HTTP 请求不写普通访问日志。
- `/health` 只证明进程存活并返回实例、epoch 和 uptime；`/ready` 默认在四服务证据未配置时明确 503，配置后仍必须同时通过 P2.10 摘要/认证/二进制证据、当前 runtime epoch、活动单实例 lease 和磁盘探测才能 200。
- 113 个测试与全部语法/污染检查通过；真实 TCP 测试覆盖 health 200、未配置 ready 503、四组件一致 ready 200、404/405，以及运行状态目录只保留正式 `gateway.epoch.json` 和 `gateway.instance.lock`。
- 连续两次构建的契约工件保持 P2.10 哈希；wheel 均为 57,686 字节、SHA-256 `7CC91A46813FCCDFECBB38503D68E6440E22CD7D882C5F58870ACE627C7F862F`。`out`、源码缓存、磁盘探测和系统临时构建目录均为 0。
- `P3.1` 修改范围：`src/total_gateway/bootstrap.py`、`runtime.py`、`server.py`、`__main__.py` 和对应 bootstrap/真实 HTTP 测试；未修改 app、冻结服务、安装目录、原 ZIP 或用户 AppData。本轮测试状态仅位于系统临时目录并已清理。
- `P3.2` 完成：7184 使用唯一 `gateway.sqlite3` 状态库，固定 SQLite application_id、schema user_version=1 和带摘要迁移记录；只允许从空库迁移，未知 application、无版本但非空、新于当前 binary、迁移记录或 schema 指纹不符均拒绝打开，不做猜测性修复或降级。
- 连接强制 WAL、`synchronous=FULL`、foreign keys、`trusted_schema=OFF`、busy timeout 和 STRICT tables；数据库文件限制为当前用户读写语义。当前存储 Schema 指纹 SHA-256 为 `2013D7E8B3711BA17E191D94A7F8A54418A7A96C211DE6D021FD7402EAB68054`。
- `aggregate_state` 保存规范化 StateSnapshot 与独立摘要；`event_log` 保存每个首次 event_id 的原始事件、接受/拒绝决定、结果快照及摘要。事件追加与 `WHERE revision=expected_revision` 状态更新处于同一 `BEGIN IMMEDIATE` 事务；更新前故障会同时回滚事件和状态。
- 同 event_id 同内容返回第一次持久决定且不追加；同 ID 换内容硬冲突。被拒绝的 revision/owner/context/transition 事件也持久化，因此不能在未来换时机重放；接受事件按 `(machine, entity_id, resulting_revision)` 唯一，两个独立连接并发争抢同 revision 时严格只有一个 APPLIED，另一个形成可审计 REVISION_CONFLICT。
- 打开时执行 SQLite `integrity_check`、foreign-key、迁移、Schema、PRAGMA、可写事务及完整应用链全检；全检重新解析每个规范 JSON、核对事件/快照摘要、用纯状态机重算 TransitionDecision、验证 revision 连续和最终快照。运行期 `/ready` 快检验证当前快照、最后接受事件、Schema、PRAGMA 和可写事务，结果短时缓存以避免轮询放大。
- 随机伪数据库不会被覆盖；Schema/index 篡改、新版本数据库、当前快照 JSON/摘要语义篡改、不可写或关闭存储都会 fail closed。P3.2 已接入 7184 `/ready`，即使四服务证据全部正确，存储检查失败仍固定返回 503。
- 123 个测试与全部语法/污染检查通过；连续两次构建的契约工件保持 P2.10 哈希，wheel 均为 64,118 字节、SHA-256 `3D9389C4184AB63918935ED07E91FF37BED09E1583D9F0C3884F8085076A3360`。`out`、源码缓存和系统临时构建目录均为 0。
- `P3.2` 修改范围：`src/total_gateway/store.py`、`runtime.py` 和对应持久化/HTTP readiness 测试；未修改 app、冻结服务、安装目录、原 ZIP 或用户 AppData，所有测试数据库均位于系统临时目录并已清理。
- `P3.3` 定向检查完成：7176 Inbox 使用独立 application/schema 指纹、WAL/FULL/STRICT/CAS 和规范化自摘要；原始入站、游标推进与 ACK permit 在同一事务提交，重复输入返回首次 permit，换内容硬冲突。
- P3.3 的 9 个快速测试覆盖迁移/健康、先持久后 ACK、ACK 幂等、入站身份冲突、游标 CAS、事务故障全回滚、重启恢复、双连接并发和语义篡改；阶段全量回归与双构建留到 P3 全部完成后统一执行。
- `P3.4` 定向检查完成：gateway.sqlite3 升级到 schema v2，Request Journal 将一个入站幂等键永久绑定到唯一 request_id；Session Actor 对同一会话 FIFO 串行、不同会话独立并行，完成当前请求与激活下一请求处于同一事务。
- P3.4 与 P3.2 相关的 17 个快速测试通过，覆盖 v1→v2 原位迁移、重复/换内容冲突、会话隔离、双连接并发、队列写入故障回滚和语义篡改。
- `P3.5` 定向检查完成：gateway.sqlite3 升级到 schema v3，状态事件、Outbox 意图及二者绑定在同一事务提交；重复事件必须携带完全相同的 Outbox 集合，拒绝的状态事件不会产生副作用意图。
- P3.5 与既有网关存储相关的 22 个快速测试通过，覆盖写入故障全回滚、重复集合冲突、claim lease、worker 所有权和终态结果幂等。
- `P3.6` 定向检查完成：7176 Delivery Ledger 持久绑定 ticket/delivery/effect/request/run/generation/渠道作用域和稳定 client message ID；副作用开始后无回执重启固定进入 RECONCILE_REQUIRED，只有显式平台查询事实才能对账收敛。
- P3.6 的 8 个快速测试通过，覆盖重复 Ticket、上下文换绑、发送边界、重启歧义、成功回执、显式对账、事务故障回滚和语义篡改。
- `P3.7` 定向检查完成：gateway.sqlite3 升级到 schema v4；通用 EffectClaim 以 request/run/run_sequence/generation/kind/ordinal/intent 摘要确定 effect_id，Effect Ledger 固定 claim、外部副作用起点和第一份机器结果。
- P3.7 的 6 个快速测试通过，覆盖稳定身份、重复 claim、第一结果不可替换、重启歧义、结果写入故障和双连接并发。
- `P3.8` 定向检查完成：内容寻址对象库只接收字节/流，以 SHA-256 物理去重、以 kind+tenant+account+conversation+digest 派生隔离引用；逻辑 revision 连续、内容与 manifest 不可更换，写后读回验收。
- P3.8 的 7 个快速测试通过，覆盖去重与多租户引用、revision 防替换、元数据故障后的安全重试、流大小上限、临时清理和 blob 篡改。
- `P3.9` 定向检查完成：实际 Windows DPAPI CurrentUser 封存/解封 Ed25519 私钥；Execution/Delivery/ServiceAuth 使用 JCS+base64url 固定签名输入，验签固定 issuer/audience/purpose/kid/key state/time/epoch/component manifest；security nonce ledger 持久防重放。
- P3.9 的 5 个快速测试通过，覆盖 DPAPI 实机往返、Ticket 篡改、错 epoch/用途、执行/投递密钥隔离以及双连接并发与重启 nonce 消耗。
- `P3.10` 定向检查完成：gateway.sqlite3 升级到 schema v6；持久 GenerationFence 支持 lease acquire/heartbeat/release/cancel/精确加代，旧代、取消后、过期或 lease 不匹配结果只记录为 ignored，不得改变当前代或产生副作用。
- P3.10 的 7 个快速测试通过，覆盖重启恢复、当前/迟到结果、取消、过期、fence 写入故障和双连接单租约竞争。
- `P3.QA` 阶段质检通过：177 个单元/契约/并发/故障测试全部通过，40 个 JavaScript 文件与全部 Python 源码语法检查通过；P3.3～P3.10 的事务回滚、双连接竞争、重启恢复、篡改、歧义发送、nonce 重放、generation 迟到隔离均纳入自动化回归。
- 质检中补齐 DPAPI `CryptUnprotectData` 返回描述缓冲区释放，并重新通过真实 Windows DPAPI、当前用户 ACL、Ed25519 签名/篡改/错 audience/错 epoch 和持久 nonce 的 5 个定向测试及全量回归。
- 当前四个运行时持久层 Schema SHA-256：gateway `2A0906BD70DE6C41331570BD37A2696AC43DC0A27E254E9587D982634EB79086`、7176 Inbox `B50FE1BC0DB9D19731581FA1E8667C4480821C8532B65F008E3F0BAEF8BE8546`、7176 Delivery Ledger `BF7D63371A8C9E37E67DBD9B11B30583E8BF0D41355D326A217C0671ED486C26`、Object Store `43C9FE675344DA729343620219487AC0E70D6CEBC1C10FE15B0766DD0D39083E`。
- 连续两次完整构建的 4 个工件逐文件一致：manifest 1,624 字节/`27D582B899B4E56A1EE6EDFEAE75D6E6ADCF41B0F82E4FAE139ED3E515FB587C`，OpenAPI 105,719 字节/`59141852E129CD33B246E0F90C18DF54128928E95B76445ACDB845990145D61F`，Schema bundle 117,665 字节/`8A6504B33912BEEE2B5C1145C943EC67E9406ABE1E0A6973AD2AFE58DEB5799E`，wheel 103,309 字节/`DB6193510FECD8D07ACBA3FD362117A8EA0A36F31147B3CDDA1A0325A8CAE3B4`。
- 契约工件与 P2.10 完全一致，证明 P3 可靠性运行时没有漂移公开 Schema；wheel 未包含 tests、计划、`__pycache__` 或 `.pyc`。阶段构建输出、测试缓存和临时构建日志已清理。
- P3 修改范围：`src/communication_service/inbox.py`、`delivery_ledger.py`，`src/total_gateway/store.py`、`runtime.py`、`coordination.py`、`effects.py`、`object_store.py`、`outbox.py`、`tickets.py` 及 8 份对应测试；未修改 app、冻结 7174/7175/7176、安装目录、原 ZIP 或用户 AppData。
- P3 已知边界：可靠性内核和 7176 两套账本当前是可测试源码组件，真实 7175/7174/7176 API 接线与渠道切流分别属于 P4、P5 和 P8；本阶段不声称已接入正式微信/飞书流量。
- `P4.1` 定向检查完成：隔离运行冻结 7175 并核对真实 `tiangong.life.api.v2` 返回；LifeClient 仅允许 loopback 上 5 个 GET 路径，拒绝重定向、重复 JSON 键、非 JSON、超大响应和外部主机，不调用 identity/soul/settings 等写接口。
- LifeClient 对 `/state` 前后双读，交叉核对活动 identity、writer epoch、Soul revision、context hash/current/verified 与 capability 投影；任何并发漂移或跨响应换绑均 fail closed。7175 返回的 `root/path` 不进入快照，也不会被 7184 打开。
- 最新上下文以规范稳定字节写入 P3 内容寻址对象库；LifeSnapshot 固定 projection source_sequence、identity revision、Soul/上下文/能力摘要。用户称呼、头像、职业和生命头像/声音作为独立 `LifeProfileBindings` 输入，避免用户身份与生命身份混用。
- P4.1 的 6 个快速测试通过，覆盖稳定重复快照、显式用户资料变化、未建立生命、无上下文、投影漂移、跨响应篡改、固定 revision/hash、重定向 token 防泄漏、重复键、错误 MIME 和响应上限；隔离探针目录及日志已清理。
- `P4.1` 修改范围：`src/total_gateway/life_client.py`、`tests/test_life_client.py`；未修改冻结 7175、app、安装目录或用户生命数据。
- `P4.2` 定向检查完成：同一 SkillSelectionService 同时支持系统最多 3 个候选推荐与模型主动 `skill.route/list/get/read`；系统推荐只能停在 candidate，模型必须成功 get/read 才能 active，模型也可显式 no_skill。无匹配时返回空候选，删除旧路由器的默认 Word Skill 回退语义。
- Skill 候选始终返回 score、required_actions、missing_actions、incompatible_reasons 和当前 CapabilityManifest 兼容性；不可用动作会阻断内容释放和激活，Skill 查询本身不构成业务完成事实。
- `P4.3` 定向检查完成：兼容加载器直接固定真实 index 与 31 份 Markdown 字节，不信任旧 `manifest.txt`；Skill source_ref/version/content SHA/required_actions 和 catalog SHA 均从当前唯一文件计算，index、任一 Markdown 或 CapabilityManifest 漂移都会 fail closed。
- 当前 index SHA-256 为 `77C99B7191FB2AAC15394D4C45E8AD3330816602472845867EA3B4165667C30F`，31-Skill catalog SHA-256 为 `95FED0D168F12E768D58EFB285028CE1475E2E4D2B297012CD9B95FD184F6770`，Word Skill 源 SHA-256 为 `F42C07E0234DF77CFC10A7FC45B077D4B0344AD89FEF3477B2EBFE63181F13A6`。
- 审查确认旧 `omni_body_skill/manifest.txt` 已严重漂移：106 项一致、13 项缺失、14 项不一致，故仅保留为历史兼容证据，不作为新网关信任根，也不直接修改冻结运行时旁的旧清单。
- P4.2/P4.3 的 8 个快速测试通过，覆盖双通道、显式放弃、无默认回退、候选确定性、模型/系统来源区分、get/read 激活、缺动作阻断、Manifest 篡改、真实 31-Skill 加载及 index/Markdown 漂移。
- `P4.2/P4.3` 修改范围：`src/total_gateway/skill_selection.py`、`tests/test_skill_selection.py`；未复制 Skill 文件、未建立第二份 Skill 源、未修改旧 manifest、app、冻结 7174 或安装目录。
- `P4.4` 定向检查完成：gateway.sqlite3 升级到 schema v7，新增持久异步 coordination mailbox；规划状态与 NEED_SKILL/NEED_CONFIRMATION 在同一事务提交，插入故障会同时回滚 request state 和等待事件。
- Skill resolver 与桌面确认 UI 使用不同 consumer/解析权限；事件必须先 PENDING、再租约 CLAIMED、最后写入不可变机器 resolution，错误 consumer/outcome/resolver、过期结果、重复换结果均拒绝。事件 API 不接收同步 callback。
- NEED_CONFIRMATION 只能绑定到 request `WAITING_CONFIRMATION`，NEED_SKILL 只能绑定到 `PLANNING`；重启、租约失效接管、到期取消、双连接并发单 claimant、v6→v7 原位迁移和语义篡改均已覆盖。
- P4.4 与既有 store/outbox 的 22 个定向测试通过，其中新增 7 个 coordination 用例；当前全阶段回归仍留到 P4.QA。
- `P4.4` 修改范围：`src/total_gateway/coordination_events.py`、`store.py`、`tests/test_coordination_events.py`；未修改前端、冻结服务或安装目录。
- `P4.5` 定向检查完成：BackendClient 只向 loopback 7174 的固定 `/api/v1/gateway/internal/execute-ticket` 路由提交规范 JSON，携带服务认证断言；不存在旧 `/chat`、`/inbound` 或透明代理回退，外部主机、重定向、错误 MIME、重复 JSON 键和超大响应均 fail closed。
- 调用前重新验签 ExecutionTicket、校验 CapabilityManifest/epoch/generation/参数摘要并拒绝宿主机路径；票据在网络副作用前写入持久 nonce ledger，重复或换声明重放不会第二次调用后端。未知网络结果和无法绑定的响应固定标记 ambiguous。
- 后端响应必须精确匹配 `tiangong.desktop.backend.v3`，ExecutionResult 与 ticket/request/run/generation/effect/action/version/result payload 摘要逐项绑定；模型文字不是该接口的成功事实。
- P4.5 与既有票据/执行契约的 27 个定向测试通过，包含真实 loopback HTTP 路由证明、无旧路由回退、参数/路径拒绝、签名/Manifest/epoch/generation 门禁、持久防重放及换结果阻断。
- 当前冻结 7174 尚未实现新 `execute-ticket` 路由；这是预期的迁移边界，当前会失败关闭，待后续替换执行入口，不建立不安全兼容旁路。
- `P4.5` 修改范围：`src/total_gateway/backend_client.py`、`tests/test_backend_client.py`；未修改冻结 7174、app、安装目录或用户运行数据。
- `P4.6` 定向检查完成：7184 使用独立 SQLite FactLedger 保存通用不可变 FactRecord，并以关联表把执行事实原子绑定到唯一 result/ticket/effect 批次；模型文本、未验证响应和伪造 BackendExecutionResponse 均没有事实写入口。
- BackendClient 验证标记、ExecutionResult、规范化 result payload、7174 响应摘要、内容寻址 payload 对象及全部 fact_id 被逐项交叉绑定；payload 可从对象库回读并重建完整响应摘要。重复写返回第一观测事实，换 result/ticket/effect 或换证据硬冲突。
- FactLedger 使用 WAL/FULL/STRICT、独立 application/schema、事务回滚、唯一约束和语义健康检查；写入 payload 对象后若事实事务失败，只可能留下不可变未引用对象，不能留下虚假事实或半批次。
- P4.6 与 BackendClient/ObjectStore/Ticket 的 24 个定向测试通过，覆盖模型文字拒绝、伪造响应拒绝、第一事实不可替换、同 effect 冲突、故障回滚、双连接竞争、重启、语义篡改及完整 payload 回读。
- 当前 FactLedger Schema SHA-256 为 `2569BF5B6F218177D231470DC77F8A888A6CD5B9609FD3E277622A6F8FCC55FE`；事实表保持通用，后续 DOCX QC 继续使用同一事实权威，不另建旁路账本。
- `P4.6` 修改范围：`src/total_gateway/fact_ledger.py`、`backend_client.py`、`tests/test_fact_ledger.py` 及 BackendClient 测试辅助；未修改冻结服务、app、安装目录或用户运行数据。
- `P4.7` 定向检查完成：Artifact Gate 只接收 producer ExecutionResult 明确列出的内容寻址 artifact 引用，交叉校验 FactLedger、ticket 租户/账号/会话/工作区/输出上限、对象 kind、声明哈希/大小、文件名、扩展名、MIME、格式策略及双次不可变回读。
- DOCX/ZIP/PDF/PNG/JPEG/JSON/text/CSV 使用显式格式白名单；ZIP 拦截路径穿越、大小写重复、加密、符号链接、CRC、解压上限和压缩比，DOCX 基础门禁要求精确 OOXML 部件、合法 XML 且禁止宏。通过后固定 artifact/revision 身份并登记不可变对象 revision，仍保持 QC_PENDING。
- P4.7 与 FactLedger/ObjectStore 的 17 个定向测试通过，覆盖跨租户、错误 object kind、未报告输出、哈希/大小/MIME/扩展/工作区换绑、伪 ZIP、路径穿越及 PDF/JSON 格式策略。
- `P4.7` 修改范围：`src/total_gateway/artifact_gate.py`、`object_store.py`、`fact_ledger.py`、`tests/test_artifact_gate.py` 及测试票据辅助；未引入宿主机路径或第二对象库。
- `P4.8` 定向检查完成：DOCX QC 独立重读已准入对象，验证 ZIP、Content Types 主文档类型、全部 XML 安全性、根/文档关系 ID 与目标、内部部件存在性、外链策略、document/body、非空段落、可见字符和真实字数；删除修订文字不计入字数。
- QC 结果使用稳定 artifact effect/result/fact 身份并写入同一 FactLedger；PASSED/FAILED 都生成 `model_generated=false` 机器事实和最终 ArtifactManifest，模型文字不能制造通过。相同内容/策略重试返回第一结果，策略变化硬冲突。
- P4.8 相关 34 个定向测试通过：1000 个真实中文字符通过，999 字、错误 Content Types、缺失关系目标和禁止外链形成失败事实；246 字节/伪 DOCX 在 QC 前被拦截；QC fact/batch 故障注入完整回滚。
- `P4.8` 修改范围：`src/total_gateway/docx_qc.py`、`fact_ledger.py`、`tests/test_docx_qc.py`；未调用旧声明但运行时未知的 `qc.docx.delivery_check` 动作，也未修改冻结 7174。
- `P4.9` 定向检查完成：唯一权威 `delivery_kernel.py` 的 `deliverable.package` 现在强制输出为尚不存在的 `.zip`、items 非空且至少含一个文件、输入与输出不同、输出不在输入目录内，并拒绝符号链接、重复归档名和 manifest-only 空包。
- 旧兼容动作先解析全部输入，再写同卷临时文件；临时 ZIP 经 CRC、成员集合和 manifest 完整回读、flush/fsync 后使用 Windows 不覆盖 rename 原子提交，任何失败清理临时文件。历史 `ai_essay.docx` 同名覆盖调用会在打开输出前被拒绝，原 Word 字节不变。
- 新 7184 `DeliveryPackager` 完全使用 ArtifactManifest/FactLedger/内容寻址对象，不接收本地路径；只允许真实 QC PASSED 事实，生成确定性 ZIP，回读后提交为 `delivery_package` 对象，同输入重试复用同一内容对象。
- P4.9 与 Artifact/QC/ObjectStore 的 29 个定向测试通过，覆盖历史同名覆盖、空 items、已存在输出、输入目录包含输出、提交故障清理、伪造 QC manifest、重复 revision、非 zip 名和确定性对象重试。
- `P4.9` 修改范围：正式 `app/backend/tiangong-backend/_internal/omni_body_skill/tools/delivery_kernel.py`、`src/total_gateway/delivery_packager.py`、`tests/test_delivery_packager.py`；没有生成修复副本、bak 文件或旁路源码树。
- `P4.10` 定向检查完成：CompletionRequirements 显式固定 request/run/generation、必须成功的 execution effect、artifact revision、文本要求和渠道门槛；模型不能提交 claimed_complete，只能提供候选文本，候选文本摘要不构成工具或 QC 事实。
- Completion Gate 直接读取 FactLedger/ObjectStore，逐 effect 验证 ExecutionResult、逐 revision 验证 ArtifactManifest/QC 事实/对象回读，并逐 OutboundPart 绑定 DeliveryReceipt；CHANNEL_ACCEPTED 与 DELIVERED 分级，文本成功而附件失败固定 PARTIAL，任何 AMBIGUOUS 固定 RECONCILE_REQUIRED。
- P4.10 与 delivery/state/artifact/fact 的 59 个组合定向测试通过，覆盖本地文件完成、纯文本聊天、模型臆想完成阻断、渠道接受/平台送达分级、附件部分失败、歧义对账、换附件回执及伪造 QC manifest。
- `P4.10` 修改范围：`src/total_gateway/completion_gate.py`、`tests/test_completion_gate.py`；未赋予模型、Skill、7174、7175 或 7176 修改 request 完成状态的权限。
- `P4.QA` 阶段质检通过：233 个单元/契约/故障/并发测试全部通过，40 个 JavaScript 源文件和 `app/src/tests` 内 94 个可读 Python 源文件语法检查通过，`git diff --check` 为 0 错误。
- P4 的网络未知结果、票据重放、双连接事实竞争、事务故障回滚、重启恢复、QC 失败事实、原子打包失败清理、部分投递和歧义对账均已进入全量自动回归；模型文字无法替代执行事实、QC 或渠道回执。
- P4 没有修改 `src/contracts`，两次完整构建的公开契约工件继续逐字节等于 P2.10：manifest 1,624 字节/`27D582B899B4E56A1EE6EDFEAE75D6E6ADCF41B0F82E4FAE139ED3E515FB587C`，OpenAPI 105,719 字节/`59141852E129CD33B246E0F90C18DF54128928E95B76445ACDB845990145D61F`，Schema bundle 117,665 字节/`8A6504B33912BEEE2B5C1145C943EC67E9406ABE1E0A6973AD2AFE58DEB5799E`。
- 两次 P4 wheel 均为 149,493 字节、SHA-256 `697144929738276FA2807033A0E84C0FA69EC1C26132AED35DF3664BA65586FC`；43 个 wheel 条目中没有 tests、app、计划、`__pycache__`、`.pyc` 或 `.pyo`。
- 扩展 QC 表后的最终 FactLedger Schema SHA-256 为 `B285E251F92CB095384BC6C8CD7C1FAF0CF95D0EFDFDF04CBF7BBC8A10093F44`，替代 P4.6 定向阶段记录的中间指纹；Schema version 仍为首次发布前的 v1 完整建库定义。
- P4 源码污染检查结果：可编辑 `src/tests` 中缓存/旁路文件为 0，18 个未跟踪项全部是本阶段计划内源文件或测试且禁用命名为 0；恢复快照原有冻结 `.pyc` 未被误删或改写，阶段 `out` 和临时构建日志已清理。
- P4 修改范围：`src/total_gateway` 的 Life/Skill/coordination/Backend/Fact/Artifact/DOCX/packaging/completion 权威源、`store.py`/`object_store.py`、对应 9 份测试，以及正式可读 `delivery_kernel.py`；未修改冻结 EXE/PYC、活动安装目录、用户 AppData 或公开契约。
- P4 已知迁移边界：冻结 7174 目前尚无受票据 `execute-ticket` 入口，故 BackendClient 会安全拒绝而不会回退旧业务路由；真实微信/飞书渠道仍未切流，属于 P5/P8。
- `P5.1` 定向检查完成：新增 7176 严格配置、单实例锁、通信运行时、渠道适配器协议和 loopback HTTP 服务；运行时只持有 durable Inbox、Delivery Ledger 和渠道注册表，不导入总网关、后端或生命模块。
- 7176 生产配置只接受 `127.0.0.1:7184` 总网关 origin，显式拒绝 `TIANGONG_BACKEND_URL`、`TIANGONG_LIFE_URL`、工作区/生命执行变量、未知通信变量、外部主机、URL 凭据和非生产端口；因此新源码无法恢复 7176→7174/7175 业务直连。
- `/health` 保持 `tiangong.communication.api.v1`，`/ready` 同时校验单实例、Inbox 和 Delivery Ledger；只读 `/api/v1/gateway/links/status` 保留兼容，settings/action 直写固定拒绝并声明只允许总网关控制，旧 inbound/chat/life 路由不存在。
- P5.1 与既有 Inbox/Delivery/包边界共 27 个定向测试通过，覆盖第二轮询器阻断、错误依赖 fail closed、真实 HTTP 契约、旧路由关闭、持久账本重启/并发/故障/歧义语义。
- `P5.1` 修改范围：`src/communication_service` 新增 bootstrap/adapters/runtime/server/`__main__` 权威源、包契约常量及 `tests/test_communication_service.py`；未修改前端、冻结 7176、活动安装目录或公开契约。
- `P5.2` 定向检查完成：依据冻结 3.3.2/当前 7176 的 iLink 字段证据重建 `message_type=1`、`item_list.text_item/voice_item`、`message_id/seq/client_id`、`from_user_id`、`session_id/group_id` 和 `context_token` 文本入站解析，不移植旧一体化管理器。
- 每条消息生成哈希化渠道引用和 P2.5 多租户作用域，先写 durable Inbox/游标再产生 ACK permit；同消息可安全重投总网关并由 request 幂等收敛，换内容/换作用域不能复用身份。
- 自消息、非白名单发送者、关闭的群、未 @ 的群消息、空文本和不支持类型都会持久留痕但 `should_forward=false`；sequence 迟到或碰撞不会覆盖当前会话，也不会污染 context token。
- iLink `context_token` 使用唯一共享 `runtime_security` CurrentUser DPAPI 实现加密，按账号/发送者/会话绑定附加熵；incoming/cache/missing 来源和 token 摘要可审计，明文不进入 envelope、decision JSON 或 SQLite，重启可恢复。
- 一个 `getupdates` 批次使用绑定原始对象和最终平台游标摘要的本地恢复 checkpoint，逐消息持久化后才把最终 `get_updates_buf` 写入游标；两条消息批次测试证明最终游标只在最后一个成员之后可见。
- P5.2 与 Inbox、通信服务、Ticket/DPAPI 共 28 个定向测试通过，覆盖重复、重启、乱序、群/自消息、空游标、语音转写、恶意 item 类型、批次顺序和真实 Windows DPAPI 回归。
- `P5.2` 修改范围：新增 `wechat_inbound.py`、`wechat_session.py`、共享 `runtime_security/dpapi.py` 及测试；`total_gateway/tickets.py` 改为引用共享实现，`pyproject.toml` 显式打包该唯一源，未复制 DPAPI 代码或修改冻结 7176。
- `P5.3` 定向检查完成：微信媒体只允许精确 HTTPS `novac2c.cdn.weixin.qq.com`，禁止 HTTP、IP/相似域名、URL 凭据、非 443 端口和全部 3xx；不启用代理、Cookie、环境认证或自动重定向。
- 下载使用受限分块流式写入隔离 `.cipher.part`，同时校验 Content-Length、协议声明大小、实际密文字节、块对齐和密文上限；AES-128-ECB key 兼容 iLink 的 base64/hex 两种证据格式，解密流单独执行明文上限、声明大小、哈希和 fsync，失败清理两类临时文件。
- P5.3 与 iLink 入站/DPAPI 共 15 个定向测试通过，覆盖真实 AES 往返、多个分块、重定向、截断、声明长度换绑、密文超限、明文超限和 CDN SSRF 变体。
- `P5.4` 定向检查完成：解密文件必须仍位于指定 staging root 且是非链接 `.plain.part`；安全文件名、白名单扩展、精确 MIME、格式魔数/尾标、UTF-8/JSON 和双次流式哈希全部一致后才可调用对象 Sink。
- ZIP/OOXML 检查已上移为唯一共享 `runtime_security/archive.py`，7184 Artifact Gate 同步改用该源；统一阻断路径穿越、大小写重复、加密项、符号链接、CRC、单项/总展开上限、压缩比、XML 实体、缺失 DOCX/XLSX/PPTX 部件和 VBA 宏，普通 ZIP 另禁主动内容扩展。
- AttachmentQuarantineLedger 使用 WAL/FULL/STRICT 持久绑定 `AttachmentRef`，事务实施总量/账号/会话/文件数配额和 TTL；过期引用不再 active。对象 Sink 返回的 revision/hash/size 必须与验收字节完全一致。
- P5.4 与共享 Artifact Gate 共 21 个定向测试通过，覆盖有效中文 DOCX、伪 DOCX、宏、穿越 ZIP、主动脚本 ZIP、危险文件名、MIME 换绑、Sink 换字节、配额和 TTL。
- `P5.3/P5.4` 修改范围：新增 `wechat_media.py`、`wechat_attachment.py`、`attachment_quarantine.py`、共享 `runtime_security/archive.py` 及对应测试，7176 readiness 加入附件账本；未写活动 AppData、未修改冻结服务或安装目录。
- `P5.5` 定向检查完成：按旧冻结协议证据实现固定 `https://ilinkai.weixin.qq.com/ilink/bot/sendmessage`、iLink headers/base_info、`message_type=2` 和 `message_state=2`；正式 HTTP 传输不使用代理、Cookie、自动重定向或可变外部 origin，并限制响应字节、重复 JSON key 和返回结构。
- 每个文本 part 保持原文逐字符无损分段；client ID 由 effect/part/segment 稳定派生，同一段的 context-token 失效和限流重试保持同 ID。`-14` 只在已使用 context token 时清除持久密文并无 token 重试一次；`-2` 使用有界递增退避和账号级速率门。
- 成功只记录 `CHANNEL_ACCEPTED`，不会伪称 `DELIVERED`；HTTP 明确拒绝与网络未知分离，首段明确限流耗尽可 `FAILED_RETRYABLE`，任一已接受分段后的未知/限流以及副作用后缺回执固定进入 `RECONCILE_REQUIRED`，重复调用返回第一次回执且不再发送。
- P5.5 复核发现 P5.2 的哈希化 `conversation_ref` 无法直接还原平台收件人；已将真实 iLink reply target 作为 DPAPI CurrentUser 密文加入同一 WeChat session ledger，只在 account 与 conversation scope 完全一致时解封，明文不进入 Envelope、Decision、回执或 SQLite。当前 session Schema SHA-256 为 `857C0B065C8116D2B342A12A0021F40AE869FE670139D6380EEB9ADA32907F46`。
- 微信文本/文件使用同一不可变渠道策略；P5.7 纳入大小、动态超时、预算和进度约束后的默认策略 SHA-256 为 `D486CBB41E0E95A7B8AC9EA5AED6EF1EFE9C74FF13E67CB2D17CD8AF93116DF7`。Ticket/Plan 必须绑定同一策略摘要，换策略、换 part、artifact 混入或会话作用域不符都会在 claim/网络副作用前拒绝。
- P5.5 与 WeChat 入站、Delivery Ledger 共 24 个定向测试通过，覆盖精确分段、稳定 ID、重复不重发、`-14` 清除重试、`-2` 有界退避、部分分段、401 明确拒绝、网络未知、重启发送边界和账本回归；AST、`git diff --check` 通过，源码缓存已清零。
- `P5.5` 修改范围：新增 `wechat_text_outbound.py` 和对应测试，扩展唯一 `wechat_session.py` 并由 `wechat_inbound.py` 写入加密 reply target；未建立收件人明文旁路文件、未修改冻结服务、安装目录或用户 AppData。
- `P5.6` 定向检查完成：文件发送只接收 DeliveryTicket 内的 Artifact grant 和 `ArtifactContentSource` 字节流，不接受任意宿主机路径；取件时逐块核对 size/SHA-256 与已通过 QC 的 ArtifactManifest，源换字节会在任何外部网络前失败。
- 按冻结协议证据实现随机 16-byte AES key、AES-128-ECB + PKCS#7 流式加密、原文 MD5 协议字段、稳定 filekey、`getuploadurl`、固定 `novac2c.cdn.weixin.qq.com/c2c/upload`、`x-encrypted-param` 和 image/video/voice/file item 映射；AES API 字段保持旧协议要求的 base64(hex ASCII) 兼容格式。
- iLink/CDN 均固定 HTTPS 精确主机、端口和路径；upload URL 必须同时绑定 `encrypted_query_param` 与稳定 filekey，拒绝 HTTP、IP、凭据、相似域、额外 query、跨域/自动重定向。明文和密文只在显式 staging root 的随机 `.part` 中流式处理，成功或失败均清理。
- 原 Delivery Ledger 扩展唯一持久 part-stage 事实链：`FETCHED→ENCRYPTED→UPLOAD_URL_GRANTED→UPLOADED→SEND_STARTED→CHANNEL_ACCEPTED`，各阶段绑定机器证据摘要；没有另建上传旁路账本。P5.6 阶段指纹为 `B893406DF08ACBE3AB8B4E9DAE388D4E109467F8ABFA1231933B4D25B3470261`，P5.7 加入进度事实表后的当前指纹见下。
- getuploadurl/CDN 失败且尚未调用 sendmessage 时可证明收件副作用不存在，返回 `FAILED_RETRYABLE`；sendmessage 网络结果未知或重启位于外部边界时固定 `RECONCILE_REQUIRED` 且不重传；`-14/-2` 沿用 P5.5 的同 client ID 清 token/限流语义。成功仍只称 `CHANNEL_ACCEPTED`。
- P5.6 与文本、入站和 Delivery Ledger 共 32 个定向测试通过，覆盖真实 AES 解密回读、完整阶段链、对象换字节、恶意 upload URL、CDN 失败、context-token 重试、网络未知、重启不重传、重复 effect 和临时清理；AST 与 `git diff --check` 通过。
- `P5.6` 修改范围：新增 `wechat_file_outbound.py` 和对应测试，扩展唯一 `delivery_ledger.py` 的 part-stage 表，并把文本/文件策略收敛为同一 `WechatOutboundPolicy`；未读取本地任意路径、未建立第二份账本、未修改冻结服务或安装目录。
- `P5.7` 定向检查完成：默认单文件产品上限固定 128 MiB，绝对契约上限预留 PKCS#7 尾块；动态上传窗口复用 P2.7 `DynamicTimeoutPolicy`，按 payload bytes、账号观测吞吐和安全系数计算，并始终受 DeliveryTicket `upload_timeout_ms` 上限约束，低于最小安全窗口时在取件/网络前拒绝。
- `WechatTransferBudget` 对 tenant+link account 实施并发文件数和预留字节双预算，不同账号隔离；上传完成后以保守 EWMA 保存进程内观测带宽，后续文件动态调整超时。进程崩溃不会恢复虚假在途预算，真实外部边界仍由 Delivery Ledger 转入歧义对账。
- FETCH/ENCRYPT/UPLOAD 三个阶段加入阈值化持久进度事实，每条绑定 effect/part/phase/completed/total/time/evidence，要求 total 固定、字节和时间严格单调；进度只证明传输字节，不构成 `CHANNEL_ACCEPTED` 或任务完成事实。当前 Delivery Ledger Schema SHA-256 为 `40DB7EC848AF9130E365780B2F24439C04C3A79A33F9768E8D0C95445298E3E6`。
- 正式 CDN HTTP body 使用进度 reader 流式回调，完成字节必须等于密文 Content-Length；动态超时同时用于对象取件、getuploadurl 和 CDN upload。sendmessage 仍由独立短发送窗口控制，避免大文件上传窗口扩大消息副作用的不确定期。
- P5.7 与微信文本/文件/入站/Delivery Ledger 共 37 个定向测试通过，覆盖超时按大小/慢带宽放大、Ticket 裁剪、短窗口拒绝、同账号并发和字节预算、EWMA、进度阈值/单调/持久健康、完整文件发送回归；AST 与 `git diff --check` 通过。
- `P5.7` 修改范围：新增唯一 `wechat_transfer_control.py` 和对应测试，扩展 `WechatOutboundPolicy`、文件管线及 Delivery Ledger 进度事实表；未建立临时进度 JSON、未修改冻结服务、安装目录或用户 AppData。
- `P5.8` 定向检查完成：只接受已有传输层验签/app 校验事实的 `im.message.receive_v1`，并把当前解析对象与已持久原始负载的精确字节数/SHA-256 重新绑定；任何换事件、换 app 或换平台租户都在 Inbox 前 fail closed。
- 飞书文本和 post 富文本经过有界解析；链接只保留可见文字而不泄露 href，SDK 嵌套 mention ID 与富文本 at 均能识别 bot。thread/root/chat 精确决定会话作用域，p2p/群策略、自消息、非白名单和群未 @ 均先持久后抑制转发。
- Inbox 首次持久与游标推进先于 ACK permit；重复事件精确返回首次 permit，旧事件迟到不会回滚当前回复路由。chat/message/root/parent/thread/sender 只保存在 CurrentUser DPAPI 密文中，且只能按 tenant+account+conversation scope 精确解封。
- 7176 runtime 已将飞书路由账本纳入 readiness 和统一关闭顺序；当前 Feishu Route Schema SHA-256 为 `A64023796C67D183B240C9AA11052C9DC2570BB5811A881DB234496C56E55BF7`。
- P5.8 与通信运行时/Inbox 共 26 个定向测试通过，覆盖首次落盘、重复、原始负载换绑、富文本、嵌套 mention、群策略、自消息、错 app/租户、不支持附件留痕、迟到重复和跨租户隔离；AST 与 `git diff --check` 通过。
- `P5.8` 修改范围：新增唯一 `feishu_inbound.py`/`feishu_route.py` 及对应测试，扩展 Inbox 精确读取与 7176 runtime；未写活动 AppData、未修改冻结服务或安装目录。
- `P5.9` 定向检查完成：image/file 事件及 post 内嵌图片先在 Inbox 留痕，再将 message_id、image_key/file_key、文件名和源消息引用写入同一 Feishu Route Ledger 的 CurrentUser DPAPI 密文表；重启后仍可恢复，明文 key/消息 ID 不进入 Envelope 或 SQLite 页。
- 资源下载只允许精确 `https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{key}?type=image|file`，禁止 HTTP、IP、相似域、URL 凭据、非 443、额外 query 和重定向；Bearer token 只放 Authorization header，不进 URL/账本/错误文本。
- 下载在隔离 staging root 中流式执行，交叉校验 Content-Length、实际字节、上限、Content-Type 和 SHA-256；403 缺 Scope、429、3xx、5xx、截断和超限均给出独立机器错误且清理临时文件。不同 tenant/account/conversation scope 在任何网络前被拒绝。
- 下载结果复用 P5.4 唯一文件安全门，继续强制安全文件名、扩展/MIME/魔数、ZIP/OOXML/宏、主动内容、配额和 TTL，通过后才产生完整租户作用域 `AttachmentRef`；没有建第二套文件验收实现。
- 加入受保护资源表后，当前 Feishu Route Ledger Schema SHA-256 为 `345E07FC6D752CB22B5B1E010057EDBBCE602A362C5344F42417112156ED511C`，替代 P5.8 只含回复路由表的中间指纹。
- P5.9 与飞书入站/共享附件/通信运行时共 28 个定向测试通过，覆盖资源 API 精确路由、SSRF 变体、文件/图片/post 内嵌图片、跨租户阻断、缺 Scope、重定向、超限、MIME/魔数、DPAPI 明文泄漏检查和临时清理；AST 与 `git diff --check` 通过。
- `P5.9` 修改范围：新增唯一 `feishu_attachment.py` 及对应测试，扩展 `feishu_inbound.py`/`feishu_route.py`，仅为共享验收门增加 MIME 查询入口；未建第二数据库、未修改冻结服务、安装目录或活动 AppData。
- `P5.10` 定向检查完成：飞书 `tenant_access_token` 使用 tenant+account 隔离缓存、提前刷新和条件变量单飞；8 个并发冷启请求仅触发 1 次凭据网络刷新，401/平台 token 失效仅强制刷新一次，旧请求不能清除已换新 token；App Secret 不持久、不进 URL/回执/错误。
- 文本和卡片由已签发的 FeishuOutboundPolicy 决定，不接受未绑定的临时渲染参数；图片/文件仅从 DeliveryTicket 内 Artifact grant 通过 `ArtifactContentSource` 取件，流式核对字节和 SHA-256 后分别调用官方 images/files API，不接受宿主机任意路径。
- 发送使用受 DPAPI 保护的 chat/message/thread 路由；有 reply ref 时必须与解封 message_id 的哈希完全一致，话题中使用 `reply_in_thread`，无 reply 时才对 chat_id 新发。recipient scope 固定为当前会话收件人，换 tenant/account/conversation/reply 在 token/网络前拒绝。
- 根据飞书 2026-04-10 官方消息/回复契约，每个 part 使用由 effect_id+part_id 稳定派生、最长 50 字符的 `uuid`；同 uuid 在一小时内由平台去重。因此 401/429 明确拒绝后可保持同 uuid 重试，未知网络结果仍保守进入 `RECONCILE_REQUIRED` 并禁止盲重发。
- 429 优先遵循平台 Retry-After，再受有界退避和 Ticket deadline 约束；文件上传、消息发送和渠道接受分阶段写入唯一 Delivery Ledger。成功回执必须有飞书 message_id 和原始响应摘要，且只声称 `CHANNEL_ACCEPTED`。
- 为同时表达微信 AES 和飞书直传，Delivery Ledger 增加通用 `READY_TO_UPLOAD` 阶段，原微信 `FETCHED→ENCRYPTED→UPLOAD_URL_GRANTED→UPLOADED` 链保持不变。当前 Delivery Ledger Schema SHA-256 为 `F3093BB2C9AFF62DE1C514603C81E09C0A426615826B05E5C83D50DFC3AEA1F4`，Feishu 默认出站策略 SHA-256 为 `180585FE5D5E5967A472628FF72EA7D92BC96CB8A6A8949F872F9423F73FA05F`。
- P5.10 最终相关回归 63 个测试全部通过，包含 token 单飞、文本/卡片/图片/文件、线程回复、平台 uuid、401、429、未知结果、重启不重发、收件人换绑、阶段链、微信文本/文件和 7176 readiness；AST 与 `git diff --check` 通过。
- `P5.10` 修改范围：新增唯一 `feishu_outbound.py` 及对应测试，扩展 Delivery Ledger 通用阶段机；未复制 token/文件安全实现，未修改冻结服务、安装目录或活动 AppData。
- `P5.11` 定向检查完成：7176 唯一公开投递入口 `VerifiedDeliveryDispatcher` 只接收完整 `DeliveryTicket + OutboundPlan`；payload-only、desktop/test 渠道或不完整类型在 handler 前拒绝。验签使用唯一共享 `runtime_security/ticket_verification.py`，总网关不再保存第二份 Ed25519 验证实现。
- Dispatcher 依次强制 TrustBundle/kid/issuer/audience/purpose/时窗/epoch/ComponentManifest、generation floor、完整 plan/part/artifact/收件会话/回复目标/策略摘要；handler 映射固定为微信和飞书，handler 只收到已授权 payload+plan，凭证、策略实例和内容对象源由控制面闭包持有，不接受宿主机路径参数。
- Delivery Ledger 在同一 `BEGIN IMMEDIATE` 事务内写入带自摘要的 `VerifiedDeliveryTicketFact` 和 effect `CLAIMED`；任一步故障整体回滚。ticket/effect/delivery 唯一约束、持久 generation fence、精确重放、身份换绑、语义健康检查和并发消费均 fail closed。
- 微信文本、微信文件和飞书发送器已删除自行创建未验证 claim 的路径，只能通过 `require_verified_delivery()` 读取此前原子落盘的票据权威；直接调用 transport service 的未验签 payload 会在任何渠道网络请求前拒绝。
- 当前 Delivery Ledger Schema SHA-256 为 `382AC1ADCFB6D239DC6817923C452460411DB6268B96C80D932A0400A2E7D8D4`，替代 P5.10 的中间指纹；仍使用同一 `communication-delivery.sqlite3`，未创建第二份 nonce/effect 数据库。
- P5 通信完整定向子集 126 个测试通过，覆盖真 Ed25519、错签名/kid/epoch/Manifest/plan/generation、payload-only、未验签 transport、事务故障回滚、崩溃恢复、8 线程重放、语义篡改及微信/飞书文本与文件回归；`git diff --check` 通过。
- `P5.11` 修改范围：新增唯一 `delivery_dispatcher.py` 和对应测试，扩展 `delivery_ledger.py` 的验签事实表并收紧三个渠道发送器，共享 Ticket verifier 从总网关移入 `runtime_security`；未修改冻结服务、安装目录、活动 AppData 或公开契约模型。
- `P5.QA` 阶段质检通过：311 个单元/契约/安全/并发/故障测试全部通过；40 个 JavaScript 源文件语法通过，`app/src/tests` 共 127 个可读 Python 源文件 AST 通过，`git diff --check` 为 0 错误。
- P5 的 Inbox 先落盘、批次游标、微信/飞书跨租户隔离、SSRF/重定向、AES/OOXML、配额/TTL、token 单飞、429、稳定渠道 ID、部分成功、网络歧义、重启恢复、完整 DeliveryTicket 验签、事务回滚和 8 线程重放均已进入全量自动回归。
- P5 未修改公开 `src/contracts`；契约兼容子集 50 个测试再次通过。两次完整构建的 4 个工件逐文件一致：manifest 1,624 字节/`27D582B899B4E56A1EE6EDFEAE75D6E6ADCF41B0F82E4FAE139ED3E515FB587C`，OpenAPI 105,719 字节/`59141852E129CD33B246E0F90C18DF54128928E95B76445ACDB845990145D61F`，Schema bundle 117,665 字节/`8A6504B33912BEEE2B5C1145C943EC67E9406ABE1E0A6973AD2AFE58DEB5799E`。
- 两次 P5 wheel 均为 228,437 字节、SHA-256 `33FE01A981D06429F90DA531EA83457ED4A7C8859EACAED0D4695C1049E03297`；65 个 wheel 条目包含通信/总网关/共享安全唯一源，不含 tests、计划、缓存、pyc/pyo 或备份文件，7176 源码对 `total_gateway` 的反向导入为 0。
- P5 源码污染检查结果：禁用命名 0、`__pycache__` 0、可编辑源码 bytecode 0、阶段 `out` 0；冻结 EXE/PYC、安装目录、活动 AppData、原 ZIP 和渠道真实账号均未修改或切流。
- P5 已知迁移边界：正式 HTTP 投递路由、信任清单装载、渠道 handler 控制面组装和真实账号切流属于 P7/P8；当前 7176 不公开不安全发送端点，故未配置这些事实时只能安全拒绝，不能误发。
- P5 阶段源码提交：`62e3b3981b1741904794c28091764092768bd433`（`Complete P5 communication adapters and delivery gate`），提交后工作树干净。
- `P6.1` 定向检查完成：建立线程安全、严格脚本化的微信/飞书协议模拟器，统一支持分块/截断资源流、文本发送、上传授权、密文上传、飞书 token、附件上传和消息发送；每个调用只记录规范证据摘要和凭证摘要，不保存 token、App Secret 或用户原文。
- 脱敏协议样本固定覆盖微信文本/语音/图片/视频/文件、context 失效/限流，以及飞书文本/富文本/图片/文件、接受/429/403；全部使用 synthetic ID 或 redacted 占位，样本 SHA-256 为 `A38BB9549DADBB60EF130BFF2D516F9F3B207B7F86B942ED44908BE8662CA93E`。
- 唯一安全文件语料以确定性代码生成 14 类样本，包括真实 1000 字 DOCX、246B/1KB 假 DOCX、宏、ZIP 穿越/主动内容/压缩比、真/伪 PNG、MP4、SILK、JSON、坏 UTF-8 和声明超限；语义清单 SHA-256 为 `44E6E166CC99CEFFA23A41D1DB7F4EC68B6534362C9B72D145FD8C0D821AF122`。
- P6.1 的 10 个定向测试通过，共享附件门对 13 个适用文件字节样本逐项执行，5 个安全样本准入、8 个恶意样本拒绝；声明超限由后续媒体流矩阵使用。未连接真实微信/飞书、未写活动 AppData。
- `P6.2` 覆盖审计完成：状态机覆盖合法/非法迁移、owner/revision/generation/迟到/取消；Gateway Store、Request Journal、Outbox、Effect Ledger 覆盖幂等键、CAS、事务回滚、双连接竞争和重启恢复。
- Ticket 覆盖真实 Ed25519/DPAPI、错签名/kid/purpose/audience/epoch/Manifest/时窗、持久 nonce 和 7176 原子消费；Skill 覆盖系统推荐、模型 route/list/get/read、显式放弃、缺动作、索引/Markdown/CapabilityManifest 漂移。
- FactLedger/Artifact/QC/Completion 覆盖模型文字无事实权、第一事实不可替换、对象/租户/工作区换绑、MIME/魔数/ZIP/OOXML、1000/999 字 DOCX、246B 假产物、部分投递和歧义对账。聚合 91 个核心不变量测试全部通过，未发现需要在 P6.2 新建旁路实现的覆盖空白。
- `P6.3` 边界矩阵复核完成：Inbox 在记录/游标/ACK 同事务插入点故障时全回滚；Journal 在队列写入故障时 request/actor 全回滚；7174 Effect 在结果写入故障或重启时保留 started 并进入歧义；Outbox 在意图写入故障时状态事件与副作用意图全回滚。
- DeliveryTicket verification/effect claim 任一步失败整体回滚；微信 CDN 失败证明 sendmessage 未开始并可重试，sendmessage 未知或外部边界重启禁止重传；飞书上传明确拒绝不发送给收件人，message send 未知进入对账且不重放。10 个边界故障用例全部通过。
- `P6.4` 自动重复矩阵完成：同一持久 Delivery Ledger 连续执行 120 个唯一 ticket/effect，固定分为 40 轮成功后重复、40 轮副作用前断网、40 轮副作用后断网歧义；模拟器实际注入 80 次网络断开。
- 120 轮中成功重复只产生 40 个外部接受效果，副作用前断网全部保持 FAILED_RETRYABLE，副作用后断网全部保持 RECONCILE_REQUIRED 且重复票据不重置；完整语义健康检查与关闭重开后复核均通过。
- `P6.5` 矩阵发现并关闭真实接线缺口：原图片/视频/文件下载与安全门已存在，但 `WechatTextInboundProcessor` 只读取 text/voice transcript，媒体 item 会被误判为空消息。现已将 image/voice media/video/file 严格解析、AES 下载和共享附件门接入 `InboundEnvelope.attachments`。
- 新入站媒体 ingestor 只接受受限 CDN 引用/AES/大小字段，图片按真实魔数命名，视频/SILK 固定安全扩展，文件名/MIME 走唯一附件策略；下载、解密、对象写入或验收任一步失败只形成 `ATTACHMENT_REJECTED` 留痕，不转发给 7184。
- Inbox 在任何媒体下载前先按 inbound id 查询已持久事件；精确重复直接复用原 AttachmentRef 和 ACK，不再二次下载。无 attachment handler 时显式 `ATTACHMENT_HANDLER_UNAVAILABLE`，不会把文件消息伪装为文本成功。
- 微信 17 个相关测试通过：文本、语音转写、真实 PNG/SILK/MP4/1000 字 DOCX 均形成作用域 AttachmentRef；伪 PNG、声明超限、半下载和 127.0.0.1 SSRF 均持久留痕但不转发，SSRF 在 transport 前拦截，临时 `.part` 为 0。
- `P6.6` 飞书矩阵完成：重复/旧事件不会回滚 route，原始事件摘要/大小换绑拒绝；post/thread/root/mention 与嵌套 SDK mention 通过，href 不进入文本；图片、文件和 post 内嵌图片均登记受保护资源并通过共享附件门。
- 跨租户 Scope 在网络前拒绝；资源 403/429/503/重定向/截断/超限分别给出稳定机器错误并清理 staging；出站 401 单次刷新、429 Retry-After、上传拒绝、未知 send 对账和收件/线程换绑均通过。
- token 并发冷启动从 8 路提高到 32 路压力，仍严格只有 1 次真实刷新且 32 个调用得到同一 token。飞书相关聚合 29 个测试全部通过，未发现新的接线缺口。
- `P6.7` Word E2E 完成：新增总网关唯一 `VerifiedArtifactContentSource`，在向 7176 暴露任何字节前逐项复核 Delivery grant、完整 ArtifactManifest、全部 QC 机器事实、对象作用域、大小和 SHA-256；7176 仍只依赖窄 `ArtifactContentSource` 协议，没有反向导入总网关。
- 真实安全语料中的 1000 字 DOCX 已贯通执行成功事实→Artifact Gate→DOCX QC PASSED→不可变 artifact revision→受验对象打开→AES 加密→微信授权/上传/sendmessage 模拟回执，最终只声明 `CHANNEL_ACCEPTED`，完整阶段链为 `FETCHED→ENCRYPTED→UPLOAD_URL_GRANTED→UPLOADED→SEND_STARTED→CHANNEL_ACCEPTED`。
- 246B 与 1KB 假 DOCX 均先被 Artifact Gate 拦截；额外构造表面 `PASSED` 的伪 manifest 后，受验内容源仍因缺少匹配的 FactLedger QC 事实在对象取件/微信网络前拒绝，两个用例的上传授权、CDN 和 sendmessage 调用均为 0，临时 staging 为 0。
- P6.7 新增 `src/total_gateway/artifact_content.py` 与 `tests/test_word_delivery_e2e.py`；文件 SHA-256 分别为 `CF9B4A29A3CD96140F35B378ED08E4F155C6C51C34D97F34A95FD2E8DDCBBAAF`、`C95F8ABD8EDE1C2F759B1D7C30B162A087A7D1CD00D43BDF27FF130955980EFD`。
- P6.7 的 Artifact/QC/微信文件相邻定向回归共 21 个测试全部通过，新增文件 AST 与 `git diff --check` 通过；按大阶段纪律未运行全量测试、双构建或阶段污染清理。
- `P6.8` 产品回归关闭四个接线缺口：工作区选择改走 Electron `workspace:setRoot`，在监督停止三服务后切换根目录并启动验证，失败时恢复旧目录和旧服务，成功后才持久化；身体面板拆分生命名称与用户称呼/工作，移除 Soul 编辑入口，暂态保存失败最多额外重试两次。
- 本地工件打开按钮现在以主进程 `shell.openPath` 的真实结果为准，失败明确显示“打开失败”，不再把调用过等同于已打开；微信和 Skill 主动申请既有入口继续通过定向回归，未在 P6 提前切换正式 7184 流量。
- P6.8 新增桌面产品静态回归，连同 Skill、微信矩阵和 Word E2E 共 15 个测试通过；40 个 JavaScript/MJS 文件及 P6.8 修改文件语法、`git diff --check` 通过。关键文件 SHA-256：`app/main.js`=`4D45AE44D4275D2A700CE0C2023637E43740C5B3CCF08BFB3979E922505DD219`，`app/preload.js`=`B61F40B9946D6F8781A96EEFD4C0E9EF1E7F90694C92E3AA6B35B124219A3CA6`，`http-runtime.mjs`=`2B8FBEB19ABEAE64E82E55F986AFDD329B36DD28466D462317F86DA7271798D0`，`body-panel.mjs`=`816EB32106A75E12A7C0687F1874AAA1F3946EB33B7D1C9B27DD6E1049E6E7D9`，`message-renderer.mjs`=`45671AFB42BD80ED4C6A2D3551E4681C320038C840B5ECE01FBBA20A4717D88D`，`test_desktop_product_regression.py`=`CA68D88533DB68B4CA392E77411BF24DDF7390D6E009B90AE4E91B060C5A0048`。
- 产品基线 EXE 已成功启动为“天工造物 v3.0 完整版 · 起源”，但 Windows 自动化在读取窗口状态时返回 `SetIsBorderRequired failed: 不支持此接口 (0x80004002)`；浏览器回退运行时初始化又返回 `Cannot redefine property: process`。因此本轮不伪造可视点击证据，真实窗口截图与 IPC 点击保留为当前验证环境限制，代码路径由确定性回归覆盖。
- `P6.QA` 整阶段质检通过：严格只运行一次全量，325 个单元/契约/安全/并发/故障测试全部通过，40 个 JavaScript/MJS 源文件语法通过，`src/tests` 共 111 个 Python 源文件 AST 通过；契约兼容、120 轮重复/断网/歧义矩阵、事务回滚、崩溃恢复与并发消费均包含在本次全量中。
- 两次 `build.ps1 -SkipChecks` 均成功且原始构建输出一致：contract manifest 1,624 字节/`27D582B899B4E56A1EE6EDFEAE75D6E6ADCF41B0F82E4FAE139ED3E515FB587C`，OpenAPI 105,719 字节/`59141852E129CD33B246E0F90C18DF54128928E95B76445ACDB845990145D61F`，Schema bundle 117,665 字节/`8A6504B33912BEEE2B5C1145C943EC67E9406ABE1E0A6973AD2AFE58DEB5799E`，wheel 231,846 字节/`C3C418620715BCDF0C22404C4AAADE341A3044156DADF572AB4D71C5FD048103`。
- 双构建后的 wheel 共 66 个条目，包含 `total_gateway/artifact_content.py`，不含 tests、缓存、pyc/pyo 或备份/补丁文件。自动汇总辅助因当前 PowerShell/.NET 不提供 `Path.GetRelativePath` 而产生无效 `count=0`，该汇总未作为证据，也未追加第三次构建；两次构建各自的原始成功输出、相同 wheel 完整哈希、相同契约摘要/长度/哈希前缀和第二次落盘完整哈希共同留证。
- P6 源码污染检查完成：禁用命名 0；清理 `src/tests` 5 个 `__pycache__` 目录共 70 项后，可编辑源码缓存/bytecode 为 0；阶段 `out` 已在核对仅含 4 个工件后安全删除。恢复包内冻结 backend/life runtime 的预置 bytecode 未修改，`git diff --check` 为 0 错误。
- `P7.1` 新增可独立执行的 `ServiceSupervisor`，以 phase 启动 7174/7175→7184→7176，分别记录 running 与 ready；同一服务并发启动/重启合并，连续 3 次健康失败才退避重启，ready 失败不误判为进程崩溃。四服务状态通过只读 IPC 暴露，主进程不再只有 7174 单点 watchdog。
- Electron 现在正式查找并启动 7184 EXE 或唯一源码入口，固定 production `127.0.0.1:7184` 和隔离 gateway 状态根，清除未知 `TIANGONG_GATEWAY_*` 环境变量；健康响应必须与本地 `gateway.epoch.json` 的 instance/epoch 完全一致才可接管遗留监听器，未知端口占用 fail closed。
- 四服务退出按 7176→7184→7175→7174 反向 drain；退出发生在启动途中时等待当前启动完成且禁止后续 phase，再停止全部服务。Electron `before-quit` 先 `preventDefault` 等待有界 drain，7184 在 Windows 使用独立进程组与 `SIGBREAK`，超时才强制清理；Python server 对 SIGINT/SIGTERM/SIGBREAK 在独立线程调用 shutdown 并在 finally 关闭数据库、对象库和单实例 lease。
- 修复三类子进程旧 exit 事件误清新句柄及父进程日志文件描述符泄漏：exit 只清理自身 child，spawn 完成立即关闭父进程复制的日志 fd。工作区切换现在纳入 7184 并复用同一 supervisor 的 drain/start/rollback，不建立第二套服务生命周期。
- P7.1 定向回归 13 个全部通过：监督器并发合并、ready/health 分离、连续失败重启、启动中退出、反向 drain、四服务静态接线、P6 产品回归、7184 配置/单实例/HTTP 边界；真实 7184 子进程在 Windows CTRL_BREAK 后正常退出，第二次使用同一状态根启动时 gateway epoch 从 1 增至 2。修改文件语法、`git diff --check` 通过，可编辑源码缓存为 0。
- P7.1 关键 SHA-256：`app/main.js`=`1BDD246AA9E1541ACF8BF1AABFF82A013434CAA67BA3E4F19CAA1B469A7F6654`，`app/preload.js`=`3C655AA5E3B89E450AC6467AE0E0E1E8C7432906E25D83C990F9A0854A1512AE`，`app/service-supervisor.js`=`0570381659CA72EE219F5500B67CC2E295D59FF69BA44B34677F0D0C1571892C`，`src/total_gateway/server.py`=`A0DF89B96B226BD32CE03F5229152611F58C034AD7A3A02B1D2093740062D237`，`tests/test_desktop_service_supervisor.py`=`E8B2F7B21C75B0024FCE08A5F2571341B170DDAC7492F2EBE1C003EC416F01F9`。
- `P7.2` 完成单入口切流：preload 的 backend/life/communication 兼容别名与新增 gateway API 全部固定为 `127.0.0.1:7184`，frontend kernel 不再按生命/通信路径选择 7175/7176，HTTP runtime 默认和桥接均只返回 7184；状态面板端口统一显示 7184。
- 主界面聊天的 JSON 与原 SSE 两条入口都改为 7184 `/api/v1/gateway/internal/inbound`，移除 `/chat` 回退和 `v3_direct` 标识。Electron 不再回退加载含旧直连逻辑的历史 HTML；正式 `frontend-v2/index.html` 缺失时明确失败。portable workspace 只信启动环境，不再由主进程 POST 7174 工作区 API。
- 7184 新增唯一 `desktop_api.py` 迁移控制面，显式列出当前产品实际使用的 39 个 method/path；不接受任意路径、任意查询、外部上游、重定向、非 JSON、重复键、非有限数字、超大请求/响应或错误桌面令牌。三个上游固定 exact loopback 7174/7175/7176，renderer 无法直接取得这些业务地址；file-origin CORS 只允许 `Origin: null` 与审查过的 header/method。
- 桌面 inbound 在跨越 7174 迁移边界前先按会话/消息生成严格 InboundEnvelope 并登记唯一 Request Journal `req_*`；同进程精确重复复用首份不可变响应，不再次调用上游。前端 presentation request ID 到 gateway request ID 的映射同步用于 run status/control，7174 收到的 request_id 与 7184 权威 ID 一致。
- 该白名单桥是 P8.3 关闭 7174 无票据执行与旧 7176 控制口之前的显式迁移边界，不是长期透明代理；未开放 `/chat` 或任意 pass-through。新 7176 对 links settings/action 的总网关原生控制实现及重启后 alias 持久恢复仍属于 P8 切流/恢复验收，不在 P7.2 冒充完成。
- P7.1+P7.2 相邻定向回归 26 个全部通过，另一次前端/桌面网关聚合 11 个通过；覆盖三服务精确路由、鉴权/CORS/JSON/查询 fail closed、桌面幂等、run alias、旧 `/chat` 拒绝、四服务监督、7184 单实例与 HTTP 边界。全部修改 JS/MJS 与 Python AST、`git diff --check` 通过，未运行 P7 全量。
- P7.2 关键 SHA-256：`app/main.js`=`76806CC2859B439B8A3A2364E6D629D60DE5CCCB3454F30A802EFCACF89170C2`，`app/preload.js`=`171ADC8BC0C27FB8BA6C656EB3B3C541A8BF69312EA63384A363183C17827D6D`，`frontend-kernel.mjs`=`4E5AF5171954D328FB17AFA36E48BE87BF74FDBFB5D9BF0B2FA15C5481D0A306`，`http-runtime.mjs`=`12CA987C22BE90EF910516CBE32FB125D4A674DAE2F16AC051F3CCE994E635E0`，`desktop_api.py`=`BF037F46E2CF48AC03C2C24E27BB6BE24A0F6AD5BD31B0764D85512A232E31DC`，`server.py`=`7764C2C3CE9CB5E3AAD60E5B3427EF04A402FE5448B99A6EDC8387439456322F`，`test_desktop_gateway_api.py`=`35AFAFFF326E41FFAFA1C7B79717B8969BE9B8927DD3CA07F030AB1BDB3D60B4`，`test_frontend_gateway_routing.py`=`080CB5FBA6E3D1F9D143851CE74062DC033DD80FC8CF504B1AA926F97CB8EA8F`。
- `P7.3` 新增 7184 唯一证据投影 `ui_projection.py`：按 request 精确读取经过摘要验证的 StateSnapshot，只聚合当前 run/generation，并复用既有 `aggregate_request_status` 权威语义；执行、产物 QC、投递分别输出状态、来源和证据可信标记，投影本身带稳定 Schema 与自摘要。
- 多实体投影采用最低保证：一个附件 `DELIVERED`、另一个仅 `CHANNEL_ACCEPTED` 时整体只显示渠道接受；一个已接受、一个失败时显示 `PARTIAL`；任一当前代歧义进入 `RECONCILE_REQUIRED` 并明确禁止盲目重发。旧 generation 歧义不会污染当前代。
- 缺少新网关状态事实时，7174 状态只标为 `LEGACY_OBSERVATION/evidence_verified=false`；没有 Artifact Snapshot 固定显示 `PENDING/ABSENT`，不会因旧后端文字或执行成功推断“无需产物”或 QC 通过。
- 桌面 `/api/v1/run/status` 在 presentation request ID 与唯一 `req_*` 双向绑定后附加 `gateway_projection`；直接使用 gateway request ID 时同样返回投影。Store/投影异常使 readiness 缓存失效并返回 503，不退回伪造状态。
- 前端新增严格 `gateway-ui-projection.mjs`，把投影转换为固定三步 `执行 / 产物 QC / 投递`；会话工作卡新增运行中“网关事实状态”三行卡片，渠道接受与送达分开，歧义显示“禁止重发，等待网关对账”。`blocked/timeout` 现在会阻断最终成功收口，不会被后续完成事件洗成成功。
- P7.3 及相邻边界最终 29 个定向测试全部通过：投影/多附件最低保证/旧代隔离/缺证据、前端 Node 映射和 blocked 最终态、桌面 7184 鉴权/别名/幂等、单入口路由、产品 UI 回归、Gateway Store 并发/篡改回归。4 个相关 JS/MJS 语法、5 个 Python 文件 AST、`git diff --check` 通过；仅清理本轮 3 个 `__pycache__`，可编辑缓存为 0，未运行 P7 全量或双构建。
- P7.3 关键 SHA-256：`ui_projection.py`=`0DA87012E5BE1E7C4E7088AA979F7EF9583ABDB2FFB5115775AC7E87E1643D51`，`gateway-ui-projection.mjs`=`DD99019EF3944A301450B7B6122098C61E466EC06C6EA7620F9DAC242EA2DE74`，`desktop_api.py`=`9EE46EE9086DCB23C21CF1D37C7D7967E3FD4790C4EDA17604CB5C56C8057A8B`，`http-runtime.mjs`=`5071925614CA43C0DD681893CE9C3581DDF3AA25139D6875FDC4B9BB86B7BC7C`，`state.mjs`=`E2F47F1C7AD70E64EBECE4CE5B2B2C7F70D10FCCC319A5CE16709FEF8832D603`，`conversation-panel.mjs`=`76CE2CB87B53345DEC0FF96200BAEA1EA054D182BCCA90D7874E50A5527CE478`，`test_gateway_ui_projection.py`=`47A90A842B43411AAAC2AA5FBA900126F433A8994C91F112A0CE5BA561B7EAD0`。
- `P7.4` 完成 FactLedger 权威 Artifact Card：7184 现在持久打开唯一 `facts.sqlite3` 并纳入 readiness，按 request/current run/generation 读取摘要验证后的 QC batch；只有 QC PASSED、对象作用域/大小/哈希与安全格式全部一致的 ArtifactManifest 才能投影为无路径卡片。卡片固定 request/run/generation/revision/manifest/content/QC 摘要和自摘要，旧 generation、换 manifest、换 card 或可执行扩展均在物化前拒绝。
- 7184 新增两个原生桌面路由：GET `/api/v1/artifacts` 只返回当前代无路径卡片，POST `/api/v1/artifacts/open` 只接受主进程专用一次启动随机令牌；该令牌不暴露给 renderer，CORS 预检也不允许其 header。打开时从 QC FactLedger 和内容寻址对象重新验收字节，再写入受限 `gateway/artifact-open/<revision>/<manifest>/` 缓存，不接受模型文字或任意宿主机路径。
- Electron 主进程对返回卡片、run/generation、缓存根、非链接真实文件、大小和 SHA-256 再验证后才调用 `shell.openPath`；只有 OS 返回空错误才向 UI 返回 `ok=true`，本地路径不回传 renderer。前端在 JSON/SSE 最终响应后查询卡片，持久保存结构化 Artifact Card，单独显示文件名、QC、revision 和摘要；旧模型文字路径仍仅为兼容附件，不会升级为网关 Artifact Card。
- P7.4 及相邻边界收口定向回归 52 个全部通过：真实 1000 字 DOCX→QC→卡片→物化、错误代/manifest/card、缓存同字节篡改、原生 API 列表/打开/主进程令牌/CORS、FactLedger readiness 篡改、renderer 路径剥离/最终卡片合并、三线状态、39 条迁移路由、产品 UI、四服务监督和 Artifact/QC 回归。6 个相关 JS/MJS 语法、9 个 Python 文件 AST、`git diff --check` 通过；清理本轮 1 个测试缓存目录后可编辑缓存为 0，未运行 P7 全量或双构建。
- P7.4 关键 SHA-256：`artifact_open.py`=`9DD302246FA0CF201FCA94F93B120BA66C1E394F440EFB6609626179697F63CB`，`artifact_content.py`=`4D31264A4A614F23BA37CCFF332FB69DC99876E5A4451A9182B56618DFD87B00`，`fact_ledger.py`=`FE0C5DE18C11C24390920FBD8AE6058A13621393BACA3EE6D6621A69EA699821`，`runtime.py`=`54ABF81701589E8E125CE949496AD5CE7F343BEDA62C1A09D04BF8856D6FEE02`，`desktop_api.py`=`A2AFD37C05A30FDF57E69B0C2C53936C227185DA584FAAADBC7DD255675BF00E`，`app/main.js`=`749F6F4E53EFB7F6AACDF785A8F798F3D0B499940848059C90B0914C40287830`，`http-runtime.mjs`=`5D168390C9D73F0DB62CB4BA35EE014A1694A4891FC9E4812EA54CE5D23CC496`，`state.mjs`=`8625BE28C91B36835F4B89A8F93F907F8C9673E2D3EA1332F3BF9B3C05F25C09`，`conversation-panel.mjs`=`311B09D6D522168CE2F53DF24AC5B06287BD3870BDD201813B523062AD8DC9AD`，`test_desktop_artifact_card.py`=`8E49BBACBF96702BE244BDD6EE829A0FB690D646EB1167C82785C352807A0546`。
- P7.4 已知迁移边界：当前冻结 7174 兼容入口不会生成新的 FactLedger ArtifactManifest，因此旧链路不会被 UI 误标为真实卡片；卡片只在票据/QC 原生链路写入机器事实后出现，7174 执行入口替换仍属于 P8。既有 Windows 自动化限制使本轮没有伪造可视点击证据，确定性回归覆盖主进程唯一打开路径与 OS 返回判定。
- `P7.5` 完成唯一发布权威：新增严格 `ReleaseManifest`，内嵌并交叉校验唯一 ComponentManifest，固定 product/version/build/channel/time、5 个组件文件、契约工件文件与内部自摘要、Schema bundle、动作注册表、CapabilityManifest、Skill index、31-Skill 语义目录、ReleasePolicy、8 份源输入和 3 棵可编辑源树；外层与内层清单均有规范 JSON 自摘要。
- 生成器从正式 `app/`、`src/`、冻结 7174/7175 文件及现有动作/Skill 权威直接计算，不保存第二份组件清单；`build.ps1` 只向 `out/release/release-manifest.json` 写一个发布文件，并继续把契约三件套与 wheel 分开。验证器限制大小、UTF-8、重复键、NaN/Infinity、严格类型、规范编码、自摘要和当前源码逐字节匹配，非空输出目录不覆盖。
- 当前清单如实使用 `development` 与 `production_claim=false`：7174/7175 绑定冻结 EXE，桌面绑定 `app/main.js`，7176/7184 绑定当前源码入口及完整源树；在 P9 生成最终 EXE/app.asar 前不会提前声明 production。5 个组件摘要均已固定，桌面主入口仍为 `749F6F4E53EFB7F6AACDF785A8F798F3D0B499940848059C90B0914C40287830`。
- P7.5 定向契约/兼容/readiness/delivery 回归 41 个全部通过，3 个修改 Python 文件 AST 与 `git diff --check` 通过；证明新增根契约后共有 49 类 Schema，移除 ReleaseManifest 可精确恢复 P2.10 Schema 基线，重复键/非有限数/非规范 JSON/自摘要篡改/源权威漂移均 fail closed。按大阶段纪律未运行 P7 全量或构建，`out` 不存在、可编辑缓存为 0。
- 同一源码连续生成与写入验证逐字节一致且输出严格只有一个 6,588 字节文件：Release 自摘要 `639D2E879DD6BF22FCB447BB0B3FA6FDB0B9AC83CFED87D18CDBB17FEE2A5C29`，文件 SHA-256 `010BEF4D58814CB2D6E25A1FB66ECE727D92DDEE444A7C5A310EAD4020F3D6F5`，内嵌 ComponentManifest `6737DE61031FB6307C12DB38607581D9E37BFC32761828A59CF0D5E6A7FB7B3F`，Schema `909F06082379828D109A7D5537EFB2C7C8ECD5997B521D4B73B9774237A58287`，动作/能力/Skill 索引/Skill 目录摘要继续分别为 `3798276F...`、`A32B9312...`、`77C99B71...`、`95FED0D1...`。
- 三棵源树摘要：communication 43 文件/`5F22D207D12E4C02643319743A589E2AA4005E0C6FF8AB4AA2BA924FABE5749D`，desktop 84 文件/`2DC6CC68A57D247BF4FEC1CF86FCDBCBC136F9685FF0871CB1517A5188D2B743`，gateway 47 文件/`D46EB97F7F678391EBF2C5FEDEFD9CC8B37CBBD7FBB359706BE93D9BDCAC5614`。
- P7.5 关键 SHA-256：`src/contracts/release.py`=`8F6D68A4B2A0BFCF942F30DC03DDF55B45E28E8460E9377EBDCAFDC263D67E20`，`src/total_gateway/release_manifest.py`=`E035EC71AD2EAFBA1954C7E86EF197EA536AC450EB3141ACD390552BBC342C96`，`scripts/build.ps1`=`5D62C82D58B0F632A92086C3FE1B91100D3EB23F5CF922044294842F6EAB3FA8`，`tests/test_release_manifest.py`=`6AA1078E247F8E67049A9E631514DBCC054E7BB2B09618347D61A61C80D5D420`。
- `P7.QA` 整阶段质检通过：严格只运行一次全量，356 个单元/契约/安全/并发/故障测试全部通过，42 个 JavaScript/MJS 源文件语法通过，`src/tests` 共 122 个 Python 源文件 AST 通过；契约兼容、120 轮重复/断网/歧义、Ticket 重放、事务回滚、Fact/Artifact 篡改、四服务并发监督、CTRL_BREAK 退出恢复和真实 Artifact Card 均包含在本次全量中。
- 两次 `build.ps1 -SkipChecks` 均成功，5 个构建工件逐路径、大小和 SHA-256 完全一致：contract manifest 1,642 字节/`97CDC522BE90E48FBB5B8A9A34C8D80BB04517344A6320A84921FBDBE9EF1D82`，OpenAPI 109,900 字节/`7BD1352A5ECE1EEB4B9674E7FF7672947B8A2780799C0643E451949C7F2493B2`，Schema bundle 124,743 字节/`F690D425F66A8FCC3E2831435816AB595144BC726BAFDD4D30EEF6B26FC2CA10`，Release Manifest 6,588 字节/`010BEF4D58814CB2D6E25A1FB66ECE727D92DDEE444A7C5A310EAD4020F3D6F5`，wheel 255,047 字节/`BD81A80EF874218635C74C8990F74FA7A766C35B7201837BF6D3196BDBA9A6F0`。
- 构建后从当前源码重新验证契约三件套和唯一 Release Manifest：49 个根 Schema，契约内部 manifest 摘要 `3757599F0ECAC556E7E61250F47DA4217094A701622AEC2371026DB0501880B6`，Schema 语义摘要 `909F06082379828D109A7D5537EFB2C7C8ECD5997B521D4B73B9774237A58287`，发布清单含 5 个组件且仍为非 production。wheel 共 71 个条目，包含 Release/Artifact/UI projection/communication dispatcher 权威源，tests、计划、缓存、bytecode、备份和补丁条目为 0。
- P7 污染检查完成：可编辑 `src/tests` 缓存/bytecode 0，禁用命名 0，Git 变化中的 bytecode 0，构建临时目录已清理，`git diff --check` 为 0；`out` 在核对恰好 5 个工件后按绝对路径和叶名双重校验安全删除。冻结 EXE/PYC、活动安装目录、用户 AppData 和真实渠道均未修改或切流。
- `P8.1` 完成严格 observe-only 影子契约：ShadowIngressCopy 绑定 durable Inbox ingress、ACK permit、完整 InboundEnvelope 和自摘要；legacy/candidate 两侧决策分别绑定来源组件/实例/原决策摘要、分类、是否转发和附件集合；ShadowComparison 只输出 MATCH/MISMATCH/等待某侧及差异字段。所有层次把 `request_creation_permitted`、`effects_permitted` 和 `model_generated` 固定为 false，改成 true 无法通过契约。
- Gateway Store 原位升级到 v8，只新增 `shadow_ingress` 与 `shadow_decision` 观测表；一个 batch 在同一事务内写入副本和决策，重复精确幂等、换副本/换同侧决策硬冲突，语义健康检查逐行重算规范 JSON 和摘要。决策插入故障使副本同时回滚，两个独立连接并发重放严格只有一份首记录，P7 v7 数据库可无损迁移；当前 Gateway Store Schema SHA-256 为 `7FAB716C039934DA78CD45F35D9ED81B57E054061878C77E8952035D8C92A8F6`。
- 7184 新增仅 loopback 私有 `/api/v1/migration/shadow/observations` 与 comparison 查询：请求必须使用 Electron 每次启动随机生成、只传给 7176/7184 的 48-byte token；renderer/preload 不可见，Origin/CORS、错误 token、重复键、NaN、非规范 JSON、超限、错误方法和换身份均在持久化前拒绝。响应固定声明 OBSERVE_ONLY、`request_created=false`、`effects_permitted=false`。
- 7176 新增窄 `CommunicationShadowMirror`，只依赖共享 contracts 和 durable ChannelAckPermit，通过精确 `127.0.0.1` HTTP origin 发送规范 JSON，不跟随重定向、不使用代理；可直接把 WeChat 自摘要 decision 或 Feishu 规范 decision 摘要投影为 candidate，legacy 观察可随后补入并得到相同/差异报告。Runtime 只暴露 observe-only 客户端，不因启用影子模式获得执行或投递权限。
- P8.1 相关聚合定向回归 77 个全部通过，其中 9 个影子专用测试覆盖匹配/差异、真实 7176→7184 HTTP、GET 比较、浏览器/CORS/鉴权、非规范输入、事务回滚、并发重放、重启恢复、v7→v8 迁移和语义篡改；11 个相关 Python 文件 AST、`app/main.js` 语法、`git diff --check` 通过。Request Journal、Outbox、Effect Ledger 在影子流量前后均严格为 0，未调用真实微信/飞书、工具或出站。
- P8.1 新增 4 个根契约后共 53 类 Schema，当前语义 SHA-256 `FE832C7B72EC854682802452552A1263D896E34405B5358C9285D8CFFD2E0D68`；移除 4 个 Shadow 根可精确恢复 P7 `909F0608...`，再移除 Release/Readiness 可逐级恢复 P2.10/P2.8，兼容门禁确认是纯增量。按大阶段纪律未运行全量或构建，`out` 不存在、可编辑缓存为 0。
- P8.1 关键 SHA-256：`contracts/shadow.py`=`0D675A711D45A9605AB4773A23B8C09EB573274CB0BABC3E960B370155A8F7D4`，`total_gateway/store.py`=`29A8C1CDBCCFA4ADD6716D675650D185ED552E7A2C726C09BA7155330892F1B6`，`total_gateway/shadow_api.py`=`776B6633140EBFD1856E2435FD72232A7992ABEE97533A67112090356DC039CE`，`communication_service/shadow_mirror.py`=`3C3CF1FD3EC16307E7BBF9A0269942836AB92E7515AFE2B43EBA8A2462A0FC8B`，`app/main.js`=`1F233B5EA5B52C699BCA40619F3CD9D97618DBDABE7C80B9C8C008A77C02015F`，`test_shadow_migration.py`=`A0CDA109A86BBBF869BEF73C062253BAFC645FA58CE2EA7D76C7C4A64E1F373F`。
- `P8.2` 完成单一 epoch 渠道切换契约：`ChannelCutoverSnapshot` 固定 `DRAINING→DRAINED→CANDIDATE_ACTIVE` 三态，`ChannelDrainEvidence` 只接受 poller/sender 已停、poll/send 在途为 0、Inbox 未 ACK 为 0、Delivery 未决为 0 的机器事实；`ChannelOwnershipLease` 精确绑定渠道/租户/账号、当前 gateway=migration epoch、候选 7176 实例、ComponentManifest、drain 摘要及 `POLL+SEND` 两种操作，最长 60 秒并以旧租约摘要形成不可跳跃续租链。
- Gateway Store 原位升级到 v9，在唯一 `gateway.sqlite3` 内新增 cutover、drain evidence 和 lease history 三表；begin/drain/activate/renew 均使用 `BEGIN IMMEDIATE`、规范 JSON、自摘要、CAS、唯一活动租约和完整语义重算，插入故障全回滚、并发激活严格一份首记录。当前 Store Schema SHA-256 为 `EDFCE16A888AAF0E7DCD3A83846D70CA6EA78B748F1B503E5D61897FFEDF10E5`。
- 7184 Runtime 已收回 epoch 参数，所有切换写入和读取自动绑定当前 `InstanceEpochLease.gateway_epoch`；跨重启时，新 epoch 必须等待旧活动租约过期再加 5 秒时钟偏差窗，且更高 epoch cutover 一旦持久出现，旧 epoch 不得再激活或续租。v6/v7/v8 数据库均可按迁移链原位到 v9，旧数据不丢失。
- 7176 新增 `ChannelAuthorityGate` 与 `CommunicationDrainInspector`：默认无租约即拒绝；旧实例、错误 ComponentManifest、错误 epoch、错误账号、过期或断链租约均不能取得权限。Gate 在租约校验与在途登记同一锁内执行，drain 先封闭新操作再读取真实 in-flight 计数；Inbox/Delivery 分别从既有唯一 SQLite 账本计算作用域摘要、未 ACK、发送在途和未决投递，不接受模型布尔声明。
- 入站 poll 边界通过 `AdapterRegistry.operation_authority(..., POLL)`，出站 `VerifiedDeliveryDispatcher` 在任何 Ticket claim/handler/渠道网络之前持有 `SEND` operation lease；测试证明候选实例可同时取得精确 POLL/SEND，旧实例两者均为 0，重启后的空 Gate fail closed，drain 中已有操作退出前不能生成零在途证据。
- P8.2 最终相邻定向回归 133 个全部通过，覆盖契约兼容、v8→v9、v6/v7 迁移链、双连接并发激活、事务故障回滚、重启恢复、旧/新 epoch 安全窗、语义篡改、真实 Inbox/Delivery drain 摘要、微信/飞书入出站和 Dispatcher 先租约后 claim；21 个相关 Python 文件 AST、`git diff --check` 通过。本轮 5 个测试缓存目录已清理，`out` 不存在，未运行 P8 全量、构建或阶段污染扫描。
- P8.2 新增 3 个根契约后共 56 类 Schema，当前语义 SHA-256 `5A749EA51E903E9E5CF571943316E8C314CDD543D95E456CB5CC41C3F6057493`；移除 3 个 Channel 根精确恢复 P8.1 `FE832C7B...`，再移除 Shadow/Release/Readiness 可逐级恢复 P7/P2 基线。关键 SHA-256：`contracts/cutover.py`=`9F9A11A202D111F48E5BE7597B8A193BB36ACBBAF3ED29B7C2BE2EA54E476830`，`total_gateway/store.py`=`A043FC31864D8A2ADFB94053C39A9F275FD000327DAF1B5A10407C35328C012A`，`communication_service/channel_authority.py`=`C34C38BC8560EB5EA4AB0185C3E88598146B123361510B0955DFF87F4976D9FF`，`communication_service/drain.py`=`46F6B463C2A71FEA4E077279B4FABCA0053E23E727420FCFEB89A489BCE3E903`，`test_channel_cutover.py`=`DCCD3C950B05D9E77B7B73B5FE7E30BD738EA1AB311B76456CAA4D6EBE5C3BB5`。
- P8.2 已知迁移边界：当前冻结旧 7176 尚未采用新 Gate，7184/7176 的正式切换控制 API 与 supervisor drain 编排属于 P8.3/P8.5；因此本小项证明新源码边界与持久 epoch 不会双写，但未声称真实账号已经切流。
- `P8.3` 完成 7174 无票据入口的可达面关闭：7184 的 desktop inbound、run control 和旧 conversation event sink 已从 7174 转发白名单移入原生失败关闭面，严格解析后固定返回 `LEGACY_BUSINESS_ROUTE_CLOSED`，不创建 Journal、不触达上游；原 `/api/v1/run/status` 只保留只读兼容观察，BackendClient 仍只有受票据 `execute-ticket` 路由且无 `/chat`/`inbound` 回退。
- Electron renderer、7174、7175 改用三套独立的每次启动随机令牌；7184 对 backend/life 按上游选用内部令牌，对 7176 不转发 renderer 令牌。renderer/preload 只得到 desktop→7184 令牌，无法用它直调 7174/7175；四端口同时固定为 exact `127.0.0.1:7174/7175/7176/7184`，不再接受环境改写业务 origin。
- Electron 7176 启动只接受新构建 EXE 或唯一 `src/communication_service` 模块入口，按 SHA-256 明确拒绝旧冻结 `613F569E...11F6`，删除 `communication_server.py` 启动回退。7176 子进程环境由 Windows 最小系统变量白名单重新构造，只注入 7184 origin、隔离状态根、影子令牌和 Python 运行参数，不继承 backend/life URL、desktop/artifact token、工作区或 provider 凭据。
- 新 7176 `/health`/`/ready` 增加 `delivery_ticket_required=true` 与 `legacy_business_dependencies_permitted=false`，Electron 同时核对 component、transport-only authority、7184 origin、影子无副作用和这两个门禁标记，因此不能把仅契约名相同的旧服务误当候选。三份历史 HTML 的 7174 `/chat` 直连/失败回退也已删除，只保留 7184 inbound；正式主窗口继续只加载 `frontend-v2`。
- P8.3 最终聚合定向回归 91 个全部通过，覆盖 BackendClient 只受票据路由、Ticket/nonce、Delivery Dispatcher、P8.2 channel lease、7176 严格配置/HTTP、三服务独立令牌、无票据三路由零上游、旧前端无 `/chat`、四服务 supervisor、Artifact/UI projection、7184 HTTP/bootstrap、产品静态回归和 P8.1 shadow。另修复 Windows 对未知 POST 返回 405 前未排空有界请求体导致客户端 `WSAECONNABORTED` 的确定性问题；7 个相关 Python 文件 AST、`app/main.js` 语法和 `git diff --check` 通过，可编辑缓存为 0。
- P8.3 只读核对当前活动安装实例：PID 18252 仍是 `D:\天工造物 v3.0 完整版\resources\communication-service\tiangong-communication-service.exe`，SHA-256 精确为旧基线 `613F569EE889B1F365B4678F02A2F2DC12507A52858A91D6B8A553880E2D11F6`，无令牌 `/health` 返回 401。本小项没有停止、修改或切换该活动实例；正式 drain/单写者切换仍归 P8.5。
- P8.3 关键 SHA-256：`app/main.js`=`F5F92464A2B873583E75E6F7ADC3D40331C275BFA50A7F34C07B01D4D31C7ADB`，`desktop_api.py`=`D2C4D9F00BB6BE5226635E1DF08626487D307ED064D8BFB3DC3D1FB43894380B`，`server.py`=`F689F72CE839C692B891BFA002C7E53302697244532251C434597B52D826BBAF`，`communication runtime.py`=`202008451D8876E7AADDFF9414CE3058BC0C2373B6716CF0E6C9E848403A0F55`，三份历史 HTML 分别为 `2FE2690F...2D2A79`、`00FC59E1...7F3468`、`9BC1CC0A...B1E642`。
- P8.3 已知边界：冻结 7174 本体仍没有 `execute-ticket` 源码入口，故新业务执行目前安全失败关闭；本步骤关闭的是所有产品可达的无票据调用面，没有伪称桌面聊天或真实渠道闭环已经恢复。新 7176 EXE 尚未构建进安装目录，活动旧 7176 的实际 drain/迁移/切换不在 P8.3 提前执行。
- `P8.4` 准入盘点：新 `CommunicationRuntime` 当前只创建 durable Inbox/Delivery/会话/附件账本和空 `AdapterRegistry`，没有微信 getupdates poller、飞书长连接/webhook worker、凭据控制面或生产入站转发；7184 目前只开放 observe-only shadow ingress，尚无生产 InboundEnvelope→Request/Session Actor→Life/Skill/ExecutionTicket→Outbox/DeliveryTicket 装配。因此现有源码不能把真实渠道消息伪装成闭环成功。
- 真实账号只读盘点未发现可用测试凭据：当前进程环境没有 WECHAT/WEIXIN/ILINK/FEISHU/LARK/COMMUNICATION 凭据变量；旧 `message_gateway/config.json` 仅显示微信/飞书启用占位但二者 `credentialed=false`，QR/auth URL 为空。当前 7174/7175/7176 和 Electron 旧 `127.0.0.2:7174` 代理仍是活动安装实例，7184 未监听；本轮未读取或输出任何密钥值，未向任何真实联系人发送消息，未停止活动进程。
- P8.4 保持 `IN_PROGRESS`：继续前需要用户明确指定微信与飞书测试账号/测试收件会话，并通过受保护 UI/配置提供凭据；同时必须先补齐候选 7176 worker 与 7184 生产编排装配。缺少这些事实时不得为了“跑通”而回退旧 7176 或 7174 无票据入口。
- `P8.4` 微信错误现场已精确定位：活动旧 7176 的登录、轮询、Inbox 提交和微信回发均成功，7175 也已为同一 `gw_8e428f969be444cd8f724f8d031587cb` 生成上下文并授权；断点位于旧 7176→Electron `127.0.0.2:7174` 临时代理→7174 入口，连接被重置且没有创建 backend run。截图中的通用错误文字是旧 `GatewayLinkManager` 在 `dispatch_result.ok=false` 时发送的确定性兜底，不是微信登录失败。本轮未修改活动安装目录或旧代理。
- `P8.4` 候选生产入站子边界完成：`ChannelAckPermit` 上移为共享契约，新增 `ProductionInboundSubmission/Acceptance`；7176 只有在 WeChat/Feishu decision 允许转发、durable permit 有效且当前 P8.2 `POLL` 租约仍属于本实例时，才可用每次 Electron 启动独立的内部 token 向 7184 固定 loopback 路由提交。7184 重新校验规范 JSON、token、Origin、epoch、租户/账号、候选实例和活动租约摘要，然后只调用 Request Journal/Session Actor 登记唯一请求；响应固定 `effects_started=false`、`completion_claimed=false`，不回退 7174、旧代理或无票据入口。
- P8.4 生产入站最终定向检查分两组通过：56 个契约/Inbox/影子/切换/通信/桌面监督测试和 38 个发布/网关 HTTP/桌面 API/Delivery/微信/飞书相邻测试均为 0 失败。真实 7176→7184 HTTP 用例证明同一消息重复提交只产生 1 个 request，Outbox 与 Effect Ledger 均为 0；错误 token、浏览器 Origin、非规范 JSON、错误租约均在 Journal 前拒绝。按大阶段纪律未运行 P8 全量、双构建或阶段污染扫描。
- P8.4 新增 3 个根契约后共 59 类 Schema，语义 SHA-256 `EC90544EFACF6F4434A17B42E9015A7365434FAE8D449445C1F6AD54FF05E194`；移除 `ChannelAckPermit/ProductionInboundSubmission/ProductionInboundAcceptance` 可精确恢复 P8.2 `5A749EA5...`，兼容门禁确认是纯增量。关键 SHA-256：`contracts/ingress.py`=`2A02455D65F55D1CC73FD2BCDD61FA57A374A5F97ABEBB121AD603C5C14030AB`，`communication_service/production_ingress.py`=`8C52380AB63B214EED0B3A40708F454707F1C6C18F07F869470AE04E242BEBF7`，`total_gateway/channel_ingress.py`=`70A2A3EC502BB1ED94B417A30AEEEF0D3BFB70CA0DA7FA4547EBD37A14570F14`，`test_production_channel_ingress.py`=`194514E4DE5A1659F8C19127F89EDEF1BE9C0C778F36C347376C1A239E8513D2`。
- P8.4 ACTIVE request 原子领取子边界完成：Gateway Store 原位升级到 v10，新增 `request_inbound_payload`，把规范化完整 InboundEnvelope 与 Request Journal/Session Actor 在同一事务落盘；worker 现在能取得用户文本和 AttachmentRef，而不是只有 request_id/摘要。v9 既有请求迁移时明确标为 `LEGACY_UNAVAILABLE`，不会猜测执行；只有相同 ingress 身份、摘要和完整 Envelope 精确重投后才可原子补回。
- 新增 `ActiveRequestActivator`，只枚举拥有完整载荷且处于 Session Actor `ACTIVE` 的请求；领取时在同一 `BEGIN IMMEDIATE` 内固定初始 run identity、generation=1、确定性 generation lease/fence 和唯一 request authority StateSnapshot。QUEUED、跨会话、换 epoch/owner、过期 lease 或半初始化状态全部在副作用前拒绝；重复领取、关闭重开和两个独立连接并发领取均收敛为首份事实。此边界不创建 execution/artifact/delivery 占位实体，避免在真实意图尚未固定时伪造 PENDING 事实，也不写 Outbox/Effect Ledger。
- P8.4 本子步骤最终定向检查分两组通过：35 个 ACTIVE/Journal/生产 ingress/P8.2 迁移切换用例与 42 个 Store/generation/UI/readiness/发布清单/包边界用例，共 77 个、0 失败；覆盖 v9→v10 旧载荷 fail closed 与精确补回、入站载荷插入故障全回滚、载荷篡改、领取事务故障、重启、双连接并发和 heartbeat。相关 7 个 Python 文件 AST、`git diff --check` 通过；按大阶段纪律未运行 P8 全量、双构建或阶段污染扫描。
- 当前 Gateway Store Schema v10 SHA-256 为 `C1756FDC8A9EA43BC1633D748C7AB6596C6CB19BFC4946DF7EBBE8CC888827DB`。关键 SHA-256：`total_gateway/store.py`=`E9C33220022137D62AF98115523F363686AE3D9490ED479474786BADAB358AC2`，`total_gateway/active_requests.py`=`80AA1C15814A68AD17B9EB307B23BE567BEC3B638E219F5214B9A97B1045A67F`，`total_gateway/runtime.py`=`687B3DCE5A443CA1A7BD4556239E76217E096DA77F8BF7CFAE629B574DD8A3EA`，`test_active_request_activation.py`=`521876FB9539DA9EEB5B9ECA8811233C38C309AD340942ED962D7E4B589D0A2A`。
- P8.4 当前剩余边界：生产请求已经能安全进入 7184、持久恢复完整 Envelope，并由唯一 worker 原子领取为受 generation fence 保护的 request authority；下一步仍需持久装配 LifeSnapshot→CapabilityManifest/Skill→ExecutionTicket→受票据 7174→Fact/Outbox→DeliveryTicket。候选 7176 的真实微信 getupdates/飞书 worker、受保护凭据控制面和 lease 安装/续租控制 API 也尚待补齐，因此当前仍不声称真实对话闭环已恢复。
- 当前唯一进行中事项：`P8.4`，使用真实测试账号验证微信/飞书文本和文件双向闭环及对端打开；继续只跑真实渠道所需的定向检查，P8 全阶段完成后统一全量、双构建和污染检查。
- `P8.4` 前端→总网关真实链路恢复：原任务最后一个确定性失败不是 renderer 发送或 7184 接单，而是一次性 LifeContext grant 把冻结 7175 返回的有限浮点展示字段交给 `canonical_sha256`；签名契约拒绝 float 后，7184 连接被重置，已越过 7174 副作用边界的 effect 只能保守落为 `AMBIGUOUS / compat.backend.outcome_ambiguous`，前端再压缩成 `gatewayrequestfailed`。兼容桥现改为对未签名冻结响应使用确定性 legacy JSON wire digest，Fact/Ticket/契约的禁 float 规则未放宽；含 `energy=0.5` 的回归已固化。
- 修复后的 P8.4 相邻定向回归 25 个全部通过，相关 Python AST 与 `git diff --check` 通过。应用自身 drain 后 7184 升至 epoch 30；从真实 renderer DOM 发送一个全新请求，7.857 秒后前端返回“前端链路正常”。桥计数 `2/2` 成功、编排 `processed_count=1` 且无最后错误，Gateway Effect Ledger 对该唯一新 request 固定为 `SUCCEEDED`，7174 创建对应 run，排除 UI 假成功与旧 effect 重放。
- P8.4 渠道剩余事实保持不夸大：候选 7176 当前只有 1 个 DPAPI 迁移的微信测试账号，既有 2 条真实微信文本已先持久后 ACK 并进入 7184，但它们发生在执行编排接线完成前，只到 `request_generation=RELEASED`，没有 Effect/Outbox/Delivery；飞书凭据、路由和资源记录仍为 0。必须用新的真实微信/飞书文本与文件分别验证执行、回发及对端打开后，才可把 P8.4 标为 DONE；P8.5/P8.6 仍不得提前完成。
- P8.4 前端控制面路由漂移已修复：7184 精确新增当前界面实际使用、且冻结 7175 已实现的 10 条生命事务路由，覆盖身份创建/绑定/切换/解绑、信箱已读/删除、主动消息确认、升级确认/取消和能力回滚；没有把 7175 的全部 59 条内部接口暴露给 renderer。当前前端生命客户端与 7184 的 20 条生命白名单完全一致，7175 路由表缺失为 0。
- 本次前端映射定向回归 34 个测试与 48 个子测试全部通过；真实运行态 53/53 条前端业务/原生控制路由 CORS 预检通过，15 条只读链路从当前 Electron 经 7184 返回成功。逐页扫描对话、运行、知识、技能、身体、设置及生命页 12 个子标签共 19 个页面状态，所有可用按钮均有直接监听、事件委托或表单提交处理，未绑定按钮为 0。
- 真实删除验收使用用户已明确尝试删除的 `生命已建立` 信箱消息：Electron 实际左键事件经 7184 转发到 7175 后，页面错误为空、信箱行数为 0、目标消息不再存在。该证据只证明桌面前端控制面链路恢复；真实微信/飞书文本与文件双向闭环仍未完成，P8.4 继续保持 `IN_PROGRESS`。

## 5. 证据登记模板

每完成一个步骤，追加以下内容：

```text
步骤：
状态：DONE/BLOCKED
修改文件：
未修改范围：
执行测试：
测试结果：
关键哈希：
已知风险：
下一步骤：
```

## 6. 原始源码到达后需要核对的既有修改

核对基准：用户提供的最初 `天工造物 v3.0 完整版安装包_3.0.0.exe`。原始与当前 `app.asar` 都包含 534 个实体文件；只有以下 5 个内部源文件不同，无新增、无删除。

| 源文件 | 原始 SHA-256 | 当前 SHA-256 | 处理决定 |
|---|---|---|---|
| `main.js` | `D3027E8DD256ECA79A74A525451AA9E297FA11B42BB69D03DFC76F733F0B80AE` | `3F3FB7F32D94B4E6F425F894B77998F168D500D719304FDF788E78AC1221C75B` | 拆分迁移；工作区/单实例部分保留，临时通信代理禁止照搬 |
| `preload.js` | `0C2FC5516D48C232F50B2A8CE8DD4A4FAC2F4DB4FDF5619A67129EDAB453B49A` | `A601154A908CD498C78E32C21BE018F251280EB93FE258AECCAC94CD3170CAE9` | 迁移 `workspace:setRoot` IPC，之后与 7184 契约统一 |
| `frontend-v2/renderer/core/message-renderer.mjs` | `E2F64535831D5682BFF98F0ED49F49314753DEEB897EBBCF921651E32946280B` | `441CF61858BE432462BCFE70AC3A769D2F0A4908F6F3B556E7A8DC5897359243` | 仅作兼容参考；最终使用结构化 Artifact Card 和真实 openPath |
| `frontend-v2/renderer/plugins/body-panel.mjs` | `122582AC7653D6AE15FFF86A6B8EA4784FC4AF95435DFD46019B18B28DA45479` | `3DF723E53E1477E2A4A68DD3A8B943DA6F6DA7C42079A8CE22C4F538BD031551` | 迁移人物/称呼/头像/声音 UI，保留 Soul 面板移除；重试提示改接 7184 ready 状态 |
| `frontend-v2/renderer/runtime/http-runtime.mjs` | `9F48989BEFB6BCEB5A117D44841F4967C42BE3B2228E50D9EE2F866C28162B19` | `5975B3C76AD7357760612AAEE221E3046959B7BF065158093D37F84AD63C8F7B` | 迁移工作区切换与用户/生命名字分离；业务请求最终统一走 7184 |

### 五个文件中的具体功能变化

1. `message-renderer.mjs`
   - 把行内代码中的绝对 Windows 路径或 `explorer "..."` 识别为可点击本地链接。
   - 这只是兼容修复，不是最终结构化“一键打开”方案。
2. `body-panel.mjs`
   - “角色管理”改为“人物与称呼”，区分大模型/生命和用户。
   - 支持名字、头像、声音、用户称呼、工作/身份。
   - 移除 Soul 文本编辑区。
   - 身体设置保存遇到瞬时后端错误时重试两次并显示明确状态。
3. `http-runtime.mjs`
   - 工作区保存从后端 `/api/v1/workspace/settings` 改为 Electron IPC。
   - 区分 `personaName` 和 `userName/userCallsign`，修正用户名/生命名混用。
4. `preload.js`
   - 新增 `setWorkspaceRoot()` 桥接到 `workspace:setRoot`。
5. `main.js`
   - 新增工作区偏好持久化、目录验证、服务重启和失败回滚。
   - 新增通信服务启动 Promise，减少并发重复启动。
   - 新增 `127.0.0.2:7174` 临时通信代理，剥离通信服务预编译的生命上下文并改写请求 ID。
   - 上述临时代理仍未完整解决微信链路，必须由正式 7184 总网关替代，不得直接合并到原始源码。

### 已证明没有改动的正式组件

以下文件与最初安装包逐 SHA-256 相同：

- 7174 `tiangong-backend.exe`
- 7175 `tiangong-life-service.exe`
- 7176 `tiangong-communication-service.exe`
- `release.json`
- `omni_body_skill/model_adapters/core.py`
- `omni_body_skill/tools/skill_router.py`
- `omni_body_skill/tools/delivery_kernel.py`
- `omni_body_skill/tools/omni_body_tool.py`
- 两份冻结 `v3/body_settings.pyc`

因此，Skill 主动查询、Word/ZIP 交付门禁和正式微信总网关目前都还没有进入产品源码。

### 工作区新增但不属于产品源码的文件

- `GATEWAY_REFACTOR_PLAN.md`：总网关实施与压缩恢复计划，需保留在源码仓库但从安装包排除。

### 临时分析产物，不得合并到源码

- `%TEMP%/tiangong-v3-wechat-analysis-20260714/`
- `%TEMP%/tiangong-v3-wechat-runtime-test/`
- `%TEMP%/tiangong-v3-app-wechat-fixed.asar`
- `%TEMP%/tiangong-v3-communication-probe.pyz`
- `%TEMP%/tiangong-v300-source-compare/`

这些目录/文件仅用于取证、运行模拟和原始对照。原始源码核对完成后统一清理。
