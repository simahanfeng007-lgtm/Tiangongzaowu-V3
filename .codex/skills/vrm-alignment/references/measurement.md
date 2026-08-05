# 实测 / 重启 / 验证流程

## 重启源码版 App（带 CDP）

```powershell
$p = Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
if ($p) { Stop-Process -Id $p -Force }
Start-Sleep -Seconds 2
$exe = "C:\TG3Clean\src\app\node_modules\electron\dist\electron.exe"
$args = @("--user-data-dir=C:\Users\77571\AppData\Local\TiangongV3-SourceWork\electron-user-data", "--remote-debugging-port=9223", ".")
Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory "C:\TG3Clean\src\app" -WindowStyle Hidden
```

等模型进入自然站姿：右上臂 `rotation.z` ≈ -1.48（T-pose 时为 0，需等驱动帧跑起来）。

## 实测入口

- CDP：`http://127.0.0.1:9223`，页面 `file:///C:/TG3Clean/src/app/frontend-v2/index.html`。
- 引擎调试句柄：`window.__avatarDebugEngine.debugInternals().scene`。
- 骨骼命名：原始 `J_Bip_[RL]_*`；归一化 `Normalized_J_Bip_[RL]_*`（驱动写归一化，蒙皮用原始，vrm.update 每帧同步）。
- 直接测量脚本：`node .codex/skills/vrm-alignment/scripts/measure-vrm-pose.cjs`。

## 坐标与轴约定（实测验证过）

- 世界系：up=+Y、角色面朝=+Z、角色右侧=-X；右臂在 -X 侧，左臂在 +X 侧。
- 手臂长轴：右手/前臂=骨本地 +X；左手/前臂=骨本地 -X。
- 前臂：ly 正=前屈；lz 正=外翻。
- 手指/拇指：本地 +X=长轴；rotation.z=屈曲（右手负）；rotation.y=分指/外展；rotation.x=扭转（拇指=旋前角）。
- 左手手指骨本地 +X 指向手腕（镜像），屈曲方向乘 sign（左 +1 变正）。
- 拇指左右非纯镜像：metaX 左右同号（右 -0.45 / 左 -0.40）。

## 为什么不能用骨节点世界坐标当关节中心

该模型骨节点 position 是“指向子关节”的静止偏移（例如 J_Bip_R_UpperArm 的位置=肘），
旋转不改变骨节点自身位置。正确做法：

1. 方向类指标：用 `matrixWorld` 旋转本地轴（`dir3`）；
2. 位置类指标（拇指指尖/指根）：用 CPU 蒙皮（与 three.js shader 一致）：

```
worldVertex = mesh.matrixWorld * bindMatrixInverse
            * ( Σ w_i * (bone_i.matrixWorld * boneInverse_i) * bindMatrix * restVertex )
```

矩阵为列主序；`skeleton.boneInverses` 直接可用。采样“主骨骼权重 > 0.4”的顶点簇质心。

## 手型方向验证（不能只测角度）

锐角测量会漏掉“弯反了”。必须用蒙皮顶点算方向点积：

```
seg = PIP簇质心 - MCP簇质心
palmNormal = hand 骨本地 -Y 的世界方向
dot = normalize(seg) · palmNormal   // 正=朝掌心弯（左右都为正才正确）
```

## 修改后的完整验收链

1. 改驱动 + 测试断言。
2. `node --test tests/test_avatar_*.test.mjs` → 177 pass / 2 skip。
3. 重启 App → 等自然站姿 → `measure-vrm-pose.cjs` 核对目标区间。
4. 截图存 `.vrm-preview/`，提交推送 GitHub。
