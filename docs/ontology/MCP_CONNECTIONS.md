# 通过现有 MCP 发现环境和调用应用

唯一连接配置仍是用户宿主的 `~/.tiangong/v3/mcp_servers.json`。字典的全部应用都有发现入口；不复制成另一套运行注册表。模型可查询服务及环境位置，然后连接读取真实工具，最后在原 Gateway 授权下调用。配置中 application ID 必须与 `system.apps` 或 `mcp.servers.list` 中的 ID 一致。

```json
{
  "servers": {
    "my-office": {
      "transport": "streamable_http",
      "url": "https://your-configured-mcp-host.example/mcp",
      "header_env": {"Authorization": "OFFICE_MCP_AUTHORIZATION"},
      "applications": ["feishu.docs", "microsoft.word"],
      "environment": {
        "location": "用户提供的服务环境位置",
        "workspace": "用户授权的测试工作区标识",
        "account_label": "用户提供的账号标签"
      },
      "enabled": false
    },
    "my-local-app": {
      "transport": "stdio",
      "command": "/absolute/path/to/installed/mcp-server",
      "args": [],
      "cwd": "/absolute/path/to/authorized/workspace",
      "env_refs": {"APP_TOKEN": "LOCAL_APP_TOKEN"},
      "applications": ["adobe.photoshop"],
      "environment": {"location": "本机", "platform": "目标操作系统"},
      "enabled": false
    }
  }
}
```

以上全部是禁用的填写示例，不是已安装服务或正式厂商 MCP 地址。宿主环境变量 `OFFICE_MCP_AUTHORIZATION` 保存完整鉴权头，例如由用户配置的 Bearer 值；模型只见变量名称。stdio 的 args、env 值及 HTTP 鉴权值不出现在服务列表。现有 literal env/headers 私有配置兼容读取，建议文件权限 0600。

发现顺序：`mcp.servers.list(args={app_id:实际ID})` → 选择已配置服务 → `mcp.tools.list(target=服务名)` → 按 next_cursor 读取工具页 → 用真实 inputSchema 构造 `mcp.tool.call` → 查询目标实际版本/内容 → 原对抗裁判。普通任务动作仍包装在模型生成的 composition 中。

环境位置是连接配置提供的数据，不是安装、授权或功能成功证明。未配置返回 not_configured；缺凭据返回变量名；未知服务不能执行；服务重定向不转发凭据。初始化、发现和调用共享有限期限；工具请求发出后丢失响应记为结果未知，先核对再重试。每次独立连接并关闭，不承诺有状态服务跨调用保留会话。

完整服务结果经已知凭据脱敏后保存在工作区 `mcp_observations/`，摘录明确标记截断及内容类型。图片/音频原文存在不表示模型已观察其内容，需要相应感知入口。工具列表返回实际 next_cursor；不能将第一页当全部。

协议依据：[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。实现只覆盖本页明确列出的功能，不宣称实现全部 MCP 可选能力或自动 OAuth 登录。
