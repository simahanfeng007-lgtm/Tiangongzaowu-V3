# Skill 45：世界顶尖级浏览器自动化交付

## 定位
用于网页资料抓取、网页截图、自动填写、页面转 PDF、下载、验证页面状态等任务。优先使用 Playwright；缺失时生成可执行桥接脚本，不伪装成功。

## 流程
1. `skill.route` 匹配后，调用 `app.native.capability_probe`，target=`browser.playwright`。
2. 若 Playwright 可用，调用 `browser.playwright.goto` / `browser.playwright.screenshot` / `browser.playwright.extract_text` / `browser.playwright.pdf`。
3. 若不可用，调用 `browser.playwright.script.create` 或 `app.bridge.pack.create`。
4. 对抓取结果调用 `preview.generate` 或具体 `qc.research.evidence_check` / `qc.seo.people_first_check`。
5. 需要下载文件时使用 `browser.playwright.script.create` 生成下载脚本，或 fallback 到 `browser.chrome.download`。
6. 打包 HTML、文本、截图、脚本、manifest。

## 质量标准
- 必须保留 URL、访问时间、HTML快照、正文文本和截图/PDF证据。
- 对 JS 动态网页，静态抓取不算完成，必须标注需要 Playwright 或人工执行桥接脚本。
- 不得绕过验证码、登录、付费墙或安全校验。
