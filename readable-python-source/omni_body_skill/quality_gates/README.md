# v3.2 质量门

质量门是工具动作，不是智能体。模型每生成一版交付物后必须调用对应 `qc.*.delivery_check`，根据 evidence 中的 issues/warnings 再调用工具返工。

## 标准动作
- `qc.docx.delivery_check`
- `qc.ppt.delivery_check`
- `qc.sheet.delivery_check`
- `qc.code.delivery_check`
- `qc.research.evidence_check`
- `qc.video.delivery_check`
- `qc.image.delivery_check`
- `qc.writing.ai_tone_check`
- `rubric.evaluate`
- `repair.plan`
- `deliverable.package`

## 验收口径
- `score >= 90`: world_class_ready
- `score >= 80`: delivery_ready
- `score >= 70`: acceptable_with_minor_repair
- `score < 70`: 必须返工
