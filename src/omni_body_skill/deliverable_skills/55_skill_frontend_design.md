# 前端美化

> GitHub 顶级设计工具链 + v3 专属美化方案。

## 参考项目

| 项目 | Stars | 用途 |
|------|-------|------|
| [Bootstrap](https://github.com/twbs/bootstrap) | 174k | CSS 框架鼻祖 |
| [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss) | 95.8k | 实用优先 CSS |
| [Material UI](https://github.com/mui/material-ui) | 98.5k | Material Design 组件库 |
| [awesome-design-md](https://github.com/VoltAgent/awesome-design-md) | 96.3k | AI 设计令牌规范 |
| [DaisyUI](https://github.com/saadeghi/daisyui) | 41.4k | Tailwind 组件库 |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | 75k+ | 可复制组件 |
| [Bulma](https://github.com/jgthms/bulma) | 50.1k | Flexbox CSS 框架 |
| [Lucide](https://github.com/lucide-icons/lucide) | 13k+ | 开源图标集 |
| [Animate.css](https://github.com/animate-css/animate.css) | 81k+ | CSS 动画库 |
| [Font Awesome](https://github.com/FortAwesome/Font-Awesome) | 74k+ | 图标字体 |

## v3 美化方案（vanilla JS，无框架）

### 1. DESIGN.md 设计令牌（推荐，参考 awesome-design-md）

在项目根创建 `DESIGN.md`，定义全局设计变量：

```markdown
# 天工造物v3 设计规范

## 颜色
- 主色: #0D9488 (teal-600)
- 主色深: #0F766E (teal-700)
- 背景: #0F172A (slate-900)
- 面板: #1E293B (slate-800)
- 边框: #334155 (slate-700)
- 文字: #F1F5F9 (slate-100)
- 文字暗: #94A3B8 (slate-400)
- 成功: #22C55E / 警告: #F59E0B / 错误: #EF4444

## 间距
- xs: 4px / sm: 8px / md: 16px / lg: 24px / xl: 32px

## 圆角
- sm: 4px / md: 8px / lg: 12px / full: 9999px

## 字体
- 系统字体栈: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif
- 等宽: "JetBrains Mono", "Fira Code", monospace
- 大小: xs:12px / sm:14px / base:16px / lg:18px / xl:24px
```

### 2. CSS 变量统一主题

在 `styles.css` 用 CSS 变量实现：

```css
:root {
  --color-bg: #0F172A;
  --color-panel: #1E293B;
  --color-border: #334155;
  --color-text: #F1F5F9;
  --color-muted: #94A3B8;
  --color-primary: #0D9488;
  --color-primary-hover: #0F766E;
  --radius-md: 8px;
  --shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
}

/* 全局应用 */
body { background: var(--color-bg); color: var(--color-text); }
.panel-card { background: var(--color-panel); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
```

### 3. 图标方案

Lucide（推荐，轻量 SVG）：
```html
<!-- CDN 引入或下载 SVG -->
<script src="https://unpkg.com/lucide@latest"></script>
<i data-lucide="search"></i>
<i data-lucide="settings"></i>
<i data-lucide="user"></i>
```

### 4. 动画微交互

Animate.css 关键动画：
```css
/* 面板淡入 */
.panel-card { animation: fadeInUp 0.3s ease; }
/* 按钮悬停 */
button { transition: all 0.15s ease; }
button:hover { transform: translateY(-1px); box-shadow: var(--shadow); }
```

### 5. 滚动条美化

```css
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--color-bg); }
::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--color-muted); }
```

### 6. 深色/浅色切换

```javascript
// 读取 localStorage 或系统偏好
const theme = localStorage.getItem("theme") || 
  (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
document.documentElement.setAttribute("data-theme", theme);
```

## 一键美化脚本

```bash
cd /d/tiangongv3/tiangong-v3-qiyuan-installer/frontend-v2-live

# 1. 下载 Lucide 图标
curl -sL "https://unpkg.com/lucide@latest/dist/umd/lucide.min.js" -o assets/lucide.min.js

# 2. 下载 Animate.css
curl -sL "https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css" -o styles/animate.min.css

# 3. 在 index.html 中引入
echo "在 <head> 中添加:"
echo '<link rel="stylesheet" href="styles/animate.min.css">'
echo '<script src="assets/lucide.min.js"></script>'
echo '<script>lucide.createIcons();</script>'
```

## 铁律

1. **DESIGN.md 先于代码** — 定义设计令牌再写 CSS
2. **CSS 变量统一管理** — 颜色/间距/圆角全用变量，改一个全局生效
3. **不引入框架** — v3 是 vanilla JS，不装 React/Vue/Bootstrap
4. **图标用 SVG** — Lucide 或 Feather Icons，不引入图标字体（太大）
5. **动画不过度** — 微交互 0.3s 以内，不干扰操作
