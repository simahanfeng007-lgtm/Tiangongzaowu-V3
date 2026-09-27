# 普通入口的本体任务复验

这是开发验收驱动，复用产品 Gateway、真实模型传输和既有组合/裁判；不修改模型输出，不在产品中新增调度器或验收数量门槛。当前驱动适用于 Linux/POSIX；Windows 与真实厂商软件另行验证。

使用安装了仓库 `requirements-source.lock` 的 Python。隔离执行需要原 Linux containment 环境；浏览器场景需要 Playwright 和可启动的 Chromium/Chrome。`--model-config` 指向已有的私有天工验收模型配置，使用其默认官方 provider；私有验收配置的 `_api_keys` 保存对应 provider 凭据，`_provider_inputs` 保存 endpoint/model 设置。参数只传文件位置，驱动沿用产品的凭据身份映射注入隔离子进程；不修改桌面凭据库，不将厂商密钥重绑到自定义地址。

```bash
python scripts/ontology-smoke/run.py local --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py browser --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py mcp --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py quality --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py document --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py novel --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py local-apps --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py mcp-tasks --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py media --vision-provider minimax --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/audit_live.py --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/audit_live.py --case local-apps --case mcp-tasks --output-root /private/ontology-evidence --attempt candidate
```

每次尝试使用新目录，重名拒绝覆盖。运行经普通桌面入站 API；输入、源码摘要、原始可见模型输出、服务端模型名、全部已知用量、回执、SQLite 账本、产物和裁决均保留。驱动期限超出时请求取消；未取得真实完成不能通过。返回码 0 只表示这个具体场景的驱动检查通过，不代表全部能力验收。

五个场景分别检查：错误聚合脚本的实际执行与修复；真实网页点击、下载和截图；受控本地 MCP 的分页、一次创建和目标回读；不执行项目代码的静态代码观察；DOCX 正常文本/表格读取和损坏文件失败。MCP 测试服务在独立目录保存对象，不代表任何厂商应用接通。

`local-apps` 实际使用 11 个 SQLite/Obsidian 接口，先保留坏 CSV 导入失败，再通过正确输入恢复并回读数据库、备份和全量 CSV；预先独立确定的预期值为 236 行、value 总和 27505。`mcp-tasks` 使用一个实际有状态 HTTP 会话、两个异步任务、一次真实创建和一次取消，再读取目标对象；该受控服务不代表云端厂商。`media` 需要私有配置中已存在、受原 provider 身份校验的图片模型；不支持或额度不足明确失败，不通过文字提示替代实际像素。视频/音频另需相应场景，图片通过不能代表它们通过。

`audit_live.py` 独立核对真实文件、已知输入真值、账本哈希链、请求/运行/代际、当前产物与最终裁决，并对比本地产品文件。对已正常关闭且无非空 WAL 的数据库使用 immutable 只读；有 WAL 时保留只读读取并核对数据库与 WAL 字节。SHM 是读者协调文件，不作为执行事实。二次审计用新的 `--audit-id` 保留旧结果。

模型原始私有推理字段不写入遥测，已知凭据脱敏。目录仍包含私有配置链接及运行密钥；分享时只导出经过核查的摘要、回执和产物，不提交整个运行目录。

小说用例以真实小说事务预置一个错误末章，再由普通 Gateway 的真实模型发现版本并使用 checkout/submit 修订。确定性回读检查正文、账本和原输入；预置不属于模型执行证据。
