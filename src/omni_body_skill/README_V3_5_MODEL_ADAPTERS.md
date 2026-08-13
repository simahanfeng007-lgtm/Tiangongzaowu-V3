# 天工 Omni Body v3.5：模型协议适配层

## 定位

v3.5 在 `omni_body` 外围增加 Model Tool Protocol Adapter，把不同模型厂商的原生工具调用格式统一转译成 CanonicalOmniCall，再交给总网关与 Runtime 裁决。

## 统一内部格式

```json
{
  "tool": "omni_body",
  "action": "skill.route",
  "target": "",
  "args": {"job": "帮我做一份企业AI培训方案Word"},
  "call_id": "call_xxx",
  "provider": "deepseek",
  "profile": "deepseek_openai"
}
```

## 宿主调用闭环

1. `model.adapter.detect` 识别 provider/model/profile。
2. `model.adapter.render_tool_schema` 生成原生工具 schema。
3. 模型输出 tool_call/tool_use/XML。
4. `model.adapter.parse_tool_call` 转成 CanonicalOmniCall，并丢弃任何模型生成的确认或授权字段。
5. 总网关和 Runtime 独立判定风险；A0—A4自动执行，A5硬拒绝。
6. `model.adapter.render_tool_result` 回渲染为原生 tool_result。

适配层不做任务规划、不做安全授权，也不向模型暴露几百个独立工具。
