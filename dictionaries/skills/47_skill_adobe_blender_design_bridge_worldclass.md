# Skill 47：世界顶尖级 Adobe / Blender 专业设计桥接交付

## 定位
用于 Photoshop 图层、Premiere/After Effects 脚本、Blender 场景/建模/渲染的专业应用桥接。工具只生成可执行桥接脚本或在本机有后端时执行，不替模型完成创意判断。

## 流程
1. 调用 `app.adapter.health` 或 `app.native.capability_probe` 检查 Adobe/Blender 环境。
2. Photoshop 任务：优先 `adobe.photoshop.uxp.script.create`，或 portable `adobe.photoshop.layer.create` 做降级预览。
3. Premiere 任务：调用 `adobe.premiere.jsx.script.create` 生成导入/时间线/导出脚本。
4. After Effects 任务：调用 `adobe.aftereffects.jsx.script.create` 生成合成/文字/渲染队列脚本。
5. Blender 任务：调用 `blender.python.script.create`；若安装 Blender 且确认，调用 `blender.python.run`。
6. 回流 PNG/MP4/BLEND/脚本后，调用 `qc.image.delivery_check` 或 `qc.video.delivery_check`。
7. 打包源脚本、预览、manifest、QC 报告。

## 质量标准
- 必须保留脚本、manifest、运行说明、预览图/视频或明确缺失原因。
- 不得把脚本生成说成 Photoshop/Premiere/Blender 已执行。
- 视觉质量由模型根据 QC issues 返工脚本或素材。
