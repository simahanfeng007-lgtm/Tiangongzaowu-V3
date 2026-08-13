# 世界顶尖级代码工程交付 Skill

## 设计依据
参考 Google Engineering Practices 的代码健康、可读性、测试、审查标准，以及现代工程交付对 README、测试、回滚和安全边界的要求。

## 输入契约
- 目标功能
- 输入/输出
- 运行环境
- 不做范围
- 测试方式
- 交付格式

## 工具流程
1. `writing.outline.create`，type=`code_project`，生成工程交付说明。
2. `code.write` 写核心代码。
3. `quality.python_syntax` 或语言对应质检。
4. `python.run` / `quality.run_tests` 执行最小验证。
5. `qc.code.delivery_check` 检查语法、测试、README、安全味道、结构。
6. 低于80分：`repair.plan`，修复后重新测试。
7. `deliverable.package` 打包源码、README、测试、运行说明、QC报告。

## 顶尖交付标准
- 可运行、可测试、可维护、可读。
- README必须包含安装、运行、测试、边界。
- 不允许无解释的危险调用：`eval/exec/shell=True/硬编码密钥`。
- 重要函数需要清晰命名和错误处理。

## 验收
- `qc.code.delivery_check.score >= 80`。
- 所有 critical/high 问题必须消除。
