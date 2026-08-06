# 停止 / 续接 / 卡死判定 — 总设计计划

日期：2026-08-06
状态：方案稿，待用户确认后实施
关联：`docs/stop-continue-design-decisions-20260806.md`（1、2 已确认；3、4 本计划定稿）

## 0. 现状盘点（复用清单，避免重复造轮子）

实现前先确认以下既有事实，全部“扩展”而非“新建”：

| 已有机制 | 位置 | 与本次方案的关系 |
|---|---|---|
| 运行态持久化（run_state JSON，temp+rename 原子写） | `v3/zongdiaodu.py` `_simple_chain_run_state_*` | #3 的基础，扩展字段而非另建存储 |
| 启动清理（running → interrupted） | `v3/zongdiaodu.py` `_cleanup_stale_run_states` | #3 启动对账已存在，补“原因 + 事件” |
| 继续决策回合与 9 次上限 | `_SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES = 9` | #1 数值已到位，改“只数无进展”语义 |
| 状态级卡死监视器（指纹/回环/意图重复） | `_SimpleChainProgressMonitor` | 已实现，作为 #1 的进展依据 |
| 强制停止自然收尾 | `_simple_chain_natural_closeout_payload` / `_natural_closeout` | #2/#4 的模型侧收尾入口 |
| 结构化步骤日志 | `duihua_qiaojie.py` `RunControlHandle.step` / `RunControlManager.step` | #4 事件流的同源挂载点（不另建日志） |
| 终态透传 | `desktop_api.py` `simple_chain_status`；`actions.mjs` | #4 前端状态区的数据入口 |
| 前端不再追加系统停止文本 | `actions.mjs`（流式/非流式路径） | #4 已完成一半，剩模板兜底文本迁移 |
| 终态文本 origin 标记（FE-02） | `duihua_qiaojie.py` 响应组装处 | 已对 incomplete/failed 标记 `origin=template`，扩展覆盖 force_stopped/interrupted |
| VRM 选中标记 key | `avatar-service.mjs` `AVATAR_SELECTED_MODEL_FLAG_KEY` | #3 中 VRM 刷新回退的排查起点 |

## 1. 目标与不变量

目标：刷新/重启后运行态可恢复；失败可解释且不再空转；聊天框只有自然语言；卡死判定可复现。

不变量（谁都不能违反）：

1. **单写者**：简单链执行进程是 run_state 唯一写者；前端、watchdog 只读。
2. **状态为真，事件为投影**：不一致时以状态文件为准；事件流可在启动时补齐。
3. **恢复只到回合边界，不自动续跑**：后端重启后 active 一律 `interrupted`，用户确认后再开新回合。
4. **持久化是尽力而为**：写失败降级标记，不阻塞任务。
5. **事件集合封闭**：一次状态迁移恰好一个事件；新状态必须新增事件。
6. **系统文本不进消息列表**：只出现在状态区。

## 2. 分项设计

### 2.1 项目 1：继续决策上限（9 次“无进展”继续）

- 保留 `_SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES = 9`，改为**进展感知**计数：
  - 每次进入继续决策前计算 `_simple_chain_progress_fingerprint(...)`；
  - 与上次决策时指纹不同 → `final_gap_retry_count` 清零（合法长任务不受限）；
  - 指纹不变且模型选择继续 → +1；达到 9 → `force_stopped`（fail-closed，沿用现有分支）。
- 全局仍受 `_SIMPLE_CHAIN_MAX_LOOP_TURNS=180` 与墙钟 5400s 约束，不会因“有进展就不限”而失控。
- 影响面：`zongdiaodu.py` 继续决策分支（约 6450-6530 行），只改计数逻辑，不动载荷。

### 2.2 项目 2：终局失败显式化

- 定义两类失败，避免混为一谈：
  - **框架/模型层终局失败**（API 错误、重试耗尽、上下文/压缩失败、不可重试错误）→ 归一为 `force_stopped`，原因带 `[terminal_model_error]` 前缀；不进入继续决策回合。
  - **任务层失败**（工具全部失败、并行块失败、模型空续）→ 保持现有 `failed` / `incomplete` 语义，模型可在主循环内换方式。
- 预算类单独走现有 `budget_reasons` → `force_stopped`（文案已区分），等价 Codex 的 `usage_limited` 收尾。
- 用户主动取消 → `interrupted`（区别于 `force_stopped`）：不发模型收尾调用，只写状态 + 事件；前端状态区显示“用户已中断”。已验证 RunControl 快照在取消时持久化为 interrupted 且保留步骤；需补的是把 simple_chain run_state 也同步标为 interrupted（当前缺口，靠启动清理兜底）。
- 实施方式：把主循环所有终局出口（现有多处 `final_chain_status = ...`）收口到 `_natural_closeout`，由它统一写状态、发事件。

### 2.3 项目 3：运行态持久化与恢复

- **扩展 run_state schema → v2**（`schema: tiangong.v3.simple_chain.run_state.v2`）：
  - 新增：`version`（CAS 防旧覆盖）、`budget`（轮次/墙钟/单工具的已用与上限）、`terminal_reason`、`last_transition`（类型/原因/轮次/时间）、`schema_version`。
  - **不设续租字段**：续租机制确认不加入；未来若加，按“新状态必须新增事件”规则扩展。
  - 终端状态（complete/failed/incomplete/force_stopped/interrupted）与原因必须落盘（现状已落状态，补原因）。
- **迁移**：读入时无 `schema_version` 视为 v1 → 补默认字段，下次保存自动升 v2；未知新版本按“无状态”启动并记日志。
- **写入**：沿用 temp+rename；加单进程写锁与 version CAS（写前校验旧 version，不匹配则丢弃本次写并告警）。
- **启动对账**：扩展 `_cleanup_stale_run_states`——改为 `interrupted` 时写入 `terminal_reason="[process_restart] run interrupted at startup"` 并补发 `state_restored` 事件。
- **前端刷新 ≠ 后端重启**：刷新只重读（前端向后端查询当前 run_state 视图），后端进程存活时任务继续跑，显示“运行中”；只有后端启动才做对账。
- **VRM 刷新回退**：单独排查 `avatar-service.mjs` 的 `selectedModelId` 恢复路径（flag 已存在，疑似 boot 未读取或保存时机问题）；修复点在前端 avatar 层，不进 run_state。

### 2.4 项目 4：事件通道与前端双通道

- **事件集合（封闭，6 种）**：`turn.failed`、`force_stopped`、`budget_limited`、`continue_decision`、`run_interrupted`、`state_restored`。
- **事件源**：不新建日志，挂到 `RunControlManager.step` 同源——已验证 step 原子持久化到 run 快照（保留最近 80 条、summary≤1200、meta 序列化、按 id 去重）；规范化事件是 steps 的机器可读投影，工作日志（quanzhuixian，xiangqing≤500）保持不动。
- **事件不进模型上下文**：模型只收 `natural_closeout` / `continue_decision` 载荷，事件只给前端状态区、watchdog、外部监控。
- **前端**：
  - 消息列表只渲染 `origin=model` 的自然语言；后端已在响应层对 incomplete/failed 标记 `origin=template`（FE-02），扩展覆盖 force_stopped/interrupted；
  - 后端兜底模板（`_simple_chain_incomplete_reply`、`_simple_chain_budget_close_reply`、中断模板）标记 `origin=system` → 渲染到聊天框外的状态条，不进消息列表；
  - 状态条显示最近事件：状态、原因（人话化）、轮次、剩余预算；可折叠；
  - 状态条**复用现有 run-progress 轮询**（http-runtime.mjs `setInterval(poll, 1000)`，失败退避、终态停轮询），不新增轮询；`refreshStatus` 是手动状态刷新，不作节奏源。
- **兜底层级**：模型自然收尾成功 → 聊天框；模型收尾失败 → 状态条（模板人话）；极端情况（UI 不可用）→ 事件本身可被外部消费。

## 3. 预期改动文件

- `app/backend/tiangong-backend/v3/zongdiaodu.py`：run_state v2、进展感知计数、终局归一、启动对账补事件。
- `app/backend/tiangong-backend/v3/duihua_qiaojie.py`：RunControlManager 事件投影、`interrupted` 映射。
- `src/total_gateway/desktop_api.py`：透传 `last_transition` / 事件尾部给前端。
- `app/frontend-v2/renderer/core/actions.mjs`：模板文本标记 `origin=system`，状态条渲染。
- `app/frontend-v2/renderer/runtime/http-runtime.mjs`：读取事件尾部与运行态。
- `app/frontend-v2/renderer/avatar/avatar-service.mjs`（及 boot）：VRM selectedModelId 恢复。
- `tests/`：新增/更新 pytest 与 Node 测试。

## 4. 实施顺序与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | 状态内核：run_state v2 + CAS + 终端原因落盘 + 事件投影 | pytest：迁移、CAS、写失败降级、事件补齐 |
| P2 | 进展感知 9 次上限 + 终局失败归一 + interrupted | pytest：有进展不限、无进展 9 次强停、API 重试耗尽 force_stopped |
| P3 | 前端双通道 + 状态条 + VRM 恢复 | Node 测试 + 手动刷新验证：聊天无系统文本、状态条显示原因、VRM 不回退 |
| P4 | 全量回归（pytest + Node）+ 源码上传 | 现有 183+ 后端、204+ 前端用例不回归 |

## 5. 第一性原理自审

### 5.1 每个新增是否解决真实症状

- 刷新回退（VRM/预算）→ P1/P3；聊天系统文本 → P3；失败空转 → P2；卡死误伤 → 已实现，P2 只补语义。

### 5.2 是否最小新增

- 全部建立在既有 run_state / RunControlManager / simple_chain_status / avatar flag 之上；未引入新存储、新消息队列、新监控平台。

### 5.3 单一真相源

- run_state（磁盘）= 真相；事件 = 投影；simple_chain_status = 视图。三者通过 `last_transition` 对齐，启动时按需补齐。

### 5.4 边界

- 不改网关 watchdog（AMBIGUOUS 机制保持）；不改 LLM 推理循环载荷；不改聊天历史存储。

### 5.5 失败模式推演

- 写盘失败 → 降级标记 + 重试，任务不阻塞；
- 进程崩溃 → 启动对账转 interrupted + 补事件；
- 模型收尾失败 → 状态条模板兜底；
- 并发写 → run_id 隔离 + version CAS；
- 旧数据 → v1→v2 默认迁移，未知版本按无状态。

## 6. 四项小事的验证记录（2026-08-06，已测试，非拍脑袋）

1. **用户取消后 run_state 保留**：验证通过。`RunControlManager.stop()` 会把 run 快照持久化为 `phase=interrupted`、`stop_requested=true`，steps 全部保留（实测：backend_received / t1 / user_stop / backend_finished），写入 per-run 文件与 latest.json；相关 pytest（test_backend_run_identity.py，5 项）通过。结论：取消保留快照；需补 simple_chain run_state 的终端标记。
2. **事件投影与日志去重**：验证通过。`RunControlManager.step` 已原子持久化 steps（保留 80 条、summary≤1200、meta 序列化、按 step id 覆盖），quanzhuixian 是跨域 trace（xiangqing≤500）。事件 = steps 规范化投影，不双写。
3. **terminal_reason 长度上限 500**：代码既有规范即 500（message / interim_reply / unresolved_question / quanzhuixian xiangqing 均 ≤500；step summary ≤1200）。500 与现状一致。
4. **状态条刷新节奏**：修正原建议。`refreshStatus` 是手动/按需刷新，不是轮询；真正可复用的是 http-runtime.mjs 的 run-progress 轮询（1 秒一次，失败指数退避，终态停轮询），前端已按 `progressStepSignature` 去重。状态条直接消费该轮询的 steps，不新增定时器。

## 7. 遗留开放项

1. 用户取消路径补齐：RunStopped 捕获处把 simple_chain run_state 标 interrupted + `terminal_reason=user_cancel` + 发 `run_interrupted` 事件（P2 实现）。
2. 多窗口同时查看同一 run：只读无冲突，1s 轮询各自去重，无需额外设计。
3. 续租机制确认不加入；封闭事件集合按需扩展的规则不变。

## 9. 工作区写入模式（2026-08-06 追加需求）

### 已确认规则

1. 设置面板“工作区”改为下拉：`工作区`（默认）/ `全盘`。
2. 选择全盘时路径输入、选择目录、打开目录全部置灰不可用；已保存的工作区路径保留，切回可恢复。
3. 全盘 = 除 Windows 核心系统文件（硬禁区）外全部可写；硬禁区沿用 `_CONTRACT_HARD_DENY_*`（SystemRoot/Program Files/ProgramData/.ssh/.aws/.gnupg/.azure/.config/磁盘根/.env）。
4. 全盘模式不打断（覆盖已有文件不弹确认），仍保留 readback 验证、回滚快照、A5 阻断。
5. 老配置（无 `workspace_mode` 字段）按 `workspace` 处理。
6. 模式持久化在 `workspace_settings.json`，启动时 main.js 注入 `TIANGONG_WORKSPACE_MODE` 到后端/网关；切换后重启应用生效。

### 实现落点

- `workspace_settings.py`：读写 `workspace_mode`（env 优先，文件次之，默认 workspace）。
- `readable-python-source/omni_body_skill/tool_contracts.py`（权威源，sync 到 4 份副本）：`_contract_path_allowed` 统一放行判定；全盘模式跳过工作区相对检查但硬禁区永不放行。
- `src/total_gateway/impact_evaluator.py`：全盘模式下 `_path_outside_workspace` 不再提高 blast。
- `app/main.js`：启动时读 workspace-preference.json 注入 `TIANGONG_WORKSPACE_MODE`；`workspace:setRoot` 事务同时提交 workspace 与 workspace_mode，写偏好文件、重启服务。
- `http-runtime.mjs`：`workspace_mode` 走 main 进程权威事务（不进 localStorage 当唯一来源）；状态载荷回读 `workspace_mode`。
- `settings-panel.mjs`：下拉 + 置灰联动 + 保存 `workspace_mode`。
- 安全：shell.run/python.run 命令文本中的绝对路径也扫硬禁区（`hard_deny_path`），全盘模式不可通过命令绕过。

### 测试

`tests/test_workspace_full_disk_mode.py`（8 项）：默认 workspace、保存/读取 full、非法值回退、老配置按 workspace、全盘放行桌面、全盘仍拦 Windows/.ssh、网关 blast 判定。

## 10. 简单链事件流（2026-08-06 实施）

- **7 种事件**：`chain_started` / `continue_decision` / `turn.failed` / `force_stopped` / `budget_limited` / `run_interrupted` / `chain_completed`（去掉 state_restored，加 started/completed）。
- **文件**：`~/.tiangong/v3/simple_chain_events/events-YYYYMMDD.jsonl`，按日轮转、保留 30 天。
- **位置不写死**：`TIANGONG_SIMPLE_CHAIN_EVENTS_ROOT` > 自动生成指针 `simple_chain_events_location.json` > 自动推导；首次运行生成指针，老安装/不同 HOME 自动适配。
- **发射点**：run_state 创建（chain_started）、继续决策回合、`_simple_chain_closeout_record` / `_simple_chain_mark_terminal`（终局映射）、启动对账（run_interrupted + 崩溃窗口终局回填）。
- **零影响**：发射失败仅返回 False；`tests/test_simple_chain_events.py` 7 项全过；全量后端 1614 过 / 3 既有环境失败，前端 213 过。
- **监控脚本**：优先读事件流，快照兜底。

## 8. 实现状态与实验记录（2026-08-06 三稿）

### 8.1 隔离实验结论（实验在 %TEMP% 拷贝副本中完成，未污染源码）

1. **指纹去噪字段集**：现状 `raw_full` 在“同一调用单调重复”30 轮内不触发卡死（P0 坐实）；`evidence_codex` 策略在 5 个场景全对（噪声稳定/路径变化/内容变化/阻塞集变化/新附件），单调重复第 7 轮触发。采用 `tool_name/tool_action/tool_args/tool_result_contract/failures/final_requirement_gaps/generated_attachments/codex_evidence` + 噪声键剔除。
2. **9 次上限语义**：带“有进展清零”后任何阈值都不误伤 30 步合法长任务；卡死场景 cap=9 在第 9 次无进展继续决策停（监视器通常第 4-5 轮先停，9 为后盾）。维持 9。
3. **run_state 保留策略**：真实目录 314 个文件/19MB、最老 13.6 天；时间窗 30 天当前删不掉任何文件，按数量“保留最新 200 个”删 114 个/释放 12.2MB。采用“200 个且 30 天，谁更严用谁”。

### 8.2 已实现源码改动（未提交前的清单，已全部过测试）

- `zongdiaodu.py`：指纹去噪；run_state v2（version/schema_version/owner_pid/budget/terminal_reason/last_transition/persistence_degraded）；写路径锁 + CAS + 降级；启动对账重写（终态白名单反转、owner 存活判定、统一根目录、保留策略）；继续决策进展感知重置；初始/循环内模型失败归一 force_stopped；stage 显式映射；收尾记录 source（model/template）。
- `duihua_qiaojie.py`：RunStopped 捕获标记 interrupted + 响应契约字段；重试耗尽标记 force_stopped；origin 按 last_transition.source 判定。
- `desktop_api.py`：透传 `terminal_reason` / `last_transition`。
- `http-runtime.mjs`：`FORCE_STOPPED` → `force_stopped`，加入终态集合。
- `actions.mjs`：取消路径不再把“已中断”模板写入聊天框。
- `conversation-panel.mjs` / `conversation.css`：模板文本不进消息列表（保留附件），新增状态条。
- VRM：查证现有代码已实现选中恢复（boot 读 flag → registry 校验 → selectModel）且有回归测试，未改代码。

### 8.3 测试结果

- 后端全量：1597 passed / 16 skipped / 3 failed。3 个失败均为既有环境问题：`builtin-models.json` 缺 `zaowu-v2`（manifest 只有 tiangong-z1）；两个 v21 测试依赖缺失的 `C:\TG3Clean\v21-work` 快照目录。
- 前端全量：215 项，213 pass / 2 skip / 0 fail。
- 新增回归：`tests/test_run_state_v2_stuck_cleanup.py` 8 项全过；`frontend-gf-safety-mapping.test.mjs` 补 force_stopped 断言。
- 镜像同步：`sync-generated-sources.py --write` 通过，`test_generated_source_mirrors_are_in_sync` 恢复通过。

### 8.4 事故记录（必须透明）

写清理回归测试时，第一版测试只隔离了 `TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT`，未隔离 `USERPROFILE/HOME/APPDATA`，导致清理函数扫描并删除了真实用户目录 `C:\Users\77571\.tiangong\v3\simple_chain_run_state` 的 313 个运行检查点（314 → 1，约 19MB；`Path.unlink` 不进回收站，不可恢复）。影响范围：仅简单链 run_state 历史检查点；源码、VRM、聊天、RunControl 快照（LOCALAPPDATA）均未受影响。根因已修复：清理测试改为全根目录隔离；该目录现存 1 个文件（已按预期标记 interrupted）。
