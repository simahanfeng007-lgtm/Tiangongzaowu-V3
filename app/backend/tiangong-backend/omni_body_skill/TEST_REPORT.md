# v3.5 Aggregate Test Report

# v3.5 Model Adapter Test Report

## Automated tests

```text
.........................                                                [100%]
25 passed in 0.28s
```

Exit code: `0`

## install_v3 dry run

```json
{
  "ok": true,
  "dry_run": true,
  "actions": [
    {
      "step": "copy_package",
      "from": "/mnt/data/tiangong_omni_body_v3_5_model_adapters",
      "to": "<USER_HOME>/.tiangong/v3/omni_body_skill"
    },
    {
      "step": "copy_tool",
      "from": "/mnt/data/tiangong_omni_body_v3_5_model_adapters/api/v1/v3/tools/omni_body.py",
      "to": "/api/v1/v3/tools/omni_body.py"
    },
    {
      "step": "merge_nengli",
      "file": "<USER_HOME>/.tiangong/v3/nengli_zhuche.json",
      "incoming_count": 47
    }
  ]
}
```

## Simulation

- Providers tested: DeepSeek, MiniMax, GLM, MiMo, GPT, Kimi, Doubao
- Roundtrip profiles tested: 9
- Roundtrip success: true
- Total runtime actions after v3.5: 751

See `examples/v3_5_model_adapters/v35_model_adapter_simulation_log.json`.

## Boundary

The adapter layer only translates model-native tool-call formats to CanonicalOmniCall and renders tool results back. It does not plan tasks, execute Skills, or generate final deliverables. Complex work still starts with `skill.route`.


---

# TEST_REPORT — Omni Body v3.3 Expanded Skill Pack

## 结果

```text
pytest tests -q: 10 passed
install_v3.py --dry-run: ok=true
v3.3 expansion simulation: 23 calls, 23 success, 0 failure
```

## 新增覆盖

- `delivery.v33.info`
- 网文章节：`writing.chapter.plan.create` + `qc.novel.chapter_check`
- 海报视觉：`poster.brief.create` + `qc.poster.commercial_check`
- 表格分析：`spreadsheet.analysis.plan.create` + `qc.sheet.analysis_report_check`
- 会议纪要：`meeting.minutes.create` + `qc.meeting.minutes_check`
- 销售话术：`sales.script.create` + `qc.sales.script_check`
- 课程教案：`course.lesson_plan.create` + `qc.course.plan_check`
- 知识库入库：`kb.ingestion_manifest.create` + `qc.kb.ingestion_check`
- 授权声音：`voice.consent_pack.create` + `qc.voice_authorized.delivery_check`
- SEO内容：`seo.content.brief.create` + `qc.seo.people_first_check`
- 内容日历：`content.calendar.create` + `qc.content.calendar_check`

## 说明

v3.3 仍然是工具，不是智能体；只通过 `omni_body(action,target,args)` 执行确定性动作并返回 evidence。QC 分数是规则型质量门，用于驱动大模型返工，不替代人类最终审美/专业审核。

---

# v3.3.1 Skill Router 补充测试

```text
pytest -q
15 passed
```

新增能力：

```text
skill.route
skill.get
skill.list
skill.step.check
skill.progress.report
```

安装干跑：

```text
python install_v3.py --dry-run
ok=true
incoming_count=45
```

模拟链路：

```text
skill.route → skill.get → skill.step.check → template.apply → file.write → docx.create → qc.docx.delivery_check → repair.plan → deliverable.package → skill.progress.report
```

模拟结果：

```text
推荐Skill：skill_word_business_proposal_worldclass_v1
QC分数：88
调用次数：12
```

结论：v3.3.1 已修正为“万能工具先分发 Skill，大模型按 Skill 操作工具”的结构；工具不隐藏运行完整 Skill。
