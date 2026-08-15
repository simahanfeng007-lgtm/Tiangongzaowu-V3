# P18-M2 可再生执行内核封板记录

日期：2026-08-15  
分支：`agent/p18-m2-regenerative-execution-kernel`  
M1 基线：`8dd94f9da744405e81c1a3ef31e218847eaf4d3e`  
待验收代码候选：`b08c0c574442482e4f7bfa1e169a6afb347837cd`  
状态：**FINAL VALIDATION IN PROGRESS — 在永久四门验收全绿前不得宣告 M2 CLOSED。**

本文件当前仅用于触发最终跨平台永久验收，不改变任何 Runtime、Gateway、Store、Effect、Continuity、Completion 或启动链代码。

最终验收门：

1. Ubuntu focused M2 + inherited regressions；
2. Windows focused M2 + inherited regressions；
3. Ubuntu full repository pytest；
4. Windows full repository pytest。

四门全部 SUCCESS 后，本文件更新为正式 M2 closeout，并记录精确测试数字、架构不变量、崩溃恢复矩阵、Effect exactly-once 语义、Checkpoint/Frontier/Ledger 结果、P0/P1/P2 结论与 M3 admission。
