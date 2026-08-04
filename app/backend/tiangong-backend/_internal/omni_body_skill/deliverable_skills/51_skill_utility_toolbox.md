# 实用工具箱

> 高频日常操作：批量重命名、PDF处理、图片压缩转换。

## 快速路由

| 用户说 | 做什么 |
|--------|--------|
| "批量改名" / "重命名这些文件" | 按规则批量重命名 |
| "合并PDF" / "拆分PDF" | PDF 合并或拆分 |
| "压缩图片" / "转换图片格式" / "图片改大小" | 图片批处理 |

---

## 一、批量重命名

### 按序号重命名
```json
{"action": "python.run", "args": {"code": "
import os, re
folder = r'目标目录'
files = sorted([f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))])
for i, f in enumerate(files, 1):
    ext = os.path.splitext(f)[1]
    new = os.path.join(folder, f'photo_{i:03d}{ext}')
    os.rename(os.path.join(folder, f), new)
    print(f'{f} → {os.path.basename(new)}')
"}}
```

### 按正则替换
```json
{"action": "python.run", "args": {"code": "
import os, re
folder = r'目标目录'
pattern = r'旧文字'
replacement = '新文字'
for f in os.listdir(folder):
    new_name = re.sub(pattern, replacement, f)
    if new_name != f:
        os.rename(os.path.join(folder, f), os.path.join(folder, new_name))
        print(f'{f} → {new_name}')
"}}
```

### 先预览再执行
先 `file.list` 看文件列表 → 用户确认 → 执行重命名。

---

## 二、PDF 合并与拆分

### 合并 PDF
```json
{"action": "python.run", "args": {"code": "
from PyPDF2 import PdfMerger
merger = PdfMerger()
files = [r'文件1.pdf', r'文件2.pdf', r'文件3.pdf']
for f in files:
    merger.append(f)
merger.write(r'输出路径/合并结果.pdf')
merger.close()
print('合并完成')
"}}
```

### 拆分 PDF
```json
{"action": "python.run", "args": {"code": "
from PyPDF2 import PdfReader, PdfWriter
reader = PdfReader(r'输入.pdf')
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    writer.write(rf'输出目录/page_{i+1:03d}.pdf')
print(f'拆分为 {len(reader.pages)} 页')
"}}
```

---

## 三、图片批量处理

### 批量压缩（限制长边）
```json
{"action": "python.run", "args": {"code": "
from PIL import Image
import os
folder = r'图片目录'
max_size = 1200
for f in os.listdir(folder):
    if f.lower().endswith(('.png','.jpg','.jpeg','.webp')):
        img = Image.open(os.path.join(folder, f))
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
        img.save(os.path.join(folder, 'compressed_'+f), optimize=True, quality=85)
        print(f'{f}: {w}x{h} → {img.size[0]}x{img.size[1]}')
"}}
```

### 格式转换（如 PNG→JPG）
```json
{"action": "python.run", "args": {"code": "
from PIL import Image
import os
folder = r'图片目录'
for f in os.listdir(folder):
    if f.lower().endswith('.png'):
        img = Image.open(os.path.join(folder, f))
        new_name = os.path.splitext(f)[0] + '.jpg'
        img.convert('RGB').save(os.path.join(folder, new_name), 'JPEG', quality=90)
        print(f'{f} → {new_name}')
"}}
```

---

## 铁律
- **先预览再操作** — 用 file.list 让用户确认
- **不覆盖原文件** — 输出到新文件名/新目录
- **出错时报告具体文件** — 不要静默失败

## 可用 Action
| action | 用途 |
|--------|------|
| `python.run` | 执行重命名/PDF/图片脚本 |
| `file.list` | 预览文件列表 |
| `shell.run` | 简单操作 |
