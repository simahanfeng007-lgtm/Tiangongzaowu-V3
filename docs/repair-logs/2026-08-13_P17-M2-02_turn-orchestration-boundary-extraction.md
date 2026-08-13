# 天工造物 V3 修复日志

## P17-M2-02：Turn Orchestration / Tool Loop Boundary Extraction

- 日期：2026-08-13
- 状态：已完成，待合并
- 阶段：P17-M2 / God Module Decomposition
- 分支：`agent/p17-m2-god-module-decomposition`
- M2-01 最终基线：`e307a90aa9d33756a58fdc7a8a38e3c04845a618`
- Clean 实现提交：`e35f25b87d616c02d2d46818bed2ba9976b82d95`
- Construction lineage closure：`35dae0d11746cd5c046b245186c33fbc879eb3b0`
- 实现提交说明：`refactor(p17-m2): extract turn loop coordination`
- Architecture Gate：Run `31714394800`
- 产品功能变更：无
- Runtime 主入口变更：无
- Tool Executor 权威变更：无
- Authority Gate / A0-A5 变更：无
- Memory / World / Life 权威变更：无
- 第二 Runtime / 第二启动链：未引入

---

## 1. 本步目标

P17-M2-01 已经把 `zongdiaodu.py` 中的 Composition Root 与 Lifecycle Wiring 外提，但 `_huanxing_simple_chain` 仍同时承担大量不同层级职责：

- turn iteration 推进；
- tool round 预算推进；
- repeat observation 计数；
- wall-clock / iteration budget 判断；
- run-state live progress 投影；
- parallel tool candidates 去重与分类；
- cached fact replay 判断；
- protected artifact guard；
- 真正工具执行；
- ToolResult 缓存；
- confirmation / gate；
- quality gate；
- 最终答案收口。

M2-02 不尝试一次性搬走整个 `_huanxing_simple_chain`。这一轮只建立一个纯协调边界，把确定可以无行为变化外提的 Turn Loop Coordination 从 God Module 中拆出。

目标结构：

```text
Zongdiaodu._huanxing_simple_chain
        |
        +--> runtime_turn_orchestration.py
        |      - TurnLoopState
        |      - evaluate_turn_budget
        |      - coordinate_parallel_steps
        |      - PreparedStep / ParallelCoordination
        |
        +--> self._jineng_zhixing(...)
               ^
               |
          唯一真实 Tool Executor 保持不变
```

---

## 2. 为什么没有整体迁移 `_huanxing_simple_chain`

审计确认 `_huanxing_simple_chain` 不是单纯的 loop helper，而是同时耦合：

1. LLM continuation；
2. tool schema / tool call parsing；
3. caller-thread / ordered execution 判定；
4. confirmation card；
5. Authority Gate；
6. protected artifact 逻辑；
7. ToolResult merge；
8. execution evidence；
9. quality gate；
10. final answer convergence。

如果整段移动，会同时改变 import topology、对象依赖、Tool Executor 调用位置和异常边界，无法再把“架构拆分”与“行为改变”分离验证。

因此 M2-02 采用窄切口：

> **先抽纯协调状态和确定性决策，不移动任何真实执行权。**

---

## 3. 新增 `runtime_turn_orchestration.py`

新增：

`app/backend/tiangong-backend/v3/runtime_turn_orchestration.py`

该模块只拥有协调数据与纯决策，不导入 `zongdiaodu`，不导入 Gate，不调用工具，不写 Memory / World / Life。

### 3.1 `TurnLoopState`

负责：

- `action_rounds`；
- `iteration_count`；
- repeat counters；
- `bump_iteration()`；
- `bump_repeat()`；
- `can_schedule()`；
- `reserve_one()`；
- `record_batch_result()`；
- `_live` progress projection。

特别保留历史推进时机：

- single tool round 在真实执行前 reserve；
- parallel tool round 在原历史位置、结果完成后推进；
- 不把 ToolResult 本身迁入该 state。

### 3.2 `evaluate_turn_budget()`

外提 iteration / wall-clock budget 判断。

历史语义必须保持：

```text
iteration_count > max_iterations
elapsed_seconds > max_wall_clock_seconds
```

即达到边界值本身不触发 exhausted，只有严格超过时触发。

### 3.3 `coordinate_parallel_steps()`

并行候选分类顺序保持：

```text
first occurrence wins
    -> prior fact reuse
    -> protected artifact guard
    -> ready
```

相同 `identity_key` 后续重复候选直接忽略；第一候选的语义优先级保持不变。

---

## 4. `zongdiaodu.py` 的变化

`Zongdiaodu._huanxing_simple_chain` 现在显式委托：

- `TurnLoopState()`；
- `turn_loop.bump_iteration()`；
- `turn_loop.bump_repeat()`；
- `turn_loop.can_schedule()`；
- `turn_loop.reserve_one()`；
- `turn_loop.record_batch_result()`；
- `turn_loop.project_live()`；
- `evaluate_turn_budget()`；
- `coordinate_parallel_steps()`。

旧的局部 `repeat_observation_counts` 与 `seen_parallel` 不再是 loop 内第二套协调状态。

### 明确保留在 `zongdiaodu.py` 的职责

以下内容本轮没有迁移：

- `tool_call_counts`；
- `tool_call_results`；
- protected path authority facts；
- generated media / attachment evidence；
- quality history；
- confirmation / permission；
- 实际 tool execution；
- continuation / final answer 收口。

原因不是它们不需要继续拆，而是这些状态已经触及执行事实、Gate 或最终因果结果，应在后续独立阶段建立更强 Contract 后再移动。

---

## 5. Tool Executor 权威未改变

本步最重要的不变量：

```python
self._jineng_zhixing(...)
```

仍然留在 `Zongdiaodu._huanxing_simple_chain` 中，并继续作为当前主链的真实工具执行入口。

`runtime_turn_orchestration.py`：

- 不执行 tool；
- 不调用 `_jineng_zhixing`；
- 不发 permission ticket；
- 不绕过 Authority Gate；
- 不重放副作用；
- 不建立第二执行器。

因此本步是 orchestration decomposition，而不是 execution architecture replacement。

---

## 6. Source Authority / Closed-world 更新

`source-ownership.json` 的 V3 `implementation_roots` 增加：

```text
runtime_turn_orchestration.py
```

最终 Source Authority Gate 继续为：

```text
16 independent authorities
1 aliases
24 generated targets
1 closed-world boundaries
```

没有增加第二 authority root。

---

## 7. 永久 Architecture Gate

`.github/workflows/architecture-gate.yml` 新增：

```text
Run P17 M2-02 turn orchestration regression
```

并把编译检查扩展为：

```text
zongdiaodu.py
runtime_bootstrap.py
runtime_composition.py
runtime_lifecycle.py
runtime_turn_orchestration.py
```

执行顺序仍然先跑 closed-world/source-authority，再做 `py_compile`，避免 `__pycache__` 干扰 M1-03 closed-world 守门。

---

## 8. 新增回归测试

新增：

`tests/test_zongdiaodu_p17_m2_02.py`

共 6 组：

1. Turn boundary 必须保持纯协调，不得导入 `Any`、`zongdiaodu`、Gate 或 executor；
2. counter 与 `_live` projection 保持历史语义；
3. budget 保持严格 `>` 边界；
4. parallel coordination 保持 first -> reuse -> guard -> ready 顺序；
5. `zongdiaodu` 必须调用新协调 seam，同时继续保有 `self._jineng_zhixing` 执行权；
6. V3 closed-world ownership 必须登记新模块。

---

## 9. 候选迁移与保护性失败记录

### 9.1 第一次候选

Run：`31711834932`

结果：候选主动中止。

原因：迁移脚本在 parallel classification 已整体替换部分 repeat counter 后，仍假定后续 guard repeat anchor 固定出现 3 次；实际剩余精确缩进匹配只有 1 次。

该失败发生在 exact-anchor protection 中，没有产生正式候选源码。

结论：迁移脚本顺序假设错误，不是产品源码回归。

### 9.2 Read-only 候选验证

Run：`31712432337`

结果：

- exact patch：PASS；
- Source Authority：PASS；
- M1 13 tests：PASS；
- M1-03 5 tests：PASS；
- M2-01 6 tests：PASS；
- M2-02 6 tests：PASS；
- `py_compile`：PASS；
- Artifact collect：FAIL。

Artifact collect 失败原因：checkout depth=1，审计用 `git diff e307a90...` 找不到浅克隆中的 M2-01 base object。

该失败发生在所有源码验证之后，不影响产品候选结论。

### 9.3 Read-only 完整候选

Run：`31712644466`

修正 checkout 历史可见性后：全部步骤 PASS，成功生成候选 Artifact。

候选 ZIP SHA256 与 GitHub Artifact digest 一致：

`af80175fd1d26cf3374811101847a7a993fa1a0a04051637234b54580f6d4798`

### 9.4 Verified blob materialization

Run：`31713797405`

第一次尝试仍受原 write-capable workflow depth=1 的 collect 限制；随后通过只读基线 ref 让既有 checkout refspec 获得 `e307a90...`，rerun 后：

- exact patch：PASS；
- 全部回归：PASS；
- compile：PASS；
- collect：PASS；
- verified files -> unreferenced Git blobs：PASS；
- Artifact upload：PASS。

最终 materialized Artifact digest：

`9d599a0fb8bb4b5d486ad40c7c7db2d02ed5582558e8227c6a909bba6e40be44`

正式候选 blob：

```text
architecture-gate.yml                  530dbe31051841f520e262dafd9e425d6bbb551c
runtime_turn_orchestration.py           20c2920aa6674492b447714ae3dd77d544d7663d
zongdiaodu.py                            b95cde759d6ec1d11b489715b6ef8bde6cac37dc
source-ownership.json                    2a18e27891a4b0b7049a3915a9b6a02ae0e85e2c
test_zongdiaodu_p17_m2_02.py             200c41db4feb9cef052b51f0c9b37a0e120d39f4
```

---

## 10. 最终 Tree 与施工历史处理

Clean 实现 commit：

`e35f25b87d616c02d2d46818bed2ba9976b82d95`

父提交严格为 M2-01：

`e307a90aa9d33756a58fdc7a8a38e3c04845a618`

相对 M2-01 的净变化严格只有 5 个正式文件：

1. `.github/workflows/architecture-gate.yml`
2. `app/backend/tiangong-backend/v3/runtime_turn_orchestration.py`
3. `app/backend/tiangong-backend/v3/zongdiaodu.py`
4. `source-ownership.json`
5. `tests/test_zongdiaodu_p17_m2_02.py`

没有 staging、probe、candidate workflow、patch payload 或 runner 出现在 clean Tree。

连接器安全层不允许 force-ref 清理施工历史，因此使用 Git 原生双父 closure：

`35dae0d11746cd5c046b245186c33fbc879eb3b0`

- 第一父：Clean M2-02 `e35f25b8...`；
- 第二父：施工 HEAD `645e16cf...`；
- Tree：与 clean M2-02 完全一致。

这样：

- M2 分支可普通 fast-forward；
- 第一父实现历史仍是 M2-01 -> M2-02；
- 施工过程作为第二父保留审计；
- 最终运行 Tree 不含施工物。

---

## 11. 最终跨平台验证

最终 closure HEAD：

`35dae0d11746cd5c046b245186c33fbc879eb3b0`

Architecture Gate Run：

`31714394800`

结果：

### Ubuntu

- Source Authority：PASS；
- generated mirrors：PASS；
- M1：13/13 PASS；
- M1-03：5/5 PASS；
- M2-01：6/6 PASS；
- M2-02：6/6 PASS；
- M2 V3 seam compile：PASS。

### Windows

同一矩阵全部 PASS。

Workflow overall：`success`。

---

## 12. M2-02 结束时架构状态

当前主链仍为：

```text
Total Gateway
    -> Zongdiaodu
        -> Turn coordination seam
        -> existing Authority / confirmation path
        -> existing self._jineng_zhixing Tool Executor
        -> ToolResult / evidence
        -> existing continuation / final convergence
```

M2-02 没有建立任何平行执行系统。

下一步应继续沿当前 seam 拆分 ToolResult / continuation boundary，而不是立即同时修改 `src/life_service/embedded_runtime.py`。先让 `zongdiaodu.py` 的 Turn/Execution/Convergence 边界稳定，再进入第二个 God Module，风险最低。
