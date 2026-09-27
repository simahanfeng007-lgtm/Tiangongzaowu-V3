# 普通入口的本体任务复验

这是开发验收驱动，复用产品 Gateway、真实模型传输和既有组合/裁判；不修改模型输出，不在产品中新增调度器或验收数量门槛。当前驱动适用于 Linux/POSIX；Windows 与真实厂商软件另行验证。

使用安装了仓库 `requirements-source.lock` 的 Python。隔离执行需要原 Linux containment 环境；浏览器场景需要 Playwright 和可启动的 Chromium/Chrome。`--model-config` 指向已有的私有天工验收模型配置，使用其默认官方 provider；私有验收配置的 `_api_keys` 保存对应 provider 凭据，`_provider_inputs` 保存 endpoint/model 设置。参数只传文件位置，驱动沿用产品的凭据身份映射注入隔离子进程；不修改桌面凭据库，不将厂商密钥重绑到自定义地址。

```bash
python scripts/ontology-smoke/run.py local --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py browser --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py mcp --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py quality --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/run.py document --model-config /private/model-config.json --output-root /private/ontology-evidence --attempt candidate
python scripts/ontology-smoke/audit_live.py --output-root /private/ontology-evidence --attempt candidate
```

每次尝试使用新目录，重名拒绝覆盖。运行经普通桌面入站 API；输入、源码摘要、原始可见模型输出、服务端模型名、全部已知用量、回执、SQLite 账本、产物和裁决均保留。驱动期限超出时请求取消；未取得真实完成不能通过。返回码 0 只表示这个具体场景的驱动检查通过，不代表全部能力验收。

五个场景分别检查：错误聚合脚本的实际执行与修复；真实网页点击、下载和截图；受控本地 MCP 的分页、一次创建和目标回读；不执行项目代码的静态代码观察；DOCX 正常文本/表格读取和损坏文件失败。MCP 测试服务在独立目录保存对象，不代表任何厂商应用接通。

`audit_live.py` 独立核对真实文件、已知输入真值、账本哈希链、请求/运行/代际、当前产物与最终裁决，并对比本地产品文件。对已正常关闭且无非空 WAL 的数据库使用 immutable 只读；有 WAL 时保留只读读取并核对数据库与 WAL 字节。SHM 是读者协调文件，不作为执行事实。二次审计用新的 `--audit-id` 保留旧结果。

模型原始私有推理字段不写入遥测，已知凭据脱敏。目录仍包含私有配置链接及运行密钥；分享时只导出经过核查的摘要、回执和产物，不提交整个运行目录。
