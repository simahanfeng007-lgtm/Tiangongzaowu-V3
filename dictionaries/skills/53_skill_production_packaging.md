# 产品级 EXE 打包

> 基于 electron-builder（⭐14.6k）+ NSIS 制作可分发的单文件安装包。

## 参考项目

| 项目 | Stars | 用途 |
|------|-------|------|
| [electron-builder](https://github.com/electron-userland/electron-builder) | 14.6k | Electron 全平台打包，NSIS/DMG/AppImage |
| [nexe](https://github.com/nexe/nexe) | 13.6k | Node.js → 单 exe |
| [electron-packager](https://github.com/electron/electron-packager) | — | 基础打包，配合 electron-builder 使用 |

## v3 打包架构

```
天工造物v3起源/
├── 天工造物v3起源.exe          ← Electron 主程序
├── resources/
│   ├── app.asar                ← 前端代码
│   └── app.asar.unpacked/      ← 后端 Python + 素材
├── *.dll                       ← Chromium 运行时
├── *.pak / *.dat / *.bin       ← Chromium 资源
└── locales/                    ← 多语言
```

## 方式一：electron-builder + NSIS（推荐，产品级）

### 1. 安装
```bash
npm install --save-dev electron-builder
```

### 2. package.json 配置
```json
{
  "build": {
    "appId": "com.tiangong.v3",
    "productName": "天工造物v3起源",
    "directories": {
      "output": "dist"
    },
    "win": {
      "target": [
        { "target": "nsis", "arch": ["x64"] },
        { "target": "portable", "arch": ["x64"] }
      ],
      "icon": "assets/tiangong-logo.ico"
    },
    "nsis": {
      "oneClick": false,
      "perMachine": false,
      "allowToChangeInstallationDirectory": true,
      "installerIcon": "assets/tiangong-logo.ico",
      "uninstallerIcon": "assets/tiangong-logo.ico",
      "installerHeaderIcon": "assets/tiangong-logo.ico",
      "createDesktopShortcut": true,
      "createStartMenuShortcut": true
    },
    "portable": {
      "artifactName": "天工造物v3起源-${version}-portable.exe"
    },
    "extraResources": [
      { "from": "backend-dist", "to": "app.asar.unpacked/backend" }
    ],
    "asar": true,
    "asarUnpack": ["**/*.node", "**/backend/**"]
  }
}
```

### 3. 构建
```bash
npx electron-builder build --win
```

产出：
- `dist/天工造物v3起源 Setup x.x.x.exe` — NSIS 安装包
- `dist/天工造物v3起源-x.x.x-portable.exe` — 绿色便携版

## 方式二：手动组装便携版（当前方式，快速迭代用）

```bash
# 1. 打包 asar
npx asar pack frontend-v2-live resources/app.asar

# 2. 复制 Electron 运行时
# 从 electron 安装目录或 electron-builder 缓存中复制：
#   - chrome_100_percent.pak / chrome_200_percent.pak
#   - icudtl.dat / resources.pak / snapshot_blob.bin / v8_context_snapshot.bin
#   - d3dcompiler_47.dll / ffmpeg.dll / libEGL.dll / libGLESv2.dll
#   - vk_swiftshader.dll / vulkan-1.dll
#   - locales/

# 3. 复制主程序
# cp electron.exe → 天工造物v3起源.exe

# 4. 复制后端
# cp -r backend/ → resources/app.asar.unpacked/backend/
```

## 方式三：单文件 EXE（nexe / pkg）

适用于纯 Node.js 项目，不适合 v3（有 Python 后端）。

## 自动化打包脚本模板

```json
{"action": "shell.run", "args": {"command": "cd /d/tiangongv3/tiangong-v3-qiyuan-installer && bash -c '
DIST=\"${TIANGONG_RELEASE_OUTPUT_DIR:?请先选择发布目录并设置 TIANGONG_RELEASE_OUTPUT_DIR}\"
mkdir -p \"$DIST/resources\"
# 1. asar
npx asar pack frontend-v2-live \"$DIST/resources/app.asar\"
# 2. 后端
cp -r resources/app.asar.unpacked \"$DIST/resources/\"
# 3. Electron 运行时
for f in chrome_100_percent.pak chrome_200_percent.pak icudtl.dat resources.pak snapshot_blob.bin v8_context_snapshot.bin *.dll vk_swiftshader_icd.json; do
  cp \"$f\" \"$DIST/\" 2>/dev/null
done
cp -r locales \"$DIST/\"
# 4. 入口文件
cp main.js preload.js \"$DIST/\"
cp 天工造物v3起源.exe \"$DIST/\"
echo \"打包完成: $DIST\"
'"}}
```

## 铁律

1. **asar.unpacked 不改结构** — 后端路径必须和开发时一致
2. **DLL 一个不能少** — 缺任何 Chromium DLL 都报 0xC0000135
3. **package.json 放根目录** — electron-builder 从根目录读配置
4. **打包后验证** — 复制到空目录启动一次，确认无 DLL 错误
5. **版本号递增** — 每次发布改 `version` 字段
