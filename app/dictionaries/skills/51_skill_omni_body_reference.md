# Omni Body 万能工具参考

> 所有文件/命令/文档/媒体操作统一走 `omni_body` 工具。传 `action` + `target` + `args`。

## 调用格式

```
omni_body action="<动作名>" target="<路径或对象>" args={...}
```

`target` 可用绝对路径（`C:\Users\...`）或相对 workspace 的路径。`args` 是可选的 JSON 对象。

## Action 速查

### 系统
| action | 用途 |
|--------|------|
| `system.capabilities` | 列出所有可用 action |
| `system.health` | 查看运行时状态 |

### 文件操作（优先用，有快照回滚）
| action | 用途 | 风险 |
|--------|------|------|
| `file.list` | 列出目录下文件 | A0 |
| `file.read` | 读文本文件 | A0 |
| `file.search` | 搜索文件名或内容 | A0 |
| `file.hash` | 计算文件 SHA256 | A0 |
| `file.write` | 写文本/二进制文件 | A3 |
| `file.append` | 追加内容到文件 | A3 |
| `file.copy` | 复制文件/目录 | A3 |
| `file.move` | 移动文件/目录 | A3 |
| `file.rename` | 重命名 | A3 |
| `file.mkdir` | 创建目录 | A2 |
| `file.delete_to_trash` | 移到回收站 | A4 |
| `zip.create` | 创建 zip 压缩包 | A2 |
| `zip.extract` | 解压 zip | A4 |

### 代码
| action | 用途 | 风险 |
|--------|------|------|
| `code.read` | 读代码文件 | A0 |
| `code.write` | 写代码文件 | A3 |
| `code.patch_replace` | 文本/正则替换 | A3 |
| `quality.python_syntax` | 检查 Python 语法 | A0 |

### Shell / Python
| action | 用途 | 风险 | 备注 |
|--------|------|------|------|
| `shell.run` | 执行 shell 命令 | A4 | `args.command` 传命令字符串；命令仅在隔离沙箱内通过显式 PowerShell/sh 进程执行，禁止宿主机 `shell=True`。 |
| `python.run` | 执行 Python 脚本 | A4 | `args.code` 传代码 |

### 文档
| action | 用途 | 风险 |
|--------|------|------|
| `docx.create` | 创建 Word 文档 | A2 |
| `pptx.create` | 创建 PPT 演示 | A2 |
| `sheet.create` | 创建 Excel 表格 | A2 |
| `sheet.read` | 读取 Excel/CSV | A0 |
| `pdf.extract_text` | 提取 PDF 文本 | A0 |

### 图片 / 音视频
| action | 用途 | 风险 |
|--------|------|------|
| `image.info` | 查看图片信息 | A0 |
| `image.resize` | 缩放图片 | A2 |
| `image.crop` | 裁剪图片 | A2 |
| `image.convert` | 转换格式 | A2 |
| `image.create_canvas` | 创建画布 | A2 |
| `image.add_text` | 图片上加文字 | A2 |
| `image.compose` | 图片叠加 | A2 |
| `video.info` | 查看视频信息 | A0 |
| `video.cut` | 裁剪视频片段 | A2 |
| `video.extract_audio` | 提取音频 | A2 |
| `video.add_audio` | 替换音频轨 | A2 |
| `video.slideshow` | 图片合成视频 | A2 |
| `audio.trim` | 裁剪音频 | A2 |
| `audio.concat` | 拼接音频 | A2 |
| `audio.tts` | 文字转语音 | A2 |

### 网络
| action | 用途 | 风险 |
|--------|------|------|
| `web.search` | 网页搜索 | A0 |
| `http.get` | HTTP GET 请求 | A0 |
| `web.download` | 下载文件 | A2 |

### 回滚
| action | 用途 |
|--------|------|
| `rollback.list` | 列出可回滚的操作 |
| `rollback.apply` | 回滚到快照 |

## 常见场景速查

| 场景 | 用哪个 |
|------|--------|
| 看桌面有什么文件 | `file.list C:\Users\...\Desktop` |
| 读一个文件内容 | `file.read 路径` |
| 写一个新文件 | `file.write 路径 args={"content":"..."}` |
| 创建文件夹 | `file.mkdir 路径` 或 `shell.run args={"command":"md 路径"}` |
| 移动/归类文件 | **少量**：`file.move`；**批量通配符**：`shell.run args={"command":"move /Y 源 目标"}` |
| 搜索代码/文本 | `file.search args={"pattern":"关键词"}` |
| 执行命令 | `shell.run args={"command":"你的命令"}` |
| 运行 Python | `python.run args={"code":"print(1)"}` |
| 生成 Word/PPT/Excel | `docx.create` / `pptx.create` / `sheet.create` |
| 下载文件 | `web.download args={"url":"..."}` |
| 网页搜索 | `web.search args={"query":"关键词"}` |

## 铁律

- **先 list 后动手** — 不知道有什么文件就用 `file.list`
- **move 优先于 del** — 移动错了能找回，删除不行
- **同盘整理用 shell.run 批量** — 不需要逐文件 `file.move`
- **target 是目标路径，不是描述** — 传 `C:\Users\...\Desktop`，不传 "用户桌面"
- **`2>nul` 静默错误** — Windows shell 命令末尾加 `2>nul` 避免无文件时报错
