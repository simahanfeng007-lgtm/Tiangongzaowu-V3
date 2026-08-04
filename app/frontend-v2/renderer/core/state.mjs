import { projectMessageKind } from "./truth-projection.mjs";

const MESSAGE_KEY = "linyuanzhe.messages";
const SESSIONS_KEY = "linyuanzhe.sessions";
const ACTIVE_SESSION_KEY = "linyuanzhe.activeSessionId";
const MESSAGE_MAX_CONTENT = 15000;
const MESSAGE_ATTACHMENT_LIMIT = 32;
const MESSAGE_MAX_DATA_URL = 6 * 1024 * 1024;
const PROGRESS_MAX_JSON = 32 * 1024;
const MESSAGE_ROLES = new Set(["user", "assistant"]);

export const DEFAULT_SOUL_PROMPT = [
  "你是临渊者，天工造物的基础工作 Soul。",
  "默认姿态：先理解目标，再主动补齐必要上下文，优先核实真实运行和文件状态；能动手就动手，能验证就验证。",
  "工作方式：拆解任务、选择最小有效工具、持续收口；遇到错误先诊断根因并重试一次，避免空泛解释。",
  "交付标准：回答简洁清楚，说明已做什么、结果、验证与未完成风险；不编造工具结果，不隐藏失败。",
  "边界：用户目标、安全策略和 A5 硬拦优先于任何表达风格。"
].join("\n");

const defaultSettings = {
  workspace: "",
  storageRoot: "",
  storageRootMode: "default",
  knowledgeRoot: "",
  mode: "auto",
  personaName: "临渊者",
  soulPrompt: DEFAULT_SOUL_PROMPT,
  personaAvatarDataUrl: "",
  bodyPreset: "standard",
  bodyVoiceReplyEnabled: false,
  bodyVoicePreset: "qiyuan_clear",
  bodyVoiceName: "",
  bodyVoiceCustomName: "",
  bodyVoiceCustomPath: "",
  bodyVoiceCustomState: "empty",
  bodyVoiceLang: "zh-CN",
  bodyVoiceRate: 1,
  bodyVoicePitch: 1.04,
  bodyVoiceVolume: 1,
  bodyVoicePresets: [],
  userDisplayName: "",
  userCallsign: "",
  userWork: "",
  userAvatarDataUrl: "",
  userProfileSummary: "",
  userContextEnabled: true,
  themeStyle: "ink_teal",
  modelService: "custom",
  modelProvider: "",
  modelBaseUrl: "",
  modelName: "",
  modelApiKey: "",
  modelMatchedProvider: "",
  modelProviderMatch: null,
  modelProviderPresets: [],
  modelProviderProfiles: {},
  modelThinkingEnabled: false,
  modelThinkingDepth: "",
  modelMultimodalInput: "auto",
  modelImageInput: "auto",
  modelVideoInput: "auto",
  modelAudioInput: "auto",
  webSearchProvider: "auto",
  imageGenerationMode: "auto",
  plannerMode: "",
  toolMode: "",
};

const defaultRun = {
  phase: "idle",
  ok: true,
  code: "",
  elapsedMs: 0,
  stdout: "",
  stderr: "",
  mode: "auto"
};

const defaultBackendConfig = {
  loading: false,
  ok: null,
  stdout: "",
  stderr: "",
  code: ""
};

const defaultRunProgress = {
  requestId: "",
  sessionId: "",
  phase: "idle",
  ok: null,
  startedAt: 0,
  finishedAt: 0,
  anchorAt: 0,
  codexPlan: null,
  codexProgress: null,
  steps: []
};

const RUN_TERMINAL_PHASES = new Set(["finished", "interrupted", "orphaned"]);

function structuredCloneSafe(value) {
  if (typeof structuredClone === "function") {
    try { return structuredClone(value); } catch {}
  }
  return JSON.parse(JSON.stringify(value ?? null));
}


function cleanSelectedSkill(skill) {
  if (!skill || typeof skill !== "object") return null;
  const id = String(skill.id || skill.abilityId || skill.ability_id || skill.name || "").trim();
  const name = String(skill.name || skill.abilityName || skill.ability_name || skill.title || skill.id || "").trim();
  if (!id && !name) return null;
  return {
    id: id || name,
    name: name || id,
    displayName: String(skill.displayName || "").trim(),
    displayDescription: String(skill.displayDescription || "").trim(),
    category: String(skill.category || ""),
    description: String(skill.description || ""),
    toolNames: Array.isArray(skill.toolNames) ? skill.toolNames.map((item) => String(item || "")).filter(Boolean).slice(0, 24) : []
  };
}

function publicSelectedSkills(skills) {
  return (Array.isArray(skills) ? skills : []).map((skill) => ({ ...skill }));
}

function boundedText(value, maxLength = 4096) {
  return String(value ?? "").replace(/\u0000/g, "").slice(0, maxLength);
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function boundedJsonObject(value, maxLength = PROGRESS_MAX_JSON) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  try {
    const serialized = JSON.stringify(value);
    if (!serialized || serialized.length > maxLength) return null;
    return JSON.parse(serialized);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function isInternalProgressStep(step) {
  return String(
    step?.visibility
    || step?.meta?.visibility
    || ""
  ).trim().toLowerCase() === "internal";
}

function cleanProgressStep(step) {
  const progressSnapshot = step?.progress_snapshot || step?.progressSnapshot || null;
  const meta = boundedJsonObject(step?.meta);
  return {
    id: boundedText(step?.id || step?.step_id || step?.title || "step", 256),
    title: boundedText(step?.title || step?.step_id || "运行步骤", 500),
    status: boundedText(step?.status || "pending", 64),
    summary: boundedText(step?.summary || "", 4000),
    meta,
    stepId: boundedText(step?.plan_step_id || step?.codex_step_id || step?.stepId || "", 256),
    substep: boundedText(step?.substep || "", 500),
    toolName: boundedText(step?.tool_name || step?.toolName || "", 256),
    progressSnapshot: boundedJsonObject(progressSnapshot),
    totalProgress: finiteNumber(step?.total_progress ?? step?.totalProgress ?? progressSnapshot?.total_progress),
    confidence: finiteNumber(step?.confidence ?? progressSnapshot?.confidence),
    riskScore: finiteNumber(step?.risk_score ?? step?.riskScore ?? progressSnapshot?.risk_score),
    healthScore: finiteNumber(step?.health_score ?? step?.healthScore ?? progressSnapshot?.health_score),
    ts: finiteNumber(step?.ts, Date.now() / 1000)
  };
}

function mergeProgressStep(steps, step) {
  const next = Array.isArray(steps) ? steps.filter((item) => item.id !== step.id) : [];
  next.push(step);
  return next.slice(-18);
}

function terminalPhaseFromProgressEvent(step, event, currentPhase) {
  const current = String(currentPhase || "").trim();
  if (RUN_TERMINAL_PHASES.has(current)) return current;
  const phase = String(event?.runPhase || event?.run_phase || event?.phase || "").trim();
  if (RUN_TERMINAL_PHASES.has(phase)) return phase;
  if (step.id === "backend_finished") return "finished";
  return "running";
}

function publicRunProgress(progress) {
  return {
    requestId: progress.requestId,
    sessionId: progress.sessionId,
    phase: progress.phase,
    ok: progress.ok,
    startedAt: progress.startedAt,
    finishedAt: progress.finishedAt,
    anchorAt: progress.anchorAt,
    anchorMessageId: progress.anchorMessageId || null,
    codexPlan: progress.codexPlan ? { ...progress.codexPlan } : null,
    codexProgress: progress.codexProgress ? { ...progress.codexProgress } : null,
    steps: progress.steps
      .filter((step) => !isInternalProgressStep(step))
      .map((step) => ({ ...step }))
  };
}

function scopedStorageKey(base, lifeId = "") {
  const scope = String(lifeId || "").trim();
  return scope ? `${base}.${encodeURIComponent(scope)}` : base;
}

function loadMessages(lifeId = "") {
  try {
    return JSON.parse(localStorage.getItem(scopedStorageKey(MESSAGE_KEY, lifeId)) || "[]");
  } catch {
    return [];
  }
}

function saveMessages(messages, lifeId = "") {
  safeStorageSet(scopedStorageKey(MESSAGE_KEY, lifeId), JSON.stringify(messages.slice(-80)));
}

function nowSessionId() {
  return `session_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function msgId() {
  return "msg_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
}

function cleanMessages(messages, sessionId = "") {
  return Array.isArray(messages)
    ? messages.map((item) => ({
        id: boundedText(item?.id || msgId(), 256),
        role: MESSAGE_ROLES.has(String(item?.role || "")) ? String(item.role) : "",
        content: boundedText(item?.content || "", MESSAGE_MAX_CONTENT),
        attachments: cleanAttachments(item?.attachments),
        error: Boolean(item?.error),
        at: Number(item?.at || Date.now()),
        sessionId: boundedText(item?.sessionId || item?.session_id || item?.conversationId || item?.conversation_id || sessionId || "", 256),
        kind: item?.kind ? boundedText(item.kind, 64) : null,
        requestId: item?.requestId || item?.request_id ? boundedText(item?.requestId || item?.request_id, 256) : null
      })).filter((item) => item.role && item.content).slice(-80)
    : [];
}

function cleanAttachments(attachments) {
  return Array.isArray(attachments)
    ? attachments.map((item) => ({
        artifact_schema: String(item?.artifact_schema || ""),
        gateway_request_id: String(item?.gateway_request_id || ""),
        run_id: String(item?.run_id || ""),
        generation: Number.isInteger(item?.generation) ? item.generation : 0,
        artifact_id: String(item?.artifact_id || ""),
        artifact_revision_id: String(item?.artifact_revision_id || ""),
        revision: Number.isInteger(item?.revision) ? item.revision : 0,
        filename: String(item?.filename || item?.name || item?.file_name || ""),
        size_bytes: Number(item?.size_bytes || item?.size || 0),
        mime: String(item?.mime || item?.type || ""),
        artifact_kind: String(item?.artifact_kind || ""),
        format_id: String(item?.format_id || ""),
        content_sha256: String(item?.content_sha256 || ""),
        manifest_sha256: String(item?.manifest_sha256 || ""),
        qc_state: String(item?.qc_state || ""),
        qc_checks: Array.isArray(item?.qc_checks) ? item.qc_checks.map((value) => String(value || "")).filter(Boolean).slice(0, 64) : [],
        created_at_ms: Number(item?.created_at_ms || 0),
        open_capability: String(item?.open_capability || ""),
        card_sha256: String(item?.card_sha256 || ""),
        name: String(item?.name || item?.filename || item?.file_name || ""),
        path: item?.artifact_schema === "tiangong.gateway.artifact-card.v1" ? "" : String(item?.path || ""),
        url: item?.artifact_schema === "tiangong.gateway.artifact-card.v1" ? "" : String(item?.url || ""),
        dataUrl: item?.artifact_schema === "tiangong.gateway.artifact-card.v1" ? "" : String(item?.dataUrl || item?.data_url || ""),
        ext: String(item?.ext || "").toLowerCase(),
        kind: String(item?.kind || ""),
        type: String(item?.type || ""),
        size: Number(item?.size || 0),
        documentId: String(item?.documentId || item?.document_id || ""),
        status: String(item?.status || ""),
        summary: String(item?.summary || ""),
        citationCount: Number(item?.citationCount || item?.citation_count || 0),
        error: String(item?.error || ""),
        importError: String(item?.importError || item?.import_error || "")
      })).map((item) => ({
        ...item,
        artifact_schema: boundedText(item.artifact_schema, 128),
        gateway_request_id: boundedText(item.gateway_request_id, 256),
        run_id: boundedText(item.run_id, 256),
        artifact_id: boundedText(item.artifact_id, 256),
        artifact_revision_id: boundedText(item.artifact_revision_id, 256),
        name: boundedText(item.name, 512),
        filename: boundedText(item.filename, 512),
        mime: boundedText(item.mime, 256),
        artifact_kind: boundedText(item.artifact_kind, 128),
        format_id: boundedText(item.format_id, 64),
        content_sha256: boundedText(item.content_sha256, 128),
        manifest_sha256: boundedText(item.manifest_sha256, 128),
        qc_state: boundedText(item.qc_state, 64),
        qc_checks: item.qc_checks.map((value) => boundedText(value, 256)),
        open_capability: boundedText(item.open_capability, 128),
        card_sha256: boundedText(item.card_sha256, 128),
        path: boundedText(item.path, 4096),
        url: boundedText(item.url, 4096),
        dataUrl: boundedText(item.dataUrl, MESSAGE_MAX_DATA_URL),
        ext: boundedText(item.ext, 32),
        kind: boundedText(item.kind, 64),
        type: boundedText(item.type, 256),
        documentId: boundedText(item.documentId, 256),
        status: boundedText(item.status, 64),
        summary: boundedText(item.summary, 4000),
        error: boundedText(item.error, 1000),
        importError: boundedText(item.importError, 1000),
      })).filter((item) => item.artifact_schema === "tiangong.gateway.artifact-card.v1" || item.name || item.path || item.url || item.dataUrl).slice(0, MESSAGE_ATTACHMENT_LIMIT)
    : [];
}

function sessionTitle(messages) {
  const firstUser = messages.find((item) => item.role === "user" && item.content);
  if (!firstUser) return "新对话";
  return String(firstUser.content || "").replace(/\s+/g, " ").trim().slice(0, 48) || "新对话";
}

function createSession(messages = []) {
  const id = nowSessionId();
  const clean = cleanMessages(messages, id);
  const now = Date.now();
  return {
    id,
    title: sessionTitle(clean),
    messages: clean,
    createdAt: clean[0]?.at || now,
    updatedAt: clean.at(-1)?.at || now
  };
}

function normalizeSessions(rawSessions) {
  const sessions = Array.isArray(rawSessions) ? rawSessions : [];
  return sessions.map((item) => {
    const id = String(item?.id || nowSessionId());
    const messages = cleanMessages(item?.messages, id);
    return {
      id,
      title: boundedText(item?.title || sessionTitle(messages), 80),
      messages,
      createdAt: Number(item?.createdAt || messages[0]?.at || Date.now()),
      updatedAt: Number(item?.updatedAt || messages.at(-1)?.at || Date.now())
    };
  }).filter((item) => item.id);
}

function hasConversationState(lifeId = "") {
  return localStorage.getItem(scopedStorageKey(SESSIONS_KEY, lifeId)) !== null;
}

function loadConversationState(lifeId = "") {
  let sessions = [];
  try {
    sessions = normalizeSessions(JSON.parse(localStorage.getItem(scopedStorageKey(SESSIONS_KEY, lifeId)) || "[]"));
  } catch {
    sessions = [];
  }
  if (!sessions.length) {
    const legacyMessages = cleanMessages(loadMessages(lifeId));
    sessions = [createSession(legacyMessages)];
  }
  let activeSessionId = localStorage.getItem(scopedStorageKey(ACTIVE_SESSION_KEY, lifeId)) || "";
  if (!sessions.some((item) => item.id === activeSessionId)) {
    activeSessionId = sessions[0]?.id || "";
  }
  return { sessions, activeSessionId };
}

function saveConversationState(sessions, activeSessionId, lifeId = "") {
  const compact = normalizeSessions(sessions).slice(-30);
  safeStorageSet(scopedStorageKey(SESSIONS_KEY, lifeId), JSON.stringify(compact));
  safeStorageSet(scopedStorageKey(ACTIVE_SESSION_KEY, lifeId), activeSessionId || compact[0]?.id || "");
  const active = compact.find((item) => item.id === activeSessionId) || compact[0];
  saveMessages(active?.messages || [], lifeId);
}

function activeSession(sessions, activeSessionId) {
  return sessions.find((item) => item.id === activeSessionId) || sessions[0] || createSession();
}

function publicSessionList(sessions, activeSessionId) {
  return [...sessions]
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, 20)
    .map((item) => {
      const latest = [...item.messages]
        .reverse()
        .find((message) => projectMessageKind(message) !== "unknown");
      return {
        id: item.id,
        title: item.title || sessionTitle(item.messages),
        count: item.messages.length,
        updatedAt: item.updatedAt,
        active: item.id === activeSessionId,
        preview: latest?.content || ""
      };
    });
}

export function createState() {
  const listeners = new Map();
  const conversation = loadConversationState("");
  const data = {
    lifeId: "",
    settings: { ...defaultSettings },
    activePage: "chat",
    activeSkillCategory: "all",
    selectedSkills: [],
    sessions: conversation.sessions,
    activeSessionId: conversation.activeSessionId,
    messages: activeSession(conversation.sessions, conversation.activeSessionId).messages,
    busyBySession: {},
    lastRunBySession: {},
    runProgressBySession: {},
    _streamSaveTimers: {},
    kernelStatus: {
      phase: "created",
      compatible: null,
      life: { ready: null, available: null, degraded: false, error: "", phase: "unknown" },
      lastError: null
    },
    runtimeStatus: { text: "待连接", loading: false, ok: null, payload: null },
    backendConfig: { ...defaultBackendConfig }
  };

  function _getRP(sid) { return data.runProgressBySession[sid] || { ...defaultRunProgress }; }
  function _getLR(sid) { return data.lastRunBySession[sid] || { ...defaultRun }; }
  function _getBZ(sid) { return data.busyBySession[sid] || false; }

  function snapshot() {
    const aid = data.activeSessionId;
    return {
      settings: { ...data.settings },
      lifeId: data.lifeId,
      activePage: data.activePage,
      activeSkillCategory: data.activeSkillCategory,
      selectedSkills: publicSelectedSkills(data.selectedSkills),
      sessions: publicSessionList(data.sessions, aid),
      activeSessionId: aid,
      messages: [...data.messages],
      busy: _getBZ(aid),
      lastRun: { ..._getLR(aid) },
      runProgress: publicRunProgress(_getRP(aid)),
      kernelStatus: structuredCloneSafe(data.kernelStatus),
      runtimeStatus: { ...data.runtimeStatus },
      backendConfig: { ...data.backendConfig }
    };
  }

  function on(eventName, handler) {
    const handlers = listeners.get(eventName) || new Set();
    handlers.add(handler);
    listeners.set(eventName, handlers);
    return () => handlers.delete(handler);
  }

  function emit(eventName, payload) {
    for (const handler of listeners.get(eventName) || []) {
      handler(payload);
    }
    for (const handler of listeners.get("*") || []) {
      handler(snapshot());
    }
  }

  function setSettings(next) {
    data.settings = { ...data.settings, ...(next || {}) };
    emit("settings", { ...data.settings });
  }

  function setLifeScope(lifeId) {
    const nextLifeId = String(lifeId || "").trim();
    if (!nextLifeId || nextLifeId === data.lifeId) return data.lifeId;

    for (const timer of Object.values(data._streamSaveTimers)) {
      if (timer) window.clearTimeout(timer);
    }
    data._streamSaveTimers = {};

    if (data.lifeId) {
      saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    } else if (!hasConversationState(nextLifeId)) {
      // One-time migration: the pre-life frontend conversation cache belongs to
      // the first explicitly active life. It is copied, never shared afterward.
      saveConversationState(data.sessions, data.activeSessionId, nextLifeId);
    }

    const next = loadConversationState(nextLifeId);
    data.lifeId = nextLifeId;
    data.sessions = next.sessions;
    data.activeSessionId = next.activeSessionId;
    data.messages = activeSession(next.sessions, next.activeSessionId).messages;
    data.selectedSkills = [];
    data.busyBySession = {};
    data.lastRunBySession = {};
    data.runProgressBySession = {};

    emit("lifeScope", nextLifeId);
    emit("selectedSkills", []);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    emit("runProgress", publicRunProgress(defaultRunProgress));
    emit("run", { ...defaultRun });
    emit("busy", false);
    return data.lifeId;
  }

  function setActivePage(page) {
    const next = ["chat", "execute", "knowledge", "skills", "body", "lifecycle", "settings"].includes(page) ? page : "chat";
    data.activePage = next;
    emit("page", next);
  }

  function setActiveSkillCategory(category) {
    const next = String(category || "all").trim() || "all";
    data.activeSkillCategory = next;
    emit("skillCategory", next);
  }

  function toggleSelectedSkill(skill) {
    const cleaned = cleanSelectedSkill(skill);
    if (!cleaned) return publicSelectedSkills(data.selectedSkills);
    const key = String(cleaned.id || cleaned.name).toLowerCase();
    const exists = data.selectedSkills.some((item) => String(item.id || item.name).toLowerCase() === key);
    data.selectedSkills = exists
      ? data.selectedSkills.filter((item) => String(item.id || item.name).toLowerCase() !== key)
      : [...data.selectedSkills, cleaned].slice(-5);
    emit("selectedSkills", publicSelectedSkills(data.selectedSkills));
    return publicSelectedSkills(data.selectedSkills);
  }

  function clearSelectedSkills() {
    data.selectedSkills = [];
    emit("selectedSkills", []);
  }

  function setBusy(sessionId, next) {
    if (typeof next === "undefined") { next = sessionId; sessionId = data.activeSessionId; }
    data.busyBySession[String(sessionId || data.activeSessionId)] = Boolean(next);
    emit("busy", _getBZ(data.activeSessionId));
  }

  function setLastRun(sessionId, next) {
    if (typeof next === "undefined") { next = sessionId; sessionId = data.activeSessionId; }
    data.lastRunBySession[String(sessionId || data.activeSessionId)] = { ...defaultRun, ...(next || {}) };
    emit("run", { ..._getLR(data.activeSessionId) });
  }

  function setRuntimeStatus(next) {
    data.runtimeStatus = { ...data.runtimeStatus, ...(next || {}) };
    emit("runtimeStatus", { ...data.runtimeStatus });
  }

  function setKernelStatus(next) {
    data.kernelStatus = structuredCloneSafe(next || data.kernelStatus);
    emit("kernelStatus", structuredCloneSafe(data.kernelStatus));
  }

  function setBackendConfig(next) {
    data.backendConfig = { ...data.backendConfig, ...(next || {}) };
    emit("backendConfig", { ...data.backendConfig });
  }

  function resetRunState() {
    const sid = data.activeSessionId;
    data.busyBySession[sid] = false;
    data.lastRunBySession[sid] = { ...defaultRun };
    data.runProgressBySession[sid] = { ...defaultRunProgress };
    emit("busy", false);
    emit("run", { ...defaultRun });
    emit("runProgress", publicRunProgress(defaultRunProgress));
  }

  function startRunProgress(sessionId, requestId, options = {}) {
    if (typeof requestId === "undefined" || (typeof requestId === "object" && !Array.isArray(requestId))) {
      // old signature: startRunProgress(requestId, options)
      options = requestId || {};
      requestId = sessionId;
      sessionId = data.activeSessionId;
    }
    const sid = String(sessionId || data.activeSessionId);
    const now = Date.now();
    data.runProgressBySession[sid] = {
      ...defaultRunProgress,
      requestId: String(requestId || ""),
      sessionId: sid,
      phase: "running",
      startedAt: now,
      anchorAt: Number(options.anchorAt || now),
      anchorMessageId: options.anchorMessageId || null,
      steps: [
        cleanProgressStep({
          id: "backend_wait",
          title: "发送到后端",
          status: "running",
          summary: "前端消息已交给桌面运行桥"
        })
      ]
    };
    if (sid === data.activeSessionId) emit("runProgress", publicRunProgress(data.runProgressBySession[sid]));
  }

  function applyRunProgress(sessionId, event) {
    if (!event || (typeof event === "object" && !event.requestId && !event.request_id)) {
      // old signature: applyRunProgress(event)
      event = sessionId;
      sessionId = data.activeSessionId;
    }
    if (isInternalProgressStep(event)) return;
    const sid = String(sessionId || data.activeSessionId);
    const incomingRequestId = String(event?.requestId || event?.request_id || "");
    if (!incomingRequestId) return;

    let progress = _getRP(sid);
    if (progress.requestId && progress.phase === "orphaned") return;
    if (!progress.requestId) {
      const now = Date.now();
      progress = {
        ...defaultRunProgress,
        requestId: incomingRequestId,
        sessionId: String(event?.sessionId || event?.session_id || sid),
        phase: String(event?.runPhase || event?.run_phase || event?.phase || "running"),
        startedAt: Number(event?.runStartedAt || event?.run_started_at || now),
        anchorAt: Number(event?.anchorAt || event?.anchor_at || now),
        steps: []
      };
    }
    if (incomingRequestId !== progress.requestId) return;

    const step = cleanProgressStep(event);
    const structuredPlan = event?.structured_plan || event?.structuredPlan || null;
    const progressSnapshot = event?.progress_snapshot || event?.progressSnapshot || step.progressSnapshot || null;
    const nextPhase = terminalPhaseFromProgressEvent(step, event, progress.phase);
    const terminal = RUN_TERMINAL_PHASES.has(nextPhase);
    const stepFailed = ["failed", "blocked", "timeout"].includes(step.status);
    const eventOk = typeof event?.ok === "boolean" ? event.ok : undefined;
    progress = {
      ...progress,
      phase: nextPhase,
      ok: nextPhase === "orphaned" ? null : terminal ? Boolean(eventOk ?? !stepFailed) : progress.ok,
      finishedAt: terminal ? (progress.finishedAt || Date.now()) : progress.finishedAt,
      codexPlan: boundedJsonObject(structuredPlan) || progress.codexPlan,
      codexProgress: boundedJsonObject(progressSnapshot) || progress.codexProgress,
      steps: mergeProgressStep(progress.steps, step)
    };
    data.runProgressBySession[sid] = progress;
    if (sid === data.activeSessionId) emit("runProgress", publicRunProgress(progress));
  }

  function finishRunProgress(sessionId, requestId, ok = true) {
    if (typeof sessionId === "string" && sessionId.startsWith("run_") && !sessionId.startsWith("session_")) {
      // old signature: finishRunProgress(requestId, ok)
      ok = requestId !== false;
      requestId = sessionId;
      sessionId = data.activeSessionId;
    }
    const sid = String(sessionId || data.activeSessionId);
    const progress = _getRP(sid);
    if (!progress.requestId || String(requestId || "") !== progress.requestId) return;
    if (progress.phase === "orphaned") return;
    const now = Date.now();
    const blockingFailure = progress.codexProgress?.status === "failed"
      || progress.steps.some((step) => ["failed", "blocked", "timeout"].includes(step.status));
    const finalOk = Boolean(ok) && !blockingFailure;
    const steps = mergeProgressStep(progress.steps, cleanProgressStep({
      id: "frontend_complete",
      title: finalOk ? "收到最终回复" : "执行返回异常",
      status: finalOk ? "done" : "failed",
      summary: finalOk ? "后端已返回本轮结果" : "后端返回失败信息，请查看运行日志"
    }));
    const next = {
      ...progress,
      phase: "finished",
      ok: finalOk,
      finishedAt: now,
      steps
    };
    data.runProgressBySession[sid] = next;
    if (sid === data.activeSessionId) emit("runProgress", publicRunProgress(next));
  }

  function interruptRunProgress(sessionId, requestId, summary = "") {
    if (typeof sessionId === "string" && sessionId.startsWith("run_") && !sessionId.startsWith("session_")) {
      // old signature: interruptRunProgress(requestId, summary)
      summary = requestId || "";
      requestId = sessionId;
      sessionId = data.activeSessionId;
    }
    const sid = String(sessionId || data.activeSessionId);
    const progress = _getRP(sid);
    if (!progress.requestId || String(requestId || "") !== progress.requestId) return;
    if (progress.phase === "orphaned") return;
    const now = Date.now();
    const steps = mergeProgressStep(progress.steps, cleanProgressStep({
      id: "frontend_interrupt",
      title: "用户中断",
      status: "interrupted",
      summary: summary || "进度和上下文已保留，可继续。"
    }));
    const next = {
      ...progress,
      phase: "interrupted",
      ok: null,
      finishedAt: now,
      steps
    };
    data.runProgressBySession[sid] = next;
    if (sid === data.activeSessionId) emit("runProgress", publicRunProgress(next));
  }

  function clearRunProgress(sessionId, requestId) {
    if (typeof sessionId === "string" && sessionId.startsWith("run_") && !sessionId.startsWith("session_")) {
      // old signature: clearRunProgress(requestId)
      requestId = sessionId;
      sessionId = data.activeSessionId;
    }
    const sid = String(sessionId || data.activeSessionId);
    if (requestId && _getRP(sid).requestId !== String(requestId)) return;
    data.runProgressBySession[sid] = { ...defaultRunProgress };
    if (sid === data.activeSessionId) emit("runProgress", publicRunProgress(defaultRunProgress));
  }

  function addMessage(role, content, error = false, options = {}) {
    role = MESSAGE_ROLES.has(String(role || "")) ? String(role) : "assistant";
    const requestedId = String(options.id || "").trim();
    if (requestedId) {
      for (const session of data.sessions) {
        const existing = session.messages.find((item) => item.id === requestedId);
        if (existing) return existing;
      }
    }
    const parsedAt = typeof options.at === "number" ? options.at : Date.parse(String(options.at || ""));
    const message = {
      id: requestedId || msgId(),
      role,
      content: String(content || "").slice(0, MESSAGE_MAX_CONTENT),
      attachments: cleanAttachments(options.attachments),
      error,
      at: Number.isFinite(parsedAt) ? parsedAt : Date.now(),
      sessionId: data.activeSessionId,
      kind: options.kind || null,
      requestId: options.requestId || null,
      meta: options.meta && typeof options.meta === "object" ? { ...options.meta } : {}
    };
    data.messages = [...data.messages, message].slice(-80);
    data.sessions = data.sessions.map((session) => {
      if (session.id !== data.activeSessionId) return session;
      const messages = [...session.messages, message].slice(-80);
      return {
        ...session,
        title: sessionTitle(messages),
        messages,
        updatedAt: message.at
      };
    });
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return message;
  }

  function streamAppend(text) {
    const messages = data.messages;
    if (!messages.length) return;
    const last = messages[messages.length - 1];
    if (last.role !== "assistant") return;
    last.content = String(last.content + text).slice(0, MESSAGE_MAX_CONTENT);
    data.sessions = data.sessions.map((session) => {
      if (session.id !== data.activeSessionId) return session;
      const sm = session.messages;
      if (sm.length) sm[sm.length - 1] = { ...last };
      return { ...session, updatedAt: Date.now() };
    });
    emit("messages", [...data.messages]);
  }

  function replaceLastMessage(text) {
    const messages = data.messages;
    if (!messages.length) return;
    const last = messages[messages.length - 1];
    if (last.role !== "assistant") return;
    last.content = String(text || "").slice(0, MESSAGE_MAX_CONTENT);
    data.sessions = data.sessions.map((session) => {
      if (session.id !== data.activeSessionId) return session;
      const sm = session.messages;
      if (sm.length) sm[sm.length - 1] = { ...last };
      return { ...session, updatedAt: Date.now() };
    });
    emit("messages", [...data.messages]);
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
  }

  function _findMsg(sessionId, messageId) {
    const session = data.sessions.find(s => s.id === sessionId);
    if (!session) return null;
    return session.messages.find(m => m.id === messageId) || null;
  }

  function streamAppendById({ sessionId, messageId, delta }) {
    if (!delta) return;
    const msg = _findMsg(sessionId, messageId);
    if (!msg || msg.role !== "assistant") return;
    msg.content = String(msg.content + delta).slice(0, MESSAGE_MAX_CONTENT);
    // 同步到 data.messages
    const idx = data.messages.findIndex(m => m.id === messageId);
    if (idx >= 0) data.messages[idx] = { ...msg };
    data.sessions = data.sessions.map(s => {
      if (s.id !== sessionId) return s;
      const sm = s.messages;
      const mi = sm.findIndex(m => m.id === messageId);
      if (mi >= 0) sm[mi] = { ...msg };
      return { ...s, updatedAt: Date.now() };
    });
    emit("messages", [...data.messages]);
    // 防抖持久化：300ms 内的连续 chunk 只存最后一次，per-session
    if (data._streamSaveTimers[sessionId]) window.clearTimeout(data._streamSaveTimers[sessionId]);
    data._streamSaveTimers[sessionId] = window.setTimeout(() => {
      data._streamSaveTimers[sessionId] = null;
      saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    }, 300);
  }

  function replaceMessageById({ sessionId, messageId, text, error = false, attachments, meta }) {
    const msg = _findMsg(sessionId, messageId);
    if (!msg || msg.role !== "assistant") return;
    msg.content = String(text || "").slice(0, MESSAGE_MAX_CONTENT);
    if (typeof error !== "undefined") msg.error = Boolean(error);
    if (typeof attachments !== "undefined") msg.attachments = cleanAttachments(attachments);
    if (meta && typeof meta === "object") msg.meta = { ...(msg.meta || {}), ...meta };
    const idx = data.messages.findIndex(m => m.id === messageId);
    if (idx >= 0) data.messages[idx] = { ...msg };
    data.sessions = data.sessions.map(s => {
      if (s.id !== sessionId) return s;
      const sm = s.messages;
      const mi = sm.findIndex(m => m.id === messageId);
      if (mi >= 0) sm[mi] = { ...msg };
      return { ...s, updatedAt: Date.now() };
    });
    emit("messages", [...data.messages]);
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
  }

  function clearMessages() {
    data.messages = [];
    resetRunState();
    data.sessions = data.sessions.map((session) => session.id === data.activeSessionId
      ? { ...session, title: "新对话", messages: [], updatedAt: Date.now() }
      : session);
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    emit("messages", []);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
  }

  function startNewConversation(options = {}) {
    const force = Boolean(options.force);
    const current = activeSession(data.sessions, data.activeSessionId);
    if (!force && !current.messages.length) {
      resetRunState();
      emit("messages", [...data.messages]);
      emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
      return data.activeSessionId;
    }
    const session = createSession([]);
    data.sessions = [session, ...data.sessions].slice(0, 30);
    data.activeSessionId = session.id;
    data.messages = [];
    resetRunState();
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    emit("messages", []);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return data.activeSessionId;
  }

  function switchConversation(sessionId) {
    const next = data.sessions.find((session) => session.id === sessionId);
    if (!next) return;
    data.activeSessionId = next.id;
    data.messages = cleanMessages(next.messages, next.id);
    // 不再调用 resetRunState() —— 旧 session 的运行状态保留在 per-session 字典中
    // 新 session 的运行状态从 runProgressBySession[next.id] / lastRunBySession[next.id] 读取
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    // 通知 runProgress 状态切换（让呼吸灯等 UI 组件响应）
    emit("runProgress", publicRunProgress(_getRP(data.activeSessionId)));
    emit("run", { ..._getLR(data.activeSessionId) });
    emit("busy", _getBZ(data.activeSessionId));
  }

  function deleteConversation(sessionId) {
    const targetId = String(sessionId || "");
    const target = data.sessions.find((session) => session.id === targetId);
    if (!target) return data.activeSessionId;
    // 使用 per-session busy 检查：阻止删除正在运行的活跃会话
    if (_getBZ(target.id) && target.id === data.activeSessionId) return data.activeSessionId;

    const wasActive = target.id === data.activeSessionId;
    let sessions = data.sessions.filter((session) => session.id !== target.id);
    if (!sessions.length) sessions = [createSession([])];

    // 清理被删除 session 的 per-session 状态
    delete data.busyBySession[targetId];
    delete data.lastRunBySession[targetId];
    delete data.runProgressBySession[targetId];
    if (data._streamSaveTimers[targetId]) {
      window.clearTimeout(data._streamSaveTimers[targetId]);
      delete data._streamSaveTimers[targetId];
    }

    if (wasActive) {
      const next = [...sessions].sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))[0] || sessions[0];
      data.activeSessionId = next.id;
      data.messages = [...next.messages];
      resetRunState();
    } else {
      const active = activeSession(sessions, data.activeSessionId);
      data.activeSessionId = active.id;
      data.messages = [...active.messages];
    }

    data.sessions = sessions;
    saveConversationState(data.sessions, data.activeSessionId, data.lifeId);
    emit("messages", [...data.messages]);
    emit("sessions", publicSessionList(data.sessions, data.activeSessionId));
    return data.activeSessionId;
  }

  return {
    snapshot,
    on,
    setSettings,
    setLifeScope,
    setActivePage,
    setActiveSkillCategory,
    toggleSelectedSkill,
    clearSelectedSkills,
    setBusy,
    setLastRun,
    setKernelStatus,
    setRuntimeStatus,
    setBackendConfig,
    startRunProgress,
    applyRunProgress,
    finishRunProgress,
    interruptRunProgress,
    clearRunProgress,
    addMessage,
    streamAppend,
    streamAppendById,
    replaceLastMessage,
    replaceMessageById,
    clearMessages,
    startNewConversation,
    switchConversation,
    deleteConversation
  };
}
