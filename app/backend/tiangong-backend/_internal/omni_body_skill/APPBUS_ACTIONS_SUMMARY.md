# 应用动作清单

字典版本：`2026.09.27.8`。本表来自当前字典登记，表示接口声明，不证明厂商账号、原生应用或实际任务已经验收。

- 全量字典：791 项
- 应用：100 个
- 关联到应用的动作：615 项
- 声明已有实现的应用动作：128 项（包括别名和本地替代）
- 待接应用动作：487 项

以 `system.app_registry`、`mcp.servers.list` 和 `mcp.bindings.list` 返回的当前字典及连接状态为准。原生 Excel 图表仍待后端和实际对象回读，正确的应用关联不等于已实现。

|应用 ID|名称|类别|登记状态|声明已有实现|待接后端|
|---|---|---|---|---:|---:|
|core.filesystem|Core Filesystem|core|mixed_core_executable|11|0|
|core.archive|Core Archive|core|core_executable|2|0|
|core.code|Core Code & Python|developer|mixed_core_executable|7|0|
|core.office|Portable Office Generators|office|mixed_core_executable|7|0|
|core.image|Portable Image Core|image|core_executable|8|0|
|core.audio|Portable Audio Core|audio|mixed_core_executable|3|0|
|core.video|Portable Video Core|video|mixed_core_executable|5|0|
|core.rollback|Rollback Core|system|core_executable|2|0|
|microsoft.word|Microsoft Word|office|mixed_core_alias_and_adapter|2|0|
|microsoft.excel|Microsoft Excel|office|mixed_core_alias_and_adapter|2|1|
|microsoft.powerpoint|Microsoft PowerPoint|office|mixed_core_alias_and_adapter|1|0|
|wps.writer|WPS Writer|office|mixed_core_alias_and_adapter|2|0|
|wps.spreadsheet|WPS Spreadsheets|office|mixed_core_alias_and_adapter|2|0|
|wps.presentation|WPS Presentation|office|mixed_core_alias_and_adapter|1|0|
|ffmpeg|FFmpeg|video_audio|core_executable_if_installed|7|0|
|pillow|Pillow|image|core_executable_if_installed|8|0|
|browser.chrome|Google Chrome|browser|adapter_required|8|10|
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
|feishu.docs|飞书文档|office_cloud|adapter_required|5|2|
|feishu.sheets|飞书表格|office_cloud|adapter_required|0|6|
|feishu.wiki|飞书知识库|knowledge|adapter_required|0|6|
|feishu.im|飞书消息|collaboration|adapter_required|0|5|
|dingtalk.docs|钉钉文档|office_cloud|adapter_required|0|7|
|dingtalk.im|钉钉消息|collaboration|adapter_required|0|5|
|wechat_work|企业微信|collaboration|adapter_required|0|6|
|wechat.desktop|微信桌面端|collaboration_gui|adapter_required|0|5|
|yuque|语雀|knowledge|adapter_required|0|6|
|notion|Notion|knowledge_project|adapter_required|0|7|
|obsidian|Obsidian|notes|adapter_required|7|0|
|adobe.photoshop|Adobe Photoshop|image_design|adapter_required|8|4|
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
|jianying|剪映|video_editing|adapter_required|7|3|
|davinci.resolve|DaVinci Resolve|video_editing|adapter_required|0|8|
|finalcut|Final Cut Pro|video_editing|adapter_required|0|7|
|audacity|Audacity|audio_editing|adapter_required|0|7|
|reaper|REAPER|audio_editing|adapter_required|0|7|
|ableton.live|Ableton Live|music|adapter_required|0|6|
|flstudio|FL Studio|music|adapter_required|0|5|
|vscode|Visual Studio Code|developer|adapter_required|0|7|
|jetbrains.idea|JetBrains IDE|developer|adapter_required|0|6|
|git|Git|developer|adapter_required|5|5|
|github|GitHub|developer_cloud|adapter_required|0|7|
|gitlab|GitLab|developer_cloud|adapter_required|0|5|
|docker|Docker|devops|adapter_required|0|7|
|kubernetes|Kubernetes|devops|adapter_required|0|6|
|jupyter|Jupyter Notebook|developer_data|adapter_required|0|6|
|sqlite|SQLite|database|adapter_required|5|0|
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
|elevenlabs|ElevenLabs|tts_voice|adapter_required|1|2|
|windows.desktop|Windows Desktop|desktop|adapter_required|4|6|
|macos.desktop|macOS Desktop|desktop|adapter_required|4|6|
|linux.desktop|Linux Desktop|desktop|adapter_required|4|6|
|windows.powershell|PowerShell|shell|adapter_required|0|5|
