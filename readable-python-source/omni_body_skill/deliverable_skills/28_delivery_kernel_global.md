# v3.2 Delivery Kernel 总协议

## 定位
本包仍然是工具系统，不是智能体。大模型必须显式调用 `omni_body(action,target,args)`，并遵循“生成 → 质检 → 返工 → 再质检 → 打包”的交付闭环。

## 强制流程
1. 先调用 `template.list` 或读取对应 Skill。
2. 生成结构化大纲：`writing.outline.create` 或 `template.apply`。
3. 用原有工具生成交付物：docx/pptx/xlsx/code/video/image。
4. 调用对应 `qc.*.delivery_check`。
5. 若分数低于 80，调用 `repair.plan` 生成返工清单，然后修改交付物。
6. 最终调用 `deliverable.package` 打包交付物、QC报告、源材料和说明。

## 禁止
- 不允许 goal-only 调用。
- 不允许质检失败后直接宣称完成。
- 不允许 adapter-only 功能假成功。
