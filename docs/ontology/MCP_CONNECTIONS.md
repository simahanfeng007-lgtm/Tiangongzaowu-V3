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

环境位置是连接配置提供的数据，不是安装、授权或功能成功证明。未配置返回 not_configured；缺凭据返回变量名；未知服务不能执行；服务重定向不转发凭据。初始化、发现和调用共享有限期限；工具请求发出后丢失响应记为结果未知，先核对再重试。

2026.09.27.6 起，同一宿主任务、身份、代际和工作区内复用有界会话（最多 32 个，闲置 15 分钟）。模型不能指定作用域。发现回执返回 `connection_id`，后续有状态动作可绑定该 ID；账号、配置、连接失败或过期使旧引用失效，重新观察后才能决定下一动作。`mcp.session.close` 关闭本作用域连接，不承诺取消远端任务。无宿主任务身份及显式 `session_mode: invocation` 保留单次连接。进程重启后不伪造旧会话；HTTP 远端保留的作业可按原账号和任务 ID 查询，stdio 服务进程自身丢失的作业需由服务提供恢复能力。

支持 Tasks 的服务必须在初始化和具体工具 `execution.taskSupport` 中声明支持。`mcp.tool.call` 可传 `task: {ttl: 60000}`，返回 `taskId`、`connection_fingerprint` 和 pending。之后用 `mcp.tasks.get/result/cancel` 的 `task_id` 与原 `connection_fingerprint` 查询；`mcp.tasks.list` 分页发现。新连接可以查询原远端任务，但配置或授权身份改变时禁止混用。作业受理不是完成，取消不是回滚，failed/cancelled 不能交付为成功；调用失败后不自动重放。完整回执仍保存到原工作区和执行事实链，没有另建任务库。

可选 OAuth 使用同一配置的 `oauth: {issuer, client_id, scopes}`；issuer 与预注册公共客户端必须来自账号所有者。`mcp.auth.begin` 返回登录 URL，账号所有者在浏览器授权后 `mcp.auth.status` 查询。采用发行者固定、资源元数据发现、PKCE S256、state/可选 iss 核对及 loopback 回调；刷新保留授权身份，重新登录或配置变化使旧身份失效。未实现动态客户端注册和所有厂商扩展。凭据写在原配置的私有 `oauth_credentials`，POSIX 要求 0600；这不是操作系统加密保险库，Windows 依赖宿主私有目录 ACL，模型回执不包含 token。

宿主可用 `python scripts/configure-mcp.py --help` 登记真实服务、应用及环境位置。该命令不安装服务、不猜厂商地址、不代用户登录。覆盖已有配置须显式 `--replace`。在实际 `tools/list` 观察后，可配置 `action_bindings: {"实际字典动作": {"tool": "实际工具名", "input_schema_sha256": "实测摘要"}}`。模型用 `mcp.bindings.list` 发现；`mcp.action.call` 在执行前核对应用关联、当前工具 schema 与连接身份。参数为真实远端具体参数，不引入映射语言；需求含义与结果仍需实际任务证明，绑定本身不会把待实现统计改成已实现。

完整服务结果经已知凭据脱敏后保存在工作区 `mcp_observations/`，摘录明确标记截断及内容类型。图片/音频原文存在不表示模型已观察其内容，需要相应感知入口。工具列表返回实际 next_cursor；不能将第一页当全部。

协议依据：[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)、[Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。实现只覆盖本页明确列出的功能；真实厂商接入和目标原生软件验收分别记录。
