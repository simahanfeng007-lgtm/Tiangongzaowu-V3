# 世界顶尖级会议纪要与行动跟进交付

## 定位
这是 v3.3 扩展交付 Skill。它不是智能体，只规定大模型如何使用唯一工具 `omni_body` 完成高质量交付。

## 输入契约
- 任务目标
- 目标受众/使用场景
- 输入素材或约束
- 交付格式
- 质量门阈值：默认 80 可交付，90 世界级可交付

## 标准流程
1. 调用 `meeting.minutes.create` 生成交付骨架或计划。
2. 根据用户素材补全内容，不允许空泛占位。
3. 调用 `docx.create` 或对应 core/app action 生成最终交付物。
4. 调用 `qc.meeting.minutes_check` 进行质量门检查。
5. 若 score < 80，调用 `repair.plan` 生成返工计划。
6. 按 high/critical 问题优先返工，再重新运行 `qc.meeting.minutes_check`。
7. 调用 `deliverable.package` 打包交付物、QC报告、源素材和说明。

## 质量标准
使用模板 `meeting_minutes` 对应 rubric。验收必须包含：结构完整、受众明确、证据/细节充分、可执行、可复核、低AI腔。

## 工具调用样例
```json
{
  "action": "meeting.minutes.create",
  "target": "draft.md",
  "args": {
    "objective": "填写交付目标",
    "audience": "填写受众"
  }
}
```

```json
{
  "action": "qc.meeting.minutes_check",
  "target": "draft.md",
  "args": {}
}
```

## 返工原则
- 先修结构缺失，再修证据不足，再修表达质量。
- 每次返工后必须重跑 QC。
- 不允许因工具返回成功就直接交付，必须看 `score/grade/issues/warnings`。
