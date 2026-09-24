# Skill 44：世界顶尖级专业应用桥接交付

## 定位
该 Skill 用于让大模型拿到任务后，先判断是否需要真实专业应用能力，然后调用 `omni_body` 的 v3.4 专业应用层完成能力探测、脚本桥接、请求包生成、执行验证和质量门返工。

## 输入契约
- 目标应用：浏览器 / Office / WPS / Photoshop / Premiere / After Effects / Blender / Figma / Canva / 飞书 / 企业微信 / Git / Docker / SQLite。
- 目标动作：打开、读取、生成、导出、截图、自动化、请求包、桥接脚本、质检。
- 用户权限：是否已安装应用、是否有账号、是否允许本地执行、是否允许发送外部请求。
- 交付物：脚本、请求包、导出文件、截图、报告、zip。

## 模型执行流程
1. 调用 `app.adapter.health`，确认应用、依赖、凭证和可执行后端。
2. 调用 `app.native.capability_probe`，只针对目标应用做深度探测。
3. 若 native_ready，调用对应原子动作执行；若只有 bridge_ready，调用 `app.bridge.pack.create` 或专属 `*.script.create`。
4. 生成桥接包后，调用 `file.read` 读取 manifest/RUNBOOK，向用户说明需要在目标应用环境中执行。
5. 执行结果回流后，调用对应 `qc.*` 质量门。
6. 质量门未通过，调用 `repair.plan` 生成返工计划，再让模型修正脚本/请求包/交付物。
7. 通过后调用 `deliverable.package` 打包。

## 推荐动作
- `app.adapter.health`
- `app.adapter.matrix`
- `app.native.capability_probe`
- `app.bridge.script.create`
- `app.bridge.pack.create`
- `preview.generate`
- `repair.plan`
- `deliverable.package`

## 验收标准
- 不得把“桥接脚本已生成”说成“应用已执行”。
- 所有 native 缺失必须列出 missing_module / missing_executable / missing_env。
- 每个桥接包必须包含 manifest、脚本、RUNBOOK。
- 外部 API 请求不得自动发送，除非用户明确确认并提供凭证。
