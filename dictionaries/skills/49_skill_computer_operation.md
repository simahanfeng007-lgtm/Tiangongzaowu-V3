# 电脑操作

> `shell.run` 可执行任意命令。权限已全开，但**每次执行前确认命令安全**。

## 快速路由

| 意图 | 命令模板 |
|------|---------|
| 列出文件 | `dir "路径"` (Win) / `ls -la "路径"` |
| 查找文件 | `where /R 目录 文件名` (Win) / `find 目录 -name "*.ext"` |
| 查看进程 | `tasklist` |
| 磁盘空间 | `wmic logicaldisk get size,freespace,caption` |
| 系统信息 | `systeminfo` |
| 网络状态 | `ipconfig /all` |
| 测网络通断 | `ping -n 3 地址` |
| 装 Python 包 | `pip install 包名` |
| 运行脚本 | `python 脚本路径` |
| Git 状态 | `git status` |
| Git 日志 | `git log --oneline -10` |
| 读文本 | `type 文件路径` (Win) / `cat 文件路径` |
| 搜索文本 | `findstr "关键词" 文件` (Win) / `grep "关键词" 文件` |

## 命令格式
```json
{"action": "shell.run", "args": {"command": "要执行的命令"}}
```

## 铁律

1. **用完整路径** — 不假定当前目录
2. **先读后写** — 修改文件前先 `file.read` 确认内容
3. **不链式管道破坏性操作** — `del /f /s` 类命令格外谨慎
4. **超30秒的任务** — 告诉用户可能较慢
5. **标准输出有限** — 返回前 8000 字符，长输出用 `> 文件` 重定向再 `file.read`

## 工作目录
- Windows 默认 `%USERPROFILE%`
- 写文件建议明确路径，如 `%USERPROFILE%\Desktop\xxx`

## 禁止
- `format`、`diskpart` 等磁盘级命令
- `rmdir /s /q C:\` 等递归删除系统目录
- 未经确认不修改注册表
- 不下载执行未验证的脚本

## 完成后的输出
- 成功 → 展示关键结果 + 文件路径（如有产出）
- 失败 → 贴错误信息前 500 字符 + 排查建议
