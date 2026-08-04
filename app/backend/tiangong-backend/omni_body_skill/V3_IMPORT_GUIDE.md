# 天工 v3 接入说明

## 文件落点

真实可执行工具：`api/v1/v3/tools/omni_body.py`。工具描述：`api/v1/v3/tools/omni_body.tool.json`。能力注册由运行时清单生成。

## 唯一工具入口

不要把动作表中的 action 分别注册成模型工具。只注册 `omni_body`，通过 `action`、`target`、`args` 分发：

```json
{
  "action": "file.write",
  "target": "reports/result.md",
  "args": {"content": "完成内容"}
}
```

模型调用中禁止出现 `confirm`、`confirmed`、`allow_shell`、`allow_python` 或工作区授权字段。A0—A4由 Runtime 自动执行；A5在进入工具前硬拒绝，模型不能自行解除。

## shell / python

`python.run` 与 `shell.run` 均通过统一沙箱执行。示例：

```json
{
  "action": "shell.run",
  "target": "",
  "args": {"command": ["python", "--version"]}
}
```

执行环境使用私有工作区副本、净化后的环境变量、资源约束和原子回写。危险动作由 Runtime 判为 A5 后直接拒绝。

## 工作目录

工作区由宿主 Runtime 绑定，模型不能通过工具参数改变。所有文件路径都必须位于当前工作区内。
