# Bundled skills compatibility mirrors

此目录中的受管 Skill 已不再是人工可编辑权威源。

当前权威位置：

- Novel creation：`src/bundled_skills/novel-creation/`
- Omni Body：`src/omni_body_skill/`

`readable-python-source/` 仅保留兼容镜像和历史可读路径。修改受管 Skill 时必须修改 `src/` 权威源，再运行 `python scripts/sync-generated-sources.py --write` 生成镜像。
