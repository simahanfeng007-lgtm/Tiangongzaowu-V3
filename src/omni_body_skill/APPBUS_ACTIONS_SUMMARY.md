# v3.5 Action Summary

- Total runtime actions: 751
- Model adapter actions: 7
- Supported primary provider profiles: GPT/OpenAI, DeepSeek, MiniMax, GLM/Z.AI, Xiaomi MiMo, Kimi/Moonshot, Doubao/Volcengine Ark
- Single v3 tool remains: `omni_body`

# Omni Body App Bus 应用工具组清单（补齐版）

- 应用工具组：100 个
- 应用动作：614 个
- app-bus 已实现动作：110 个
- 本版新增便携 fallback：浏览器静态抓取、Photoshop式图层项目、剪映式视频项目、飞书本地文档、TTS、桌面基础适配。

|应用ID|名称|类别|状态|已实现|需适配器|
|---|---|---|---:|---:|---:|
|core.filesystem|Core Filesystem|core|portable_executable|11|0|
|core.archive|Core Archive|core|portable_executable|2|0|
|core.code|Core Code & Python|developer|portable_executable|7|0|
|core.office|Portable Office Generators|office|portable_executable|7|0|
|core.image|Portable Image Core|image|portable_executable|8|0|
|core.audio|Portable Audio Core|audio|portable_executable|3|0|
|core.video|Portable Video Core|video|portable_executable|5|0|
|core.rollback|Rollback Core|system|portable_executable|2|0|
|microsoft.word|Microsoft Word|office|portable_executable|2|0|
|microsoft.excel|Microsoft Excel|office|portable_executable|2|0|
|microsoft.powerpoint|Microsoft PowerPoint|office|portable_executable|1|0|
|wps.writer|WPS Writer|office|portable_executable|2|0|
|wps.spreadsheet|WPS Spreadsheets|office|portable_executable|2|0|
|wps.presentation|WPS Presentation|office|portable_executable|1|0|
|ffmpeg|FFmpeg|video_audio|portable_executable|7|0|
|pillow|Pillow|image|portable_executable|8|0|
|browser.chrome|Google Chrome|browser|mixed_portable_and_adapter|7|11|
|browser.edge|Microsoft Edge|browser|adapter_required|0|10|
|browser.firefox|Mozilla Firefox|browser|adapter_required|0|10|
|google.docs|Google Docs|office_cloud|adapter_required|0|7|
|google.sheets|Google Sheets|office_cloud|adapter_required|0|7|
|google.slides|Google Slides|office_cloud|adapter_required|0|6|
|google.drive|Google Drive|storage|adapter_required|0|8|
|gmail|Gmail|mail|adapter_required|0|7|
|microsoft.outlook|Microsoft Outlook|mail_calendar|adapter_required|0|7|
|microsoft.onedrive|OneDrive|storage|adapter_required|0|7|
|microsoft.sharepoint|SharePoint|storage_docs|adapter_required|0|7|
|microsoft.teams|Microsoft Teams|collaboration|adapter_required|0|6|
|microsoft.onenote|OneNote|notes|adapter_required|0|5|
|microsoft.visio|Visio|diagram|adapter_required|0|5|
|feishu.docs|飞书文档|office_cloud|mixed_portable_and_adapter|5|2|
|feishu.sheets|飞书表格|office_cloud|adapter_required|0|6|
|feishu.wiki|飞书知识库|knowledge|adapter_required|0|6|
|feishu.im|飞书消息|collaboration|adapter_required|0|5|
|dingtalk.docs|钉钉文档|office_cloud|adapter_required|0|7|
|dingtalk.im|钉钉消息|collaboration|adapter_required|0|5|
|wechat_work|企业微信|collaboration|adapter_required|0|6|
|wechat.desktop|微信桌面端|collaboration_gui|adapter_required|0|5|
|yuque|语雀|knowledge|adapter_required|0|6|
|notion|Notion|knowledge_project|adapter_required|0|7|
|obsidian|Obsidian|notes|adapter_required|0|7|
|adobe.photoshop|Adobe Photoshop|image_design|mixed_portable_and_adapter|8|4|
|adobe.illustrator|Adobe Illustrator|vector_design|adapter_required|0|9|
|adobe.indesign|Adobe InDesign|layout_design|adapter_required|0|7|
|adobe.lightroom|Adobe Lightroom|photo|adapter_required|0|6|
|figma|Figma|design_collab|adapter_required|0|8|
|canva|Canva|design_cloud|adapter_required|0|7|
|sketch|Sketch|design|adapter_required|0|6|
|adobe.premiere|Adobe Premiere Pro|video_editing|adapter_required|0|10|
|adobe.aftereffects|Adobe After Effects|motion_graphics|adapter_required|0|8|
|adobe.audition|Adobe Audition|audio_editing|adapter_required|0|6|
|capcut|CapCut|video_editing|adapter_required|0|10|
|jianying|剪映|video_editing|mixed_portable_and_adapter|7|3|
|davinci.resolve|DaVinci Resolve|video_editing|adapter_required|0|8|
|finalcut|Final Cut Pro|video_editing|adapter_required|0|7|
|audacity|Audacity|audio_editing|adapter_required|0|7|
|reaper|REAPER|audio_editing|adapter_required|0|7|
|ableton.live|Ableton Live|music|adapter_required|0|6|
|flstudio|FL Studio|music|adapter_required|0|5|
|vscode|Visual Studio Code|developer|adapter_required|0|7|
|jetbrains.idea|JetBrains IDE|developer|adapter_required|0|6|
|git|Git|developer|adapter_required|0|10|
|github|GitHub|developer_cloud|adapter_required|0|7|
|gitlab|GitLab|developer_cloud|adapter_required|0|5|
|docker|Docker|devops|adapter_required|0|7|
|kubernetes|Kubernetes|devops|adapter_required|0|6|
|jupyter|Jupyter Notebook|developer_data|adapter_required|0|6|
|sqlite|SQLite|database|adapter_required|0|5|
|postgresql|PostgreSQL|database|adapter_required|0|5|
|mysql|MySQL|database|adapter_required|0|5|
|redis|Redis|database_cache|adapter_required|0|5|
|powerbi|Power BI|bi|adapter_required|0|4|
|tableau|Tableau|bi|adapter_required|0|4|
|airtable|Airtable|database_cloud|adapter_required|0|6|
|trello|Trello|project|adapter_required|0|5|
|jira|Jira|project|adapter_required|0|5|
|linear|Linear|project|adapter_required|0|5|
|asana|Asana|project|adapter_required|0|5|
|clickup|ClickUp|project|adapter_required|0|5|
|salesforce|Salesforce|crm|adapter_required|0|6|
|hubspot|HubSpot|crm|adapter_required|0|6|
|zoho.crm|Zoho CRM|crm|adapter_required|0|6|
|sap|SAP|erp|adapter_required|0|5|
|kingdee|金蝶|erp|adapter_required|0|5|
|yonyou|用友|erp|adapter_required|0|5|
|shopify|Shopify|ecommerce|adapter_required|0|5|
|wordpress|WordPress|cms|adapter_required|0|5|
|openai_api|OpenAI API|ai_model|adapter_required|0|6|
|deepseek_api|DeepSeek API|ai_model|adapter_required|0|4|
|anthropic_api|Anthropic API|ai_model|adapter_required|0|3|
|gemini_api|Gemini API|ai_model|adapter_required|0|4|
|comfyui|ComfyUI|image_ai|adapter_required|0|6|
|stable_diffusion_webui|Stable Diffusion WebUI|image_ai|adapter_required|0|5|
|whisper|Whisper ASR|audio_ai|adapter_required|0|4|
|tts.edge|Edge TTS|tts|adapter_required|0|2|
|elevenlabs|ElevenLabs|tts_voice|mixed_portable_and_adapter|1|2|
|windows.desktop|Windows Desktop|desktop|mixed_portable_and_adapter|4|6|
|macos.desktop|macOS Desktop|desktop|mixed_portable_and_adapter|4|6|
|linux.desktop|Linux Desktop|desktop|mixed_portable_and_adapter|4|6|
|windows.powershell|PowerShell|shell|adapter_required|0|5|


## v3.3 Expanded Skill Pack

新增 21 个扩展交付 action，总方向为：网文、海报视觉、表格分析、会议纪要、销售话术、课程教案、知识库入库、授权声音、SEO内容、内容日历。仍只注册一个 v3 工具 `omni_body`，所有新增能力以 action 方式挂载。

新增 actions 见 `registry/delivery_actions.json` 与 `tools/delivery_v33.py`。

---

# v3.3.1 Skill Router Actions

新增模型可调用动作：

```text
skill.route              根据任务描述匹配 Skill，只返回 Skill 卡片，不执行交付
skill.get                获取完整 Skill Markdown 和执行契约
skill.list               列出模型可见交付 Skill
skill.step.check         根据 completed_actions / last_qc 检查流程位置，返回允许的下一步 action
skill.progress.report    汇总已完成动作、QC历史、产物和下一步状态
```

边界：`omni_body` 是工具，不是智能体；Skill 由模型执行，工具只负责 Skill 分发、原子动作、质量门、返工计划和打包。


# v3.4 Professional Apps

Total runtime actions: 744
V3.4 professional actions: 35
Professional profiles: 13

See `README_V3_4_PROFESSIONAL_APPS.md`.
