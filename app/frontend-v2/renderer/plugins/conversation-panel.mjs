import { spokenBackendText } from "../core/formatters.mjs";
import { classifyRunInput, decodeConfirmCardContent, CONFIRM_DECISION_LABELS } from "../core/actions.mjs";
import { renderMediaAttachment, renderMessageContent } from "../core/message-renderer.mjs";
import { renderUserAvatar as renderSharedUserAvatar } from "../core/user-avatar.mjs";
import { normalizeUserIdentity } from "../runtime/life-view-model.mjs";
import { requestVoiceOutput } from "../runtime/http-runtime.mjs";
import { dispatchSpeechPhase } from "../avatar/speech-phase-events.mjs";

const DEFAULT_LOGO_SRC = "../assets/tiangong-avatar.png";
const CHAT_ATTACHMENT_LIMIT = 20;
const CHAT_ATTACHMENT_DATA_URL_BYTES = 8 * 1024 * 1024;

(function(){try{var k="tiangong_frontend_settings";var r=localStorage.getItem(k);if(r){var s=JSON.parse(r);if(s.personaAvatarDataUrl){delete s.personaAvatarDataUrl;localStorage.setItem(k,JSON.stringify(s))}}}catch(e){}})();

function formatTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function escHtml(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function attachmentKey(item) {
  return String(item?.artifact_revision_id || item?.object_id || item?.attachment?.object_id || item?.documentId || item?.document_id || item?.path || item?.url || item?.dataUrl || item?.data_url || item?.name || "");
}

function isGatewayArtifactCard(item) {
  return Boolean(
    item?.artifact_schema === "tiangong.gateway.artifact-card.v1"
    && /^req_[0-9a-f]{64}$/.test(String(item?.gateway_request_id || ""))
    && /^run_[0-9a-f]{64}$/.test(String(item?.run_id || ""))
    && Number.isInteger(item?.generation)
    && item.generation >= 0
    && /^arv_[0-9a-f]{64}$/.test(String(item?.artifact_revision_id || ""))
    && /^[0-9a-f]{64}$/.test(String(item?.manifest_sha256 || ""))
    && /^[0-9a-f]{64}$/.test(String(item?.card_sha256 || ""))
    && item?.qc_state === "PASSED"
    && item?.open_capability === "gateway_artifact_revision"
  );
}

function shortDigest(value) {
  const text = String(value || "");
  return text.length > 16 ? `${text.slice(0, 8)}…${text.slice(-8)}` : text;
}

function attachmentStatusText(item) {
  const status = String(item?.status || "");
  if (status === "imported") return "已入库";
  if (status === "uploaded") return "已上传";
  if (status === "attached") return "已附加";
  if (status === "selected") return "已选择";
  if (status === "failed") return "导入失败";
  return "待发送";
}

function attachmentLooksLikeMedia(item) {
  const value = String(item?.path || item?.name || "");
  return /\.(png|jpe?g|gif|webp|bmp|ico|avif|svg|tiff?|mp4|webm|ogv|mov|mkv|avi|m4v|wmv|flv|mpe?g|3gp|m2?ts|mp3|wav|ogg|m4a|flac|aac|opus|wma)$/i.test(value);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    // P2-09: refuse to convert oversized files into Data URLs (memory spike +
    // UI freeze). Local files normally carry a real path and go through the
    // trusted path channel instead; only small clipboard-only items may use
    // the Data URL fallback.
    if (Number(file?.size || 0) > CHAT_ATTACHMENT_DATA_URL_BYTES) {
      reject(new Error(`附件超过 ${Math.round(CHAT_ATTACHMENT_DATA_URL_BYTES / 1024 / 1024)} MB，不能整体转成 Data URL；请直接提供本地文件路径。`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function personaName(settings) {
  return String(settings?.personaName || "临渊者").trim() || "临渊者";
}

function personaInitial(settings) {
  return personaName(settings).slice(0, 1) || "临";
}

function renderPersonaAvatar(container, settings) {
  container.innerHTML = "";
  const img = document.createElement("img");
  img.src = String(settings?.personaAvatarDataUrl || "") || DEFAULT_LOGO_SRC;
  img.alt = personaName(settings);
  img.className = "persona-avatar-img";
  container.appendChild(img);
}

function userDisplayName(settings) {
  return normalizeUserIdentity(settings).callsign;
}

function renderUserAvatar(container, settings) {
  renderSharedUserAvatar(container, settings, {
    alt: `${userDisplayName(settings)}的头像`,
    className: "user-avatar-img",
    fallbackGlyph: normalizeUserIdentity(settings).fallbackGlyph,
  });
}

function renderEmptyState(container, settings) {
  container.innerHTML = `
    <div class="empty-state">
      <div class="empty-avatar" data-persona-avatar></div>
      <h3>天工造物 · ${escHtml(personaName(settings))}</h3>
      <p>输入任务或对话，后端会按自动流程进入执行链条。</p>
    </div>
  `;
  renderPersonaAvatar(container.querySelector("[data-persona-avatar]"), settings);
}

function progressStatusText(status) {
  const value = String(status || "pending");
  if (["done", "ok", "success", "completed"].includes(value)) return "完成";
  if (value === "neutral") return "未要求质检";
  if (["failed", "blocked", "timeout"].includes(value)) return "异常";
  if (["interrupted", "canceled", "cancelled"].includes(value)) return "已中断";
  if (["running", "loading"].includes(value)) return "进行中";
  return "等待";
}

function progressClass(status) {
  const value = String(status || "pending");
  if (["done", "ok", "success", "completed"].includes(value)) return "done";
  if (value === "neutral") return "pending";
  if (["failed", "blocked", "timeout"].includes(value)) return "failed";
  if (["interrupted", "canceled", "cancelled"].includes(value)) return "interrupted";
  if (["running", "loading"].includes(value)) return "running";
  return "pending";
}

function percent(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number * 100)));
}

function visibleProgressSteps(progress) {
  return (Array.isArray(progress?.steps) ? progress.steps : []).filter((step) => (
    String(step?.visibility || step?.meta?.visibility || "").trim().toLowerCase() !== "internal"
  ));
}

function visibleProgress(progress, activeSessionId) {
  const phase = String(progress?.phase || "");
  const finishedOk = phase === "finished" && progress?.ok !== false;
  const steps = visibleProgressSteps(progress);
  const hasGatewayFacts = steps.some(
    (step) => String(step?.meta?.type || "").toUpperCase() === "GATEWAY_STATE_PROJECTION"
  );
  return progress
    && phase !== "idle"
    && (!finishedOk || hasGatewayFacts)
    && progress.sessionId === activeSessionId
    && steps.length;
}

function supportsSpeech() {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function speechVoices() {
  if (!supportsSpeech()) return [];
  try {
    return window.speechSynthesis.getVoices?.() || [];
  } catch {
    return [];
  }
}

function voicePresetRows(settings = {}) {
  return Array.isArray(settings.bodyVoicePresets) ? settings.bodyVoicePresets : [];
}

function pickSpeechVoice(settings = {}) {
  const voices = speechVoices();
  const selected = String(settings.bodyVoiceName || "").trim();
  if (selected) {
    const voice = voices.find((item) => String(item.name || "") === selected);
    if (voice) return voice;
  }
  const presets = voicePresetRows(settings);
  const preset = presets.find((item) => String(item.id || "") === String(settings.bodyVoicePreset || ""));
  const preferredSource = Array.isArray(preset?.preferred_names) ? preset.preferred_names : Array.isArray(preset?.preferredNames) ? preset.preferredNames : [];
  const preferred = preferredSource.map((item) => String(item).toLowerCase());
  const lang = String(settings.bodyVoiceLang || preset?.lang || "zh-CN").toLowerCase();
  const sameLang = voices.filter((voice) => String(voice.lang || "").toLowerCase().startsWith(lang.slice(0, 2)));
  return sameLang.find((voice) => preferred.some((name) => String(voice.name || "").toLowerCase().includes(name)))
    || sameLang[0]
    || voices[0]
    || null;
}

function providerVoiceId(settings = {}) {
  const configured = String(settings.bodyVoiceNativeId || "").trim();
  if (configured) return configured;
  const preset = voicePresetRows(settings).find((item) => String(item.id || "") === String(settings.bodyVoicePreset || ""));
  const names = Array.isArray(preset?.preferred_names) ? preset.preferred_names : Array.isArray(preset?.preferredNames) ? preset.preferredNames : [];
  return String(names.find((name) => /Neural$/i.test(String(name))) || "");
}

let generatedVoiceAudio = null;

function speakWithBrowser(text, settings = {}) {
  if (!supportsSpeech()) return false;
  const clean = spokenBackendText(text).replace(/\s+/g, " ").trim();
  if (!clean) return false;
  const utterance = new SpeechSynthesisUtterance(clean.slice(0, 1200));
  utterance.lang = String(settings.bodyVoiceLang || "zh-CN");
  utterance.rate = Math.max(0.5, Math.min(1.6, Number(settings.bodyVoiceRate || 1)));
  utterance.pitch = Math.max(0.5, Math.min(1.8, Number(settings.bodyVoicePitch || 1.04)));
  utterance.volume = Math.max(0, Math.min(1, Number(settings.bodyVoiceVolume ?? 1)));
  const voice = pickSpeechVoice(settings);
  if (voice) utterance.voice = voice;
  utterance.onstart = () => dispatchSpeechPhase("start"); // P6a §17：播放开始补事件（不改播放逻辑）
  utterance.onboundary = () => dispatchSpeechPhase("energy"); // P6a §17：边界事件即能量节拍
  utterance.onend = utterance.onerror = () => dispatchSpeechPhase("stop"); // P6a §17：播放停止补事件
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  return true;
}

async function playGeneratedVoice(result, settings = {}) {
  const encoded = String(result?.audio_base64 || "");
  if (!encoded || typeof Audio === "undefined") return false;
  try {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    if (generatedVoiceAudio) {
      generatedVoiceAudio.pause();
      URL.revokeObjectURL(generatedVoiceAudio.src);
    }
    const url = URL.createObjectURL(new Blob([bytes], { type: String(result.mime || "audio/mpeg") }));
    const audio = new Audio(url);
    audio.volume = Math.max(0, Math.min(1, Number(settings.bodyVoiceVolume ?? 1)));
    generatedVoiceAudio = audio;
    const release = () => {
      dispatchSpeechPhase("stop"); // P6a §17：ended/error 终态补 stop 事件
      URL.revokeObjectURL(url);
      if (generatedVoiceAudio === audio) generatedVoiceAudio = null;
    };
    audio.addEventListener("ended", release, { once: true });
    audio.addEventListener("error", release, { once: true });
    audio.addEventListener("timeupdate", () => dispatchSpeechPhase("energy")); // P6a §17：播放节拍补 energy（无 analyser，不携带能量值）
    await audio.play();
    dispatchSpeechPhase("start"); // P6a §17：播放开始补事件（不改播放逻辑）
    return true;
  } catch {
    return false;
  }
}

async function speakAssistantReply(text, settings = {}) {
  if (!settings.bodyVoiceReplyEnabled) return false;
  const clean = spokenBackendText(text).replace(/\s+/g, " ").trim();
  if (!clean) return false;
  const mode = String(settings.bodyVoiceOutputMode || "auto");
  if (mode !== "browser_tts") {
    try {
      const result = await requestVoiceOutput({
        text: clean.slice(0, 1200),
        mode,
        voice_id: providerVoiceId(settings),
      });
      if (result?.ok && await playGeneratedVoice(result, settings)) return true;
    } catch {
      // The configured service is unavailable.  Browser TTS is the declared local fallback.
    }
  }
  return speakWithBrowser(clean, settings);
}

function appendMetaLine(container, label, value) {
  const text = String(value || "").trim();
  if (!text) return;
  const row = document.createElement("div");
  row.className = "code-workflow-line";
  const key = document.createElement("span");
  key.className = "code-workflow-key";
  key.textContent = label;
  const val = document.createElement("span");
  val.className = "code-workflow-value";
  val.textContent = text;
  row.appendChild(key);
  row.appendChild(val);
  container.appendChild(row);
}

function appendToolChips(container, label, tools) {
  const items = Array.isArray(tools) ? tools.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 10) : [];
  if (!items.length) return;
  const wrap = document.createElement("div");
  wrap.className = "code-workflow-tools";
  const title = document.createElement("span");
  title.className = "code-workflow-key";
  title.textContent = label;
  wrap.appendChild(title);
  const chips = document.createElement("div");
  chips.className = "code-workflow-chip-list";
  for (const item of items) {
    const chip = document.createElement("span");
    chip.className = "code-workflow-chip";
    chip.textContent = item;
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);
  container.appendChild(wrap);
}

function formatContractItems(items) {
  if (!Array.isArray(items)) return "";
  return items.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 3).join(" | ");
}

function appendCodeWorkflowCard(container, meta) {
  if (!meta || meta.schema !== "tiangong.v3.code_workflow_recommendation.v1") return false;
  const card = document.createElement("div");
  card.className = "code-workflow-card";
  const title = document.createElement("div");
  title.className = "code-workflow-card-title";
  title.textContent = meta.dispatcherSkillLabel || "代码工程调度";
  card.appendChild(title);
  appendMetaLine(card, "当前", meta.currentSkillLabel || meta.currentSkillId);
  appendMetaLine(card, "目标", meta.currentFocus);
  appendMetaLine(card, "下一步", meta.nextSkillLabel || meta.nextSkillId);
  appendToolChips(card, "下一工具组", meta.nextToolGroup);
  container.appendChild(card);
  return true;
}

function toolDisplayReply(meta, step) {
  const contract = meta?.resultContract && typeof meta.resultContract === "object" ? meta.resultContract : null;
  return String(
    contract?.error
    || meta?.resultSummary
    || contract?.summary
    || contract?.status
    || meta?.resultStatus
    || step?.summary
    || meta?.userFacingText
    || ""
  ).trim();
}

function appendToolDispatchCard(container, meta, step) {
  if (!meta || meta.schema !== "tiangong.v3.tool_dispatch.v1") return false;
  const reply = toolDisplayReply(meta, step);
  const card = document.createElement("div");
  card.className = "code-workflow-card tool-dispatch-card";
  const title = document.createElement("div");
  title.className = "code-workflow-card-title";
  title.textContent = meta.toolLabel || meta.toolName || step?.title || "工具调用";
  card.appendChild(title);
  appendMetaLine(card, "回复", reply);
  container.appendChild(card);
  return true;
}

function appendTaskGraphCard(container, meta) {
  if (!meta || meta.schema !== "tiangong.v3.task_graph_event.v1") return false;
  const nodeEvents = new Set(["node_started", "node_completed", "node_retry", "node_blocked"]);
  if (nodeEvents.has(meta.event)) {
    const node = meta.node || {};
    const card = document.createElement("div");
    card.className = "code-workflow-card task-graph-card task-graph-event-card";
    const title = document.createElement("div");
    title.className = "code-workflow-card-title";
    const titles = {
      node_started: "TaskGraph / Current node",
      node_completed: "TaskGraph / Node completed",
      node_retry: "TaskGraph / Node retry",
      node_blocked: "TaskGraph / Node blocked",
    };
    title.textContent = titles[meta.event] || "TaskGraph / Node";
    card.appendChild(title);
    appendMetaLine(card, "Node", node.title || node.id || meta.node_id);
    appendMetaLine(card, "Skill", node.skill_id || "");
    appendMetaLine(card, "Status", node.status || meta.event);
    if (meta.failure?.failure_kind) appendMetaLine(card, "Failure", meta.failure.failure_kind);
    if (meta.failure?.attempt || meta.failure?.max_attempts) appendMetaLine(card, "Attempt", `${meta.failure?.attempt || 0}/${meta.failure?.max_attempts || "?"}`);
    if (meta.failure?.decision) appendMetaLine(card, "Decision", meta.failure.decision);
    if (meta.evidence?.reason) appendMetaLine(card, "Reason", meta.evidence.reason);
    if (meta.evidence?.tool_name) appendMetaLine(card, "Evidence", meta.evidence.tool_name);
    if (meta.evidence?.tool_result_contract?.status) appendMetaLine(card, "Result", meta.evidence.tool_result_contract.status);
    if (meta.evidence?.tool_result_contract?.write_effect) appendMetaLine(card, "Effect", "写入");
    if (meta.evidence?.error) appendMetaLine(card, "Error", meta.evidence.error);
    appendToolChips(card, "Tools", node.tool_policy || []);
    container.appendChild(card);
    return true;
  }
  if (meta.event === "graph_completed") {
    const card = document.createElement("div");
    card.className = "code-workflow-card task-graph-card task-graph-event-card";
    const title = document.createElement("div");
    title.className = "code-workflow-card-title";
    title.textContent = "TaskGraph / Completed";
    card.appendChild(title);
    appendMetaLine(card, "Task", meta.task_id || "");
    appendMetaLine(card, "Status", meta.graph_status || meta.event);
    container.appendChild(card);
    return true;
  }
  if (meta.event !== "graph_created") return false;
  const graph = meta.taskGraph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const card = document.createElement("div");
  card.className = "code-workflow-card task-graph-card";
  const title = document.createElement("div");
  title.className = "code-workflow-card-title";
  title.textContent = graph.mode === "composite" ? "TaskGraph · 复合任务" : "TaskGraph · 单阶段任务";
  card.appendChild(title);
  appendMetaLine(card, "目标", graph.goal);
  appendMetaLine(card, "节点", `${nodes.length} 项`);
  const list = document.createElement("div");
  list.className = "task-graph-node-list";
  for (const node of nodes.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = `task-graph-node ${String(node.id || "") === String(graph.current_node_id || "") ? "active" : ""}`;
    const name = document.createElement("span");
    name.className = "task-graph-node-name";
    name.textContent = node.title || node.id || "任务节点";
    const skill = document.createElement("span");
    skill.className = "task-graph-node-skill";
    const tools = Array.isArray(node.tool_policy) && node.tool_policy.length ? node.tool_policy.join(", ") : "none";
    skill.textContent = `${node.skill_id || "skill"} · ${tools}`;
    row.appendChild(name);
    row.appendChild(skill);
    list.appendChild(row);
  }
  card.appendChild(list);
  container.appendChild(card);
  return true;
}

function appendTaskGraphPatchCard(container, meta) {
  if (!meta || meta.schema !== "tiangong.v3.task_graph_patch.v1") return false;
  const patch = meta.patch || {};
  const card = document.createElement("div");
  card.className = "code-workflow-card task-graph-card";
  const title = document.createElement("div");
  title.className = "code-workflow-card-title";
  title.textContent = "TaskGraph · 运行中重规划";
  card.appendChild(title);
  appendMetaLine(card, "动作", patch.op);
  appendMetaLine(card, "策略", patch.decision);
  appendMetaLine(card, "失败", patch.failure_kind);
  appendMetaLine(card, "原因", patch.reason);
  appendMetaLine(card, "目标节点", patch.target_node_id);
  appendMetaLine(card, "恢复节点", patch.replacement_node_id);
  if (patch.node) appendMetaLine(card, "新增节点", patch.node.title || patch.node.id);
  container.appendChild(card);
  return true;
}

function appendGatewayStateCard(container, steps) {
  const laneSteps = new Map();
  for (const step of Array.isArray(steps) ? steps : []) {
    const meta = step?.meta && typeof step.meta === "object" ? step.meta : null;
    if (String(meta?.type || "").toUpperCase() !== "GATEWAY_STATE_PROJECTION") continue;
    const machine = String(meta?.machine || "");
    if (["execution", "artifact", "delivery"].includes(machine)) laneSteps.set(machine, step);
  }
  if (!laneSteps.size) return false;
  const card = document.createElement("div");
  card.className = "code-workflow-card gateway-state-card";
  const title = document.createElement("div");
  title.className = "code-workflow-card-title";
  title.textContent = "网关事实状态";
  card.appendChild(title);
  const laneLabels = { execution: "执行", artifact: "产物 QC", delivery: "投递" };
  for (const machine of ["execution", "artifact", "delivery"]) {
    const step = laneSteps.get(machine);
    if (!step) continue;
    const row = document.createElement("div");
    row.className = `gateway-state-row gateway-state-${progressClass(step.status)}`;
    row.dataset.gatewayStateMachine = machine;
    const key = document.createElement("span");
    key.className = "gateway-state-key";
    key.textContent = laneLabels[machine];
    const value = document.createElement("span");
    value.className = "gateway-state-value";
    const state = document.createElement("strong");
    state.textContent = `${progressStatusText(step.status)} · ${step.title || laneLabels[machine]}`;
    const summary = document.createElement("small");
    summary.textContent = String(step.summary || "");
    value.appendChild(state);
    value.appendChild(summary);
    row.appendChild(key);
    row.appendChild(value);
    card.appendChild(row);
  }
  container.appendChild(card);
  return true;
}

function appendProgressMetaCard(container, step) {
  const meta = step?.meta && typeof step.meta === "object" ? step.meta : null;
  if (!meta) return;
  appendToolDispatchCard(container, meta, step);
}

function progressSignature(progress) {
  const steps = visibleProgressSteps(progress);
  const last = steps[steps.length - 1];
  const stepText = last?.summary || last?.title || '';
  return String(progress?.phase || "") + "|" + String(progress?.ok ?? "") + "|" + String(progress?.requestId || "") + "|" + stepText;
}

export function progressDisplayText(progress = {}) {
  if (progress?.phase === "finished") return "完成";
  // GF 门（草案 §8）：非成功相位的呼吸灯文案，明确不给"仍在跑"的假安心
  if (progress?.phase === "reconcile_required") return "结果待对账，禁止重试";
  if (progress?.phase === "partial") return "部分完成";
  if (progress?.phase === "incident") return "结果矛盾，按非成功处理";
  if (progress?.phase === "unknown") return "状态未知，按未成功处理";
  if (progress?.ok === false) return "中断";
  const steps = visibleProgressSteps(progress);
  const currentStep = steps[steps.length - 1] || null;
  const currentType = String(currentStep?.meta?.type || "").toUpperCase();
  const currentId = String(currentStep?.id || "").toLowerCase();
  const isToolStep = currentType === "TOOL_EFFECT_PREPARED"
    || currentType === "TOOL_FINISHED"
    || currentId.startsWith("tool_")
    || Boolean(currentStep?.toolName);
  if (currentType === "GATEWAY_STATE_PROJECTION") {
    return String(currentStep?.title || "网关状态").trim() || "网关状态";
  }
  // HOTFIX-20260728: 呼吸灯只做状态指示，绝不显示模型 interim 回复正文
  // （正文由流式气泡阶段式呈现），避免呼吸灯与流式输出显示同样内容。
  if (isToolStep) {
    const toolName = String(currentStep?.toolName || "").trim();
    if (toolName) return `调用工具：${toolName}`;
    const title = String(currentStep?.title || "").trim();
    return title ? `调用工具：${title}` : "调用工具";
  }
  return "思考中";
}

// GF 门（草案 §8）：非成功相位卡片的展示模型（纯函数，便于 node 测试）。
// 这些卡片是系统裁决提示，不是模型发言；reconcile_required 不提供任何
// 重试/重发入口（不产出按钮，只有说明文案）。
export function gatewayPhaseCardModel(phase) {
  const value = String(phase || "").trim();
  if (value === "reconcile_required") {
    return {
      phase: value,
      tone: "blocked",
      title: "结果待对账",
      lines: [
        "网关正在核对本轮的执行与投递事实，结果尚未确认。",
        "禁止重试或重发，请等待网关对账完成。",
      ],
    };
  }
  if (value === "partial") {
    return {
      phase: value,
      tone: "partial",
      title: "部分完成",
      lines: [
        "网关裁决本次任务只达成了一部分。",
        "未达成的部分不得当作成功。",
      ],
    };
  }
  if (value === "incident") {
    return {
      phase: value,
      tone: "incident",
      title: "结果矛盾",
      lines: [
        "检测到相互矛盾的结果，本轮已按非成功事件处理。",
        "请不要据此前结果继续。",
      ],
    };
  }
  if (value === "unknown") {
    return {
      phase: value,
      tone: "unknown",
      title: "状态未知",
      lines: ["网关未给出可核验的终态，本轮按未成功处理。"],
    };
  }
  return null;
}

// GF 门：非成功相位只作为折叠的技术详情。助手的自然总结始终是
// 主回复；网关裁决不得再以一张“系统卡”冒充或覆盖助手发言。
function appendGatewayPhaseCard(container, phase) {
  const model = gatewayPhaseCardModel(phase);
  if (!model || !container) return false;
  const card = document.createElement("details");
  card.className = `code-workflow-card gateway-phase-card gateway-phase-${model.tone}`;
  card.dataset.gatewayPhase = model.phase;
  const title = document.createElement("summary");
  title.className = "code-workflow-card-title";
  title.textContent = `技术详情 · ${model.title}`;
  card.appendChild(title);
  for (const line of model.lines) {
    const row = document.createElement("div");
    row.className = "gateway-phase-line";
    row.textContent = line;
    card.appendChild(row);
  }
  container.appendChild(card);
  return true;
}

function renderProgressContent(container, progress, animate = true) {
  const sig = progressSignature(progress);
  if (container.dataset.progressSignature === sig) return;
  container.dataset.progressSignature = sig;
  container.innerHTML = "";

  // Breathing light
  const w = document.createElement("div");
  w.className = "breathing-light-wrap";
  const dot = document.createElement("span");
  dot.className = "breathing-dot";
  const lbl = document.createElement("span");
  lbl.className = "breathing-label";
  const done = progress?.phase === "finished";
  const fail = progress?.ok === false;
  // GF 门：待对账/部分完成/矛盾/未知 使用独立相位样式（慢呼吸警示），
  // 与普通运行中/失败在视觉上可区分。
  const reconcilePhase = ["reconcile_required", "partial", "incident", "unknown"]
    .includes(String(progress?.phase || ""));
  const steps = visibleProgressSteps(progress);
  lbl.textContent = progressDisplayText(progress);
  if (done) dot.classList.add("breathing-done");
  if (fail) dot.classList.add("breathing-failed");
  if (reconcilePhase) dot.classList.add("breathing-reconcile");
  w.appendChild(dot);
  w.appendChild(lbl);
  container.appendChild(w);
  if (!done && !fail) w.classList.add("breathing-active");
  requestAnimationFrame(() => w.classList.add("breathing-visible"));

  // Tool cards for the last step with dispatch meta
  const lastStep = steps[steps.length - 1];
  appendGatewayStateCard(container, steps);
  if (lastStep) {
    appendProgressMetaCard(container, lastStep);
  }
}

export const conversationPanelPlugin = {
  id: "conversation-panel",
  slot: "conversation",
  order: 200,
  mount({ slot, state, actions, bus }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <style>
          .confirm-card { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border: 1px solid rgba(217, 164, 65, 0.45); border-radius: 10px; background: rgba(217, 164, 65, 0.08); }
          .confirm-card-title { font-weight: 600; font-size: 14px; }
          .confirm-card-rows { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
          .confirm-card-row { display: flex; gap: 8px; align-items: baseline; }
          .confirm-card-key { flex: none; opacity: 0.65; min-width: 60px; }
          .confirm-card-value { word-break: break-all; }
          .confirm-card-hint { font-size: 13px; opacity: 0.85; }
          .confirm-card-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
          .confirm-card-button { padding: 6px 12px; border-radius: 8px; border: 1px solid currentColor; background: transparent; cursor: pointer; font-size: 13px; }
          .confirm-card-button:hover:not(:disabled) { filter: brightness(1.15); }
          .confirm-card-button:disabled { opacity: 0.5; cursor: default; }
          .confirm-card-button-deny { border-color: rgba(200, 80, 80, 0.8); color: rgba(220, 110, 110, 1); }
          .confirm-card-result { font-size: 13px; }
          .confirm-card-result-granted { color: #5aa46a; }
          .confirm-card-result-denied { color: rgba(220, 110, 110, 1); }
        </style>
        <section class="page-panel chat-page active" data-page-panel="chat">
          <header class="commandbar page-header">
            <div class="title-group">
              <h2>起源</h2>
              <span id="modeLabel" class="meta-chip">对话</span>
            </div>
            <div class="commandbar-meta">
              <button id="newChat" class="icon-button" type="button" title="新对话" aria-label="新对话">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h7"/><line x1="19" y1="3" x2="19" y2="11"/><line x1="15" y1="7" x2="23" y2="7"/></svg>
              </button>
              <button id="clearChat" class="icon-button" type="button" title="清空对话" aria-label="清空对话">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </header>

          <section id="messages" class="messages" aria-live="polite"></section>

          <form id="composer" class="composer">
            <div id="attachmentTray" class="attachment-tray" hidden></div>
            <div class="composer-extras">
              <button id="attachFile" class="icon-button attach-file-button" type="button" title="上传文件/图片/压缩包/代码" aria-label="上传文件">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 1 1-2.83-2.83l8.49-8.48"/></svg>
              </button>
            </div>
            <textarea id="messageInput" rows="1" placeholder="输入任务或对话..." aria-label="消息输入框"></textarea>
            <div class="composer-run-controls">
              <button id="interruptRun" class="interrupt-button" type="button" title="中断当前执行" aria-label="中断当前执行" hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>
              </button>
              <button id="sendButton" class="send-button" type="submit" title="回车发送，Shift+回车换行" aria-label="发送">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </button>
            </div>
          </form>

          <div class="status-row">
            <span id="chatStatus">就绪</span>
            <span>后端入口：单轮任务执行</span>
          </div>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="chat"]');
    const modeLabel = panel.querySelector("#modeLabel");
    const messagesEl = panel.querySelector("#messages");
    const form = panel.querySelector("#composer");
    const input = panel.querySelector("#messageInput");
    const attachButton = panel.querySelector("#attachFile");
    const sendButton = panel.querySelector("#sendButton");
    const interruptButton = panel.querySelector("#interruptRun");
    const newChatButton = panel.querySelector("#newChat");
    const clearButton = panel.querySelector("#clearChat");
    const attachmentTray = panel.querySelector("#attachmentTray");
    const chatStatus = panel.querySelector("#chatStatus");
    const title = panel.querySelector(".title-group h2");
    let currentSettings = state.snapshot().settings;
    let speechReady = false;
    let lastSpokenAssistantKey = "";
    let pendingAttachments = [];
    const supportsAttachments = true;

    function resizeInput() {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "chat");
    }

    function shouldStickToBottom() {
      return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 96;
    }

    function scrollMessagesToBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    let bottomScrollFrame = null;
    let bottomScrollTimer = null;
    function scrollMessagesToBottomAfterLayout() {
      scrollMessagesToBottom();
      if (bottomScrollFrame !== null) cancelAnimationFrame(bottomScrollFrame);
      if (bottomScrollTimer !== null) clearTimeout(bottomScrollTimer);
      bottomScrollFrame = requestAnimationFrame(() => {
        scrollMessagesToBottom();
        bottomScrollFrame = requestAnimationFrame(() => {
          scrollMessagesToBottom();
          bottomScrollFrame = null;
        });
      });
      bottomScrollTimer = setTimeout(() => {
        scrollMessagesToBottom();
        bottomScrollTimer = null;
      }, 120);
    }

    function renderAttachmentChip(item, options = {}) {
      const chip = document.createElement("span");
      chip.className = `attachment-chip ${String(item?.status || "")}`;
      chip.title = [item?.path, item?.documentId].filter(Boolean).join("\n");
      const label = document.createElement(item?.path ? "button" : "span");
      label.className = "attachment-label";
      if (item?.path) {
        label.type = "button";
        label.dataset.openPath = item.path;
      }
      const name = document.createElement("span");
      name.className = "attachment-name";
      name.textContent = item?.name || item?.path || "file";
      const meta = document.createElement("span");
      meta.className = "attachment-meta";
      meta.textContent = [attachmentStatusText(item), formatBytes(item?.size)].filter(Boolean).join(" · ");
      label.appendChild(name);
      label.appendChild(meta);
      chip.appendChild(label);
      if (item?.path) {
        const save = document.createElement("button");
        save.type = "button";
        save.className = "attachment-save";
        save.dataset.saveAttachment = item.path;
        save.dataset.saveName = item?.name || "";
        save.title = "另存为";
        save.setAttribute("aria-label", "另存附件");
        save.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>`;
        chip.appendChild(save);
      }
      if (options.removable) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "attachment-remove";
        remove.dataset.removeAttachment = attachmentKey(item);
        remove.title = "移除";
        remove.setAttribute("aria-label", "移除文件");
        remove.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`;
        chip.appendChild(remove);
      }
      return chip;
    }

    function renderAttachmentTray() {
      attachmentTray.innerHTML = "";
      attachmentTray.hidden = pendingAttachments.length === 0;
      form.classList.toggle("has-attachments", pendingAttachments.length > 0);
      form.dataset.attachmentCount = String(pendingAttachments.length);
      attachButton.dataset.count = String(pendingAttachments.length);
      for (const item of pendingAttachments) {
        attachmentTray.appendChild(renderAttachmentChip(item, { removable: true }));
      }
    }

    function renderReferenceBar() {
    }

    function renderMessageAttachments(container, attachments) {
      const items = Array.isArray(attachments) ? attachments.filter((item) => isGatewayArtifactCard(item) || item?.name || item?.path || item?.url || item?.dataUrl) : [];
      if (!items.length) return;
      const artifactItems = items.filter(isGatewayArtifactCard);
      if (artifactItems.length) {
        const cards = document.createElement("div");
        cards.className = "artifact-card-list";
        for (const item of artifactItems) {
          const card = document.createElement("article");
          card.className = "artifact-card";
          const main = document.createElement("div");
          main.className = "artifact-card-main";
          const title = document.createElement("strong");
          title.className = "artifact-card-title";
          title.textContent = item.filename || item.name || "已验收产物";
          const facts = document.createElement("span");
          facts.className = "artifact-card-facts";
          facts.textContent = `QC PASSED · ${formatBytes(item.size_bytes)} · revision ${item.revision}`;
          const digest = document.createElement("span");
          digest.className = "artifact-card-digest";
          digest.textContent = `内容 ${shortDigest(item.content_sha256)} · 清单 ${shortDigest(item.manifest_sha256)}`;
          main.append(title, facts, digest);
          const action = document.createElement("div");
          action.className = "artifact-card-action";
          const button = document.createElement("button");
          button.type = "button";
          button.className = "artifact-card-open";
          button.dataset.openArtifact = item.artifact_revision_id;
          button._tiangongArtifactCard = item;
          button.textContent = "定位文件";
          const status = document.createElement("span");
          status.className = "artifact-card-open-status";
          status.setAttribute("aria-live", "polite");
          action.append(button, status);
          card.append(main, action);
          cards.appendChild(card);
        }
        container.appendChild(cards);
      }
      const legacyItems = items.filter((item) => !isGatewayArtifactCard(item));
      const mediaItems = legacyItems.filter((item) => (item?.path || item?.url || item?.dataUrl) && attachmentLooksLikeMedia(item));
      if (mediaItems.length) {
        const mediaWrap = document.createElement("div");
        mediaWrap.className = "message-attachment-media";
        for (const item of mediaItems) {
          const source = item.path || item.url || item.dataUrl;
          renderMediaAttachment(mediaWrap, source, { caption: item.name || source });
        }
        container.appendChild(mediaWrap);
      }
      if (legacyItems.length) {
        const wrap = document.createElement("div");
        wrap.className = "message-attachments";
        for (const item of legacyItems) {
          wrap.appendChild(renderAttachmentChip(item));
        }
        container.appendChild(wrap);
      }
    }

    function applyAttachmentResult(result, actionLabel = "文件") {
      // P2-05: a cancelled file picker must close the selection state and must
      // never merge a cancelled envelope into the pending attachment list.
      if (result?.canceled) {
        chatStatus.textContent = "已取消选择";
        return;
      }
      const items = Array.isArray(result?.attachments) ? result.attachments : [];
      const accepted = items.filter((item) => ["imported", "uploaded", "attached", "selected"].includes(String(item?.status || "")));
      const failedCount = items.filter((item) => String(item?.status || "") === "failed").length;
      const byKey = new Map(pendingAttachments.map((item) => [attachmentKey(item), item]));
      for (const item of accepted) {
        const key = attachmentKey(item);
        if (!key) continue;
        if (byKey.size >= CHAT_ATTACHMENT_LIMIT && !byKey.has(key)) break;
        byKey.set(key, item);
      }
      pendingAttachments = [...byKey.values()].slice(0, CHAT_ATTACHMENT_LIMIT);
      renderAttachmentTray();

      const importedCount = accepted.filter((item) => String(item?.status || "") === "imported").length;
      const uploadedCount = accepted.filter((item) => String(item?.status || "") === "uploaded").length;
      const attachedCount = accepted.filter((item) => String(item?.status || "") === "attached").length;
      const selectedCount = accepted.filter((item) => String(item?.status || "") === "selected").length;
      if (accepted.length) {
        const parts = [];
        if (importedCount) parts.push(`入库 ${importedCount} 个`);
        if (uploadedCount) parts.push(`上传 ${uploadedCount} 个`);
        if (attachedCount) parts.push(`附加 ${attachedCount} 个`);
        if (selectedCount) parts.push(`选择 ${selectedCount} 个`);
        const fallbackNote = result?.partial || result?.warning ? "，已保留本轮附件" : "";
        chatStatus.textContent = `${actionLabel}${parts.join("，")}${failedCount ? `，${failedCount} 个失败` : ""}${fallbackNote}`;
      } else if (result?.error) {
        chatStatus.textContent = result.error;
      } else if (failedCount) {
        chatStatus.textContent = `${failedCount} 个文件导入失败`;
      }
    }

    async function copyTextToClipboard(text, button) {
      const value = String(text || "");
      if (!value.trim()) return;
      const original = button?.dataset.defaultText || button?.textContent || "复制";
      try {
        if (window.tiangongDesktop?.writeClipboardText) {
          const result = await window.tiangongDesktop.writeClipboardText(value);
          if (result?.ok === false) throw new Error(result.error || "copy failed");
        } else if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
        } else {
          throw new Error("clipboard unavailable");
        }
        if (button) {
          button.textContent = "已复制";
          window.setTimeout(() => {
            button.textContent = original;
          }, 1200);
        }
      } catch {
        if (button) {
          button.textContent = "复制失败";
          window.setTimeout(() => {
            button.textContent = original;
          }, 1200);
        }
      }
    }

    function messageCopyText(item) {
      if (!item || item.progress) return "";
      if (item.kind === "confirm") {
        const payload = decodeConfirmCardContent(item.content);
        return payload ? `操作确认：${payload.summary || payload.action || ""}` : "";
      }
      return String(item.role === "assistant"
        ? spokenBackendText(item.content) || item.content || ""
        : item.content || "");
    }

    // ── 确认卡片（视觉对齐学习卡：标题 + 摘要行 + 操作按钮行）────────────
    function confirmRiskText(risk) {
      const key = String(risk || "").trim().toUpperCase();
      return {
        A0: "A0（只读整理）",
        A1: "A1（低风险）",
        A2: "A2（常规可逆）",
        A3: "A3（较高影响）",
        A4: "A4（高影响）",
        A5: "A5（最高风险）"
      }[key] || key || "未知";
    }

    function renderConfirmCard(container, payload) {
      container.classList.add("confirm-card");
      container.innerHTML = "";
      if (!payload || !payload.confirm_id) {
        container.textContent = "这条确认信息已损坏，请重新发送刚才的指令。";
        return;
      }
      const title = document.createElement("div");
      title.className = "confirm-card-title";
      title.textContent = "操作确认";
      container.appendChild(title);

      const rows = document.createElement("div");
      rows.className = "confirm-card-rows";
      const addRow = (label, value) => {
        const text = String(value || "").trim();
        if (!text) return;
        const row = document.createElement("div");
        row.className = "confirm-card-row";
        const key = document.createElement("span");
        key.className = "confirm-card-key";
        key.textContent = label;
        const val = document.createElement("span");
        val.className = "confirm-card-value";
        val.textContent = text;
        row.append(key, val);
        rows.appendChild(row);
      };
      addRow("操作", payload.summary || payload.action || "需要授权的操作");
      addRow("位置", payload.target);
      addRow("风险等级", confirmRiskText(payload.risk));
      container.appendChild(rows);

      const status = String(payload.status || "pending");
      const decisionLabel = CONFIRM_DECISION_LABELS[payload.decision] || "";
      // HOTFIX-20260728: 待决卡按 expires_at_ms 预判过期，不让用户点一个后端已拒的卡
      const expiredByClock = status === "pending"
        && Number(payload.expires_at_ms || 0) > 0
        && Date.now() > Number(payload.expires_at_ms);
      const effectiveStatus = expiredByClock ? "expired" : status;
      // G3 确认退役（草案 §4.2 第 6 步）：确认卡一律只读——按钮全部禁用并标注
      // “已退役，仅供查阅”；退役提示用弱样式（灰底小字），不再提供任何新确认入口。
      const retiredNote = (text) => {
        const note2 = document.createElement("div");
        note2.className = "confirm-card-result confirm-card-result-retired";
        note2.style.cssText = "color:#888;background:#f5f5f5;font-size:12px;padding:4px 8px;border-radius:4px;margin-top:6px;";
        note2.textContent = text;
        return note2;
      };
      const footer = document.createElement("div");
      footer.className = "confirm-card-footer";
      if (effectiveStatus === "pending" || effectiveStatus === "resolving") {
        const hint = document.createElement("div");
        hint.className = "confirm-card-hint";
        hint.textContent = "确认通道已退役，以下选择已不可用：";
        footer.appendChild(hint);
        const buttonRow = document.createElement("div");
        buttonRow.className = "confirm-card-actions";
        for (const decision of ["once", "session", "always", "deny"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `confirm-card-button${decision === "deny" ? " confirm-card-button-deny" : ""}`;
          button.dataset.confirmDecision = decision;
          button.dataset.confirmId = String(payload.confirm_id);
          button.disabled = true; // 已退役：一律禁用，仅保留历史渲染
          button.title = "已退役，仅供查阅";
          button.textContent = CONFIRM_DECISION_LABELS[decision];
          buttonRow.appendChild(button);
        }
        footer.appendChild(buttonRow);
        footer.appendChild(retiredNote("已退役，仅供查阅。"));
      } else {
        const result = document.createElement("div");
        result.className = `confirm-card-result confirm-card-result-${effectiveStatus}`;
        const note = String(payload.note || "").trim();
        if (effectiveStatus === "granted") {
          result.textContent = `已选择「${decisionLabel}」${note ? `：${note}` : "。"}`;
        } else if (effectiveStatus === "denied") {
          result.textContent = `已选择「${decisionLabel || "拒绝"}」${note ? `：${note}` : "，这次操作没有执行。"}`;
        } else if (effectiveStatus === "expired") {
          result.textContent = note || "确认已过期，后端不再接受这次决策。";
        } else if (effectiveStatus === "retired") {
          // 后端 410 / POLICY_CONFIRMATION_RETIRED：显示退役而非"接口不可用"
          result.textContent = note || "确认通道已退役，仅供查阅。";
        } else {
          result.textContent = `确认未完成${note ? `：${note}` : ""}。`;
        }
        footer.appendChild(result);
        if (effectiveStatus !== "retired") {
          footer.appendChild(retiredNote("已退役，仅供查阅。"));
        }
      }
      container.appendChild(footer);
    }

    function isLiveWorkMessage(item) {
      if (item?.kind !== "work" || item?.role !== "assistant") return false;
      const progress = state.snapshot().runProgress || {};
      return String(progress.phase || "") === "running"
        && String(progress.anchorMessageId || "") === String(item.id || "")
        && String(progress.sessionId || "") === String(item.sessionId || "");
    }

    function createMessageNode(item) {
      const node = document.createElement("article");
      node.className = `message ${item.role}${item.error ? " error" : ""}${item.progress ? " progress" : ""}`;
      node.dataset.messageRole = item.role || "";
      node.dataset.messageAt = String(item.at || "");
      node.dataset.messageId = String(item.id || "");
      if (item.requestId) node.dataset.requestId = String(item.requestId);
      if (item.progress) node.dataset.progressBubble = "1";

      const avatar = document.createElement("div");
      avatar.className = "message-avatar";
      if (item.role === "user") {
        renderUserAvatar(avatar, currentSettings);
      } else {
        renderPersonaAvatar(avatar, currentSettings);
      }

      const body = document.createElement("div");
      body.className = "message-body";

      const meta = document.createElement("div");
      meta.className = "message-meta";
      const name = document.createElement("span");
      name.className = "message-name";
      name.textContent = item.role === "user" ? userDisplayName(currentSettings) : personaName(currentSettings);
      const time = document.createElement("span");
      time.className = "message-time";
      time.textContent = formatTime(item.at);
      meta.appendChild(name);
      meta.appendChild(time);
      // FE-02: template-origin replies (platform fallback/incomplete text)
      // must be visibly distinguishable from model-generated assistant text.
      if (item.role === "assistant" && item.meta?.origin === "template") {
        const originTag = document.createElement("span");
        originTag.className = "message-origin-tag";
        originTag.textContent = "系统模板";
        originTag.title = "该文本由平台模板生成，并非大模型原创回复";
        meta.appendChild(originTag);
      }
      const copyText = messageCopyText(item);
      if (copyText.trim()) {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "message-copy";
        copy.dataset.defaultText = "复制";
        copy.textContent = "复制";
        copy.title = "复制这条消息";
        copy.setAttribute("aria-label", "复制这条消息");
        copy.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          copyTextToClipboard(copyText, copy);
        });
        meta.appendChild(copy);
      }

      const content = document.createElement("div");
      content.className = "message-content";
      if (item.progress) {
        renderProgressContent(content, item.progressData, false);
      } else if (isLiveWorkMessage(item)) {
        // 工作卡结构：进度区 + 回复文本区，不再创建独立进度气泡
        content.classList.add("work-card");
        const progressWrap = document.createElement("div");
        progressWrap.className = "work-progress-wrap";
        content.appendChild(progressWrap);
        const replyText = document.createElement("div");
        replyText.className = "work-reply-text";
        replyText.textContent = item.content || "";
        content.appendChild(replyText);
      } else if (item.kind === "confirm") {
        renderConfirmCard(content, decodeConfirmCardContent(item.content));
      } else {
        renderMessageContent(content, item.role === "assistant"
          ? spokenBackendText(item.content) || item.content
          : item.content);
        renderMessageAttachments(content, item.attachments);
      }

      // GF 门（草案 §8）：该消息对应轮次被网关判为非成功相位时，
      // 追加"系统裁决"卡片（非模型发言；待对账不提供重试/重发入口）。
      if (item.role === "assistant" && item.requestId) {
        const lastRun = state.snapshot().lastRun || {};
        if (String(lastRun.requestId || "") === String(item.requestId)) {
          appendGatewayPhaseCard(content, lastRun.phase);
        }
      }

      body.appendChild(meta);
      body.appendChild(content);
      node.appendChild(avatar);
      node.appendChild(body);
      return node;
    }

    function progressAnchor(progress) {
      const anchorAt = Number(progress?.anchorAt || progress?.startedAt || 0);
      const users = [...messagesEl.querySelectorAll('.message.user[data-message-at]')];
      let anchor = null;
      for (const node of users) {
        if (Number(node.dataset.messageAt || 0) <= anchorAt + 1000) anchor = node;
      }
      return anchor;
    }

    function placeProgressNode(node, progress) {
      const anchor = progressAnchor(progress);
      if (anchor?.nextSibling !== node) {
        anchor?.after(node) || messagesEl.appendChild(node);
      }
    }

    function finalizeWorkCardMessage(anchorNode) {
      const msgContent = anchorNode?.querySelector(".message-content");
      if (!msgContent) return;
      const progressWrap = msgContent.querySelector(".work-progress-wrap");
      if (progressWrap) progressWrap.remove();
      const replyText = msgContent.querySelector(".work-reply-text");
      if (!replyText) {
        if (!msgContent.querySelector(".work-progress-wrap")) msgContent.classList.remove("work-card");
        return;
      }
      const msgId = String(anchorNode.dataset.messageId || "");
      const msgData = state.snapshot().messages.find((item) => String(item.id || "") === msgId);
      const finalText = String(msgData?.content || replyText.textContent || "");
      replyText.innerHTML = "";
      renderMessageContent(replyText, spokenBackendText(finalText) || finalText);
      const fragment = document.createDocumentFragment();
      while (replyText.firstChild) fragment.appendChild(replyText.firstChild);
      msgContent.innerHTML = "";
      msgContent.classList.remove("work-card");
      msgContent.classList.add("rich-text");
      msgContent.appendChild(fragment);
    }

    function clearEmbeddedProgress() {
      const cards = [...messagesEl.querySelectorAll(".message.assistant .message-content.work-card")];
      for (const card of cards) {
        finalizeWorkCardMessage(card.closest(".message.assistant"));
      }
    }

    function renderProgress(progress = state.snapshot().runProgress, activeSessionId = state.snapshot().activeSessionId) {
      // 清理旧独立进度气泡（从旧版遗留）
      const standalone = messagesEl.querySelector('[data-progress-bubble="1"]');

      if (!visibleProgress(progress, activeSessionId)) {
        if (standalone) {
          standalone.classList.add("is-leaving");
          standalone._progressLeaveTimer = window.setTimeout(() => {
            if (standalone.classList.contains("is-leaving")) standalone.remove();
          }, 180);
        }
        clearEmbeddedProgress();
        return;
      }

      // 移除旧独立气泡
      if (standalone) standalone.remove();

      // 找 anchor 消息（绑定到同一个 messageId）
      const anchorId = progress?.anchorMessageId;
      let anchorNode = null;
      if (anchorId) {
        anchorNode = messagesEl.querySelector(`.message.assistant[data-message-id="${anchorId}"]`);
      }

      if (!anchorNode) {
        // 兜底：没有 anchor 消息时创建独立进度气泡（恢复/重连等场景）
        const stick = shouldStickToBottom();
        const node = createMessageNode({
          role: "assistant",
          progress: true,
          progressData: progress,
          at: progress.startedAt || Date.now()
        });
        node.classList.add("progress-enter");
        const anchor = progressAnchor(progress);
        anchor?.after(node) || messagesEl.appendChild(node);
        const content = node.querySelector(".message-content");
        renderProgressContent(content, progress, true);
        requestAnimationFrame(() => node.classList.remove("progress-enter"));
        if (stick) scrollMessagesToBottomAfterLayout();
        return;
      }

      // 注入进度到工作卡
      const msgContent = anchorNode.querySelector(".message-content");
      if (!msgContent) return;
      msgContent.classList.add("work-card");

      let progressWrap = anchorNode.querySelector(".work-progress-wrap");
      if (!progressWrap) {
        progressWrap = document.createElement("div");
        progressWrap.className = "work-progress-wrap";
        msgContent.insertBefore(progressWrap, msgContent.firstChild);
      }

      renderProgressContent(progressWrap, progress, true);

      // 同步消息文本内容
      const replyText = anchorNode.querySelector(".work-reply-text");
      if (!replyText) {
        const rt = document.createElement("div");
        rt.className = "work-reply-text";
        msgContent.appendChild(rt);
      }
      const rtNode = anchorNode.querySelector(".work-reply-text");
      if (rtNode && anchorId) {
        const isFinal = progress.phase === "finished" || progress.ok === false;
        if (isFinal) {
          // 流式结束：强制提交待处理帧，然后 Markdown 最终渲染
          if (_commitRafId !== null) {
            cancelAnimationFrame(_commitRafId);
            if (_commitTimer) clearTimeout(_commitTimer);
            _commitRafId = null;
            _commitTimer = null;
            commitMessageFrame();
          }
          renderFinalMarkdown(anchorNode);
        } else {
          // 流式中：纯文本更新
          const msgData = state.snapshot().messages.find(m => String(m.id) === anchorId);
          if (msgData && msgData.content) {
            rtNode.textContent = msgData.content;
          }
        }
      }
    }

    function renderMessages(messages, options = {}) {
      // Preserve the user's reading position unless they were already at the
      // bottom or the structural change is an explicit session/user anchor.
      const stick = options.forceScroll === true ? true : shouldStickToBottom();
      const prevScrollTop = messagesEl.scrollTop;
      messagesEl.innerHTML = "";
      messagesEl.classList.toggle("is-empty", messages.length === 0);

      if (!messages.length) {
        renderEmptyState(messagesEl, currentSettings);
        return;
      }

      for (const item of messages) {
        messagesEl.appendChild(createMessageNode(item));
      }

      const snap = state.snapshot();
      renderProgress(snap.runProgress, snap.activeSessionId);
      if (stick) {
        scrollMessagesToBottomAfterLayout();
      } else {
        messagesEl.scrollTop = prevScrollTop;
        requestAnimationFrame(() => {
          if (!shouldStickToBottom()) messagesEl.scrollTop = prevScrollTop;
        });
      }
      maybeSpeakLatestAssistant(messages);
    }

    function latestAssistantKey(messages) {
      const latest = [...messages].reverse().find((item) => item.role === "assistant" && !item.error && item.content && item.kind !== "confirm");
      if (!latest) return { key: "", content: "" };
      return { key: `${latest.at || ""}:${String(latest.content || "").slice(0, 80)}`, content: latest.content };
    }

    function maybeSpeakLatestAssistant(messages) {
      const latest = latestAssistantKey(messages);
      if (!speechReady) {
        speechReady = true;
        lastSpokenAssistantKey = latest.key;
        return;
      }
      if (!latest.key || latest.key === lastSpokenAssistantKey) return;
      lastSpokenAssistantKey = latest.key;
      speakAssistantReply(latest.content, currentSettings);
    }

    function renderSettings(settings) {
      currentSettings = settings;
      if (!settings.bodyVoiceReplyEnabled && supportsSpeech()) window.speechSynthesis.cancel();
      title.textContent = personaName(settings);
      modeLabel.textContent = "对话";
      renderMessages(state.snapshot().messages);
    }

    function applyHotBodyVoiceSettings(event) {
      const previous = currentSettings;
      currentSettings = { ...currentSettings, ...(event.detail || {}) };
      if (!currentSettings.bodyVoiceReplyEnabled && supportsSpeech()) window.speechSynthesis.cancel();
      const visualKeys = ["personaName", "personaAvatarDataUrl", "userName", "userCallsign", "userAvatarDataUrl"];
      if (visualKeys.some((key) => String(previous?.[key] || "") !== String(currentSettings?.[key] || ""))) {
        renderMessages(state.snapshot().messages);
      }
    }

    function chatGate() {
      const kernel = state.snapshot().kernelStatus || {};
      const life = kernel.life || {};
      const ready = kernel.phase === "ready"
        && kernel.compatible === true
        && life.ready === true
        && life.available === true;
      if (ready) return { blocked: false, message: "" };
      if (kernel.phase === "setup_required") {
        return { blocked: true, message: "生命身份需要迁移或绑定；发送已暂停以防生成错误的新身份。" };
      }
      const detail = String(kernel.lastError?.message || life.error || "").trim();
      return { blocked: true, message: detail || "运行内核正在启动，请稍候。" };
    }

    function renderBusy(busy) {
      const gate = chatGate();
      attachButton.disabled = Boolean(busy) || gate.blocked;
      sendButton.disabled = gate.blocked;
      sendButton.hidden = false;
      sendButton.title = gate.blocked ? gate.message : busy ? "普通输入排队；以“纠偏：”开头才修改当前任务" : "回车发送，Shift+回车换行";
      sendButton.setAttribute("aria-label", gate.blocked ? "生命内核未就绪，暂不可发送" : busy ? "发送排队消息或显式纠偏" : "发送");
      interruptButton.hidden = !busy;
      interruptButton.disabled = !busy;
      input.placeholder = gate.blocked ? gate.message : busy ? "运行中普通输入将排队；输入“纠偏：……”才修改当前任务" : "输入任务或对话...";
      chatStatus.textContent = gate.blocked
        ? gate.message
        : busy
          ? "后端执行中：普通输入按顺序排队，显式“纠偏：”才调整当前任务"
          : "就绪";
      renderReferenceBar();
    }

    function collectDropFiles(dataTransfer) {
      const allFiles = Array.from(dataTransfer?.files || []);
      const itemFiles = Array.from(dataTransfer?.items || [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter(Boolean);
      for (const file of itemFiles) {
        if (!allFiles.some((item) => item === file || (item.name === file.name && item.size === file.size && item.type === file.type))) {
          allFiles.push(file);
        }
      }
      return allFiles;
    }

    function dropHasFiles(dataTransfer) {
      return Array.from(dataTransfer?.types || []).includes("Files") || (dataTransfer?.files?.length || 0) > 0;
    }

    async function attachDroppedFiles(dataTransfer) {
      if (!supportsAttachments) {
        chatStatus.textContent = "当前后台只接收文字对话";
        return true;
      }
      const allFiles = collectDropFiles(dataTransfer);
      if (!allFiles.length) return false;
      if (state.snapshot().busy) {
        chatStatus.textContent = "后台执行中，稍后再添加附件";
        return true;
      }
      chatStatus.textContent = "拖入附件处理中";
      try {
        const paths = [];
        const items = [];
        for (const file of allFiles.slice(0, CHAT_ATTACHMENT_LIMIT)) {
          const filePath = String(file?.path || "");
          if (filePath) {
            paths.push(filePath);
            continue;
          }
          const dataUrl = await readFileAsDataUrl(file);
          items.push({
            name: file.name || "",
            type: file.type || "",
            size: Number(file.size || 0),
            dataUrl
          });
        }
        const result = await actions.pasteChatFiles({ paths, items });
        applyAttachmentResult(result, "已拖入：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
      return true;
    }

    let composerDragDepth = 0;
    function setComposerDragActive(active) {
      form.classList.toggle("is-drag-over", Boolean(active));
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (sendButton.disabled) return;
      const text = input.value.trim();
      const attachments = pendingAttachments.slice(0, CHAT_ATTACHMENT_LIMIT);
      if (!text && !attachments.length) return;
      const originalText = text;
      const originalAttachments = attachments;
      const snap = state.snapshot();
      const intent = classifyRunInput(text);
      const explicitGuide = Boolean(snap.busy && intent.kind === "guide");
      input.value = "";
      resizeInput();
      if (!explicitGuide) {
        pendingAttachments = [];
        renderAttachmentTray();
      }
      input.focus();
      try {
        if (typeof actions.handleRunInput !== "function") {
          throw new Error("运行输入分流接口不可用");
        }
        // handleRunInput mutates message state synchronously before its first
        // asynchronous boundary. Scroll on the next microtask, not after a
        // potentially long run that the user may have scrolled during.
        const resultPromise = actions.handleRunInput(text, attachments);
        queueMicrotask(() => scrollMessagesToBottomAfterLayout());
        const result = await resultPromise;
        if (result?.queued) {
          pendingAttachments = [];
          renderAttachmentTray();
          chatStatus.textContent = `已排队（第 ${Number(result.position || 1)} 条），当前任务完成后按顺序执行`;
        } else if (result?.guided) {
          chatStatus.textContent = result?.ok
            ? "纠偏已送达，当前任务将按新方向调整"
            : (result?.error || "纠偏发送失败");
        } else if (result?.ok === false) {
          chatStatus.textContent = result?.error || result?.stderr || "发送失败";
          // P2-03: restore the user's draft (text + attachments) when the
          // request failed instead of dropping the edit context.
          input.value = originalText;
          if (!explicitGuide) {
            pendingAttachments = originalAttachments;
            renderAttachmentTray();
          }
          resizeInput();
        }
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
        input.value = originalText;
        pendingAttachments = originalAttachments;
        renderAttachmentTray();
        resizeInput();
      }
    });

    attachButton.addEventListener("click", async () => {
      if (!supportsAttachments || attachButton.disabled) return;
      if (state.snapshot().busy) {
        chatStatus.textContent = "后台执行中，稍后再添加附件";
        return;
      }
      chatStatus.textContent = "选择文件中";
      try {
        const result = await actions.chooseChatFiles?.();
        applyAttachmentResult(result, "已选择：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
      input.focus();
    });

    interruptButton.addEventListener("click", async () => {
      if (interruptButton.disabled) return;
      interruptButton.disabled = true;
      chatStatus.textContent = "正在中断";
      try {
        const result = await actions.cancelRun?.();
        if (result?.ok || result?.interrupted || result?.canceled) {
          chatStatus.textContent = "已中断，可继续";
        } else {
          chatStatus.textContent = result?.error || "没有正在运行的任务";
        }
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      } finally {
        interruptButton.disabled = !state.snapshot().busy;
      }
    });

    input.addEventListener("input", resizeInput);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    input.addEventListener("paste", async (event) => {
      const files = Array.from(event.clipboardData?.files || []);
      const itemFiles = Array.from(event.clipboardData?.items || [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter(Boolean);
      const allFiles = [...files];
      for (const file of itemFiles) {
        if (!allFiles.some((item) => item === file || (item.name === file.name && item.size === file.size && item.type === file.type))) {
          allFiles.push(file);
        }
      }
      if (!allFiles.length) return;
      event.preventDefault();
      if (!supportsAttachments) {
        chatStatus.textContent = "当前后台只接收文字对话";
        return;
      }
      if (state.snapshot().busy) {
        chatStatus.textContent = "后端执行中，稍后再粘贴文件";
        return;
      }
      chatStatus.textContent = "粘贴文件处理中";
      try {
        const paths = [];
        const items = [];
        for (const file of allFiles.slice(0, CHAT_ATTACHMENT_LIMIT)) {
          const filePath = String(file?.path || "");
          if (filePath) {
            paths.push(filePath);
            continue;
          }
          const dataUrl = await readFileAsDataUrl(file);
          items.push({
            name: file.name || "",
            type: file.type || "",
            size: Number(file.size || 0),
            dataUrl
          });
        }
        const result = await actions.pasteChatFiles({ paths, items });
        applyAttachmentResult(result, "已粘贴：");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
    });

    form.addEventListener("dragenter", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth += 1;
      setComposerDragActive(true);
      event.dataTransfer.dropEffect = "copy";
    });

    form.addEventListener("dragover", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      setComposerDragActive(true);
      event.dataTransfer.dropEffect = "copy";
    });

    form.addEventListener("dragleave", (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth = Math.max(0, composerDragDepth - 1);
      if (composerDragDepth === 0) setComposerDragActive(false);
    });

    form.addEventListener("drop", async (event) => {
      if (!dropHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      composerDragDepth = 0;
      setComposerDragActive(false);
      await attachDroppedFiles(event.dataTransfer);
    });

    async function saveAttachmentFromButton(button) {
      const target = button?.dataset?.saveAttachment || "";
      if (!target) return;
      try {
        const result = await actions.saveTargetAs?.(target, { name: button.dataset.saveName || "" });
        if (result?.ok && !result?.canceled) chatStatus.textContent = `已另存：${result.path || ""}`;
        else if (result?.canceled) chatStatus.textContent = "已取消另存";
        else chatStatus.textContent = result?.error || "另存失败";
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      }
    }

    messagesEl.addEventListener("click", (event) => {
      const artifactButton = event.target.closest("[data-open-artifact]");
      if (artifactButton) {
        event.preventDefault();
        if (artifactButton.disabled) return;
        const status = artifactButton.parentElement?.querySelector(".artifact-card-open-status");
        artifactButton.disabled = true;
        artifactButton.textContent = "定位中";
        if (status) status.textContent = "正在校验";
        Promise.resolve(actions.openArtifact?.(artifactButton._tiangongArtifactCard))
          .then((result) => {
            artifactButton.textContent = result?.ok ? "已定位" : "重试定位";
            if (status) status.textContent = result?.ok ? "已在文件夹中选中" : `定位失败：${result?.error || "未知错误"}`;
          })
          .catch((error) => {
            artifactButton.textContent = "重试定位";
            if (status) status.textContent = `定位失败：${error?.message || String(error)}`;
          })
          .finally(() => { artifactButton.disabled = false; });
        return;
      }
      const confirmButton = event.target.closest("[data-confirm-decision]");
      if (confirmButton) {
        event.preventDefault();
        if (confirmButton.disabled) return;
        const confirmId = String(confirmButton.dataset.confirmId || "").trim();
        const decision = String(confirmButton.dataset.confirmDecision || "").trim();
        // HOTFIX-20260728: 静默早退改可见反馈——任何点击都要有回应
        if (!confirmId || !decision || typeof actions.resolvePolicyConfirmation !== "function") {
          const card = confirmButton.closest(".confirm-card");
          if (card && !card.querySelector(".confirm-card-result")) {
            const warn = document.createElement("div");
            warn.className = "confirm-card-result confirm-card-result-error";
            warn.textContent = "确认信息异常，无法提交你的选择，请重新发送刚才的指令。";
            card.appendChild(warn);
          }
          return;
        }
        // 先禁用整张卡的按钮，等待 actions 更新卡片状态
        for (const button of confirmButton.closest(".confirm-card-actions")?.querySelectorAll("button") || []) {
          button.disabled = true;
        }
        Promise.resolve(actions.resolvePolicyConfirmation({ confirmId, decision }))
          .catch(() => {});
        return;
      }
      const saveButton = event.target.closest("[data-save-attachment]");
      if (saveButton) {
        event.preventDefault();
        saveAttachmentFromButton(saveButton);
        return;
      }
      const localLink = event.target.closest("[data-open-path]");
      if (!localLink) return;
      event.preventDefault();
      actions.openPath(localLink.dataset.openPath);
    });

    attachmentTray.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-attachment]");
      if (remove) {
        pendingAttachments = pendingAttachments.filter((item) => attachmentKey(item) !== remove.dataset.removeAttachment);
        renderAttachmentTray();
        return;
      }
      const saveButton = event.target.closest("[data-save-attachment]");
      if (saveButton) {
        event.preventDefault();
        saveAttachmentFromButton(saveButton);
        return;
      }
      const localLink = event.target.closest("[data-open-path]");
      if (!localLink) return;
      event.preventDefault();
      actions.openPath(localLink.dataset.openPath);
    });

    newChatButton.addEventListener("click", () => {
      actions.startNewConversation();
      input.value = "";
      pendingAttachments = [];
      renderAttachmentTray();
      resizeInput();
      input.focus();
    });
    clearButton.addEventListener("click", async () => {
      clearButton.disabled = true;
      chatStatus.textContent = "正在清空当前对话";
      pendingAttachments = [];
      renderAttachmentTray();
      try {
        const result = await actions.clearConversation();
        chatStatus.textContent = result?.ok
          ? result?.localOnly
            ? "仅本地视图已清空，服务端记录未确认删除"
            : "当前对话已清空"
          : (result?.error || "清空对话失败");
      } catch (error) {
        chatStatus.textContent = error?.message || String(error);
      } finally {
        clearButton.disabled = false;
      }
    });

    bus.on("composer:set-text", (text) => {
      actions.setActivePage("chat");
      input.value = String(text || "");
      resizeInput();
      input.focus();
    });

    state.on("page", renderPage);

    // ── Token Commit Pipeline：帧合并提交 ──
    let _lastMsgSnapshot = "";
    let _commitRafId = null;
    let _commitTimer = null;
    let _pendingCommit = null;
    let _lastCommittedLen = 0;
    const COMMIT_CHAR_THRESHOLD = 80;
    const COMMIT_TIME_THRESHOLD = 50;

    function formatStreamingPreview(text) {
      const raw = String(text || "");
      if (raw.includes("\n\n")) return raw;
      return raw
        .replace(/([。！？；])(?=[^\n])/g, "$1\n\n")
        .replace(/([.!?;])\s+(?=[A-Z0-9\u4e00-\u9fa5])/g, "$1\n\n")
        .replace(/\s+(?=(?:[-*]|\d+[.)])\s+)/g, "\n");
    }

    function commitMessageFrame() {
      _commitRafId = null;
      if (_commitTimer) { clearTimeout(_commitTimer); _commitTimer = null; }
      const msg = _pendingCommit;
      if (!msg) return;

      const targetNode = messagesEl.querySelector(`.message.assistant[data-message-id="${msg.id}"]`);
      if (!targetNode) {
        // 首次渲染：全量重建
        renderMessages(state.snapshot().messages);
        _lastCommittedLen = msg.content ? msg.content.length : 0;
        return;
      }

      // 工作卡：流式中只更新纯文本（不跑 Markdown，避免代码块/表格重建闪烁）
      const stick = shouldStickToBottom();
      const replyText = targetNode.querySelector(".work-reply-text");
      if (replyText && !replyText.dataset.rendered) {
        replyText.textContent = formatStreamingPreview(msg.content || "");
      } else if (!replyText) {
        // 普通消息
        const msgContent = targetNode.querySelector(".message-content");
        if (msgContent && !targetNode.classList.contains("progress")) {
          msgContent.textContent = msg.content || "";
        }
      }
      if (stick) scrollMessagesToBottomAfterLayout();
      _lastCommittedLen = msg.content ? msg.content.length : 0;
    }

    function scheduleMessageCommit(last) {
      _pendingCommit = { id: last.id, content: last.content };
      const newChars = (last.content?.length || 0) - _lastCommittedLen;

      if (_commitRafId === null) {
        _commitRafId = requestAnimationFrame(commitMessageFrame);
        _commitTimer = setTimeout(() => {
          // 超时强制提交（防止慢连接下 rAF 迟迟不触发）
          if (_commitRafId !== null) {
            cancelAnimationFrame(_commitRafId);
            _commitRafId = null;
            commitMessageFrame();
          }
        }, COMMIT_TIME_THRESHOLD);
      }

      // 累积超过阈值：立即强制提交
      if (newChars >= COMMIT_CHAR_THRESHOLD) {
        if (_commitRafId !== null) cancelAnimationFrame(_commitRafId);
        if (_commitTimer) clearTimeout(_commitTimer);
        _commitRafId = null;
        _commitTimer = null;
        commitMessageFrame();
      }
    }

    // 流式结束后渲染完整 Markdown
    function renderFinalMarkdown(anchorNode) {
      const replyText = anchorNode.querySelector(".work-reply-text");
      if (!replyText || replyText.dataset.rendered === "1") return;

      const msgId = anchorNode.dataset.messageId;
      const msgData = state.snapshot().messages.find(m => String(m.id) === msgId);
      if (!msgData || !msgData.content) return;

      const stickToBottom = shouldStickToBottom();
      replyText.innerHTML = "";
      renderMessageContent(replyText, spokenBackendText(msgData.content) || msgData.content);
      renderMessageAttachments(replyText, msgData.attachments);
      replyText.dataset.rendered = "1";
      if (stickToBottom) scrollMessagesToBottomAfterLayout();
    }

    let _lastMsgCount = 0;
    let _lastActiveSessionId = state.snapshot().activeSessionId;
    state.on("messages", (messages) => {
      const snap = state.snapshot();
      const sessionChanged = snap.activeSessionId !== _lastActiveSessionId;
      const countChanged = messages.length !== _lastMsgCount;
      _lastMsgCount = messages.length;
      _lastActiveSessionId = snap.activeSessionId;

      // 结构性变化（新增/删除/清空/切换）：全量重建
      if (sessionChanged || countChanged) {
        if (_commitRafId !== null) {
          cancelAnimationFrame(_commitRafId);
          if (_commitTimer) clearTimeout(_commitTimer);
          _commitRafId = null;
          _commitTimer = null;
        }
        _lastMsgSnapshot = "";
        const justSentByUser = messages[messages.length - 1]?.role === "user";
        renderMessages(messages, { forceScroll: sessionChanged || justSentByUser });
        return;
      }

      // 内容变化（流式追加/替换）：智能 patch
      const last = messages[messages.length - 1];
      if (!last) return;
      // Stage replies are full snapshots, not deltas.  Comparing only the
      // length drops a legitimate replacement whenever two consecutive model
      // turns happen to contain the same number of characters.
      const lastSig = last ? `${last.id || ""}\u0000${last.content || ""}` : "";
      if (lastSig && lastSig === _lastMsgSnapshot) return;
      _lastMsgSnapshot = lastSig;
      scheduleMessageCommit(last);
    });
    state.on("runProgress", (progress) => renderProgress(progress, state.snapshot().activeSessionId));
    // GF 门：lastRun 相位变化（含网关非成功终态）时重渲染，确保系统裁决卡片出现
    state.on("run", () => renderMessages(state.snapshot().messages));
    state.on("settings", renderSettings);
    window.addEventListener("tiangong-body-hot-preview", applyHotBodyVoiceSettings);

    // ── SSE 结束后强制最终 Markdown 渲染 ──
    function renderFinalMessageById(messageId) {
      if (_commitRafId !== null) {
        cancelAnimationFrame(_commitRafId);
        if (_commitTimer) clearTimeout(_commitTimer);
        _commitRafId = null;
        _commitTimer = null;
        commitMessageFrame();
      }
      const node = messagesEl.querySelector(`.message.assistant[data-message-id="${messageId}"]`);
      const msgData = state.snapshot().messages.find(m => String(m.id) === messageId);
      if (!node || !msgData) return;
      const content = node.querySelector(".message-content");
      if (!content) return;
      const stickToBottom = shouldStickToBottom();
      content.querySelector(".work-progress-wrap")?.remove();
      content.innerHTML = "";
      content.classList.remove("work-card");
      content.classList.add("rich-text");
      renderMessageContent(content, spokenBackendText(msgData.content) || msgData.content);
      renderMessageAttachments(content, msgData.attachments);
      // GF 门：终态重渲染会重建内容区，系统裁决卡片需在这里补上
      const finalRun = state.snapshot().lastRun || {};
      if (String(finalRun.requestId || "") && String(finalRun.requestId) === String(msgData.requestId || "")) {
        appendGatewayPhaseCard(content, finalRun.phase);
      }
      if (stickToBottom) scrollMessagesToBottomAfterLayout();
    }

    window.addEventListener("tiangong-chat-final-render", (event) => {
      const messageId = String(event.detail?.messageId || "");
      if (!messageId) return;
      requestAnimationFrame(() => renderFinalMessageById(messageId));
    });

    state.on("busy", renderBusy);
    state.on("kernelStatus", () => renderBusy(state.snapshot().busy));
    renderPage(state.snapshot().activePage);
    renderMessages(state.snapshot().messages, { forceScroll: true });
    _lastMsgCount = state.snapshot().messages.length;  // 同步计数器，避免 countChanged 误判
    _lastActiveSessionId = state.snapshot().activeSessionId;  // 同步 session 追踪
    renderSettings(state.snapshot().settings);
    renderBusy(state.snapshot().busy);
    renderAttachmentTray();
  }
};
