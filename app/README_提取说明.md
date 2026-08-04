# 应用目录说明

当前 `app/` 不再只是冻结运行时提取物，而是包含完整 Electron 桌面源码、可读主后端源码、可读生命服务源码、Omni Body 运行树及其镜像。

源码启动使用仓库根目录的：

- `scripts/setup-source.ps1`
- `scripts/start-source.ps1`
- `start-tiangong.bat`

正式发布时，`scripts/release-win.mjs` 会在 Windows 上冻结通信服务与总网关，并要求后端、生命服务、通信服务、总网关四个原生运行组件全部通过发布清单绑定后才允许生成安装器。
