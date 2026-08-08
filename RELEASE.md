# 天工造物 v3.0 完整版发布说明

本仓库以 `electron-builder` 作为唯一桌面封装入口。Windows 本机产出 NSIS 安装器；macOS 必须在 macOS 主机或 GitHub Actions macOS Runner 上产出 DMG 和 ZIP，不能在 Windows 上交叉生成可签名、公证的正式 Mac 包。

## Windows

本地构建未签名发布候选包：

```powershell
python scripts/install-python-dependencies.py --requirements requirements-release.lock
Set-Location app
npm run release:win
```

依赖安装默认使用用户当前配置或官方源；仅在下载失败时使用国内镜像重试。
Python/PyPI 使用清华 TUNA，npm、Electron 和 electron-builder 使用其对应的国内兼容镜像。
完整策略和可覆盖环境变量见 `DEPENDENCY_SOURCES.md`。

如果运行时冻结与发布清单已经通过，仅安装器配置阶段失败，可在修正配置后执行 `node ../scripts/release-win.mjs --resume`；恢复模式会重新检查两个冻结 EXE 及二维码能力，但不会绕过探针。

构建会先运行完整测试，再从当前 `src/communication_service` 和 `src/total_gateway` 冻结全新 EXE。通信服务探针必须同时确认微信二维码和飞书能力，否则停止打包。安装器、发布清单、来源证明和 `SHA256SUMS.txt` 按 `app/package.json` 的版本自动输出到 `release-artifacts/<version>/win32-x64/`。

正式公开发布前必须配置 `TIANGONG_RELEASE_REQUIRE_SIGNING=1`、`CSC_LINK` 和 `CSC_KEY_PASSWORD`。没有代码签名的本地结果只能称为“未签名发布候选包”。

## macOS

macOS 包要求：

- Developer ID Application 证书；
- Apple 公证凭据。

macOS 与 Windows 使用同一套单进程 total gateway（`scripts/release-common.mjs` 只构建
`tiangong-total-gateway`，不存在独立的 Darwin backend/life 运行时）。
Windows 侧可运行 `scripts/dispatch-mac-release.ps1` 派发 GitHub Actions；需要配置
Developer ID 证书、Apple 公证凭据等 Secrets。任何运行时或凭据缺失都会停止构建，
避免生成不能运行的“完整版本”。

## 发布真实性

`release-manifest.json` 对桌面、后端、生命服务、通信服务和总网关记录真实可执行文件哈希。`release-provenance.json` 记录平台、架构、签名模式、源码提交和二维码构建探针结果。最终交付以 `SHA256SUMS.txt` 为校验入口，开发者信息统一为“于泳翔”。
