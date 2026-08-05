---
name: vrm-alignment
description: 天工造物 VRM 角色自然站姿与手型的生物学对齐：按 JOSR 2022 / Lee & Jung 2014 / 拇指 CMC 解剖标定 applyNaturalPose 骨骼欧拉，含实测验证、测试、重启 App、推 GitHub。当用户要求调整 VRM 站姿、手臂方向、手指/拇指角度，或报告“内扣/翘/歪/不自然”等姿态问题时使用。
---

# VRM 对齐

## 工作流（顺序不可跳）

1. 读取 `references/final-pose-data.md`，确认当前已标定的基线数据与目标区间。
2. 只改 `app/frontend-v2/renderer/avatar/engines/legacy-performance-driver.mjs` 的 `applyNaturalPose()`（骨骼欧拉、手指/拇指常量）；同步更新 `tests/test_avatar_legacy_driver.test.mjs` 中引用的断言。
3. 跑测试：`node --test tests/test_avatar_*.test.mjs`（基线 177 pass / 2 skip）。
4. 重启 App（命令见 `references/measurement.md`），等模型进入自然站姿（右上臂 rotation.z ≈ -1.48）。
5. 实测：`node .codex/skills/vrm-alignment/scripts/measure-vrm-pose.cjs`，核对指标落在目标区间；截图存 `.vrm-preview/`。
6. git add/commit/push（每次源码修改必须推送 GitHub）。

## 实测目标（当前用户认可的基线）

| 部位 | 目标 |
|---|---|
| 盂肱外展 | ~5°（女 4.9°） |
| 盂肱前屈 | ~0°（女 0.6° 后伸） |
| 肱骨内旋 | ~8.7°（女 8.5°） |
| 肘前屈（lean） | ~10°（用户认为 15° 偏大） |
| 肘外翻 | ~10.4° |
| 掌心朝向 | 朝身体（palmMedial ≥ 0.98） |
| 手指 | MCP 25–38° / PIP 24–30° / DIP 10–12°，朝掌心弯 |
| 拇指 | 沿食指下垂、指腹平贴掌心、指尖距食指线 ~30mm |

## 关键避坑（详见 references/measurement.md）

- 角色面朝 = world +Z（用头部网格几何实测确认）；chest 骨骼本地 +Z 指向背面，禁止用它定“前”。
- 手臂骨本地 +X 是长轴（左手为 -X）；前臂 ly 正=前屈、lz 正=外翻。
- 手指 `rotation.z` 才是屈曲（右手负 / 左手正，左手骨本地系镜像），`rotation.x` 只是扭转。
- 拇指 `rotation.x`=CMC 旋前角（右 -0.45 / 左 -0.40，同号非镜像），左手拇指必须独立标定。
- 骨节点世界坐标是静止偏移，不能当关节中心；用本地轴世界方向或 CPU 蒙皮公式。
- 判断弯曲方向要用真实蒙皮顶点（点积方向），不能用“锐角”测量（会漏掉反方向错误）。

## 资源

- `scripts/measure-vrm-pose.cjs` — CDP 实测脚本（欧拉 + 角度指标 + 拇指网格指标）。
- `references/final-pose-data.md` — 最终标定数据与文献来源。
- `references/measurement.md` — 实测/重启/验证流程与全部避坑细节。
