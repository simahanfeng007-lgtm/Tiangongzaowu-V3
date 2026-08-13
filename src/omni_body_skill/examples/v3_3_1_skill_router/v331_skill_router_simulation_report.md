# v3.3.1 Skill Router 模拟报告

## 流程
1. 大模型先调用 `skill.route`，工具返回匹配 Skill，不执行交付。
2. 大模型调用 `skill.get` 读取完整 Skill。
3. 大模型按 Skill 调用 `template.apply`、`file.write`、`docx.create`。
4. 大模型调用 `qc.docx.delivery_check` 质量门。
5. 大模型根据 QC 决定是否返工，最后 `deliverable.package`。

- 推荐 Skill：skill_word_business_proposal_worldclass_v1
- QC分数：88
- 产物包：v331_skill_router_sample_package.zip