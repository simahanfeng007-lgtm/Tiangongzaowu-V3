# 世界顶尖级商业汇报 PPT Skill

## 设计依据
参考金字塔原则、Duarte 受众转变/Big Idea/故事结构，以及高管汇报的先结论后证明原则。

## 输入契约
- 汇报对象
- 会议场景
- 要推动的决策
- 核心结论
- 支撑证据
- 页数范围

## 工具流程
1. `template.apply`，template_id=`executive_ppt`，生成故事线和机器可读 `design_spec.path`。
2. 模型构建 Big Idea、SCQA/金字塔结构、页面列表。
3. `pptx.create` 生成初版；传入 `template_id=executive_ppt` 和第1步返回的 `design_spec.path`。若故事线文件与设计规范同名，生成器会自动发现 `.design.json` 侧车文件。
4. `pptx.read` 核对页数、16:9、占位符、字体和有效视觉，再用 `qc.ppt.delivery_check` 检查结论句标题、页密度、CTA、结构完整度。
5. 低于80分则 `repair.plan`，返工：拆页、改标题、补证据、加决策请求。
6. `deliverable.package` 打包 pptx、故事线、QC报告。

## 顶尖交付标准
- 封面或第2页给出核心结论。
- 每页标题必须是结论句，不是名词标签。
- 一页只表达一个观点；正文不堆段落。
- 证据支持结论；图表有解释。
- 末页必须有明确决策请求或下一步行动。
- 禁止4:3、默认占位符母版和零有效视觉页面集；这些属于硬失败，不能用高文本分掩盖。

## 验收
- `qc.ppt.delivery_check.hard_gate_passed=true` 且 `score >= 80` 才能交付。
