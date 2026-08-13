# Source Map

This integrated skill was distilled from desktop archives:

- `<USER_DESKTOP>\skills.zip`
- `<USER_DESKTOP>\工具.zip`

The original archive contained many separate skills such as `一键小说工厂`, `正文流水线`, `创作宪法`, `小说追踪`, `品控引擎`, `审核引擎`, `命名引擎`, `反AI规则`, and `番茄发布`.

Design decision:

- Keep one public skill entry: `novel-creation`.
- Keep one deterministic tool: `scripts/novel_tool.py`.
- Move detailed rules into small references loaded only when needed.
- Do not preserve the heavy multi-agent runtime chain by default.
