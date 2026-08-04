#!/usr/bin/env node
// Packaged-build 34-skill full-chain E2E (T22).
// Requires the packaged app launched with --remote-debugging-port=9224.
// Each skill sends a natural-language task through the real chat surface and
// records: terminal run state, assistant reply, error-card absence, elapsed.
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { execFileSync } from "node:child_process";

const endpoint = process.env.TIANGONG_CDP_ENDPOINT || "http://127.0.0.1:9224";
const limit = Number(process.argv[2] || "0");
const fromArg = process.argv.find((item) => item.startsWith("--from="));
const fromOrdinal = fromArg ? Number(fromArg.split("=")[1] || "0") : 0;
const fromEnv = Number(process.env.TIANGONG_E2E_FROM || "0");
const effectiveFrom = fromOrdinal > 0 ? fromOrdinal : fromEnv;
const onlyArg = process.argv.find((item) => item.startsWith("--only="));
const onlyOrdinals = onlyArg
  ? new Set(String(onlyArg.split("=")[1] || "").split(",").map((s) => Number(s.trim())).filter((n) => Number.isInteger(n) && n > 0))
  : new Set();
const outputDir = process.env.TIANGONG_E2E_OUTPUT || "output/packaged-skill-e2e-v3";
const activityLog = process.env.TIANGONG_E2E_ACTIVITY_LOG || "";
const artifactRoot = process.env.TIANGONG_E2E_WORKSPACE_ROOT || "";
const gatewayDb = process.env.TIANGONG_E2E_GATEWAY_DB || "";

// MM-FE-08: rows carry artifact existence + SHA evidence, not just text.
const EXPECTED_ARTIFACTS = {
  1: ["learning_cards/sandwich_reading.md", "notes/sandwich-reading-method.md", "learning-card-sandwich-reading.md"],
  2: ["output/e2e/02-core.md"], 3: ["output/e2e/03-long.md"], 4: ["output/e2e/04-proposal.docx"],
  5: ["output/e2e/05-report.pptx"], 6: ["output/e2e/06-calc.py"], 7: ["output/e2e/07-research.md"],
  8: ["output/e2e/08-video.mp4"], 9: ["output/e2e/09-novel/README.md"], 10: ["output/e2e/10-chapter.docx"],
  11: ["output/e2e/11-poster.png"], 12: ["output/e2e/12-analysis.xlsx"], 13: ["output/e2e/13-minutes.docx"],
  14: ["output/e2e/14-sales.docx"], 15: ["output/e2e/15-course.pptx"], 16: ["output/e2e/16-knowledge.md"],
  17: ["output/e2e/17-voice.md"], 18: ["output/e2e/18-seo.md"], 19: ["output/e2e/19-calendar.xlsx"],
  20: ["output/e2e/20-probe.md"], 21: ["output/e2e/21-browser.png"], 22: ["output/e2e/22-office.docx"],
  23: ["output/e2e/23-scene.py"], 24: ["output/e2e/24-python.txt"], 25: ["output/e2e/25-cleanup.md"],
  26: ["output/e2e/26-utility.md"], 27: ["output/e2e/27-converter.md"], 28: ["output/e2e/28-search.md"],
  29: ["output/e2e/29-packaging.md"], 30: ["output/e2e/30-frontend.md"], 31: ["output/e2e/31-design.md"],
  32: ["output/e2e/32-vrm.json"], 33: ["output/e2e/33-map.md"], 34: ["output/e2e/34-omni.txt"],
};

// Task 09 creates a novel project directory, not a README at its root.
EXPECTED_ARTIFACTS[9] = [
  "output/e2e/09-novel/正文/第一章_耳鸣.md",
  "output/e2e/09-novel/正文/第一章 耳鸣.md",
  "output/e2e/09-novel/设定/世界观.md",
  "output/e2e/09-novel/大纲/章节目录.md",
];

function artifactEvidence(ordinal, sinceMs = 0) {
  // Novel projects are free-form directories; any freshly written file under
  // output/e2e/09-novel counts as the real deliverable (the gate uses the
  // same prefix rule for T22).
  if (ordinal === 9 && artifactRoot) {
    const novelRoot = join(artifactRoot, "output", "e2e", "09-novel");
    try {
      const walk = (dir) => {
        for (const entry of readdirSync(dir, { withFileTypes: true })) {
          const full = join(dir, entry.name);
          if (entry.isDirectory()) {
            const found = walk(full);
            if (found) return found;
          } else if (entry.isFile()) {
            const st = statSync(full);
            if (st.size > 0 && (!sinceMs || st.mtimeMs >= sinceMs)) {
              return {
                artifact_verified: true,
                artifact_sha256: createHash("sha256").update(readFileSync(full)).digest("hex"),
              };
            }
          }
        }
        return null;
      };
      const found = walk(novelRoot);
      if (found) return found;
    } catch {}
  }
  const candidates = EXPECTED_ARTIFACTS[ordinal] || [];
  if (!artifactRoot) return { artifact_verified: false, artifact_sha256: "" };
  for (const rel of candidates) {
    try {
      const full = join(artifactRoot, rel);
      const st = statSync(full);
      if (existsSync(full) && st.isFile() && st.size > 0 && (!sinceMs || st.mtimeMs >= sinceMs)) {
        return {
          artifact_verified: true,
          artifact_sha256: createHash("sha256").update(readFileSync(full)).digest("hex"),
        };
      }
    } catch {}
  }
  return { artifact_verified: false, artifact_sha256: "" };
}

// Each row must produce its deliverable in THIS attempt.  Remove stale targets
// before a rerun so a previous round's artifact neither satisfies the
// freshness check nor blocks regeneration via the artifact-protection guard.
function clearArtifacts(ordinal) {
  if (!artifactRoot) return;
  const targets = [...(EXPECTED_ARTIFACTS[ordinal] || [])];
  if (ordinal === 9) targets.push("output/e2e/09-novel");
  const rootResolved = resolve(artifactRoot);
  for (const rel of targets) {
    try {
      const full = resolve(rootResolved, rel);
      if (!full.startsWith(rootResolved + sep)) continue;
      if (existsSync(full)) rmSync(full, { recursive: true, force: true });
    } catch {}
  }
}

// Learning-card tasks deliver into the gateway object store, not the
// workspace filesystem.  Verify the TERMINAL_RESULT capsule that binds the
// exact prompt before claiming completion evidence.
function objectStoreEvidence(ordinal, sinceMs = 0) {
  if (ordinal !== 1 || !gatewayDb || !PROMPTS[ordinal - 1]) {
    return { object_store_verified: false, object_store_sha256: "" };
  }
  try {
    const script = [
      "import sqlite3,sys,json",
      "db,needle=sys.argv[1],sys.argv[2]",
      "con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)",
      "rows=con.execute(\"SELECT capsule_sha256, capsule_json, created_at_ms FROM request_capsules WHERE capsule_kind='TERMINAL_RESULT' AND CAST(capsule_json AS TEXT) LIKE ? ORDER BY created_at_ms DESC LIMIT 1\",('%'+needle+'%',)).fetchall()",
      "print(json.dumps({'sha': rows[0][0] if rows else '', 'created_at_ms': rows[0][2] if rows else 0, 'ok': bool(rows)}))",
    ].join(";");
    const out = execFileSync(
      "python",
      ["-c", script, gatewayDb, String(PROMPTS[ordinal - 1]).slice(0, 10)],
      { encoding: "utf8", timeout: 15000 },
    ).trim();
    const parsed = JSON.parse(out);
    return {
      object_store_verified: parsed.ok === true && (!sinceMs || Number(parsed.created_at_ms || 0) >= sinceMs),
      object_store_sha256: String(parsed.sha || ""),
    };
  } catch {
    return { object_store_verified: false, object_store_sha256: "" };
  }
}

// CC-loop structure: terminal state and completion are different facts.  A
// row that admits platform-budget exhaustion ("预算上限/主动停了下来/未完成项已
// 如实列出") is terminal but NOT complete, and must never count as a pass.
const INCOMPLETE_MARKERS = /预算上限|主动停了下来|未完成项已如实列出|同一个动作重复了太多次/;

function finalizeRow(row) {
  const sinceMs = Number(row.started_at_ms || 0);
  Object.assign(row, artifactEvidence(row.ordinal, sinceMs), objectStoreEvidence(row.ordinal, sinceMs));
  const reply = String(row.reply || "");
  const incompleteAdmission = INCOMPLETE_MARKERS.test(reply);
  const completionEvidence = row.artifact_verified === true || row.object_store_verified === true;
  row.completion_evidence = !incompleteAdmission && completionEvidence;
  row.ok = Boolean(
    row.terminal_observed === true
    && !row.error_card
    && !row.echo
    && !row.thinking
    && !incompleteAdmission
    && completionEvidence
  );
  return row;
}

function backendActivityMs() {
  if (!activityLog) return 0;
  try {
    return require("node:fs").statSync(activityLog).mtimeMs;
  } catch {
    return 0;
  }
}

const PROMPTS = [
  "记录一条学习卡片：三明治阅读法，来源为读书笔记。",
  "创建文件 output/e2e/02-core.md，内容为 CORE_ACTIONS_OK。",
  "生成一份长文档大纲，保存为 output/e2e/03-long.md。",
  "生成商务方案 Word 文档 output/e2e/04-proposal.docx，标题《AI 客服试点方案》，包含目标与验收。",
  "生成季度经营回顾 PPT output/e2e/05-report.pptx，两页：关键指标、下一步。",
  "创建 Python 文件 output/e2e/06-calc.py，包含 add 函数并保存。",
  "整理一份研究证据清单，保存为 output/e2e/07-research.md。",
  "生成 3 秒幻灯片视频 output/e2e/08-video.mp4，封面 320x180。",
  "创建小说项目 output/e2e/09-novel，科幻题材，一章即可。",
  "生成网文章节 Word 文档 output/e2e/10-chapter.docx。",
  "生成 720x960 蓝色海报图片 output/e2e/11-poster.png。",
  "生成经营分析 Excel output/e2e/12-analysis.xlsx，包含两个月收支数据。",
  "生成会议纪要 Word 文档 output/e2e/13-minutes.docx。",
  "生成 B2B 销售话术 Word 文档 output/e2e/14-sales.docx。",
  "生成课程教案 PPT output/e2e/15-course.pptx，两页。",
  "导入知识条目：技能验证要点，保存为 output/e2e/16-knowledge.md。",
  "生成授权音频交付清单，保存为 output/e2e/17-voice.md。",
  "生成 SEO 友好文章，保存为 output/e2e/18-seo.md。",
  "生成内容排期 Excel output/e2e/19-calendar.xlsx。",
  "生成本机可用原生应用能力清单，保存为 output/e2e/20-probe.md。",
  "用浏览器打开 data:text/html,<h1>E2E_OK</h1> 并截图保存 output/e2e/21-browser.png。",
  "生成 Office 桥接文档 output/e2e/22-office.docx。",
  "生成 Blender 场景脚本文件 output/e2e/23-scene.py（写入脚本内容即可）。",
  "把 Python 版本与环境说明写入文件 output/e2e/24-python.txt。",
  "生成桌面清理计划，保存为 output/e2e/25-cleanup.md。",
  "生成实用工具箱说明，保存为 output/e2e/26-utility.md。",
  "生成格式转换说明，保存为 output/e2e/27-converter.md。",
  "搜索 Python pathlib 官方文档，把带来源链接的摘要保存为 output/e2e/28-search.md。",
  "生成打包发布清单，保存为 output/e2e/29-packaging.md。",
  "生成前端优化建议，保存为 output/e2e/30-frontend.md。",
  "生成前端设计规范，保存为 output/e2e/31-design.md。",
  "确保 output/e2e/32-vrm.json 是有效的 VRM 弹簧骨配置：文件不存在就先创建，然后把弹簧骨刚度 stiffness 设为 0.6，并读回验证。",
  "生成思维导图 output/e2e/33-map.md，主题：技能验证。",
  "创建文件 output/e2e/34-omni.txt，内容 OMNI_BODY_REFERENCE_OK。",
];

async function connect() {
  const targets = await (await fetch(`${endpoint}/json/list`)).json();
  const page = targets.find((t) => t.type === "page" && t.url.includes("frontend-v2/index.html"));
  if (!page?.webSocketDebuggerUrl) throw new Error("frontend CDP target unavailable");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(String(e.data));
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
  });
  return {
    ws,
    call(method, params = {}) {
      const i = ++id;
      return new Promise((resolve, reject) => {
        pending.set(i, { resolve, reject });
        ws.send(JSON.stringify({ id: i, method, params }));
      });
    },
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitIdle(cdp, capMs = 900000) {
  const expr = `(() => {
    const tas = [...document.querySelectorAll('textarea')].map(el => el.getAttribute('placeholder')||'');
    const idle = tas.some(ph => ph.includes('输入任务或对话')) && !tas.some(ph => ph.includes('排队'));
    return idle;
  })()`;
  const deadline = Date.now() + capMs;
  let lastMessageCount = -1;
  let stalledSince = 0;
  while (Date.now() < deadline) {
    const r = await cdp.call("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.result.value === true) return true;
    // Dead-run recovery: if the run shows no new message for 120s while we
    // wait, interrupt it once (isolated test profile only) and continue.
    const countExpr = `(() => {
      const msgs = [...document.querySelectorAll('[class*="message"]')].length;
      const hasInterrupt = [...document.querySelectorAll('button')].some(b => (b.getAttribute('aria-label')||'') === '中断当前执行');
      return JSON.stringify({ count: msgs, hasInterrupt });
    })()`;
    const c = await cdp.call("Runtime.evaluate", { expression: countExpr, returnByValue: true });
    try {
      const state = JSON.parse(c.result.value);
      if (lastMessageCount === -1) lastMessageCount = state.count;
      // Backend activity (model/tool trace) is the authoritative liveness
      // signal.  A run that is still calling the model or executing tools
      // must never be interrupted just because the UI renders no new bubble.
      const backendActive = Date.now() - backendActivityMs() < 60000;
      if (state.count === lastMessageCount && !backendActive) {
        if (!stalledSince) stalledSince = Date.now();
        else if (Date.now() - stalledSince > 120000 && state.hasInterrupt) {
          await cdp.call("Runtime.evaluate", {
            expression: `([...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||'') === '中断当前执行')||{}).click?.()`,
            returnByValue: true,
          });
          return "interrupted";
        }
      } else {
        lastMessageCount = state.count;
        stalledSince = 0;
      }
    } catch {}
    await sleep(5000);
  }
  return false;
}

async function runSkill(cdp, skillId, ordinal, prompt) {
  const started = Date.now();
  const idleResult = await waitIdle(cdp);
  if (idleResult === "interrupted") {
    // A stuck run was interrupted: never resend the same task (the gateway
    // worker may still be wedged). Mark it failed and move to the next one.
    return { ordinal, skill_id: skillId, ok: false, reason: "run_stuck_interrupted", reply: "", reply_len: 0, terminal_observed: false, error_card: true, elapsed_ms: Date.now() - started };
  } else if (idleResult !== true) {
    return { ordinal, skill_id: skillId, ok: false, reason: "run_stuck", reply: "", reply_len: 0, terminal_observed: false, error_card: true, elapsed_ms: Date.now() - started };
  }
  const setExpr = `(() => {
    let ta = [...document.querySelectorAll('textarea')].find(el => (el.getAttribute('placeholder')||'').includes('输入任务或对话'));
    if (!ta) {
      const rail = [...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||'') === '对话');
      if (rail) { rail.click(); return 'SWITCHED'; }
      return 'NO_TEXTAREA';
    }
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, ${JSON.stringify(prompt)});
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return 'SET';
  })()`;
  const setRes = await cdp.call("Runtime.evaluate", { expression: setExpr, returnByValue: true });
  if (setRes.result.value === "SWITCHED") {
    await sleep(3000);
    const retry = await cdp.call("Runtime.evaluate", { expression: setExpr.replace("'SWITCHED'", "'SET'"), returnByValue: true });
    if (retry.result.value !== "SET") return { ordinal, skill_id: skillId, ok: false, reason: "input_unavailable", reply: "", reply_len: 0, terminal_observed: false, error_card: true, elapsed_ms: Date.now() - started };
  }
  if (setRes.result.value !== "SET") return { ordinal, skill_id: skillId, ok: false, reason: "input_unavailable", reply: "", reply_len: 0, terminal_observed: false, error_card: true, elapsed_ms: Date.now() - started };
  const clickExpr = `(() => {
    const candidates = [...document.querySelectorAll('button')].filter(b => {
      const aria = (b.getAttribute('aria-label')||'').toLowerCase();
      const cls = (b.className||'').toString().toLowerCase();
      return aria.includes('发送') || cls.includes('send');
    });
    if (!candidates.length) return 'NO_SEND';
    candidates[0].click();
    return 'CLICKED';
  })()`;
  await cdp.call("Runtime.evaluate", { expression: clickExpr, returnByValue: true });
  const readExpr = `(() => {
    const msgs = [...document.querySelectorAll('[class*="message"]')].map(el => el.textContent.trim()).filter(t => t && t.length > 2);
    const busy = document.body.innerText.includes('思考中') || document.body.innerText.includes('执行中');
    const tas = [...document.querySelectorAll('textarea')].map(el => el.getAttribute('placeholder')||'');
    const idle = tas.some(ph => ph.includes('输入任务或对话')) && !tas.some(ph => ph.includes('排队'));
    const confirm = [...document.querySelectorAll('button')].some(b => {
      const text = (b.textContent||'').trim();
      return text === '本次允许' || text === '总是允许';
    });
    return JSON.stringify({ count: msgs.length, last: msgs.slice(-2), all: msgs.slice(-40), busy, confirm, idle });
  })()`;
  let lastState = "";
  let stable = 0;
  const deadline = Date.now() + 900000;
  while (Date.now() < deadline) {
    const r = await cdp.call("Runtime.evaluate", { expression: readExpr, returnByValue: true });
    lastState = r.result.value;
    const data = JSON.parse(lastState);
    const joined = data.last.join("\n");
    if (data.confirm) {
      const denyExpr = `(() => {
        const b = [...document.querySelectorAll('button')].find(el => (el.textContent||'').trim() === '拒绝');
        if (b) { b.click(); return 'DENIED'; }
        return 'NO_DENY';
      })()`;
      await cdp.call("Runtime.evaluate", { expression: denyExpr, returnByValue: true });
      return { ordinal, skill_id: skillId, ok: false, reason: "confirmation_card", reply: "", reply_len: 0, terminal_observed: false, error_card: true, elapsed_ms: Date.now() - started };
    }
    const echo = data.last.some((text) => text.startsWith(prompt.slice(0, 12)));
    // Terminal truth comes from the input box becoming idle again.  A stale
    // "思考中" banner in the page body, or a legitimate "…" inside a reply
    // (e.g. a truncated hash), must not turn a completed run into a timeout.
    if (data.idle && joined.length > 8 && !echo) {
      stable += 1;
      if (stable >= 2) break;
    } else {
      stable = 0;
    }
    await sleep(5000);
  }
  const data = JSON.parse(lastState);
  // The UI may render an interim "我先…/【等待…】" message as the last
  // assistant bubble while the real completion sits earlier in the list.
  // Pick the last message that is not an interim marker (or lacks a clear
  // artifact evidence path) so the recorded reply reflects the terminal turn.
  const isInterim = (text) => /【等待|稍后|等我|我先确认|先确认一下/.test(text)
    && !/(已生成|已写入|已经写好|保存为|路径[:：]|SHA256|sha256)/i.test(text);
  const candidates = (data.all && data.all.length ? data.all : data.last)
    .filter((text) => text && !isInterim(text));
  const reply = candidates.length
    ? candidates[candidates.length - 1]
    : (data.last[data.last.length - 1] || "");
  // A reply that carries artifact evidence (path/hash/generated wording) is a
  // real completion even when its content mentions "不可用"/"失败" (e.g. a
  // capability inventory that lists unavailable adapters).  Only a reply with
  // no artifact evidence and a failure marker is an error card.
  const hasArtifactEvidence = /(output[\\/][\w\-.\\/]+|已生成|已写入|已经写好|保存为|路径[:：]|SHA256|sha256)/i.test(reply);
  const errorCard = !hasArtifactEvidence && /错误|失败|不可用|异常/.test(reply) && !/完成|成功/.test(reply);
  const echo = reply.startsWith(prompt.slice(0, 12));
  const thinking = reply.startsWith("思考中");
  return finalizeRow({
    ordinal,
    skill_id: skillId,
    reason: "",
    started_at_ms: started,
    reply: reply.slice(0, 400),
    reply_len: reply.length,
    terminal_observed: data.idle,
    error_card: errorCard,
    echo,
    thinking,
    elapsed_ms: Date.now() - started,
  });
}

mkdirSync(outputDir, { recursive: true });
const force = process.argv.includes("--force");
if (force) {
  writeFileSync(`${outputDir}/progress.ndjson`, "");
}
const cdp = await connect();
// One-time cleanup: clear a stuck run left by a killed runner. Never called
// mid-suite so active user-visible requests are not interrupted.
const stuckExpr = `(() => {
  const b = [...document.querySelectorAll('button')].find(el => (el.getAttribute('aria-label')||'') === '中断当前执行');
  if (!b) return 'NO_STUCK';
  b.click();
  return 'INTERRUPTED_ONCE';
})()`;
const stuck = await cdp.call("Runtime.evaluate", { expression: stuckExpr, returnByValue: true });
if (stuck.result.value === "INTERRUPTED_ONCE") {
  console.log("cleared stuck run at startup");
  await sleep(6000);
}
// Auto-resume: find the last skill prompt already present in the conversation.
let resumeFrom = 0;
if (onlyOrdinals.size > 0) {
  resumeFrom = 0;
} else if (effectiveFrom > 0) {
  resumeFrom = Math.max(0, effectiveFrom - 1);
} else if (!force) {
  const scanExpr = `(() => {
    const msgs = [...document.querySelectorAll('[class*="message"]')].map(el => el.textContent.trim()).filter(t => t && t.length > 2);
    return JSON.stringify(msgs.slice(-80));
  })()`;
  const scan = await cdp.call("Runtime.evaluate", { expression: scanExpr, returnByValue: true });
  const history = JSON.parse(scan.result.value || "[]");
  for (let index = PROMPTS.length - 1; index >= 0; index -= 1) {
    const needle = PROMPTS[index].slice(0, 12);
    if (history.some((text) => text.startsWith(needle))) {
      resumeFrom = index + 1;
      break;
    }
  }
}
const rows = [];
const count = onlyOrdinals.size > 0 ? PROMPTS.length : (limit > 0 ? Math.min(limit, PROMPTS.length) : PROMPTS.length);
let previous = [];
try {
  previous = JSON.parse(`[${readFileSync(`${outputDir}/progress.ndjson`, "utf8").trim().split("\n").filter(Boolean).join(",")}]`);
} catch {}
for (let index = resumeFrom; index < count; index += 1) {
  const ordinal = index + 1;
  if (onlyOrdinals.size > 0 && !onlyOrdinals.has(ordinal)) continue;
  const skillId = `skill_${ordinal}`;
  clearArtifacts(ordinal);
  const row = await runSkill(cdp, skillId, ordinal, PROMPTS[index]);
  rows.push(row);
  appendFileSync(`${outputDir}/progress.ndjson`, JSON.stringify(row) + "\n");
  console.log(JSON.stringify({ ordinal, ok: row.ok, reply_len: row.reply_len, elapsed_ms: row.elapsed_ms }));
}
cdp.ws.close();
const byOrdinal = new Map();
for (const row of [...previous, ...rows]) {
  byOrdinal.set(row.ordinal, row);
}
const allRows = [...byOrdinal.values()].sort((a, b) => a.ordinal - b.ordinal).map((row) => finalizeRow(row));
const payload = {
  schema: "tiangong.packaged-skill-e2e.v1",
  build: "g6b-candidate-win-unpacked",
  total: allRows.length,
  pass: allRows.filter((r) => r.ok).length,
  rows: allRows,
};
const raw = JSON.stringify(payload, null, 2);
writeFileSync(`${outputDir}/report.json`, raw);
writeFileSync(`${outputDir}/report.sha256`, `${createHash("sha256").update(raw).digest("hex")}  report.json\n`);
console.log(JSON.stringify({ report: `${outputDir}/report.json`, pass: payload.pass, total: payload.total }));
