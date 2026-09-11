# P10 R3 代码接续检查点

本记录区分工程代码、固定提交验证及真实部署观察。用户的真实部署在其他电脑，
本机工作范围是继续原计划的代码与工程验证。P10 未合并 main，R3 部署退出条件
仍待复核；这里没有进入 P11 或删除旧兼容权威。

## 已完成代码

- 从 `02213f6` 中的传输分块恢复 R3-B 原补丁，逐文件校验 26 个 Git blob 身份；
  恢复后清理临时传输分块和导出工作流。
- `dff11074411075cc9067a611f66e34eb2c8bf923`：四类 V3 实际兼容入口观测接入唯一
  Life journal，保留逐入口 coverage start；修复普通 Windows DOS 盘符路径被
  `OBJ_DONT_REPARSE` 误拒绝的问题，保留同 handle 观察、设备身份核对和 junction
  拒绝。Plane 明确升级 1.13，105 条冻结及 17 个独立 Source Authority 保留。
- `a70b0b668eeee12fe7822627b6862fbe6eaa7cde`：R3-C 可复跑的代表性证据测试；同一
  Life 下核对历史、pending、unknown、18 个实际入口调用、签名 journal 重放和
  `SIDE_EFFECT_STARTED` 在途任务的 P9 World/Method Source pin。
- `ed6094e2d7712b054dcd054615c70b442f2af005`：仅修正新测试 fixture 的 SQLite
  连接清理。`git diff a70b0b6 ed6094e -- src app source-ownership.json docs/p19-r2`
  为空，产品源码、生成镜像及冻结权威不变。

## 已核对的固定提交结果

| 提交/环境 | 原始结果 | 证据 |
|---|---|---|
| dff1107 / Ubuntu CI | 1488 passed，36 skipped，11 subtests，452.17 秒 | [run 34455647768](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34455647768) |
| dff1107 / Windows CI | 1513 passed，11 skipped，11 subtests，1129.64 秒 | 同 run，job 102801280595 |
| a70b0b6 / Ubuntu CI | 1489 passed，36 skipped，11 subtests，487.98 秒 | [run 34456716836](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34456716836) |
| a70b0b6 / Windows CI | 1514 passed，11 skipped，11 subtests，1816.51 秒 | 同 run，job 102804717968 |
| a70b0b6 / 本机 Windows | 1513 passed，12 skipped，11 subtests，1579.71 秒 | `output/p10-fixed-local-a70b0b6/validation-summary.json` |
| ed6094e / 本机定向 | 最终观察场景 1 passed，12.39 秒 | `output/p10-r3c-fixture-close.log` |
| ed6094e / Ubuntu CI | 1489 passed，36 skipped，11 subtests，473.42 秒 | [run 34459398714](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34459398714)，job 102813356948 |
| ed6094e / Windows CI | 1514 passed，11 skipped，11 subtests，1605.92 秒 | 同 run，job 102813356757 |

上述完成的运行均为 0 failed/errors，保留 5 条既有 Pydantic warnings。本机完整组
直接复用提交中的 P10 workflow 选择及验证脚本，移除全部 UPDATE 标志和 Windows
CI 跳过覆盖变量。源检查、提交镜像检查和测试输入前后 SHA-256 对比均通过。
本机 12 个 skip 中，9 个涉及源码发布不含的旧冻结/打包运行时，3 个因为本机
没有符号链接创建权限；没有把这些缺失能力记成 PASS。

`ed6094e` 的完整双平台 CI 已 SUCCESS：
[run 34459398714](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34459398714)
绑定完整 head `ed6094e2d7712b054dcd054615c70b442f2af005`。两个 job 均完成
输入不变断言和原始证据上传。本记录随后仅以文档提交补入，不把文档提交的 head
冒充上述已测试的代码 head；代码、测试、工作流和冻结文件仍保持一致。

## 当前候选的代表性证据

固定 `a70b0b6`、干净工作区下的窗口为 5,347 毫秒，18 个入口各一次实际调用。
四条分类记录为：Source Evolution 待迁移学习卡、历史 active artifact、pending
patch、unknown ownership 各一条；summary 显示 pending 2、unknown 1。起止记录
哈希和归属一致，重放后计数与 coverage start 一致。在途 effect 处于
`SIDE_EFFECT_STARTED`；新的 Source 发布与 70 次 World 更新没有切换原固定来源。

完整 JSON、逐入口 journal 事件和输入身份在本机
`output/p10-fixed-local-a70b0b6/r3c-representative/representative-window.json`，
CI 对应 JSON 随各平台 Actions artifact 保存，原工作流保留期限为 14 天。
本机另外保存 GitHub 原始 job 日志；artifact ZIP 的工具下载引用在本机返回 403，
未把该 ZIP 记成已本地校验。远端 artifact 仍可在对应 run 中获取。

## 尚未完成的计划项

R3-C 仍需实际部署或独立复核认可的代表性持续窗口、真实残留与归属核对，以及
在途来源抽查。当前短时 fixture 证据明确保持 `r3_exit_ready=false` 和
`production_zero_usage_proven=false`；有实际旧调用就继续保留兼容面。

之后 R4 才执行最终固定候选的完整 Architecture、Python/Node、P14/P19 等门禁、
正常 PR 和主干核对。本次 P10 focused 组虽包含完整 P19 Golden，仍不等于已经
完成 R4。P8/P9 的真实模型任务、正式签审、打包补丁及生产回退债继续保留。

main 基线仍是 `b1ea3e9d511aae9cabcc816e071ad3da645ae753`；合并检查点为
10/18=55.6%，不是产品验收率。
