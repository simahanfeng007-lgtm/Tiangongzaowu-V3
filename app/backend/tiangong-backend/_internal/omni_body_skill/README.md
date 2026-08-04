# Tiangong Omni Body v3.5 Model Adapters

本包基于 v3.4 专业应用升级，新增 v3.5 模型协议适配层。详见 `README_V3_5_MODEL_ADAPTERS.md`。

# v3.4 Professional Apps 更新

本包已升级为 `tiangong_omni_body_v3_4_professional_apps`：

- 保留唯一 v3 工具：`omni_body`。
- 新增专业级应用桥接层：Playwright、Office/WPS、Adobe、Blender、Figma/Canva/飞书/企微请求包、Git、Docker、SQLite。
- 新增 `app.adapter.health / app.native.capability_probe / app.bridge.script.create / app.bridge.pack.create`。
- 新增四个专业应用 Skill：专业应用桥接、浏览器自动化、Office/WPS原生桥接、Adobe/Blender设计桥接。
- 工具不隐藏执行完整 Skill；桥接脚本生成不等于应用已执行。

详见：`README_V3_4_PROFESSIONAL_APPS.md`。

---

# v3.3.1 Skill Router 更新

本包已升级为 `tiangong_omni_body_v3_3_1_skill_router`：

- 新增 `skill.route / skill.get / skill.list / skill.step.check / skill.progress.report`。
- `omni_body` 先把任务匹配到 Skill，再由大模型按 Skill 调用原子工具。
- 工具不隐藏执行完整 Skill，不把高层 create 动作当最终交付。
- 复杂交付闭环：Skill 路由 → 取 Skill → 生成 → QC → 返工 → 再QC → 打包。

详见：`README_V3_3_1_SKILL_ROUTER.md`。

---

# Tiangong Omni Body v3 Skill

这是按天工 v3 两层标准重构后的 Omni Body 包。

## 结论

本包现在可以作为 v3 原生工具接入，但接入方式不是把 55 个 action 全部注册成工具，而是只注册一个真实可执行工具：

```text
/api/v1/v3/tools/omni_body.py
```

模型调用时必须传：

```json
{
  "action": "file.read",
  "target": "demo.txt",
  "args": {}
}
```

`action` 由 `omni_body` 内部分发。没有后端适配器的动作不会假成功。

## v3 Tool

工具入口：

```python
from api.v1.v3.tools.omni_body import run_omni_body

res = run_omni_body({
    "workspace": "./workspace",
    "action": "file.write",
    "target": "hello.txt",
    "args": {"content": "你好"}
})
```

返回格式符合 v3：

```json
{
  "schema": "tiangong.v3.omni_body.v1",
  "ok": true,
  "zhuangtai": "wancheng",
  "gongju": "omni_body",
  "action": "file.write",
  "target": "hello.txt",
  "result": {},
  "llm_brief": "...",
  "evidence": {
    "path": "...",
    "exists": true,
    "sha256": "...",
    "bytes": 12
  }
}
```

失败时：

```json
{
  "ok": false,
  "zhuangtai": "cuowu",
  "cuowu": "[A5_REJECTED] A5 action is forbidden for autonomous execution"
}
```

## v3 Skill / 能力注册

能力注册文件：

```text
v3/nengli_zhuche.json
v3/registry/nengli_zhuche.append.json
api/v1/v3/skills/*.json
```

关键工具能力：

```text
id: nengli_omni_body_v1
model_visible_skill: true
model_visible_tool: true
tool_release_state: released
tool_callable: true
registers_tool: true
tool_names: ["omni_body"]
```

工作流 Skill 只作为能力提示层，不注册成新工具。

## 安装

先 dry-run：

```bash
python install_v3.py --dry-run
```

正式安装：

```bash
python install_v3.py \
  --tools-dir /api/v1/v3/tools \
  --nengli-file ~/.tiangong/v3/nengli_zhuche.json
```

安装脚本会：

1. 将包复制到 `~/.tiangong/v3/omni_body_skill`。
2. 将 `api/v1/v3/tools/omni_body.py` 复制到 `/api/v1/v3/tools/omni_body.py`。
3. 将能力条目合并进 `~/.tiangong/v3/nengli_zhuche.json`。

如果 v3 运行工具的进程无法自动定位包根目录，设置：

```bash
export TIANGONG_OMNI_BODY_ROOT=~/.tiangong/v3/omni_body_skill
```

## 已实现动作

核心动作见：

```text
registry/actions.json
```

已实现的主要类别：

```text
file.*
zip.*
code.*
quality.*
python.run
shell.run
docx.create
pptx.create
sheet.create / sheet.read
mindmap.create
pdf.create_from_text / pdf.extract_text
image.*
audio.tone / audio.trim / audio.concat
video.info / video.cut / video.extract_audio / video.add_audio / video.slideshow
rollback.*
```

## 未接适配器动作

这些不会注册成独立 v3 tool，也不会假成功：

```text
audio.tts
voice.clone_authorized
browser.open
browser.search_web
desktop.screenshot
desktop.click
desktop.type
desktop.hotkey
```

调用时会返回：

```text
ok=false
zhuangtai=cuowu
cuowu=[ADAPTER_REQUIRED] ...
```

## 依赖策略

v3 最小依赖：

```text
python-docx
python-pptx
```

增强依赖写在：

```text
requirements-optional.txt
```

已经处理的冻结后端问题：

- `sheet.read` 已增加 `wb.close()`，避免 Windows 锁住 `.xlsx`。
- `sheet.create/sheet.read` 有 stdlib 简易 xlsx fallback。
- `pdf.create_from_text` 有 stdlib 简易 PDF fallback；没有 reportlab 时不再直接烟测失败。
- adapter-only 动作不会标成已实现工具。

## 测试

```bash
python tests/test_smoke.py
python tests/test_v3_adapter.py
```

本次构建测试结果：

```text
SMOKE_TEST_PASS
V3_ADAPTER_TEST_PASS
```

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


## v3.3 Expanded Skill Pack

在 v3.2 Delivery Kernel 基础上新增 10 类高频交付 Skill、21 个扩展 action、10 个模板、10 个 rubric 和对应 QC 质量门。详见 `README_V3_3_EXPANDED_SKILL_PACK.md`。
