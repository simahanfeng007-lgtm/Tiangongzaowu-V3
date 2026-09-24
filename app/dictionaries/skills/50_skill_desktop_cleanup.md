# 桌面清理与文件整理

> 适用：桌面乱了、下载文件夹爆满、磁盘空间不足。**同盘整理不涉及文件内容，直接用 shell.run 批量移动，不要逐文件操作。**

## 快速路由

| 用户说 | 做什么 |
|--------|--------|
| "整理桌面" / "桌面好乱" | 按扩展名分文件夹归类 |
| "清理下载" | 整理 Downloads 文件夹 |

## 桌面整理流程

### 第一步：获取桌面路径

系统提示中已注入桌面路径（`- 桌面: C:\Users\xxx\Desktop`），记为 `{desktop}`。

### 第二步：创建分类文件夹 + 批量移动（一气呵成）

直接用 `shell.run`，每个命令串起来。**不需要先 list 再逐个 move，不需要备份，不需要算哈希。**

```
md "{desktop}\文档" 2>nul
md "{desktop}\图片" 2>nul
md "{desktop}\安装包" 2>nul
md "{desktop}\压缩包" 2>nul
md "{desktop}\模型" 2>nul
md "{desktop}\其他" 2>nul
move "{desktop}\*.docx" "{desktop}\文档\" 2>nul
move "{desktop}\*.pdf"   "{desktop}\文档\" 2>nul
move "{desktop}\*.png"   "{desktop}\图片\" 2>nul
move "{desktop}\*.jpg"   "{desktop}\图片\" 2>nul
move "{desktop}\*.jpeg"  "{desktop}\图片\" 2>nul
move "{desktop}\*.gif"   "{desktop}\图片\" 2>nul
move "{desktop}\*.webp"  "{desktop}\图片\" 2>nul
move "{desktop}\*.exe"   "{desktop}\安装包\" 2>nul
move "{desktop}\*.msi"   "{desktop}\安装包\" 2>nul
move "{desktop}\*.zip"   "{desktop}\压缩包\" 2>nul
move "{desktop}\*.rar"   "{desktop}\压缩包\" 2>nul
move "{desktop}\*.7z"    "{desktop}\压缩包\" 2>nul
move "{desktop}\*.vrm"   "{desktop}\模型\" 2>nul
move "{desktop}\*.glb"   "{desktop}\模型\" 2>nul
```

`2>nul` 是 Windows 的静默错误——文件夹已存在或某类没有文件时不报错。

### 第三步：汇报结果

用 `file.list {desktop}` 看一眼还剩什么，告诉用户移了多少。

## 铁律

- **用 shell.run 批量，不用 file.move 逐个** — 同盘移动秒完成
- **move 比 del 安全** — 错了能找回
- **不碰文件夹、不碰快捷方式(.lnk)** — 只处理散落文件
- **不碰系统目录、不碰隐藏文件**
- **做完汇报** — 移了多少文件

## 可用 Action

| action | 用途 |
|--------|------|
| `shell.run` | 批量移动（命令在隔离沙箱内通过显式 PowerShell/sh 进程执行，禁止宿主机 shell=True） |
| `file.list` | 整理前后看一眼桌面 |
