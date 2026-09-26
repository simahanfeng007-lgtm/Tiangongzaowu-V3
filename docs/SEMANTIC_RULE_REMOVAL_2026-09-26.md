# 删除程序自然语言裁决

用户任务由模型理解；程序不再用关键词、正则和内容评分来猜任务类型、读写意图、隐含义务、字数/列数要求、完成状态、继续目标或学习/记忆触发。前后端及实际执行路径已同步清理。

13 个内容类 QC/评分动作改为返回 `assessment_mode=model_required`、`score=null`、`acceptance=null`；这是未做内容评价，不能解释为质量 PASS。字典及发布视图同步为本地候选 2026.09.26.1，791 Tools、0 预置业务 Skills。验证面 1.36。

程序继续校验显式结构化权限、工具真实失败、签名/身份/来源、文件格式及字节证据；历史已封存记录保持原样。精确 `permission.denied` 错误码仍归入权限失败，自由文本不能设置该分类。

## 验证记录

- Python 全库：6203 passed、11 failed、93 skipped、1152 subtests passed，1116.04 秒。11 项均已处理；受影响模块及新增专项复测 220 passed、0 failed、1 skipped、22 subtests passed，16.14 秒。未新增跳过，未将原失败日志改写为全绿，未再次重复整个 Python 全库。
- Node 全库：259 passed、0 failed、2 skipped。精确 LFS 资产只读挂载于测试子进程，跟踪指针未改动。
- 官方 Source Authority、生成镜像、字典发布视图、冻结和 P17 动态/义务扫描通过。
- 最终源码真实模型回归：Word 本体模式 COMPLETED、文件核验 PASS（71.792 秒）；Excel 全量短编码模式 COMPLETED、文件核验 PASS（44.392 秒，8 次短码替换）。服务端模型 deepseek-flash，主任务模型错误 0；每组各 1 个辅助模型错误另行保留，不能称全链路零错误。
- 当前 791 工具及 11 份主文档通过编码还原一致性检查。此处是两个专项回归，不是新版完整 A/C 统计实验。

基线 `a372dbc7fb7d5218c07d96c33d21d29da4f48694`。最终 886 文件源码清单 SHA-256：`b4b3685ee0dec9063c647015d7ac205281263f9c15bfd1d6c2523a6c91c119cc`。全库之后唯一权威源码改动是权限错误码映射，对应受影响模块已复测，最终两次真实回归也对应此源码。

本地完整报告与原始证据位于项目外层 `acceptance/semantic-rule-removal-2026-09-26/`：`清理报告.md`、`candidate-source.json`、`validation-summary.json`、`final-live-summary.json`、`validation/`、`harness-final/runs/final-protocol-fix/`。旧失败和中间尝试全部保留。

本次仅为本地源码候选，未推送、未构建安装包、未替换正在使用的安装版本；没有 Windows 原生或生产部署验证。原 P11 本地分支及旧 32 次字典实验不改写。
