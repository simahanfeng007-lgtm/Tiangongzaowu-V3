# adapters

这里是应用适配器标准，不是智能体规划层。

- `omni_body` 是唯一 v3 可执行工具入口。
- `registry/apps.json` 把应用拆成工具组。
- `registry/app_actions.json` 把应用动作挂载到 `omni_body` 的 action 表。
- `implemented=false` 的动作不会假成功，会返回 `[ADAPTER_REQUIRED]`。

接真实应用时，每个 adapter 只需要实现 `health / describe_actions / execute / verify / rollback`，不要在 adapter 内做自主任务规划。
