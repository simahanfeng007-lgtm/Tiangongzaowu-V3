# 万能格式转换

> 图片 · 音频 · 视频 · 文档 · 电子书 — 全覆盖格式互转。
> 核心引擎：ffmpeg + pandoc + Pillow + calibre。

## 快速路由

| 用户说 | 做什么 |
|--------|--------|
| "把这段视频转成MP4" | 视频格式转换 |
| "把MP3转WAV" | 音频格式转换 |
| "把图片转成WebP" | 图片格式转换 |
| "把Markdown转成Word" | 文档格式转换 |
| "把EPUB转MOBI" | 电子书格式转换 |
| "压缩这个视频" | 视频压缩（改码率/分辨率） |
| "提取视频里的音频" | 视频→音频分离 |

---

## 零、先检测可用工具

```json
{"action": "shell.run", "args": {"command": "which ffmpeg && which pandoc && which ebook-convert && python -c 'from PIL import Image; print(\"Pillow OK\")' 2>&1"}}
```

缺什么装什么：
```bash
# ffmpeg
winget install ffmpeg
# pandoc
winget install pandoc
# calibre (含 ebook-convert)
winget install calibre
```

---

## 一、音频转换

### MP3 ↔ WAV ↔ FLAC ↔ AAC ↔ OGG ↔ M4A

**首选：ffmpeg（一行搞定）**

```bash
ffmpeg -i "输入.mp3" -b:a 192k "输出.wav"
```

```bash
# 批量：当前目录所有 m4a → mp3
for f in *.m4a; do ffmpeg -i "$f" -b:a 192k "${f%.m4a}.mp3"; done
```

**备用：Python pydub**
```json
{"action": "python.run", "args": {"code": "
from pydub import AudioSegment
audio = AudioSegment.from_file(r'输入.m4a', format='m4a')
audio.export(r'输出.mp3', format='mp3', bitrate='192k')
print('完成')
"}}
```

### 常用参数
| 参数 | 含义 |
|------|------|
| `-b:a 192k` | 比特率 192kbps |
| `-ar 44100` | 采样率 44.1kHz |
| `-ac 2` | 双声道 |
| `-vn` | 不要视频流 |

---

## 二、视频转换

### MP4 ↔ MKV ↔ AVI ↔ MOV ↔ WEBM ↔ GIF

**首选：ffmpeg**

```bash
# 格式转换（复制流，秒级完成）
ffmpeg -i "输入.mkv" -c copy "输出.mp4"

# 编码转换（H.264 → H.265 压缩）
ffmpeg -i "输入.mp4" -c:v libx265 -crf 28 -c:a aac -b:a 128k "输出.mp4"

# 压缩视频（降低码率/分辨率）
ffmpeg -i "输入.mp4" -vf "scale=1280:-1" -crf 28 -c:a aac -b:a 96k "压缩后.mp4"

# 视频 → GIF
ffmpeg -i "输入.mp4" -vf "fps=10,scale=480:-1" -loop 0 "输出.gif"

# 提取音频（视频 → MP3）
ffmpeg -i "输入.mp4" -vn -b:a 192k "输出.mp3"
```

**备用：Python moviepy**
```json
{"action": "python.run", "args": {"code": "
from moviepy.editor import VideoFileClip
clip = VideoFileClip(r'输入.avi')
clip.write_videofile(r'输出.mp4', codec='libx264', audio_codec='aac')
print('完成')
"}}
```

### 常用参数
| 参数 | 含义 |
|------|------|
| `-c copy` | 复制流不重新编码（快） |
| `-c:v libx264` | H.264 视频编码 |
| `-crf 28` | 质量 0-51（越小越好，23 默认） |
| `-vf scale=1280:-1` | 宽度缩至1280，高度等比 |
| `-vn` | 去除视频流（只留音频） |
| `-an` | 去除音频流（只留视频） |

---

## 三、图片转换

### PNG ↔ JPG ↔ WebP ↔ AVIF ↔ BMP ↔ TIFF ↔ GIF

**首选：Python Pillow（批量神器）**

```json
{"action": "python.run", "args": {"code": "
from PIL import Image
import os

folder = r'图片目录'
target_format = 'WEBP'  # 改成想要的格式
ext_map = {'JPG': '.jpg', 'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'BMP': '.bmp', 'TIFF': '.tif', 'GIF': '.gif'}

for f in os.listdir(folder):
    name, ext = os.path.splitext(f)
    ext_lower = ext.lower()
    if ext_lower in ['.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.gif']:
        img = Image.open(os.path.join(folder, f))
        new_ext = ext_map.get(target_format, '.' + target_format.lower())
        new_name = name + new_ext
        if target_format in ('JPG', 'JPEG'):
            img = img.convert('RGB')
        img.save(os.path.join(folder, new_name), target_format)
        print(f'{f} → {new_name}')
print('批量完成')
"}}
```

**Shell 单文件快速转换（需 ImageMagick）**
```bash
magick "输入.png" "输出.webp"
magick "输入.png" -resize 50% "缩小版.png"
```

---

## 四、文档转换

### DOCX ↔ MD ↔ HTML ↔ PDF ↔ EPUB ↔ TXT ↔ RTF

**首选：pandoc（格式之王）**

```bash
# Markdown → DOCX
pandoc "输入.md" -o "输出.docx"

# DOCX → Markdown
pandoc "输入.docx" -o "输出.md" --wrap=none

# Markdown → PDF（需 LaTeX 或 wkhtmltopdf）
pandoc "输入.md" -o "输出.pdf" --pdf-engine=xelatex

# HTML → Markdown
pandoc "输入.html" -o "输出.md"

# Markdown → EPUB
pandoc "输入.md" -o "输出.epub" --metadata title="书名"
```

**Python 备选**
```json
{"action": "python.run", "args": {"code": "
# MD → DOCX
from docx import Document
doc = Document()
with open(r'输入.md', 'r', encoding='utf-8') as f:
    for line in f:
        doc.add_paragraph(line.strip())
doc.save(r'输出.docx')
print('完成')
"}}
```

### 文档转换矩阵
| 源→目标 | 最佳工具 |
|---------|---------|
| MD→DOCX | pandoc |
| DOCX→MD | pandoc |
| MD→PDF | pandoc + xelatex |
| HTML→MD | pandoc |
| EPUB↔PDF | calibre |
| TXT→任意 | pandoc |

---

## 五、电子书转换

### EPUB ↔ MOBI ↔ AZW3 ↔ PDF ↔ TXT

```bash
# EPUB → MOBI
ebook-convert "输入.epub" "输出.mobi"

# EPUB → PDF
ebook-convert "输入.epub" "输出.pdf"

# TXT → EPUB（指定标题作者）
ebook-convert "输入.txt" "输出.epub" --title "书名" --authors "作者"
```

---

## 六、批量处理模板

```bash
# 批量：当前目录所有格式转目标格式
# 例：所有 .wav → .mp3
for f in *.wav; do
    ffmpeg -i "$f" -b:a 192k "${f%.wav}.mp3"
    echo "$f 完成"
done
```

---

## 铁律

1. **先 `which` 检测工具** — ffmpeg/pandoc/calibre 装好再用
2. **不覆盖原文件** — 输出到新文件名或新目录
3. **批量操作先预览** — `ls *.wav` 让用户确认再跑
4. **出错不静默** — 每个文件报转换状态
5. **优先用 `-c copy`** — 同容器换封装不重新编码，秒级完成
6. **大文件先采样** — 视频可先用 `-t 10` 取 10 秒测试参数

## 依赖速查
| 工具 | 安装 | 覆盖 |
|------|------|------|
| ffmpeg | `winget install ffmpeg` | 音/视频 |
| pandoc | `winget install pandoc` | 文档 |
| calibre | `winget install calibre` | 电子书 |
| ImageMagick | `winget install ImageMagick` | 图片 |
| pydub | `pip install pydub` | 音频备选 |
| moviepy | `pip install moviepy` | 视频备选 |
| Pillow | `pip install Pillow` | 图片备选 |
