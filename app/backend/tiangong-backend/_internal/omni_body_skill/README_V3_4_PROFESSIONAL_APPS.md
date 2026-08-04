# Omni Body v3.4 Professional Apps

## 定位
v3.4 是“专业级应用升级”。它不改变 `omni_body` 单工具入口，不把应用适配器拆成独立 v3 tool，也不让工具执行隐藏智能体流程。

正确闭环仍然是：

```text
大模型调用 skill.route / skill.get
→ 读取专业应用 Skill
→ 调用 app.adapter.health / app.native.capability_probe
→ 调用明确原子动作或生成桥接包
→ 应用执行结果回流
→ 调用 qc.* 质量门
→ 大模型返工
→ deliverable.package 打包
```

## v3.4 新增规模

- 总 action 数：744
- v3.4 专业应用 action：35
- 专业应用 profile：13
- 自动化测试：20 passed
- 安装 dry-run：ok=true

## 新增专业应用能力组

1. Playwright 浏览器自动化：脚本生成、URL打开、文本抽取、截图、PDF导出。
2. Microsoft Office / Graph：Office COM 桥接脚本、Word/PPT导出PDF桥接、Graph请求包。
3. WPS：WPS桥接说明与文件级 fallback。
4. Adobe Photoshop：UXP/PSJS脚本生成，同时保留便携图层PNG fallback。
5. Adobe Premiere / After Effects：ExtendScript JSX脚本生成。
6. Blender：Python脚本生成；本机安装 Blender 且确认后可 `blender --background` 执行。
7. Figma / Canva / 飞书 / 企业微信：API/request pack 生成，缺凭证不假执行。
8. Git：status/diff/log/add/commit 受控执行。
9. Docker：health/ps/compose config 只读检查。
10. SQLite：本地数据库查询，写操作必须 confirmed=true。

## 核心新增 action

```text
v34.professional_apps.info
app.adapter.health
app.adapter.matrix
app.native.capability_probe
app.bridge.script.create
app.bridge.pack.create
browser.playwright.script.create
browser.playwright.goto
browser.playwright.screenshot
browser.playwright.extract_text
browser.playwright.pdf
microsoft.graph.request_pack.create
microsoft.office.com.script.create
microsoft.word.native.export_pdf
microsoft.excel.native.chart.create
microsoft.powerpoint.native.export_pdf
wps.native.script.create
adobe.photoshop.uxp.script.create
adobe.premiere.jsx.script.create
adobe.aftereffects.jsx.script.create
blender.python.script.create
blender.python.run
figma.api.request_pack.create
canva.api.request_pack.create
feishu.api.request_pack.create
wechat_work.webhook.request_pack.create
git.status / git.diff / git.log / git.add / git.commit
docker.health / docker.ps / docker.compose.config
sqlite.query
```

## 边界

- 桥接脚本生成 ≠ 目标应用已经执行。
- request pack 生成 ≠ 外部 API 已提交。
- 缺失 Playwright/Office/Photoshop/Blender/账号凭证时，工具返回缺失证据或生成桥接包，不假成功。
- A0—A4由 Runtime 自动执行；声音克隆等 A5 动作仍被硬拒绝。
