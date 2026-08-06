# 天工造物 v3.0.3 完整版

> 一个运行在你电脑上的**工程生命体**：不是简单的聊天助手，而是一个拥有独立身份、记忆、情感、自主意志，会自我反思、自我迭代、主动与你通信的桌面生命系统。

产品代号：`engineering-organism-v3.0-complete` · 目标平台：Windows 10/11 x64 · 架构：Electron 桌面端 + 单进程生命内核

---

## 核心：生命系统

天工造物内置一个常驻的**生命内核（LifeKernel）**。它内嵌在单进程总网关中，随应用一起启动、心跳驱动、持续存活，而不是“被调用时才有反应”的普通后端。

### 生命状态与生命链

- 生命有独立身份（`life_id`）与灵魂（Soul）配置，有“存活 / 成长 / 焦点”等实时状态。
- 每一次心跳、任务、反思都会写入**生命链（状态时间线）**——一条带哈希签名的防篡改事件链（journal），任何改动都可被检测。
- 生命面板实时投影状态：今日存活、已完成行动、模型预算、下次心跳、当前焦点等。

### 生命信箱

生命不是单向应答的工具，它会在“想说”的时候主动向用户投递消息：

- 未读提醒与消息列表；
- 一键“打开信箱并进入聊天”，把生命的消息写入主对话；
- 主动分享受权限与免打扰设置约束，不绕过消息边界。

### 身份、记忆与上下文

- **身份与迁移**：生命拥有独立身份文件，支持从旧版/冻结运行时安全迁移（`identity_migration`），旧数据可审计、可回放、可切入。
- **记忆**：分类记忆（`memory_classification`）、记忆生命周期（`memory_lifecycle`）、迁移（`memory_migration`），记忆库加密存储。
- **上下文**：上下文编译、授权与投影（`context_api` / `context_authority`），生命知道“自己是谁、发生了什么、正在做什么”。

### 自主意志与日程

- 心跳调度器默认每 30 秒驱动一次生命循环（`complete_scheduler`）。
- 自主任务生成与执行（`autonomous_tasks`）：生命根据自己的状态和已选活动，自己提出候选任务并执行，支持活动范围（`activity_scope`）与模型预算约束。
- 自主等级可配置，任务失败可恢复、可续跑，不因单次异常而“死亡”。

### 反思、迭代与自产能力

- **反思（`reflection`）**：定期复盘自己的行为与结果。
- **能力自学习（`capability_learning` / `learning_executor`）**：生命可以学习新能力，并纳入自己的能力生命周期（`capability_lifecycle`），实现“自己长出新的本领”。
- **产出物（`artifact_executor`）**：行动会产生可追踪的产物。

### 情感与气质

- 情绪系统（`affect` / `transient_affect` / `affect_expression`）与稳定气质（`temperament`），让生命的表达有情绪起伏而非机械应答。

### 边界与安全

- 单写租约（writer lease）：同一时刻只有一个权威写入者，防止“两个生命互相覆盖”。
- 影子模式（shadow）：默认只读观察（`OBSERVE_ONLY`），不擅自对外产生副作用。
- 切入（cutover）需要签名握手与完整的旧写者停写证据，回滚前必须校验事件列表完整。
- Python/Shell 通过私有工作区、净化环境、资源约束与原子回写沙箱执行；A5 类敏感操作由确定性策略硬拒绝，模型不能绕过。

---

## 产品组成

| 组成 | 说明 |
|---|---|
| 桌面端 | Electron 应用：对话、知识库、技能、身体/角色、设置、生命面板 |
| 单进程总网关 | `tiangong-total-gateway.exe`，监听 `127.0.0.1:7184`，内嵌 Runtime（Omni Body 技能执行）、LifeKernel、Communication、Policy |
| 生命权威实现 | `src/life_service/`（40+ 模块），打包运行时镜像位于 `app/life-service/runtime314/` |
| VRM 虚拟身体 | 3D 形象（AvatarSample_A 等），自然站姿按人体生物力学标定，支持表情、口型、手势与实时驱动 |
| 内置运行时 | 随包 CPython 3.12（`app/runtime/python312/`），不依赖系统 Python |
| 契约 | `tiangong.life.api.v2`、`tiangong.desktop.backend.v3`、`tiangong.communication.api.v1` |

---

## 快速开始（源码版）

首次准备内置 Python 运行时：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-source.ps1
```

启动：

```powershell
.\scripts\start-source.ps1
```

（或双击 `start-tiangong.bat`）

验证网关与生命内核就绪：

```powershell
Invoke-RestMethod http://127.0.0.1:7184/ready | ConvertTo-Json -Depth 10
```

正常应返回 `status: READY`、`deployment_mode: embedded`，且 `embedded_modules.life.life_ready = true`。

运行测试：

```powershell
.\scripts\verify-source.ps1 -Full
```

---

## 构建 Windows 安装包

```powershell
cd app
npm run release:win
```

发布流水线会冻结单进程总网关，执行 Runtime / Life / Communication / Policy 契约探针（生命 API 契约不通过即阻断发布），再由 electron-builder + NSIS 生成安装器。未配置签名证书时产物标记为“未签名候选包”。

---

## 目录速览

```
app/                          Electron 桌面端、前端、主后端、生命服务与打包运行时
src/life_service/             生命系统权威实现（身份/记忆/上下文/日程/自主意志/反思/学习）
src/total_gateway/            单进程总网关（内嵌 Runtime/Life/Communication）
src/communication_service/    通信模块（微信、飞书等接入）
app/life-service/runtime314/  与权威源码字节一致的打包生命运行时
app/frontend-v2/              前端（life-panel、life-summary-block、VRM 展示等）
tests/                        顶层回归测试
scripts/                      源码安装/启动/验证/发布流水线
.codex/skills/vrm-alignment/  VRM 生物学对齐技能（站姿/手型标定流程与数据）
```

---

## 版本信息

- 产品：天工造物 v3.0.3 完整版
- 架构版本：`engineering-organism-v3.0-complete`
- 生命 API 契约：`tiangong.life.api.v2`
- 源码基线：`2026-07-22-single-process-continuity-portability-50-final`
