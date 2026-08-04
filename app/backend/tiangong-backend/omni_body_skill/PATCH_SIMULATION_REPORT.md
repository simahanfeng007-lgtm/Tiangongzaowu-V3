# Omni Body App Bus Plus 补齐模拟报告

- 工作区：/mnt/data/omni_body_patch_workspace
- 调用总数：11
- 成功：10
- 失败/门控：1

|动作|状态|说明|
|---|---:|---|
|browser.chrome.goto|OK|omni_body browser.chrome.goto completed for data:text/html,<title>测试页</title><h1>你好</h1><p>Omni Body browser fallback</p>|
|browser.chrome.extract_text|OK|omni_body browser.chrome.extract_text completed for data:text/html,<title>提取</title><p>文本提取成功</p>|
|adobe.photoshop.document.create|OK|omni_body adobe.photoshop.document.create completed for design.psd|
|adobe.photoshop.layer.create|OK|omni_body adobe.photoshop.layer.create completed for design.psd|
|jianying.project.create|OK|omni_body jianying.project.create completed for project01|
|jianying.subtitle.add|OK|omni_body jianying.subtitle.add completed for project01|
|jianying.export.mp4|OK|omni_body jianying.export.mp4 completed for project01|
|feishu.docs.doc.create|OK|omni_body feishu.docs.doc.create completed for [no target]|
|audio.tts|OK|omni_body audio.tts completed for [no target]|
|elevenlabs.tts.create|OK|omni_body elevenlabs.tts.create completed for [no target]|
|voice.clone_authorized|BLOCK/FAIL|[ADAPTER_REQUIRED] voice_backend_with_consent: Only for owned/consented voices; disabled by default and requires explicit external consent gate.|

## 生成文件

- `design.psd.omni_ps.json`
- `design.psd.omni_ps.png`
- `project01.omni_jy.json`
- `jianying_title_card.png`
- `browser_snapshots/20260705_004434_881_browser.chrome.goto_text_html_title_测试页__title_h1_你好__h1_p_Omni_Body_browser_fallback__p.html`
- `browser_snapshots/20260705_004434_881_browser.chrome.goto_text_html_title_测试页__title_h1_你好__h1_p_Omni_Body_browser_fallback__p.txt`
- `media/jianying_output.mp4`
- `media/tts.wav`
- `media/elevenlabs_fallback.wav`
- `feishu_docs/测试文档.md`
- `feishu_docs/测试文档.docx`
