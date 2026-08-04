# CHANGELOG v3.2

## Added
- 新增 Delivery Kernel 工具模块：`tools/delivery_kernel.py`
- 新增 17 个交付级 action
- 新增 5 个世界级标杆 Skill
- 新增模板库、rubric、质量门说明
- 新增 v3 skill 注册 JSON
- 新增测试与交付模拟报告

## Changed
- `tools/omni_body_tool.py` 挂载 `DELIVERY_ACTIONS`，仍通过单工具 `omni_body` 分发。
- `registry/actions.appbus.merged.json` 更新总动作数量与 delivery 动作数量。
- `registry/v3_nengli_zhuche.append.json` 追加 v3.2 能力条目。

## Not Changed
- 不新增第二个 v3 tool。
- 不把 QC/模板动作拆成独立工具。
- 不做自主规划智能体。
