# VRM 身体展示优化

> 基于 three-vrm (⭐2k, pixiv 官方) + 社区 23 Issues 的已知问题和修复方案。

## 参考项目

| 项目 | Stars | 用途 |
|------|-------|------|
| [pixiv/three-vrm](https://github.com/pixiv/three-vrm) | 2k | VRM → Three.js 官方加载器 |
| [moeru-ai/airi](https://github.com/moeru-ai/airi) | 41.5k | AI 伴侣，VRM/Live2D 渲染 |
| [super-agent-party](https://github.com/heshengtao/super-agent-party) | 2.5k | AI 伴侣 + VRM |
| [VRM-Addon-for-Blender](https://github.com/saturday06/VRM-Addon-for-Blender) | 1.7k | Blender VRM 插件 |

## three-vrm 已知问题（来自 GitHub Issues）

| Issue | 问题 | 影响 |
|-------|------|------|
| #1326 | VRM0 morph binds, 目标 mesh 未定义时无警告 | BlendShape 表情不生效却无报错 |
| #1839 | MToon outline 颜色溢出到网格内部 | 描边显示异常 |
| #1838 | MToon sphere 渲染偏暗 | 模型颜色比预期深 |
| #1824 | Three.js r183 兼容性 | 新版 Three.js 材质报错 |

## v3 当前 VRM 架构

```
vrm-inspector-panel.mjs
  ├── iframe → 桌面宠物.html (Three.js + three-vrm)
  ├── postMessage 通信
  └── 滑块控制 → callFrame() → iframe contentWindow
```

### 常见数值不对应问题：

**1. 镜头限制值可能超出 three-vrm 坐标系**

当前值：
```javascript
CAMERA_LIMITS = {
  focus: [-0.32, 0.32],    // ← 可能过小，无法聚焦到脸部
  height: [-0.28, 0.28],   // ← 可能不够覆盖全身
  distance: [-1.5, 1.5],   // ← 负值表示拉近？语义可能反了
  side: [-0.65, 0.65]      // ← 可能不够覆盖侧脸
};
```

修复：参考 three-vRM 官方示例的 OrbitControls 范围
```javascript
// three-vrm 推荐值（基于官方 examples/humanoid.html）
CAMERA_LIMITS = {
  distance: [0.3, 3.0],    // 正值 = 距离，越近越大
  height: [-1.0, 1.0],     // Y 轴偏移
  side: [-1.0, 1.0],       // X 轴偏移
  focus: [-0.5, 0.5]       // Z 轴焦点偏移
};
```

**2. 灯光值可能与 MToon 材质参数不对应**

three-vrm 使用 MToon 材质，光照参数物理含义不同：
```javascript
// MToon 实际参数
LIGHTING_LIMITS = {
  key: [0.3, 2.5],          // 主光源强度（MToon 对此敏感）
  angle: [-Math.PI, Math.PI], // 弧度，不是倍数
  ambient: [0.1, 1.5],      // 环境光（MToon shadeColor 受此影响）
  exposure: [0.5, 2.0]      // 色调映射曝光
};
```

**3. BlendShape 名称不匹配**

VRM 模型的表情混合形状名称因模型而异。v3 当前直接传字符串给 `setExpression()`，但不同模型的 BlendShape 名称可能不同（如 `joy` vs `happy` vs `fun`）。

修复：先从 VRM 模型读取实际 BlendShape 列表再匹配
```javascript
// 在 桌面宠物.html 中
function setExpression(name, intensity) {
  const vrm = currentVRM;
  if (!vrm || !vrm.expressionManager) return;
  // 先获取模型实际 blendShape 名称
  const shapes = vrm.expressionManager.getExpressionNames();
  // 模糊匹配
  const matched = shapes.find(s => s.toLowerCase().includes(name.toLowerCase()));
  if (matched) {
    vrm.expressionManager.setValue(matched, intensity);
  }
}
```

**4. SpringBone 物理计算不同步**

VRM SpringBone 依赖帧率，不同设备表现不同。修复：统一使用 `clock.getDelta()` 并限制最大 delta。

```javascript
// 在渲染循环中
const delta = Math.min(clock.getDelta(), 0.1); // 限制最大步长
vrm.update(delta);
```

## 调试方法

### 查看模型 BlendShape 列表
```json
{"action": "shell.run", "args": {"command": "cd /d/tiangongv3/tiangong-v3-qiyuan-installer && node -e \"const fs=require('fs'); const buf=fs.readFileSync('resources/app.asar.unpacked/assets/avatars/imported/qiyuan_frost_blade_cc0.vrm'); const text=buf.toString('utf-8', 0, Math.min(buf.length, 500000)); const matches=[...text.matchAll(/\\\"name\\\":\s*\\\"([^\\\"]+)\\\"/g)]; console.log([...new Set(matches.map(m=>m[1]))].filter(s=>/blend|shape|morph|exp/i.test(s)).join('\n'))\""}}
```

### 检查 three-vrm 版本
```json
{"action": "shell.run", "args": {"command": "cat /d/tiangongv3/tiangong-v3-qiyuan-installer/resources/app.asar.unpacked/backend/package.json 2>/dev/null | python -c \"import json,sys; d=json.load(sys.stdin); deps=d.get('dependencies',{}); print({k:v for k,v in deps.items() if 'vrm' in k.lower() or 'three' in k.lower()})\""}}
```

## 铁律

1. **先读模型 BlendShape 再设置表情** — 硬编码名必然不对
2. **镜头用绝对值** — 不要用偏移量，用 target+position 双参数
3. **MToon 材质用 shadeColor 调暗部** — 不要只调 ambient
4. **SpringBone 限制 delta < 0.1** — 防止帧率波动导致抖动
5. **three-vrm 升级到最新** — 修复 #1824 #1326 等已知 bug
