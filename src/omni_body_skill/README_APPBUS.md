# Tiangong Omni Body v3 App Bus

这是 v3 原生的单工具应用能力总线：只注册一个真实工具 `omni_body`，所有应用工具组都挂载为 `action`。

## 当前规模

- 应用工具组：100 个
- 挂载动作：614 个
- 真实可执行核心：文件、代码、zip、docx、pptx、xlsx/csv、PDF、图片、音频基础、视频基础、回滚
- 应用级动作：已挂载协议；未接后端的动作返回 `[ADAPTER_REQUIRED]`，不假成功

## 工具边界

这是工具，不是智能体：

- 不接受模糊 goal 替代 action
- 不做任务自主规划
- 不绕过权限/验证码/登录/支付/二次确认
- 不把 adapter-only 动作标成已实现

## 调用

```json
{"action":"system.app_registry"}
{"action":"core.filesystem.file.write","target":"a.txt","args":{"content":"hello"}}
{"action":"microsoft.word.docx.create","target":"demo.docx","args":{"title":"Demo","sections":[{"heading":"H1","paragraphs":["P1"]}]}}
{"action":"adobe.photoshop.layer.create","target":"design.psd","args":{"name":"title"}}
```

最后一个如果没有 Photoshop adapter，会返回 adapter_required。

## App Bus Plus 补齐说明

本版新增便携 fallback，不再让以下动作仅停留在 adapter-only：

- `browser.chrome.goto` / `browser.chrome.extract_text` / `browser.chrome.download` / `browser.chrome.pdf.print` / `browser.chrome.screenshot`
- `adobe.photoshop.document.create` / `adobe.photoshop.layer.create` / `adobe.photoshop.text.add` / `adobe.photoshop.export.png`
- `jianying.project.create` / `jianying.media.import` / `jianying.timeline.cut` / `jianying.subtitle.add` / `jianying.music.add` / `jianying.cover.create` / `jianying.export.mp4`
- `feishu.docs.doc.create` / `feishu.docs.doc.read` / `feishu.docs.doc.update` / `feishu.docs.export.docx` / `feishu.docs.export.pdf`
- `audio.tts` / `elevenlabs.tts.create`
- `desktop.screenshot` / `desktop.click` / `desktop.type` / `desktop.hotkey` 以及 windows/macos/linux desktop aliases

这些 fallback 是“工具执行能力补齐”，不是智能体规划。模型仍必须传明确 `action/target/args`。

### 重要边界

- 浏览器 fallback 是静态抓取，不等于完整 Chrome 自动化；JS 点击、登录态、动态页面需要 Playwright/CDP。
- Photoshop fallback 是 JSON + PNG 便携图层项目，不等于真实 PSD 原生编辑。
- 剪映 fallback 是 JSON + ffmpeg 渲染，不等于剪映原生模板/特效系统。
- 飞书 fallback 生成本地 Markdown/DOCX，不等于远程飞书云文档；云端创建需飞书 API adapter。
- 声音克隆保持 A5 门控，不默认执行。可使用 `audio.tts` 做普通合成语音。
