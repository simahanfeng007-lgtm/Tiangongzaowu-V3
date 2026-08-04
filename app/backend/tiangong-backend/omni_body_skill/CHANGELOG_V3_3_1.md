# CHANGELOG v3.3.1

## 主要修正

1. 新增 Skill Router：`skill.route / skill.get / skill.list / skill.step.check / skill.progress.report`。
2. 明确 `omni_body` 是工具，不是智能体；工具返回 Skill，模型执行 Skill。
3. 修复 v3.3 高层 create 动作边界：所有 create 类扩展动作标记为模板/骨架助手，不再被视为最终交付。
4. 新增 `registry/skill_router_index.json`，包含 15 个模型可见标杆 Skill。
5. 新增流程守卫 `skill.step.check`，防止模型跳过模板、生产、QC、返工、打包阶段。
6. 新增模拟报告与样例包，验证“路由 → 取 Skill → 生成 → QC → 返工/打包”的闭环。

## 不变项

- v3 仍只注册一个真实工具：`omni_body`。
- 不新增第二个工具。
- 不让工具隐藏执行完整 Skill。
- `planOnly=false` 保持不变；因为原子动作和 QC 仍是真执行。

