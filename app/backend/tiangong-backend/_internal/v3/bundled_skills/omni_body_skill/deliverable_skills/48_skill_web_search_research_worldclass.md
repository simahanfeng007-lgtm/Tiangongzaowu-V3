# 网络搜索

> 用 `web.search` 搜。后端自动在百度/搜狗/DDG/Bing/Google/RSS 间降级。跟终端 curl 一个效果。

## 调用

```json
{"action": "web.search", "args": {"query": "关键词"}}
```
搜图片：`web.image_search`，搜新闻加 `"mode": "news"`。

## 兜底：shell.run + curl 百度

如果 `web.search` 异常，直接用终端 curl（跟你手动 curl 完全一样）：

```bash
curl -sL "https://www.baidu.com/s?wd=关键词" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0" \
  -H "Accept-Language: zh-CN" -H "Cookie: BAIDUID=1" | \
  python -c "import sys,re,html;t=sys.stdin.read();[print(f'{i+1}.{html.unescape(re.sub(r\"<[^>]+>\",\" \",m.group(1)).strip())[:100]}')for i,m in enumerate(re.finditer(r\"<h3[^>]*>(.*?)</h3>\",t,re.I),1)]"
```

## 拿到结果必须做的事

整理成表格，**标题必须挂超链接**：

```markdown
## 搜索：{关键词}
> {引擎} · {N}条

| # | 标题 | 来源 |
|---|------|------|
| 1 | [标题文字](https://实际url) | domain.com |
| 2 | [标题文字](https://实际url) | domain.com |
```

每条结果中的 `url` 字段就是超链接地址，`domain` 是来源域名。**必须逐条挂上，不可只写文字不写链接。**

## 可用 Action

| action | 用途 |
|--------|------|
| `web.search` | 网页搜索 |
| `web.image_search` | 图片搜索 |
| `shell.run` | curl 兜底 |
| `http.get` | 抓全文 |
