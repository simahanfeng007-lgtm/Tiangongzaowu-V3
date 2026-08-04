# 短视频生成

> 通过 LLM 内置生视频能力（MiniMax video-01）直接生成。

## 快速路由
用户要视频 → `video.generate` 直接生成。不要多步。

## 用法
```json
{"action": "video.generate", "args": {"prompt": "视频描述", "duration": 6, "resolution": "768P"}}
```

### 参数
- `args.prompt` — 视频描述
- `args.duration` — 时长（秒），1-30，默认 6
- `args.resolution` — 分辨率：768P / 1080P

### 视频生成是异步的，需要等待几十秒到几分钟

### 完成后
- 文件路径用 `MEDIA:` 前缀发给用户
