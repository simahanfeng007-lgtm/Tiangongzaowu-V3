# 天工造物 V3 — 交付物清单与测试基线

编制：2026-09-21。基线：main `5e38a323917ce13c2d04608762b74516a7761b6f`（源码树 `9353a826d316054d644535427725048247c91d49`）。
用途：正式工程交付物索引与精确测试基线；后续任何变更以此为起点核对。

## 一、交付物清单

| 交付物 | 路径 | 说明 |
|---|---|---|
| 源码清单 | `docs/delivery/SOURCE_MANIFEST_5e38a323917.json` | 2664 文件的 SHA-256 哈希与 head/tree 绑定 |
| 基线增量补丁 | `docs/delivery/PATCH_af2fdf1_to_5e38a323917.patch` | 60 提交 / 76 文件 / +17863 行（af2fdf1→5e38a32） |
| 终态态势总册 | `docs/capability-composition/FINAL_ENGINEERING_STATUS_2026-09-21.md` | 一页接手全景 |
| 工作包台账 | `docs/capability-composition/P12_TO_P17_EXECUTION_LEDGER_2026-09-20.md` | 逐包状态与 BLOCKED 原因 |
| P11 任务矩阵 | `docs/p11-matrix/TASK_MATRIX_FROZEN_2026-09-20.json` | 200 任务+40 故障（输入侧冻结） |
| P13 退役矩阵 | `docs/capability-composition/P13_A_RETIREMENT_MATRIX_2026-09-20.json` | 20 面/1 候选（重建校验） |
| P17 动态面台账 | `docs/capability-composition/P17_A_DYNAMIC_SURFACE_2026-09-20.json` | 121 命中全注记 |
| P17 欠项扫描 | `docs/capability-composition/P17_B_OBLIGATION_SCAN_2026-09-20.json` | 0 needs_review |
| 验证平面声明 | `docs/p19-r2/VERIFICATION_PLANE_1_{19-23}.txt` | 1.19→1.23 逐版本变更声明 |

**注**：源码 ZIP 须在独立工作树由 `git archive HEAD` 生成并核对与 SOURCE_MANIFEST 一致后分发；本仓库内不提交 ZIP（避免 Git LFS 二进制膨胀）。补丁须在独立工作树 `git apply --check` 验证后使用。

## 二、精确测试基线（Linux 全库，本地 3.14 环境）

| 指标 | 数值 |
|---|---|
| 测试采集（collected） | **5630** |
| 实际通过 | **5550** |
| 失败 | **10**（本地环境特异，见下注） |
| 跳过 | **70** |
| 子测试通过 | **1199** |

**本地 10 项失败注记**：均为平台/版本特异（PowerShell 不可用于 Linux、Python 3.14 vs CI 3.12 的 import 顺序差、原生 Windows 路径测试），**在 CI Ubuntu+Windows 双平台均通过**（CI 为权威——Architecture gate 全绿）。逐项：capability_manifest 导入序×2、container native、life store path、source policy preflight、tool source launch×2、launch probe、PowerShell wrapper、manifest import×1。

## 三、CI 权威门禁状态（当前 main）

| 工作流 | 状态 |
|---|---|
| Architecture gate（Ubuntu 全库 + Windows 八分片 + source-authority 双平台 + node 双平台） | ✅ success |
| P19-R2 Golden Gate（双平台） | ✅ success |
| P14 repository perception | ✅ success |
| 验证平面 | 1.23 |
| Gateway Store | v33 |

## 四、后续验收所需资源（逐条在册，与台账一致）

R03 四模型凭据 / R04 签审 / R05 生产观测窗口 / R02 原生环境 / R06 独立复核 / R07 发行签名+LFS+安装机 / v1.2 母版原件（SHA-256 `691e857e…f895`）。
