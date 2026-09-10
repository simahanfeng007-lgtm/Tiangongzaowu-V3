# P10 R3-C：可复跑的代表性观察证据

状态：观察证据代码与本机联动验证已完成，独立部署观察未完成，R3 尚未退出。
用户的真实部署在其他电脑；本次工作范围是继续计划内代码与工程验证。

## 新增验证

`tests/test_learning_observation_window_p10.py` 使用一次性的本地 Life 数据、真实
Gateway SQLite、WorldStateStore 和测试签审的 Method Source Git fixture。
P9 fixture 现在接受可选 Life ID，默认值维持原样；本场景绑定实际创建的 Life ID，
并明确断言两侧身份相同。它没有修改生产 scope 解析、生命周期或授权逻辑。

1. 在旧记录迁移前注册执行计划、固定来源、claim 并开始一个 effect，确认持久化
   状态为 `SIDE_EFFECT_STARTED`。不调用模型或外部执行器。
2. 构造历史 active artifact、旧学习卡、pending patch 与缺失目标的未知归属记录；
   保存迁移前、观察起点、终点和重放后的记录哈希、归属及分类数量。
3. 安装实际 observer 并持久化 18 个入口的 coverage start。使用系统实时时钟记录
   起止，没有扩大模拟时钟来制造长窗口。
4. 通过原实现调用 14 个 Embedded Life 旧入口和 4 个 V3 兼容面。逐入口增量必须
   与 strict journal reader 读出的实际 observation 事件逐条相符，且时间落在窗口内。
5. 在观察期间接受新的 Method Source 发布，推进 70 次 World 更新并发生历史裁剪。
   新 current 必须与旧 pin 不同；在途任务仍读取原 World/Method，原 pin 也必须
   能从重新打开的磁盘 store 读回。effect 始终处于已开始状态。
6. 仅移除一次性 fixture 的 usage projection，再通过正常 Life journal replay
   恢复。记录内容、调用计数、coverage 起点和 P9 来源均保持一致。

历史样本、World 事件时间及 Method 签审是测试 fixture；观察窗口时间为真实墙钟。
本测试不能证明真实部署已经观察，也没有产生真实业务任务完成证据。

## 证据产物

每次成功运行写 `representative-window.json`，包括提交、dirty 状态、关键输入
SHA-256、四份快照、逐入口增量、窗口 journal 事件、固定的 WorldState/Method
Source 身份和在途 effect 状态。文件用独占创建，拒绝覆盖前次证据。

P10 CI 自动选择该 `test_learning_*` 文件，并把产物纳入原有每个平台、每个 head
的 Actions artifact：`output/p10-r1/r3c-representative/representative-window.json`。
artifact 的 `identity.json` 还核验整个测试输入在运行期间未变化。

本地可从仓库根目录运行（输出目录需换为未使用的名称）：

```powershell
$env:PYTHONUTF8 = '1'
$env:P10_OBSERVATION_ARTIFACT_DIR = 'output/p10-r3c-review-001'
.venv/Scripts/python.exe -m pytest -q tests/test_learning_observation_window_p10.py
```

该命令只运行一次性代表性测试，不采集或更改正在运行的部署。

## 本机结果与保留边界

- P9 既有 retention/lifecycle 回归 69 项通过；加初版联动场景的一组共 70 passed，
  5 条既有 warnings，212.73 秒。
- 加强为真实持久化 `SIDE_EFFECT_STARTED` 后，最终联动场景独立复跑通过，
  用时 12.37 秒，观察窗口 5,821 毫秒。
- 加强时一次测试错误使用状态名 `STARTED` 而失败；核对现有 store 状态定义后
  修正为 `SIDE_EFFECT_STARTED`。生产状态机未修改，失败日志保留在本机 output。
- 本机运行时测试仍是未提交输入，报告明确 `worktree_dirty=true` 并记录文件哈希；
  固定提交的双平台结果须以之后的 CI 为准。

观察到旧调用即继续保留对应兼容面。证据固定标记
`production_zero_usage_proven=false`、`independent_window_review_completed=false`、
`r3_exit_ready=false`。不使用 migration row 中声明式的 `source_pin_retained` 字段
代替这里实际读取的 P9 pin 证据。

仍须在实际部署或经独立复核的代表性工作负载中决定观察窗口的充分性，核对真实
残留与归属、在途来源和错误日志。该项是部署验证待办，不能凭本次测试关账。
在 R3 退出前，P10 不合并 main；P8/P9 的实际任务、正式签审及运维回退债仍保留。
