# Skill 46：世界顶尖级 Office / WPS 原生桥接交付

## 定位
用于高质量 Word、Excel、PPT、PDF 导出、图表、批量格式处理。优先使用文件级生成；需要真实应用能力时生成 Office COM / Microsoft Graph / WPS 桥接包。

## 流程
1. 调用 `app.native.capability_probe`，target=`microsoft.office` 或 `wps`。
2. 文档/表格/演示稿可直接用 `docx.create` / `sheet.create` / `pptx.create` 生成。
3. 需要原生 PDF 导出、复杂格式或 Office 特性时，调用 `microsoft.office.com.script.create` 或 `microsoft.graph.request_pack.create`。
4. WPS 环境调用 `wps.native.script.create` 生成桥接说明。
5. 生成或回流后调用 `qc.docx.delivery_check` / `qc.ppt.delivery_check` / `qc.sheet.delivery_check`。
6. 未通过则 `repair.plan`，大模型重写内容或重新生成文件。

## 质量标准
- 明确区分：文件级生成、Office COM 原生执行、Graph API 请求包、WPS 桥接说明。
- 导出 PDF 必须有文件存在、页数/体积/可打开证据。
- 不得假装已经操作用户本地 Office/WPS。
