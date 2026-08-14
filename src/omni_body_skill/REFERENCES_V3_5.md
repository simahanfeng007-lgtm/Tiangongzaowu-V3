# v3.5 参考基准

本版本按模型平台官方/主流工具调用协议设计：

- OpenAI Function Calling / Tool Calling：OpenAI-style `tools[type=function]`、`tool_calls`、`role=tool`。
- DeepSeek Tool Calls：DeepSeek API 提供 OpenAI-compatible tool calling。
- MiniMax：官方 OpenAI-compatible API 与 MiniMax-M2/M2.5 工具调用指南。
- GLM / Z.AI：Function Calling 文档，响应含 `tool_calls`、`function.name`、`function.arguments`。
- Xiaomi MiMo：官方说明兼容 OpenAI API 与 Anthropic API，Chat Completions 支持 tools。
- Kimi / Moonshot：Kimi Tool Calls / Tool Use 文档。
- 豆包 / 火山方舟：Function Calling 文档。

工程原则：协议碎片化由 `model_adapters` 吸收，`omni_body` 内部保持统一 `action/target/args`。
