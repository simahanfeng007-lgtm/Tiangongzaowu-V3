# P15-D 时态源策略核验矩阵与阶段收口记录

编制：2026-09-20。基线：main `fa8bdab`（含 P15-A PR #85、P15-B PR #86）。
性质：核验记录与补缺证明；不改变任何权限语义。

## 一、三时态核验矩阵（要求 → 承载测试）

| 时态 | 要求（交接书 P15-D） | 承载 | 状态 |
|---|---|---|---|
| 新请求 | 使用获批当前版本 | `test_source_composition_preparation_p12.py`（prepare 绑定当前 World/Source/registry；`COMPOSITION_SOURCE_SYSTEM_REGISTRY_MISMATCH`） | ✅ 已覆盖 |
| pre-Plan | World/Source 变化拒绝旧准备或显式重规划 | 同上：`WORLD_NO_LONGER_CURRENT`、`PREPARATION_DRIFT`、bundle/归档字节漂移拒、`test_gateway_generation_release_rejects_late_response` | ✅ 已覆盖 |
| pre-Plan（新增） | 受损归档世界上的新准备也拒绝 | `test_temporal_source_policy_p15d.py::test_new_preparation_on_damaged_archive_is_refused` | ✅ 本批补齐 |
| sealed 在途 | 继续使用原历史来源与授权 | R1C4 往返 + `test_method_run_retention_p9.py`（`test_retained_world_survives_pruning_restart_and_release`、`test_historical_method_pin_survives_more_than_default_history`） | ✅ 已覆盖 |
| sealed 撤销 | 归档被篡改/删除后读取拒绝，绝不回退 latest 或静默替换 | `test_temporal_source_policy_p15d.py`（篡改/删除两例） | ✅ 本批补齐 |
| sealed 撤销（登记侧） | 执行授权对 registry/permission/manifest 漂移 fail-closed | `test_composition_grant_authority_p7c1.py`（`test_current_registry_manifest_permission_and_schema_drift_fail_closed` 等系列） | ✅ 已覆盖 |
| 重新规划 | 不偷改原封存计划；新计划/新代际 | `test_source_registration_intake_p12.py`（identity reuse 冲突拒）；R1C2 代际/请求漂移拒 | ✅ 已覆盖 |
| 终止释放 | 释放幂等、崩溃恢复、GC/裁剪/磁盘重放不删运行源 | `test_method_run_retention_p9.py` 全套 + P10 的 70 次 World 更新/迁移/重放代表性用例（D05 范围） | ✅ 已覆盖（70 次用例按交接书为复用，不替代 P16 更高要求） |
| 撤销后审计 | 登记与 pin 保持可观测 | `test_temporal_source_policy_p15d.py::test_sealed_read_refuses_tampered_archive` 尾部断言 | ✅ 本批补齐 |

结论：P15-D 的合同全部有测试承载；本批新增 3 项补缺反例（篡改/删除/受损世界新准备），无生产代码变更——既有权威（`_read_archive` 字节与只读防线、`method_world_for_state` 签名验证、P7C1 漂移闭环）已按设计 fail-closed，正是核验所要证明之事。

## 二、P15 阶段收口盘点

| 工作包 | 状态 | 说明 |
|---|---|---|
| P15-A Source 变化→World 失效 | **MERGED**（PR #85，验证平面 1.22） | Tool 发布经原失效链；Method 侧为既有链。源删除/Schema 变化等扩展矩阵属 P15 完整验收 |
| P15-B Source 变化→经验过期 | **MERGED**（PR #86） | 原 P5 标记 + 原级联；CURRENT 无副作用；无相似度复活路径 |
| P15-C 重验形成新经验 | **机制已保证，完整验收 OPEN** | REVALIDATION_REQUIRED 不自动复活；重验只能是新源上的完整 Plan→执行→归因（R1C4+R1F 链路具备）。真实/受控重验任务矩阵待 P16/R1G 环境 |
| P15-D 三时态版本策略 | **MERGED（本批）** | 见上矩阵 |
| P15-EXIT | **未关闭** | 需完整 Source 增改删/回滚端到端矩阵与双平台门禁；真实验收依赖 R02/R03 |

P15 阶段工程接线全部就位；阶段不因本批关闭（完整验收另列）。
