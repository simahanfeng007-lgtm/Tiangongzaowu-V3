# 世界顶尖级商业 Word 方案 Skill

## 设计依据
参考 APMP 的提案管理最佳实践、Plain Language 清晰写作原则，以及咨询式商业文档的“先结论、后证据、再行动”结构。

## 适用
客户方案、AI转型方案、项目建议书、市场调研方案、培训落地方案。

## 输入契约
- 受众/决策人
- 业务目标
- 当前问题
- 已知证据/素材
- 预算/周期/边界
- 期望行动

## 工具流程
1. `template.apply`，template_id=`business_proposal`，生成方案骨架。
2. 模型补充执行摘要、问题、方案、实施、收益、风险、行动建议。
3. `docx.create` 生成 Word。
4. `qc.docx.delivery_check` 检查结构、清晰度、证据、行动性、AI腔。
5. `qc.writing.ai_tone_check` 进一步查泛化表达。
6. 分数低于80：调用 `repair.plan`，模型按问题返工。
7. `deliverable.package` 打包 docx、md大纲、QC报告。

## 顶尖交付标准
- 第一屏能让决策人知道“要不要做、为什么现在做、需要批准什么”。
- 章节必须有：执行摘要、现状问题、解决方案、实施路径、收益证据、风险假设、行动建议。
- 每个重大判断必须有事实、数据、案例或明确假设支撑。
- 不写空话；所有“提升/优化/赋能”必须可量化或可验证。

## 验收
- `qc.docx.delivery_check.score >= 80` 才能交付。
- `score >= 90` 可标记 world_class_ready。
