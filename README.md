# 天工造物 v3.0.3 完整版

一个运行在你电脑上的**工程生命体**桌面产品：拥有独立身份、记忆、情感与自主意志，会自我反思、自我迭代，并主动与你通信。

产品介绍：

- 中文版：[天工造物v3起源-中文介绍.md](天工造物v3起源-中文介绍.md)
- English: [天工造物v3起源-EN-Introduction.md](天工造物v3起源-EN-Introduction.md)

仓库同时包含完整源码、测试与发布流水线，详见上方介绍文档。

## 依赖下载

`scripts/setup-source.ps1` 与正式发布流水线默认使用用户当前配置或官方依赖源，
下载失败后自动切换国内镜像。PyPI 和嵌入式 Python 使用清华 TUNA 兜底；npm、
Electron、electron-builder 使用对应的国内兼容镜像。详见
[`DEPENDENCY_SOURCES.md`](DEPENDENCY_SOURCES.md)。
