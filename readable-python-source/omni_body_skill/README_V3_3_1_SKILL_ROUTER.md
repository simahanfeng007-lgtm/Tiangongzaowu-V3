# Tiangong Omni Body v3.3.1 Skill Router

## 定位
v3.3.1 修正 v3.2/v3.3 的边界：

- `omni_body` 仍然是唯一 v3 可执行工具。
- `skill.route / skill.get / skill.list / skill.step.check / skill.progress.report` 只负责把合适的 Skill 交给大模型、校验流程位置、报告进度。
- 工具不读取 Skill 后自行执行完整工作流。
- 大模型必须读取 Skill，并按 Skill 调用原子工具、QC质量门、返工工具、打包工具。

正确闭环：

```text
用户任务
  ↓
大模型调用 omni_body.skill.route
  ↓
omni_body 返回匹配 Skill 卡片
  ↓
大模型调用 skill.get 读取完整 Skill
  ↓
大模型按 Skill 流程调用原子工具
  ↓
大模型调用 qc.* 质量门
  ↓
不通过则返工，再 qc
  ↓
通过后 deliverable.package
```

## 新增 action

```text
skill.route
skill.get
skill.list
skill.step.check
skill.progress.report
```

## 高层 create 动作边界修复
v3.3 中以下动作不再被视作最终交付生成器，只作为模板/骨架助手：

```text
writing.chapter.plan.create
poster.brief.create
spreadsheet.analysis.plan.create
meeting.minutes.create
sales.script.create
course.lesson_plan.create
kb.ingestion_manifest.create
voice.consent_pack.create
seo.content.brief.create
content.calendar.create
```

它们返回结果中会带：

```json
{
  "not_final_delivery": true,
  "tool_boundary": {
    "role": "template_or_skeleton_helper",
    "model_must_complete_content": true
  }
}
```

## 典型调用

### 1. 路由任务到 Skill

```json
{
  "action": "skill.route",
  "args": {
    "job": "帮我做一份给客户看的企业AI培训方案Word，要求专业可成交",
    "context": {"deliverable": "docx", "intent": "商业方案"}
  }
}
```

### 2. 获取完整 Skill

```json
{
  "action": "skill.get",
  "target": "skill_word_business_proposal_worldclass_v1"
}
```

### 3. 检查流程位置

```json
{
  "action": "skill.step.check",
  "target": "skill_word_business_proposal_worldclass_v1",
  "args": {
    "completed_actions": ["template.apply", "docx.create", "qc.docx.delivery_check"],
    "last_qc": {"score": 72, "acceptance": false},
    "artifacts": ["proposal.docx"]
  }
}
```

返回 `repair_loop`，模型必须返工。

## 已纳入路由的标杆 Skill

- 商业 Word 方案
- 商业汇报 PPT
- 代码工程交付
- 资料/论文综述
- 短视频交付
- 网文章节
- 海报/修图视觉
- 表格分析报告
- 会议纪要与行动跟进
- B2B销售话术
- 课程/教案
- 知识库入库
- 授权声音/音频
- SEO/网页内容
- 内容日历

## 测试

```bash
pytest -q
python install_v3.py --dry-run
```

当前测试：15 passed。

