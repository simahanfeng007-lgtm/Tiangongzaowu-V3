# 天工造物 v3 单进程最终源码状态

日期：2026-07-21

## 当前拓扑

- 对外唯一端口：7184
- 默认单一 Python 应用进程
- Runtime、LifeKernel、Communication、PolicyEngine、Ticket/Grant、Omni Runtime 均嵌入 7184
- 7174、7175、7176 默认不监听
- LifeKernel 保留 standalone 独立运行入口
- 同一生命数据目录通过唯一写入者租约防止双写

## 最终验证

- 四轮多 Agent 对抗筛查与修复完成
- 单进程真实端到端：20/20 通过
- 完整 pytest：786 passed、10 skipped、554 subtests passed、0 failed
- Python 文件解析：636
- JavaScript 文件语法检查：51
- 生成镜像校验：通过
- 三份单进程发布清单校验：通过
- `git diff --check`：通过

详细结果见：

- `ADVERSARIAL_FINAL_CLOSEOUT_REPORT_20260721.md`
- `repair_validation/adversarial_final_evidence_20260721.json`
- `repair_validation/single_process_live_e2e_evidence.json`

## 启动

Windows 源码模式：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-source.ps1
.\scripts\start-source.ps1
```

也可在初始化后双击根目录 `start-tiangong.bat`。

默认不要设置 `TIANGONG_LEGACY_MULTI_PROCESS`。正式安装包会忽略该变量；Life 独立维护应使用 LifeKernel standalone 入口，而不是恢复旧桌面四服务拓扑。

## 平台边界

当前源码、单端口应用链、模拟模型链和发布清单已验证。Windows DPAPI、PyInstaller、NSIS、代码签名以及真实第三方模型网络仍需在 Windows 10/11 x64 发布机执行最终平台验收。
