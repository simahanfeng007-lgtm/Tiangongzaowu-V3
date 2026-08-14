# v3.5 Model Protocol Adapter Layer

该目录把 DeepSeek、MiniMax、GLM、MiMo、GPT、Kimi、豆包等模型的原生工具调用协议转译成 `omni_body` 内部统一指令。它不是智能体，不规划任务，只做协议渲染、解析、参数归一和结果回传。

内部标准调用：

```json
{
  "tool": "omni_body",
  "action": "skill.route",
  "target": "",
  "args": {"job": "做一份企业AI培训方案Word"}
}
```

模型不得传入 `confirm`、`confirmed`、`allow_shell` 等授权字段。风险等级由 Runtime 独立计算：A0—A4自动执行，A5硬拒绝。

宿主 Runtime 的标准流程：

1. 用 `model.adapter.render_tool_schema` 给对应模型渲染原生工具定义。
2. 模型返回 tool_call/tool_use/XML 后，用 `model.adapter.parse_tool_call` 转成 CanonicalOmniCall。
3. 宿主把调用交给总网关和 Runtime 裁决，再执行 `run_omni_body`。
4. 用 `model.adapter.render_tool_result` 把结果转回对应模型的 tool_result 格式。
