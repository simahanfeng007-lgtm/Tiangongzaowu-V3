# readable-python-source compatibility mirrors

P17-M1-02 之后，此目录不再承载独立的人工可编辑生产权威源码。

当前真正的人工权威源：

- `src/life_bootstrap/`
- `src/omni_body_skill/`
- `src/bundled_skills/novel-creation/`

本目录保留旧路径仅用于兼容、检索和历史工具链；受 `source-ownership.json` 与 `scripts/sync-generated-sources.py` 管理的内容必须由权威源生成，禁止直接修改镜像后提交。

修改流程：

1. 修改 `src/` 下对应权威源。
2. 运行 `python scripts/sync-generated-sources.py --write`。
3. 运行 `python scripts/check-source-authority.py`。
4. 运行 Architecture Gate / 对应回归测试。
