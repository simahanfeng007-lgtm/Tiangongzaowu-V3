# CHANGELOG v3.5

## 新增

- 新增 `model_adapters/` 模型协议适配层。
- 新增 `model_adapters/profiles.json`，覆盖 DeepSeek、MiniMax、GLM、MiMo、GPT、Kimi、豆包。
- 新增 7 个模型适配 action：
  - `model.adapter.info`
  - `model.adapter.list`
  - `model.adapter.detect`
  - `model.adapter.render_tool_schema`
  - `model.adapter.parse_tool_call`
  - `model.adapter.render_tool_result`
  - `model.adapter.roundtrip_test`
- 新增 OpenAI-compatible、Anthropic-compatible、Gemini-style 预留、XML/tag raw tool-call 解析器。
- 新增参数归一：`path/url/resource -> target`，`payload -> args`，`command/operation/op -> action`，`confirmed -> confirm`。

## 保持不变

- v3 仍然只注册一个真实工具：`omni_body`。
- v3.3.1 Skill Router 仍然是任务流程入口。
- v3.4 专业应用桥接层继续保留。
- 适配层不是智能体，不做自主规划。
