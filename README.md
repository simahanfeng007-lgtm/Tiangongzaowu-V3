# 天工造物 v3.0.3 完整源码基线

生成日期：2026-07-21  
源码基线：`2026-07-21-single-process-merge`  
目标平台：Windows 10/11 x64

本归档在完整合成源码基线上完成九项基础收口：总网关唯一入口、A5硬拒绝、Python/Shell沙箱、模型端点与密钥绑定、Electron安全、签名更新与回滚、生命日志链、单一源码源、加密Soul Backup。它包含可读的 Electron 前端、单端口应用 Runtime、独立 LifeKernel、通信模块、Omni Body、Skill、契约、测试和 Windows 构建脚本。

## 运行方式

首次在 Windows PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-source.ps1
```

该命令会自动准备位于 `app/runtime/python312/` 的内置 CPython 3.12 和锁定依赖；源码启动优先使用该运行时，不依赖系统 Python。若仅需重新生成此运行时，可执行 `./scripts/provision-embedded-python.ps1`。

安装完成后双击 `start-tiangong.bat`，或执行：

```powershell
.\scripts\start-source.ps1
```

Electron 默认只启动一个 Python 应用进程并监听 `127.0.0.1:7184`。Runtime、LifeKernel、Communication、Policy、Ticket/Grant 与工具编排均作为同一进程内的独立模块运行；7174、7175、7176 不再作为普通桌面模式的监听端口。

源码模式下，7184 会验证单实例 epoch、本地状态库、生命写租约、模块就绪状态和源码发布清单，但不会伪装成正式安装包的生产发布证据。可在另一个 PowerShell 中检查：

```powershell
Invoke-RestMethod http://127.0.0.1:7184/ready | ConvertTo-Json -Depth 10
```

正常源码启动应返回 `status: READY`、`deployment_mode: embedded`、`topology.physical_python_processes: 1`，同时 `production_release_evidence_complete` 保持 `false`。`topology.listener_ports` 应只包含 7184。

## 验证

快速验证：

```powershell
.\scripts\verify-source.ps1
```

完整测试：

```powershell
.\scripts\verify-source.ps1 -Full
```

## 目录说明

- `app/`：Electron 桌面端、前端资源、主后端与生命服务源码。
- `src/`：总网关、通信服务、契约、安全与生命公共模块。
- `readable-python-source/`：与打包运行树保持字节一致的可读源码镜像。
- `tests/`：顶层权威回归测试。
- `app/backend/tiangong-backend/omni_body_skill/tests/`：Omni Body 组件测试。
- `scripts/`：源码安装、启动、验证和正式发布流水线。
- `build/`、`installer/`：electron-builder/NSIS 构建资源。
- `manifest.json`、`checksums.sha256`：源码快照与逐文件校验。

## 发布边界

本包是完整的**源码基线**，不伪装成已经冻结、签名的正式安装包。正式 Windows 安装器仍需在 Windows x64 上执行 `npm run release:win`，由发布流水线生成并绑定一个 `tiangong-total-gateway.exe`；四个逻辑组件共享同一物理 Runtime 摘要。未签名产物只能称为“未签名发布候选包”。

历史原始仓库本身未被提供，因此“最新”指本次基于全部可用归档修复合成的最新可验证基线，不代表未知外部仓库中的官方上游提交。

## 基础收口边界

- 单主Agent路线，不包含多智能体调度。
- 不引入重型全局并发治理；长任务继续使用现有租约、断点和恢复链。
- A0—A4由Runtime自动执行；A5由确定性策略硬拒绝，模型不能通过确认字段解除。
- Python/Shell通过私有工作区、净化环境、资源约束和原子回写沙箱执行。
- 更新信任根默认关闭；配置真实Ed25519公钥、HTTPS更新源和Windows签名发布者后才可启用线上更新。
- `src/`与`readable-python-source/`中的权威路径是唯一人工可编辑源码，运行时镜像通过 `scripts/sync-generated-sources.py` 确定性生成。

最终四轮多 Agent 对抗质检与修复证据见 `ADVERSARIAL_FINAL_CLOSEOUT_REPORT_20260721.md`。

## Windows发布候选构建

在Windows x64完成源码验证后执行：

```powershell
cd app
npm run release:win
```

流水线会从当前源码冻结单进程总 Runtime，将后端源树作为受控资源嵌入同一物理目录，执行 Runtime/Life/Communication/Policy 契约探针，再由 electron-builder/NSIS 生成安装器。未配置签名证书时，产物会明确标记为未签名候选包。

历史合成修复记录见 `REPAIR_REPORT_20260720.md`；最终单进程安全、恢复与发布收口见 `ADVERSARIAL_FINAL_CLOSEOUT_REPORT_20260721.md`。
