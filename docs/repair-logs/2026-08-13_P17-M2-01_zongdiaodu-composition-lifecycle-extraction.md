# 天工造物 V3 修复日志

## P17-M2-01：Zongdiaodu Composition / Lifecycle Extraction

- 日期：2026-08-13
- 状态：已完成，待合并
- 阶段：P17-M2 / God Module Decomposition
- 分支：`agent/p17-m2-god-module-decomposition`
- M1 最终基线：`18e7d671ea06b7a871c9d404539588b161e9eccb`
- 实现提交：`9277cdb73e17a6b04a67e26edfa6bdc6068c5ea4`
- 实现提交说明：`refactor(p17-m2): extract zongdiaodu composition lifecycle`
- Architecture Gate：Run `31706280843`
- 产品功能变更：无
- Runtime 行为变更：无
- 启动入口变更：无
- 第二 Runtime / 第二启动链：未引入

---

## 1. 本步目标

P17-M1 已完成源码权威收口：

1. M1-01：Source Authority Guard；
2. M1-02：物理源码权威收敛；
3. M1-03：V3 closed-world source boundary。

进入 M2 后，不再继续做目录治理，而开始处理真正的 God Module 职责问题。

`app/backend/tiangong-backend/v3/zongdiaodu.py` 是 V3 的唯一总调度入口，但在 M2-01 前同时承担：

- LLM transport / Gutong 构造；
- 多个引擎的 concrete dependency construction；
- 生命周期锁创建；
- legacy heartbeat compatibility object 定义；
- World Understanding observer 的 import-time bootstrap；
- Runtime 启动 wiring；
- Runtime 停止 wiring；
- 以及真正的总调度业务逻辑。

这些职责全部堆叠在同一文件，导致后续继续拆解工具循环、上下文构造、生命链桥接时，很难判断哪些代码属于“业务调度”，哪些只是“对象怎么创建 / 系统怎么启动”。

M2-01 的目标因此被严格限制为：

> **只抽离 Composition Root 与 Lifecycle Wiring，不改变任何运行语义。**

本步禁止：

- 新建第二套 Runtime；
- 新建第二启动入口；
- 改 Total Gateway；
- 改 Memory / World / Life 事实权威；
- 改工具执行链；
- 改 A0-A5 裁决；
- 改主循环业务逻辑；
- 以 monkey patch 或外挂层替代原代码。

---

## 2. 修复前结构问题

### 2.1 `Zongdiaodu.__init__` 同时承担对象组装与业务对象初始化

修复前 constructor 会直接：

- 创建 `HttpKehuduan`；
- 创建 `GutongCeng`；
- 创建 `GuanchaYinqing`；
- 创建 `JinhuaYinqing`；
- 创建 `JinhuaBiaodaRouter`；
- 创建 `JinhuaBihuanYinqing`；
- 创建 `ZiyuYinqing`；
- 创建 `_lifecycle_lock`；
- 创建 `_active_user_run_lock`。

这意味着总调度类既是 orchestration host，又是 composition root。

风险：

1. concrete dependency 与业务调度强耦合；
2. 后续拆模块容易形成重复实例；
3. 单元测试必须绕过大量 constructor side effects；
4. AI/工程师容易在新模块中再次构建一套同类引擎。

### 2.2 `qidong()` / `tingzhi()` 内嵌 Runtime wiring

修复前 `qidong()` 直接执行：

```text
_cleanup_stale_run_states
    ↓
heartbeat update/start（条件）
    ↓
TONGBU.qidong
    ↓
QIAOJIE.shezhi_zongdiaodu
    ↓
QIAOJIE.qidong
```

`tingzhi()` 直接停止 heartbeat。

这些代码属于 lifecycle wiring，而不是总调度业务。

### 2.3 World Understanding observer 在 `zongdiaodu.py` 中直接 import + install

修复前：

```python
from .world_understanding_production import install_world_understanding_observer
install_world_understanding_observer()
```

这使 `zongdiaodu.py` 同时承担 bootstrap ownership。

注意：M2-01 **没有删除 import-time 注册语义**。

原因是目前不能在没有完整 caller 证明的情况下假设所有消费者都只通过显式 `qidong()` 进入。

因此本步只是把 bootstrap ownership 移到单独 seam，`zongdiaodu.py` 仍在模块加载阶段调用该 seam。

这是行为保持，而不是最终生命周期形态。

### 2.4 `_DetachedLegacyHeartbeat` compatibility surface 内嵌总调度

这个对象只是旧生命链 detach 后的 compatibility surface，不属于总调度核心业务实现。

---

## 3. 最终架构改造

### 3.1 新增 `runtime_composition.py`

路径：

`app/backend/tiangong-backend/v3/runtime_composition.py`

职责：

> V3 唯一 `Zongdiaodu` 实例的 typed composition root。

新增：

- `LockPort(Protocol)`；
- `ZongdiaoduComposition`；
- `build_zongdiaodu_composition(...)`。

由该模块统一构造原 constructor 中的 concrete dependencies：

- `HttpKehuduan`；
- `GutongCeng`；
- `GuanchaYinqing`；
- `JinhuaYinqing`；
- `JinhuaBiaodaRouter`；
- `JinhuaBihuanYinqing`；
- `ZiyuYinqing`；
- lifecycle lock；
- active user run lock。

关键约束：

- 不 import `zongdiaodu`；
- 不创建第二个 `Zongdiaodu`；
- 不创建 scheduler；
- 不持有 mutable runtime state；
- 不拥有执行权威；
- 不改变原字段名。

### 3.2 新增 `runtime_lifecycle.py`

路径：

`app/backend/tiangong-backend/v3/runtime_lifecycle.py`

新增 typed contracts：

- `HeartbeatPort(Protocol)`；
- `ZongdiaoduLifecycleHost(Protocol)`。

迁移：

- `DetachedLegacyHeartbeat`；
- `start_zongdiaodu_runtime(...)`；
- `stop_zongdiaodu_runtime(...)`。

启动顺序保持原样：

```text
host._cleanup_stale_run_states()
    ↓
if life_chain_enabled:
    heartbeat.gengxin_shenti
    heartbeat.qidong
    ↓
TONGBU.qidong()
    ↓
QIAOJIE.shezhi_zongdiaodu(host)
    ↓
QIAOJIE.qidong()
```

停止语义保持原样：

```text
if life_chain_enabled:
    heartbeat.tingzhi()
```

没有添加新的 lifecycle state machine。

### 3.3 新增 `runtime_bootstrap.py`

路径：

`app/backend/tiangong-backend/v3/runtime_bootstrap.py`

新增：

`install_zongdiaodu_import_observers()`

它只代理现有：

`install_world_understanding_observer()`

关键决定：

- bootstrap ownership 从 God Module 中抽离；
- 当前 import-time 触发时机保持不变；
- 暂不把 observer 安装迁入 `qidong()`。

### 3.4 `zongdiaodu.py` 收缩为 host + orchestration

实际 diff 相对 M1 final：

- additions：31；
- deletions：53；
- changes：84。

删除 constructor-only imports：

- `HttpKehuduan`；
- `GuanchaYinqing`；
- `JinhuaBiaodaRouter`；
- `JinhuaBihuanYinqing`；
- `JinhuaYinqing`；
- `ZiyuYinqing`。

保留：

- `GutongCeng` import，因为文件其他代码仍使用其 static parsing 能力；
- `threading` import，因为文件其他区域仍使用线程原语；
- `HuifuXinxi` import。

constructor 现在只消费：

`build_zongdiaodu_composition(...)`

并继续把对象绑定到历史字段名：

- `self.http_kehuduan`；
- `self.gutong`；
- `self.guancha_yq`；
- `self.jinhua_yq`；
- `self.jinhua_biaoda`；
- `self.jinhua_bihuan`；
- `self.ziyu_yq`；
- `self._lifecycle_lock`；
- `self._active_user_run_lock`。

因此旧调用方不需要改字段访问。

`qidong()` 现在只调用：

`start_zongdiaodu_runtime(self, ...)`

`tingzhi()` 现在只调用：

`stop_zongdiaodu_runtime(self, ...)`

### 3.5 V3 closed-world ownership 同步更新

`source-ownership.json` 中 `v3-backend-main.boundary_policy.implementation_roots` 新增：

- `runtime_bootstrap.py`；
- `runtime_composition.py`；
- `runtime_lifecycle.py`。

因此三个新模块不是“游离新增文件”，而是正式纳入 M1-03 established closed-world boundary。

---

## 4. 永久回归守门

新增：

`tests/test_zongdiaodu_p17_m2_01.py`

共 6 组测试。

### 4.1 Bootstrap delegation

验证：

- `zongdiaodu.py` 不再直接 import `install_world_understanding_observer`；
- 不再直接调用 `install_world_understanding_observer()`；
- 模块顶层仍调用 `install_zongdiaodu_import_observers()`；
- `runtime_bootstrap.py` 内仍调用原 observer installer。

目的：

> 抽 ownership，不改变时机。

### 4.2 Constructor composition ownership

验证：

- constructor 只调用一次 `build_zongdiaodu_composition`；
- constructor 不再直接创建各 concrete engines；
- constructor 不再直接创建 `threading.Lock()`。

### 4.3 Composition root concrete construction

验证 concrete construction 已集中到 `runtime_composition.py`，并阻止该模块反向 import `zongdiaodu`。

### 4.4 Lifecycle delegation

验证：

- `Zongdiaodu.qidong()` 调用 lifecycle port；
- `Zongdiaodu.tingzhi()` 调用 lifecycle port；
- `qidong()` 不再直接调用 `TONGBU.qidong` / `QIAOJIE.qidong`；
- `tingzhi()` 不再直接操作 heartbeat。

### 4.5 Typed lifecycle contract + historical ordering

验证：

- 使用 `Protocol`；
- lifecycle seam 不引入 `Any`；
- 启动调用顺序与原实现完全一致。

### 4.6 Closed-world registration

验证三个新 runtime seam 都已经进入 V3 implementation roots。

---

## 5. Architecture Gate 永久增强

`.github/workflows/architecture-gate.yml` 新增：

1. `Run P17 M2-01 Zongdiaodu composition regression`
2. `Compile P17 M2-01 V3 seams`

编译检查放在 closed-world / source-authority 检查之后。

原因：

Python `py_compile` 会创建 `__pycache__`；如果先编译，再执行 M1-03 closed-world guard，临时缓存目录会被正确识别为未分类 V3 顶层路径。

永久顺序因此固定为：

```text
Source Authority
    ↓
Generated Mirror
    ↓
M1 regression
    ↓
M1-03 closed-world regression
    ↓
M2-01 architecture regression
    ↓
py_compile
```

---

## 6. 候选构建阶段发现的问题

### 6.1 第一次 candidate validation 被 `__pycache__` 拦截

第一次候选 CI 中：

- exact anchor patch：成功；
- staging source promotion：成功；
- ownership patch：成功；
- architecture gate patch：成功；
- 失败点：`check-source-authority.py`。

错误：

`v3-backend-main.boundary_policy: unclassified immediate children: ['__pycache__']`

根因：

候选 workflow 先执行 `py_compile`，在 `v3/` 产生 `__pycache__`，随后 closed-world guard 按设计将其拦截。

这不是 M2 源码失败，而是验证顺序错误。

修复：

- 不删除目录；
- 不弱化 closed-world；
- 不给 `__pycache__` 开白名单；
- 只把 `py_compile` 移到所有 source-authority 检查之后。

第二次 candidate validation 全部通过。

该事件证明 M1-03 守门确实能够阻止未分类 V3 顶层内容。

---

## 7. 44 万字节 God Module 的安全迁移方式

`zongdiaodu.py` 约 443 KB。

由于 GitHub Contents API 对文件修改采用整文件 replacement，本步没有直接手工重写整文件，也没有重新生成一份替代 `zongdiaodu.py`。

采用：

1. 对原权威 blob 执行 deterministic exact-anchor replacement；
2. 每个 anchor 必须严格出现一次，否则迁移中止；
3. 在 GitHub Actions checkout 内生成候选文件；
4. 运行完整架构验证；
5. 输出 Artifact；
6. 校验 Artifact ZIP SHA256；
7. 校验候选文件 SHA256；
8. 校验候选文件 Git blob SHA；
9. 由 GitHub Git Data API 生成未引用 blobs；
10. 最终由 connector 显式创建 Git Tree / Commit / Ref。

候选 Artifact digest：

`sha256:055a3a84e66ac9aa380b76a8055594b575c9c766ffb7ff92f8947542af219bfa`

本地下载校验与该 digest 一致。

最终 `zongdiaodu.py` Git blob：

`e58a794ef1db64da74152df9d38b04d1cd233cfd`

最终候选 SHA256：

`0d3f62824dfb940d3f91a83b5eaf7d7c84ef457b2e5450f37f9b9647732a0a16`

施工用 staging、migration script、temporary workflow 均未保留在最终 tree。

并且 M2 分支历史最终清理为：

`M1 final -> M2-01 implementation`

施工提交不进入最终 M2 历史。

---

## 8. 最终验证结果

Architecture Gate Run：

`31706280843`

HEAD：

`9277cdb73e17a6b04a67e26edfa6bdc6068c5ea4`

最终状态：

`completed / success`

平台：

- Ubuntu：PASS；
- Windows：PASS。

### Source Authority

结果：

`16 independent authorities, 1 aliases, 24 generated targets, 1 closed-world boundaries`

### Generated mirrors

结果：

`{"ok": true, "mode": "check-committed", "config": "source-ownership.json"}`

### M1 regression

- 13 tests；
- PASS。

### M1-03 V3 boundary regression

- 5 tests；
- PASS。

### M2-01 regression

- 6 tests；
- PASS。

测试覆盖：

- bootstrap delegation；
- composition root ownership；
- constructor de-construction；
- lifecycle delegation；
- typed lifecycle contract；
- startup ordering；
- closed-world registration。

### Cross-platform compile

永久 Gate 对以下文件在 Ubuntu / Windows 两端执行 `py_compile`：

- `zongdiaodu.py`；
- `runtime_bootstrap.py`；
- `runtime_composition.py`；
- `runtime_lifecycle.py`。

两端均 PASS。

---

## 9. 行为保持声明

M2-01 没有改变：

- 唯一 Total Gateway；
- 唯一 V3 `Zongdiaodu`；
- 现有 `qidong()` 外部入口；
- 现有 `tingzhi()` 外部入口；
- 现有字段名；
- LLM transport 选择语义；
- heartbeat enable 条件；
- startup 顺序；
- World Understanding observer 安装时机；
- Memory 写读权威；
- World State 权威；
- Life Runtime；
- RunContext；
- Tool execution；
- A0-A5 authorization / confirmation；
- SSE / user-visible product behavior。

本步只是把：

```text
怎么创建对象
怎么接启动/停止
谁拥有 bootstrap wiring
```

从 God Module 中分离。

---

## 10. 当前架构结果

修复前：

```text
zongdiaodu.py
 ├─ Composition Root
 ├─ Bootstrap Owner
 ├─ Lifecycle Wiring
 ├─ Compatibility Stub
 └─ Orchestration God Module
```

修复后：

```text
runtime_bootstrap.py
    └─ observer bootstrap ownership

runtime_composition.py
    └─ typed dependency construction

runtime_lifecycle.py
    └─ typed startup / stop wiring

zongdiaodu.py
    └─ 唯一 V3 orchestration host
```

这是 M2 后续继续拆 God Module 的前置 seam，而不是新架构旁路。

---

## 11. 下一步建议：P17-M2-02

M2-01 完成后，下一刀建议继续保持行为等价：

**P17-M2-02：Turn Orchestration / Tool Loop Boundary Extraction**

优先从 `zongdiaodu.py` 中抽取：

- turn/run orchestration state transition；
- tool-call loop coordination；
- tool-result convergence / continuation decisions；
- 与 context projection 的边界。

约束：

- 不创建新 Runtime；
- 不创建第二工具执行器；
- Authority Gate / Tool Executor 仍只有一个；
- 先抽 typed Port/Service，再移动逻辑；
- 不在 M2-02 同时改行为策略。

`embedded_runtime.py` 暂不与 `zongdiaodu.py` 同刀拆分，避免两个 God Module 同时变化导致回归面失控。
