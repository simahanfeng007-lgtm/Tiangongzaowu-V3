# 固定候选缓存对照复现材料

此目录不含凭据、运行密钥、旧运行状态或原始私有推理。`prepare.py` 只校验源码并建立本地引用，不发模型请求。它把新实验绑定到本机实际 Git 提交；`reproduced_from_product_commit` 仅是历史来源，不能把新结果冒充原批次。

1. 使用包含本轮修复的干净 v3 checkout，按仓库原有步骤安装 `requirements-source.lock`、生成运行镜像和所需 Linux bubblewrap / FFmpeg 依赖。这里冻结的是 893 个产品文件；若不同，准备脚本拒绝启动。
2. 在个人私有目录准备既有 Tiangong 模型配置（当前基准为 `deepseek_v4` / `deepseek-chat` / `https://api.deepseek.com`，可用凭据由你自己的配置提供）。不要把配置放入 Git，也不要共享 `private/` 或新运行的运行密钥。
3. 先把本目录复制到源码仓库之外的新目录，在新目录运行：

```sh
python prepare.py --source /path/to/repository --profile /private/path/model-config.json
python cache-harness/run_cache_comparison.py
```

第二条命令会进行真实付费模型请求：8 类合成任务 × 3 次 × 2 种上下文模式，总共 48 次任务、每次可以多次调用模型，单任务观察期限 240 秒。两个独立 Gateway 进程按交替顺序运行。不要在执行期间编辑源码、提示词、输入文件或检查函数。

主指标要求 Gateway COMPLETED 和独立文件检查同时通过。普通模型/协议失败保留在分母中；启动故障、源码漂移或错误完成会停止调度。所有结果写到 `cache-harness/runs/`，再次运行应使用新的解压目录，不能覆盖失败尝试。

`canonical` 和 `reuse` 比较上下文复用；两者使用相同完整字典和工具，不是短编码/本体实验。少量合成任务、单模型与 provider 跨任务预热限制因果解释，无法代表全部现实任务或生产准入。README 中的 Python 应为已装依赖的同一运行环境。
