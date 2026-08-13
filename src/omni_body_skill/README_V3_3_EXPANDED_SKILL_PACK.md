# Omni Body v3.3 Expanded Skill Pack

v3.3 在 v3.2 Delivery Kernel 上扩展十类高频交付能力：网文章节、海报视觉、表格分析、会议纪要、B2B销售话术、课程教案、知识库入库、授权声音/音频、People-first SEO内容、内容日历。

设计原则：

1. 仍然只注册一个真实工具 `omni_body`。
2. 新增能力全部是 action + QC + Skill 流程，不做智能体。
3. 工具只执行明确动作并返回 evidence。
4. 质量达不到 80 分必须返工；90 分以上才标记 world_class_ready。
5. 声音相关只做授权交付包与合规质检，不默认执行声音克隆。

## 新增 actions

见 `registry/delivery_actions.json` 和 `tools/delivery_v33.py`。

## 新增 Skill

见 `skills/34_*.md` 至 `skills/43_*.md`。

## 调用例

```json
{
  "action": "meeting.minutes.create",
  "target": "meeting.md",
  "args": {"topic": "客户项目推进会"}
}
```

```json
{
  "action": "qc.meeting.minutes_check",
  "target": "meeting.md",
  "args": {}
}
```
