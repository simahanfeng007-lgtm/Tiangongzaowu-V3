# 隐私净化记录

本交付副本未包含：

- `%APPDATA%\tiangong-v3-qiyuan` 中的聊天、记忆、情感、生命身份、日志、数据库、检查点和加密凭证。
- `%LOCALAPPDATA%` 更新缓存。
- `.codex-remote-attachments` 用户附件。
- 构建日志、旧版 ASAR、安装包备份、回滚备份和 `__pycache__`。
- 当前机器用户名路径、工作区绝对路径和桌面输出路径。

已处理：

- 两份 `body_settings.pyc` 中的个人 soul 默认名已替换为空值。
- NSIS 的输入/输出路径已改为编译参数。
- 文档与测试样例中的个人电脑路径已改为通用示例。
- 未发现明文 API Key、JWT、私钥或固定的 `TIANGONG_DESKTOP_TOKEN`。

所有凭证、soul、头像、声音和用户身份均应在新环境中重新配置或首次运行生成。
