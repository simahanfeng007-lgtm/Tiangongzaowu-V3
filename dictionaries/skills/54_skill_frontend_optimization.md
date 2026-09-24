# 前端性能优化

> GitHub 顶级工具链：Lighthouse(⭐30k) 审计 → Terser/PurgeCSS 压缩 → Sharp 图片优化 → 懒加载。

## 参考项目

| 项目 | Stars | 用途 |
|------|-------|------|
| [Lighthouse](https://github.com/GoogleChrome/lighthouse) | 30.5k | Google 自动化性能审计 |
| [Vite](https://github.com/vitejs/vite) | 81.8k | 下一代构建工具 |
| [esbuild](https://github.com/evanw/esbuild) | 40.0k | 极速打包器 |
| [Sharp](https://github.com/lovell/sharp) | 30k+ | 高性能图片处理 |
| [PurgeCSS](https://github.com/FullHuman/purgecss) | 7.9k | 删除未使用 CSS |
| [Terser](https://github.com/terser/terser) | 8.7k | JS 压缩混淆 |

## v3 前端结构

```
frontend-v2-live/
├── index.html              ← 入口
├── renderer/
│   ├── app.mjs             ← 主逻辑
│   ├── core/               ← 核心模块
│   │   ├── state.mjs       ← 状态管理
│   │   ├── actions.mjs     ← 动作
│   │   └── ...
│   ├── plugins/            ← 面板插件 (body/conversation/knowledge/...)
│   │   ├── body-panel.mjs
│   │   ├── conversation-panel.mjs
│   │   └── ...
│   └── runtime/            ← 运行时 (HTTP/API)
│       └── http-runtime.mjs
├── styles/
└── assets/
    └── tiangong-logo.png
```

## 优化清单

### 1. 图片优化（立竿见影）

```bash
# PNG → WebP (体积减 60-80%)
cd frontend-v2-live/assets
npx sharp-cli --input tiangong-logo.png --output tiangong-logo.webp
```

或批量：
```bash
find frontend-v2-live -name "*.png" -exec npx sharp-cli --input {} --output {}.webp \;
```

### 2. JS/CSS 压缩

```bash
# JS 压缩 (Terser)
npx terser renderer/app.mjs -o renderer/app.min.mjs -c -m
# 批量
find renderer -name "*.mjs" -exec npx terser {} -o {}.min -c -m \;

# CSS 压缩 (csso)
npx csso styles.css -o styles.min.css
```

### 3. 删除未使用 CSS (PurgeCSS)

```bash
npx purgecss --css styles.css --content "renderer/**/*.mjs" "index.html" --output .
```

### 4. HTML 压缩

```bash
npx html-minifier-terser index.html -o index.min.html \
  --collapse-whitespace --remove-comments --minify-css --minify-js
```

### 5. 插件懒加载

v3 当前所有 panel 插件同步加载。改为动态 import：

```javascript
// 原来：静态 import
import { bodyPanelPlugin } from "./plugins/body-panel.mjs";

// 优化：按需动态 import
async function loadPanel(name) {
  const modules = {
    body: () => import("./plugins/body-panel.mjs"),
    conversation: () => import("./plugins/conversation-panel.mjs"),
    knowledge: () => import("./plugins/knowledge-panel.mjs"),
  };
  return modules[name]?.();
}
```

### 6. Lighthouse 审计

```bash
# 安装
npm install -g lighthouse
# 运行（v3 需在 Electron 中打开后审计）
lighthouse http://localhost:7174 --view
```

关注指标：
- **FCP** (First Contentful Paint) < 1.8s
- **LCP** (Largest Contentful Paint) < 2.5s
- **TBT** (Total Blocking Time) < 200ms
- **CLS** (Cumulative Layout Shift) < 0.1

### 7. asar 压缩级别

electron-builder 默认 asar 不压缩。改为：

```json
{
  "build": {
    "asar": {
      "smartUnpack": true,
      "ordering": "string"  
    }
  }
}
```

## 一键优化脚本

```bash
cd /d/tiangongv3/tiangong-v3-qiyuan-installer/frontend-v2-live
echo "=== 图片 → WebP ==="
find . -name "*.png" -exec bash -c 'npx sharp-cli --input "$1" --output "${1%.png}.webp"' _ {} \;
echo "=== CSS 压缩 ==="
npx csso renderer/app.css -o renderer/app.min.css 2>/dev/null
echo "=== PurgeCSS ==="
npx purgecss --css "renderer/**/*.css" --content "renderer/**/*.mjs" "index.html" --output renderer/ 2>/dev/null
echo "=== JS 压缩 ==="
for f in renderer/app.mjs renderer/core/state.mjs renderer/runtime/http-runtime.mjs; do
  npx terser "$f" -o "${f%.mjs}.min.mjs" -c -m 2>/dev/null
done
echo "=== 完成 ==="
```

## 铁律

1. **优化前先备份** — `cp -r frontend-v2-live frontend-v2-live.bak`
2. **改完要验证** — Electron 中打开确认无白屏/报错
3. **图片优先** — WebP 体积减 60-80%，投入产出比最高
4. **不改结构** — 只压缩不重构，保持插件注册机制不变
5. **懒加载逐步来** — 先改非核心 panel，核心面板（conversation）保持同步加载
