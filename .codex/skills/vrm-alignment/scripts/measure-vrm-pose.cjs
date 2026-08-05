// VRM 自然站姿实测脚本：连接运行中的 Electron CDP（默认 127.0.0.1:9223），
// 读取 applyNaturalPose 生效后的骨骼欧拉、角度指标与拇指蒙皮指标。
// 用法：node .codex/skills/vrm-alignment/scripts/measure-vrm-pose.cjs [port]
const PORT = Number(process.argv[2] || 9223);

function loadPlaywright() {
  const roots = [
    "C:/Users/77571/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright",
    process.env.LOCALAPPDATA + "/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright",
    process.env.USERPROFILE + "/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright",
  ];
  for (const r of roots) {
    try { return require(r); } catch { /* next */ }
  }
  throw new Error("playwright not found; set a valid path in loadPlaywright()");
}

(async () => {
  const { chromium } = loadPlaywright();
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
  const pages = [];
  for (const ctx of browser.contexts()) pages.push(...ctx.pages());
  const main = pages.find((p) => p.url().includes("frontend-v2/index.html")) || pages[0];
  if (!main) { console.log(JSON.stringify({ error: "main page not found" })); await browser.close(); return; }

  let result = null;
  for (let attempt = 0; attempt < 40; attempt++) {
    result = await main.evaluate(() => {
      const eng = window.__avatarDebugEngine;
      if (!eng || typeof eng.debugInternals !== "function") return null;
      const scene = eng.debugInternals().scene;
      const rua = scene.getObjectByName("J_Bip_R_UpperArm");
      if (!rua || Math.abs(rua.rotation.z) < 0.5) return null; // 尚未进入自然站姿
      const get = (n) => scene.getObjectByName(n);
      const Quaternion = scene.quaternion.constructor;
      const Matrix4 = scene.matrix.constructor;
      const Vector3 = scene.position.constructor;
      const m4 = new Matrix4();
      const up = new Vector3(0, 1, 0);
      const fwd = new Vector3(0, 0, 1); // 角色面朝 +Z（头部网格实测）
      const right = new Vector3(-1, 0, 0);
      const dir3 = (o, ax) => new Vector3(ax, 0, 0)
        .applyQuaternion(new Quaternion().setFromRotationMatrix(m4.extractRotation(o.matrixWorld))).normalize();
      const ang = (a, b) => Math.acos(Math.max(-1, Math.min(1, a.dot(b)))) * 180 / Math.PI;
      const acute = (a, b) => { const d = ang(a, b); return +Math.min(d, 180 - d).toFixed(1); };

      function arm(side, axSign, outSign) {
        const ua = get(`J_Bip_${side}_UpperArm`), la = get(`J_Bip_${side}_LowerArm`), ha = get(`J_Bip_${side}_Hand`);
        const u = dir3(ua, axSign), f = dir3(la, axSign), h = dir3(ha, axSign);
        const lateral = outSign === 1 ? right : right.clone().negate();
        const palm = new Vector3(0, -1, 0).applyQuaternion(
          new Quaternion().setFromRotationMatrix(m4.extractRotation(ha.matrixWorld))).normalize();
        const rot = (o) => [Number(o.rotation.x), Number(o.rotation.y), Number(o.rotation.z)].map(v => +v.toFixed(4));
        return {
          eulers: { upper: rot(ua), lower: rot(la), hand: rot(ha) },
          abd: +(Math.atan2(u.dot(lateral), -u.dot(up)) * 180 / Math.PI).toFixed(2),
          flex: +(Math.atan2(u.dot(fwd), -u.dot(up)) * 180 / Math.PI).toFixed(2),
          lean: +(Math.atan2(f.dot(fwd), -f.dot(up)) * 180 / Math.PI).toFixed(2),
          valg: +(Math.atan2(f.dot(lateral), -f.dot(up)) * 180 / Math.PI).toFixed(2),
          elbowFlex: +ang(u, f).toFixed(2),
          palmMedial: +(outSign === 1 ? -palm.dot(right) : palm.dot(right)).toFixed(3),
          fingerVsForearm: +ang(h, f).toFixed(2),
        };
      }

      function fingers(side, axHand) {
        const ax = side === "R" ? "R" : "L";
        const hd = dir3(get(`J_Bip_${ax}_Hand`), axHand);
        const res = {};
        for (const f of ["Index", "Middle", "Ring", "Little"]) {
          const d1 = dir3(get(`J_Bip_${ax}_${f}1`), 1), d2 = dir3(get(`J_Bip_${ax}_${f}2`), 1), d3 = dir3(get(`J_Bip_${ax}_${f}3`), 1);
          res[f] = { mcp: acute(d1, hd), pip: acute(d2, d1), dip: acute(d3, d2) };
        }
        const tm = dir3(get(`J_Bip_${ax}_Thumb1`), 1), tp = dir3(get(`J_Bip_${ax}_Thumb2`), 1), td = dir3(get(`J_Bip_${ax}_Thumb3`), 1);
        res.Thumb = { cmc: acute(tm, hd), mcp: acute(tp, tm), ip: acute(td, tp) };
        return res;
      }

      // ── 拇指蒙皮指标（真实网格）────────────────────────────
      let mesh = null;
      scene.traverse(o => { if (!mesh && o.isSkinnedMesh && o.geometry && o.name === "Bodybaked") mesh = o; });
      const thumb = {};
      if (mesh) {
        const skeleton = mesh.skeleton;
        const names = skeleton.bones.map(b => b.name);
        const bones = skeleton.bones;
        const geo = mesh.geometry;
        const posAttr = geo.attributes.position;
        const skIdx = geo.attributes.skinIndex;
        const skW = geo.attributes.skinWeight;
        const n = posAttr.count;
        const matVec = (m, v) => [m[0]*v[0]+m[4]*v[1]+m[8]*v[2]+m[12], m[1]*v[0]+m[5]*v[1]+m[9]*v[2]+m[13], m[2]*v[0]+m[6]*v[1]+m[10]*v[2]+m[14]];
        const matMul = (a, b) => { const o = new Array(16); for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) o[c*4+r] = a[r]*b[c*4] + a[4+r]*b[c*4+1] + a[8+r]*b[c*4+2] + a[12+r]*b[c*4+3]; return o; };
        const bind = mesh.bindMatrix.elements, bindInv = mesh.bindMatrixInverse.elements, mm = mesh.matrixWorld.elements;
        const boneInv = skeleton.boneInverses.map(mi => mi.elements);
        const skin = (i) => {
          const i4 = i*4;
          const rest = [posAttr.getX(i), posAttr.getY(i), posAttr.getZ(i)];
          const b = matVec(bind, rest);
          let sum = [0,0,0];
          for (let k = 0; k < 4; k++) {
            const w = skW.array[i4+k]; if (!w) continue;
            const bi = skIdx.array[i4+k];
            if (bi === undefined || bi >= bones.length) continue;
            const sk = matVec(matMul(bones[bi].matrixWorld.elements, boneInv[bi]), b);
            sum[0]+=sk[0]*w; sum[1]+=sk[1]*w; sum[2]+=sk[2]*w;
          }
          return matVec(mm, matVec(bindInv, sum));
        };
        const groups = {};
        for (const s of ["R","L"]) for (const bn of [`J_Bip_${s}_Thumb1`,`J_Bip_${s}_Thumb2`,`J_Bip_${s}_Thumb3`,`J_Bip_${s}_Index1`,`J_Bip_${s}_Index2`,`J_Bip_${s}_Index3`]) groups[bn] = [];
        for (let i = 0; i < n; i++) {
          const i4 = i*4;
          let best = -1, bestW = 0;
          for (let k = 0; k < 4; k++) { const w = skW.array[i4+k]; if (w > bestW) { bestW = w; best = skIdx.array[i4+k]; } }
          const bn = bones[best]?.name;
          if (bn && groups[bn] !== undefined && bestW > 0.4) groups[bn].push(i);
        }
        const centroid = (list) => { if (!list.length) return null; const c=[0,0,0]; for (const i of list) { const p = skin(i); c[0]+=p[0]; c[1]+=p[1]; c[2]+=p[2]; } const l=list.length; return [c[0]/l,c[1]/l,c[2]/l]; };
        const distToSeg = (p, a, b) => {
          const ab = [b[0]-a[0], b[1]-a[1], b[2]-a[2]];
          const ap = [p[0]-a[0], p[1]-a[1], p[2]-a[2]];
          const l2 = ab[0]*ab[0]+ab[1]*ab[1]+ab[2]*ab[2] || 1;
          let t = (ap[0]*ab[0]+ap[1]*ab[1]+ap[2]*ab[2])/l2;
          t = Math.max(0, Math.min(1, t));
          return Math.hypot(p[0]-(a[0]+ab[0]*t), p[1]-(a[1]+ab[1]*t), p[2]-(a[2]+ab[2]*t));
        };
        for (const s of ["R","L"]) {
          const cmc = centroid(groups[`J_Bip_${s}_Thumb1`]);
          const mcp = centroid(groups[`J_Bip_${s}_Thumb2`]);
          const tip = centroid(groups[`J_Bip_${s}_Thumb3`]);
          const iMCP = centroid(groups[`J_Bip_${s}_Index1`]);
          const iPIP = centroid(groups[`J_Bip_${s}_Index2`]);
          const iTIP = centroid(groups[`J_Bip_${s}_Index3`]);
          const md = [mcp[0]-cmc[0], mcp[1]-cmc[1], mcp[2]-cmc[2]];
          const ml = Math.hypot(...md) || 1;
          const pad = new Vector3(0,-1,0).applyQuaternion(new Quaternion().setFromRotationMatrix(m4.extractRotation(get(`J_Bip_${s}_Thumb2`).matrixWorld))).normalize().toArray();
          thumb[s] = {
            metaDir: [md[0]/ml, md[1]/ml, md[2]/ml].map(v=>+v.toFixed(3)),
            pad: pad.map(v=>+v.toFixed(3)),
            tip: tip.map(v=>+v.toFixed(4)),
            dIndex: +distToSeg(tip, iPIP, iTIP).toFixed(4),
            hang: +(mcp[1]-tip[1]).toFixed(4),
            rootMedial: +(s==="R" ? mcp[0]-iMCP[0] : iMCP[0]-mcp[0]).toFixed(4),
          };
        }
      }
      return {
        R: arm("R", 1, 1),
        L: arm("L", -1, -1),
        fingersR: fingers("R", 1),
        fingersL: fingers("L", -1),
        thumb,
      };
    }).catch(() => null);
    if (result) break;
    await main.waitForTimeout(2500);
  }

  console.log(JSON.stringify(result, null, 2));
  await browser.close();
})();
