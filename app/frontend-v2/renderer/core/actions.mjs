import { backendReply, extractStatusPayload, humanizeBackendError, parseBackendSteps, runtimeStatusText, spokenBackendText } from "./formatters.mjs";

const THEME_STYLES = new Set(["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"]);

export function classifyRunInput(value) {
  const text = String(value || "").trim();
  const patterns = [
    /^\/(?:guide|纠偏)(?:\s+|[:：]\s*)/i,
    /^(?:运行中)?纠偏\s*[:：]\s*/i,
    /^【(?:运行中)?纠偏】\s*/i,
  ];
  for (const pattern of patterns) {
    if (!pattern.test(text)) continue;
    return { kind: "guide", text: text.replace(pattern, "").trim() };
  }
  return { kind: "queue", text };
}

export function shouldUseDirectLearning(message) {
  // Ordinary chat always reaches the model; learning has an explicit UI action.
  return false;
}

function parseReplyPayload(value) {
  if (value && typeof value === "object") return value;
  try {
    const parsed = JSON.parse(String(value || ""));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function claimsCompleted(result, displayText) {
  if (result?.ok === false || ["partial", "failed", "reconcile_required", "unknown", "incident"].includes(String(result?.phase || ""))) return false;
  if (typeof result?.task_completed === "boolean") return result.task_completed;
  const payload = parseReplyPayload(result?.stdout);
  const status = String(
    result?.simple_chain_status
    || payload?.simple_chain_status
    || payload?.zhuangtai
    || ""
  ).trim().toLowerCase();
  return ["complete", "completed", "wancheng", "完成"].includes(status);
}

export function requiresDeterministicWebQa(rootGoal, projectRoot) {
  return false;
}

function webQaSummary(result) {
  if (!result) return "";
  if (result.ok) {
    const evidence = result.evidence || {};
    return `【确定性 Web 验收通过】页面可见字符 ${Number(evidence.bodyChars || 0)}，可见按钮 ${Number(evidence.visibleButtonCount || 0)}，已绑定 ${Number(evidence.boundButtonCount || 0)}，本地资源 ${Number(evidence.localAssetCount || 0)}。`;
  }
  const issues = Array.isArray(result.issues) && result.issues.length
    ? result.issues.slice(0, 8).join("；")
    : (result.error || "未知 Web 验收失败");
  return `【确定性 Web 验收未通过】${issues}`;
}

export function autoContinuationDecision({ result, displayText, sendMode, runOptions }) {
  const payload = parseReplyPayload(result?.stdout);
  const status = String(result?.simple_chain_status || payload?.simple_chain_status || result?.phase || "").toLowerCase();
  const requiresUser = ["awaiting_user", "confirm_pending", "blocked"].includes(status);
  const recoverable = ["incomplete", "needs_continue", "force_stopped", "interrupted"].includes(status);
  return {
    shouldContinue: false, requiresUser, recoverable,
    thinCompletion: false, recoverableFailure: false, deterministicVerificationFailure: false,
    leaseBatchMismatch: false, verificationDebt: false, responseRequestsContinue: false,
    checkpointEvidence: "", reason: recoverable ? "checkpoint" : "",
    nextState: { ...(runOptions?.autoContinueState || {}) },
    stopReason: requiresUser ? "requires_user" : ""
  };
}

export function autoContinuationPrompt(decision) {
  return "请结合保存的检查点和实际工具结果继续当前任务。";
}

export function autoContinuationStopNotice(decision) {
  // FE-07: when the GF gate forbids continuation, the user must see why and
  // what to do next instead of an empty stop notice.
  if (!decision) return "";
  if (decision.requiresUser) {
    return "\n\n任务已暂停：需要你决定或授权后才能继续。请处理上面的询问或说明下一步。";
  }
  if (decision.stopReason === "not_recoverable") {
    return "\n\n任务已停止：当前状态不可自动恢复，未完成项已如实保留。如需继续，请重新提交任务或说明新的要求。";
  }
  return "";
}

export function inferActiveProjectRoot(message = "", workspace = "", rootGoal = "", explicitRoot = "", evidenceText = "") {
  const base = String(workspace || "").trim().replace(/[\\/]+$/, "");
  const explicit = String(explicitRoot || "").trim();
  if (!explicit) return "";
  if (/(?:^|[\\/])\.\.(?:[\\/]|$)/.test(explicit)) return "";
  const normalized = explicit.replace(/\\/g, "/").toLowerCase();
  const root = base.replace(/\\/g, "/").toLowerCase();
  return !root || normalized === root || normalized.startsWith(`${root}/`) ? explicit : "";
}

function makeRequestId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// 确认卡片：负载经 JSON 编码进消息 content（state.addMessage 只保留固定字段），
// conversation-panel 按 kind === "confirm" 解码渲染。前缀即机器标记，不展示给用户。
const CONFIRM_CARD_PREFIX = "[tiangong-confirm-card]";

export function encodeConfirmCardContent(payload = {}) {
  return `${CONFIRM_CARD_PREFIX}${JSON.stringify(payload)}`;
}

export function decodeConfirmCardContent(content) {
  const text = String(content || "");
  if (!text.startsWith(CONFIRM_CARD_PREFIX)) return null;
  try {
    const payload = JSON.parse(text.slice(CONFIRM_CARD_PREFIX.length));
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

export const CONFIRM_DECISION_LABELS = {
  once: "本次允许",
  session: "本会话允许",
  always: "总是允许",
  deny: "拒绝"
};

function extractFinalReplyText(value) {
  let data = value;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return data;
    }
  }
  return data?.huifu
    || data?.data?.huifu
    || data?.data?.text
    || data?.data?.message
    || data?.reply
    || data?.text
    || data?.message
    || "";
}


function normalizeModeValue(value) {
  const mode = String(value || "").trim().toLowerCase();
  return mode === "chat" || mode === "work" || mode === "auto" ? mode : "";
}



export function inferSendMode(message, settings = {}, selectedSkills = [], runOptions = {}) {
  const explicit = normalizeModeValue(runOptions.mode || runOptions.workMode);
  if (explicit) return explicit;
  return normalizeModeValue(settings.mode) || "auto";
}

function isContinuationRequest(value) {
  return false;
}

function findParentGoal(messages) {
  const rows = Array.isArray(messages) ? messages : [];
  const latest = [...rows].reverse().find(item => item?.role === "user");
  return String(latest?.content || latest?.text || "").trim();
}

export function normalizeBackendDeliveryIntent(value) {
  return String(value || "");
}

let terminalRunListenerInstalled = false;

export function createActions({ runtime, state, kernel = null }) {
  const pendingUserTurns = [];
  let drainingPendingTurns = false;

  function discardPendingUserTurns(sessionId) {
    const targetId = String(sessionId || "");
    if (!targetId) return 0;
    let removed = 0;
    for (let index = pendingUserTurns.length - 1; index >= 0; index -= 1) {
      if (String(pendingUserTurns[index]?.sessionId || "") !== targetId) continue;
      pendingUserTurns.splice(index, 1);
      removed += 1;
    }
    return removed;
  }

  function ensureFinalMessage(detail = {}) {
    const run = detail.run && typeof detail.run === "object" ? detail.run : {};
    const presentationId = String(
      detail.presentationRequestId
      || detail.requestId
      || run.presentation_request_id
      || run.request_id
      || run.requestId
      || ""
    );
    const gatewayId = String(
      detail.gatewayRequestId
      || detail.gateway_request_id
      || run.gateway_request_id
      || run.gatewayRequestId
      || ""
    );
    const runId = gatewayId || presentationId;
    let finalText = String(detail.text || run.final_response || run.reply || "");
    if (!runId || !finalText) return;
    try {
      const parsed = JSON.parse(finalText);
      if (parsed && typeof parsed === "object" && typeof parsed.huifu === "string") {
        finalText = parsed.huifu;
      }
    } catch (_error) {
      // final_response 是纯文本时直接使用。
    }
    if (!finalText) return;
    const sessionId = detail.sessionId || state.snapshot().activeSessionId;
    const messages = state.snapshot().messages || [];
    const normalized = (value) => String(value || "").trim();
    const prefix = finalText.length > 60 ? finalText.slice(0, 60) : "";
    // 同一轮已经有一条相同自然收尾 → 不再重复新增。
    const exists = messages.some((item) => item.role === "assistant" && (
      String(item.meta?.runId || "") === runId
      || (presentationId && String(item.requestId || "") === presentationId && normalized(item.content || "") === normalized(finalText))
      || (presentationId && prefix && String(item.requestId || "") === presentationId && String(item.content || "").startsWith(prefix))
    ));
    if (exists) return;
    const origin = String(detail.origin || run.origin || "model");
    const attachments = Array.isArray(detail.attachments)
      ? detail.attachments
      : (Array.isArray(run.generated_attachments) ? run.generated_attachments : []);
    // 优先填充本轮占位消息（工作卡），保证“一轮只出一条终局”。
    const placeholder = presentationId
      ? messages.find((item) => item.role === "assistant" && String(item.requestId || "") === presentationId)
      : null;
    if (placeholder) {
      state.replaceMessageById({
        sessionId: placeholder.sessionId || sessionId,
        messageId: placeholder.id,
        text: finalText,
        error: false,
        attachments,
        meta: { origin, runId, gatewayRequestId: gatewayId }
      });
      return;
    }
    state.addMessage("assistant", finalText, false, {
      id: `final-${runId}`,
      requestId: presentationId || runId,
      meta: { origin, runId },
      attachments,
    });
  }

  // 当前轮是否已有一条由终局事件交付的 assistant 收尾消息。
  // finalText 非空：内容相同即视为已交付；finalText 为空：只有带终局标记且
  // 有正文的消息才算已交付，避免把“正在写入…”阶段文本误判为最终回复。
  function findTurnFinalMessage(requestId, finalText) {
    const messages = state.snapshot().messages || [];
    const text = String(finalText || "").trim();
    const prefix = text.length > 60 ? text.slice(0, 60) : "";
    for (const item of messages) {
      if (item.role !== "assistant") continue;
      const sameTurn = String(item.requestId || "") === String(requestId || "");
      const delivered = Boolean(item.meta?.runId || item.meta?.gatewayRequestId);
      const content = String(item.content || "").trim();
      const textMatch = text
        ? content === text || (prefix && content.startsWith(prefix))
        : Boolean(content);
      if (sameTurn && delivered && textMatch) return item;
      if (text && sameTurn && textMatch) return item;
    }
    return null;
  }

  function mergeTerminalIntoLastRun(next) {
    const previous = state.snapshot().lastRun || {};
    return {
      ...(next || {}),
      terminal: (next?.terminal) || previous.terminal || null,
      simple_chain_status: String(next?.simple_chain_status || previous.simple_chain_status || ""),
    };
  }

  if (!terminalRunListenerInstalled && typeof window !== "undefined") {
    terminalRunListenerInstalled = true;
    window.addEventListener("tiangong-terminal-run", (event) => {
      try {
        const detail = event?.detail || {};
        ensureFinalMessage(detail);
        const run = detail.run && typeof detail.run === "object" ? detail.run : {};
        if (run && (run.final_response || run.terminal || run.simple_chain_status)) {
          state.setLastRun(detail.sessionId || state.snapshot().activeSessionId, {
            ...run,
            requestId: detail.requestId || String(run.request_id || run.requestId || ""),
            sessionId: detail.sessionId,
            phase: String(run.status || run.phase || "finished"),
            terminal: run.terminal || null,
            simple_chain_status: String(run.simple_chain_status || ""),
            finishedAt: Date.now(),
          });
        }
      } catch (_error) {
        // 终局对账/状态合并失败不影响主流程。
      }
    });
  }

  if (runtime?.onRunStep) {
    runtime.onRunStep((event) => state.applyRunProgress(event));
  }
  if (runtime?.onLearningMessage) {
    runtime.onLearningMessage((event) => {
      const role = event?.role === "user" ? "user" : "assistant";
      const content = String(event?.content || "").trim();
      if (!content) return;
      state.addMessage(role, content, Boolean(event?.error));
      refreshStatus().catch(() => {});
    });
  }

  async function loadSettings() {
    if (!runtime?.getSettings) return;
    const next = await runtime.getSettings();
    state.setSettings(next);
  }

  function currentThemeStyle() {
    const theme = String(state.snapshot().settings.themeStyle || "").trim();
    return THEME_STYLES.has(theme) ? theme : "ink_teal";
  }

  function normalizeSettingsPatch(next) {
    const patch = { ...(next || {}) };
    if (Object.prototype.hasOwnProperty.call(patch, "themeStyle")) {
      const theme = String(patch.themeStyle || "").trim();
      patch.themeStyle = THEME_STYLES.has(theme) ? theme : currentThemeStyle();
    }
    return patch;
  }

  async function setMode(mode) {
    try {
      await hotSwitchSettings({ mode }, { refreshConfig: false });
    } catch {
      state.setSettings({ mode });
    }
  }

  function setActivePage(page) {
    state.setActivePage(page);
  }

  async function detachActiveRun(reason = "new_conversation") {
    const snap = state.snapshot();
    const requestId = snap.runProgress.requestId || snap.lastRun.requestId || "";
    const running = snap.busy || snap.runProgress.phase === "running" || snap.lastRun.phase === "running";
    if (requestId && running && runtime?.cancel) {
      try {
        await runtime.cancel({ requestId, reason });
      } catch {
        // Starting a fresh conversation must not be blocked by a stale run.
      }
    }
    state.setBusy(false);
    state.clearRunProgress();
    state.setLastRun({ phase: "idle", ok: true, mode: "auto" });
  }

  async function startNewConversation() {
    await detachActiveRun("new_conversation");
    state.startNewConversation({ force: true });
    state.setActivePage("chat");
  }

  function switchConversation(sessionId) {
    state.switchConversation(sessionId);
    state.setActivePage("chat");
    queueMicrotask(() => {
      void drainPendingUserTurns();
    });
  }

  function deleteConversation(sessionId) {
    const targetId = String(sessionId || "");
    discardPendingUserTurns(targetId);
    if (targetId) {
      runtime?.conversationEvents?.({
        action: "delete",
        sessionId: targetId,
        reason: "user_deleted_conversation"
      }).catch(() => {});
    }
    state.deleteConversation(sessionId);
    state.setActivePage("chat");
  }

  function setActiveSkillCategory(category) {
    state.setActiveSkillCategory(category);
  }

  function toggleSelectedSkill(skill) {
    return state.toggleSelectedSkill(skill);
  }

  function clearSelectedSkills() {
    state.clearSelectedSkills();
  }

  async function saveSettings(next) {
    const normalized = normalizeSettingsPatch(next);
    const explicitTheme = Object.prototype.hasOwnProperty.call(normalized, "themeStyle");
    const previousSettings = state.snapshot().settings;
    state.setSettings(normalized);
    try {
      const saved = await runtime?.setSettings?.(normalized);
      const merged = saved
        ? (explicitTheme ? { ...saved, themeStyle: normalized.themeStyle } : saved)
        : state.snapshot().settings;
      if (saved) state.setSettings(merged);
      return merged;
    } catch (error) {
      state.setSettings(previousSettings);
      throw error;
    }
  }

  async function deleteProviderApiKey(provider) {
    if (!runtime?.deleteProviderApiKey) throw new Error("本机安全凭据删除通道不可用");
    const result = await runtime.deleteProviderApiKey(provider);
    await loadSettings();
    await refreshConfig();
    return result;
  }

  async function chooseWorkspace() {
    if (!runtime?.chooseWorkspace) return;
    const next = await runtime.chooseWorkspace();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseWorkspaceRoot(root) {
    if (!runtime?.chooseWorkspaceRoot) return state.snapshot().settings;
    const next = await runtime.chooseWorkspaceRoot(root);
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseStorageRoot() {
    if (!runtime?.chooseStorageRoot) return state.snapshot().settings;
    const next = await runtime.chooseStorageRoot();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseKnowledgeRoot() {
    if (!runtime?.chooseKnowledgeRoot) return state.snapshot().settings;
    const next = await runtime.chooseKnowledgeRoot();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function choosePersonaAvatar() {
    if (!runtime?.choosePersonaAvatar) return state.snapshot().settings;
    const next = await runtime.choosePersonaAvatar();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseVoiceSample() {
    if (!runtime?.chooseVoiceSample) return state.snapshot().settings;
    const next = await runtime.chooseVoiceSample();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function chooseUserAvatar() {
    if (!runtime?.chooseUserAvatar) return state.snapshot().settings;
    const next = await runtime.chooseUserAvatar();
    if (next) state.setSettings(next);
    return state.snapshot().settings;
  }

  async function openWorkspace() {
    const { settings } = state.snapshot();
    if (runtime?.openPath && settings.workspace) {
      await runtime.openPath(settings.workspace);
    }
  }

  async function openPath(targetPath) {
    if (runtime?.openPath && targetPath) {
      return runtime.openPath(targetPath);
    }
    return { ok: false, error: "open_path_unavailable" };
  }

  async function openArtifact(item) {
    if (runtime?.openArtifact && item?.artifact_schema === "tiangong.gateway.artifact-card.v1") {
      return runtime.openArtifact(item);
    }
    return { ok: false, error: "artifact_open_unavailable" };
  }

  async function saveTargetAs(target, payload = {}) {
    if (runtime?.saveTargetAs && target) {
      return runtime.saveTargetAs(target, payload);
    }
    return { ok: false, error: "save_target_unavailable" };
  }

  async function listDailyLogs() {
    if (!runtime?.listDailyLogs) return { ok: false, error: "日志桥接不可用", logs: [] };
    return runtime.listDailyLogs();
  }

  async function listSkills() {
    if (!runtime?.skillsList) {
      return { ok: false, error: "技能桥接不可用", categories: [], abilities: [], summary: {} };
    }
    return runtime.skillsList();
  }

  async function deleteSkill(ability) {
    if (!runtime?.deleteSkill) {
      return { ok: false, error: "技能删除桥接不可用" };
    }
    const payload = ability && typeof ability === "object"
      ? { ...ability }
      : { ability_id: ability };
    return runtime.deleteSkill({ ...payload, actor: "user" });
  }

  async function activateSkill(ability) {
    if (!runtime?.activateSkill) {
      return { ok: false, error: "技能激活桥接不可用" };
    }
    const payload = ability && typeof ability === "object"
      ? { ...ability }
      : { artifact_id: ability };
    return runtime.activateSkill({ ...payload, actor: "user" });
  }

  async function openDailyLog(date) {
    if (!runtime?.openDailyLog) return { ok: false, error: "日志打开桥接不可用" };
    const payload = typeof date === "string" ? { date } : (date || {});
    return runtime.openDailyLog(payload);
  }

  async function deleteDailyLog(date) {
    if (!runtime?.deleteDailyLog) return { ok: false, error: "日志删除桥接不可用", logs: [] };
    const payload = typeof date === "string" ? { date } : (date || {});
    return runtime.deleteDailyLog(payload);
  }

  function knowledgePayload(extra = {}) {
    const { settings } = state.snapshot();
    return { workspace: settings.workspace, knowledgeRoot: settings.knowledgeRoot, ...extra };
  }

  function lifecyclePayload(extra = {}) {
    const { settings } = state.snapshot();
    return { workspace: settings.workspace, ...extra };
  }

  async function listKnowledge() {
    if (!runtime?.knowledgeList) return { ok: false, error: "知识库桥接不可用", documents: [] };
    return runtime.knowledgeList(knowledgePayload());
  }

  async function importKnowledgeFiles() {
    if (!runtime?.chooseKnowledgeFiles) return { ok: false, error: "知识库导入不可用", documents: [] };
    return runtime.chooseKnowledgeFiles(knowledgePayload());
  }

  function chatAttachmentPayload(extra = {}) {
    const snapshot = state.snapshot();
    return knowledgePayload({
      session_id: String(snapshot.activeSessionId || ""),
      activeSessionId: String(snapshot.activeSessionId || ""),
      ...extra,
    });
  }

  async function chooseChatFiles() {
    if (!runtime?.chooseChatFiles) return { ok: false, error: "会话文件上传不可用", attachments: [] };
    return runtime.chooseChatFiles(chatAttachmentPayload());
  }

  async function pasteChatFiles(payload = {}) {
    if (!runtime?.pasteChatFiles) return { ok: false, error: "粘贴文件上传不可用", attachments: [] };
    return runtime.pasteChatFiles(chatAttachmentPayload(payload));
  }

  async function queryKnowledge(documentId, query, topK = 6) {
    if (!runtime?.knowledgeQuery) return { ok: false, error: "知识库查询不可用" };
    return runtime.knowledgeQuery(knowledgePayload({ document_id: documentId, query, top_k: topK }));
  }

  async function searchKnowledge(query, topK = 8) {
    if (!runtime?.knowledgeSearch) return { ok: false, error: "知识库搜索不可用", cards: [] };
    const payload = typeof query === "object"
      ? query
      : { query, top_k: topK };
    return runtime.knowledgeSearch(knowledgePayload(payload));
  }

  async function organizeKnowledge(payload = {}) {
    if (!runtime?.knowledgeOrganize) return { ok: false, error: "知识库整理不可用", documents: [] };
    return runtime.knowledgeOrganize(knowledgePayload(payload));
  }

  async function exportKnowledge(documentId, format = "md") {
    if (!runtime?.knowledgeExport) return { ok: false, error: "知识库导出不可用" };
    return runtime.knowledgeExport(knowledgePayload({ document_id: documentId, format }));
  }

  async function removeKnowledge(documentId) {
    if (!runtime?.knowledgeRemove) return { ok: false, error: "知识库删除不可用", documents: [] };
    return runtime.knowledgeRemove(knowledgePayload({ document_id: documentId }));
  }

  async function confirmLifecycleUpdate(updateId) {
    if (!runtime?.confirmLifecycleUpdate) return { ok: false, error: "生命周期确认桥接不可用" };
    const result = await runtime.confirmLifecycleUpdate(lifecyclePayload({ id: updateId, ticketId: updateId }));
    await refreshStatus();
    return result;
  }

  // ── 确认卡片（policy/confirm 通道）────────────────────────────
  function confirmCardMessageId(confirmId) {
    return `confirm_${String(confirmId || "").trim()}`;
  }

  function registerConfirmCard(data = {}, originalMessage = "") {
    const confirmId = String(data?.confirm_id || data?.confirmId || "").trim();
    if (!confirmId) return null;
    const payload = {
      confirm_id: confirmId,
      action: String(data?.action || "").trim(),
      target: String(data?.target || "").trim(),
      summary: String(data?.summary || "").trim(),
      risk: String(data?.risk || "").trim(),
      tool: String(data?.tool || data?.name || "").trim(),
      original: String(originalMessage || "").trim(),
      status: "pending",
      decision: "",
      note: "",
      expires_at_ms: Number(data?.expires_at_ms || 0) || 0,
      at: Date.now()
    };
    // 固定 id：同一 confirm_id 的事件（轮询 + 终态兜底 + SSE）只生成一张卡
    return state.addMessage("assistant", encodeConfirmCardContent(payload), false, {
      kind: "confirm",
      id: confirmCardMessageId(confirmId)
    });
  }

  function updateConfirmCard(confirmId, patch = {}) {
    const snapshot = state.snapshot();
    const messageId = confirmCardMessageId(confirmId);
    const existing = snapshot.messages.find((item) => String(item?.id || "") === messageId);
    if (!existing) return;
    const payload = { ...(decodeConfirmCardContent(existing.content) || { confirm_id: String(confirmId || "") }), ...patch };
    state.replaceMessageById({
      sessionId: String(existing.sessionId || snapshot.activeSessionId || ""),
      messageId,
      text: encodeConfirmCardContent(payload),
      error: false
    });
  }

  // G3 确认退役（草案 §4.2 第 6 步）：policy/confirm 通道已退役。
  // 不再调用 runtime.confirmPolicy（后端固定 410），点击只在卡片上给出
  // “确认通道已退役”的只读提示；历史卡一律只读，绝不发起新确认、绝不重放原指令。
  async function resolvePolicyConfirmation({ confirmId = "" } = {}) {
    const id = String(confirmId || "").trim();
    if (id) {
      updateConfirmCard(id, {
        status: "retired",
        note: "确认通道已退役，仅供查阅。请直接重新发送指令，由新的安全流程处理。"
      });
    }
    return { ok: false, error: "POLICY_CONFIRMATION_RETIRED", retired: true };
  }

  async function denyLifecycleUpdate(updateId) {
    if (!runtime?.denyLifecycleUpdate) return { ok: false, error: "生命周期拒绝桥接不可用" };
    const result = await runtime.denyLifecycleUpdate(lifecyclePayload({ id: updateId, ticketId: updateId }));
    await refreshStatus();
    return result;
  }

  async function deleteLearningExperience(experienceId) {
    if (!runtime?.deleteLearningExperience) return { ok: false, error: "学习池删除桥接不可用" };
    const result = await runtime.deleteLearningExperience(lifecyclePayload({ id: experienceId, experienceId }));
    await refreshStatus();
    return result;
  }

  async function refreshStatus() {
    if (!runtime?.status) {
      state.setRuntimeStatus({ text: "桌面桥接不可用", loading: false, ok: false, payload: null });
      return { ok: false, stderr: "桌面桥接不可用" };
    }

    state.setRuntimeStatus({ text: "检查中", loading: true, ok: null });
    const result = await runtime.status();
    state.setRuntimeStatus({
      text: runtimeStatusText(result),
      loading: false,
      ok: Boolean(result.ok),
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      code: result.code ?? "",
      payload: result.ok ? extractStatusPayload(result.stdout || "") : null
    });
    return result;
  }

  async function refreshConfig() {
    if (!runtime?.config) {
      state.setBackendConfig({ loading: false, ok: false, stderr: "桌面桥接不可用" });
      return { ok: false, stderr: "桌面桥接不可用" };
    }

    state.setBackendConfig({ loading: true, ok: null, stdout: "", stderr: "", code: "" });
    const result = await runtime.config();
    state.setBackendConfig({
      loading: false,
      ok: Boolean(result.ok),
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      code: result.code ?? ""
    });
    return result;
  }

  async function messageChannelStatus() {
    if (!runtime?.messageChannelStatus) {
      return { ok: false, error: "消息通道桥接不可用", channels: {} };
    }
    return runtime.messageChannelStatus();
  }

  async function connectMessageChannel(payload = {}) {
    if (!runtime?.connectMessageChannel) {
      return { ok: false, error: "消息通道桥接不可用" };
    }
    return runtime.connectMessageChannel(payload);
  }

  async function gatewayLinksStatus() {
    if (!runtime?.gatewayLinksStatus) {
      return { ok: false, error: "gateway_links_unavailable", settings: {}, links: {} };
    }
    return runtime.gatewayLinksStatus();
  }

  async function saveGatewayLinks(payload = {}) {
    if (!runtime?.saveGatewayLinks) {
      return { ok: false, error: "gateway_links_unavailable", settings: {}, links: {} };
    }
    return runtime.saveGatewayLinks(payload);
  }

  async function gatewayLinksAction(payload = {}) {
    if (!runtime?.gatewayLinksAction) {
      return { ok: false, error: "gateway_links_action_unavailable" };
    }
    return runtime.gatewayLinksAction(payload);
  }

  async function hotSwitchSettings(next, options = {}) {
    const saved = await saveSettings(next);
    let statusResult;
    let configResult = null;
    try {
      statusResult = await refreshStatus();
    } catch (error) {
      statusResult = { ok: false, stderr: error?.message || String(error) };
      state.setRuntimeStatus({ text: statusResult.stderr, loading: false, ok: false, stderr: statusResult.stderr, payload: null });
    }
    const shouldRefreshConfig = options.refreshConfig !== false;
    if (shouldRefreshConfig) {
      try {
        configResult = await refreshConfig();
      } catch (error) {
        configResult = { ok: false, stderr: error?.message || String(error) };
        state.setBackendConfig({ loading: false, ok: false, stderr: configResult.stderr });
      }
    }
    return {
      saved,
      statusResult,
      configResult,
      ok: Boolean(statusResult?.ok) && (!shouldRefreshConfig || Boolean(configResult?.ok))
    };
  }

  async function cancelRun() {
    const snap = state.snapshot();
    const requestId = snap.runProgress.requestId || snap.lastRun.requestId || "";
    if (!requestId || !runtime?.cancel) return { ok: false, error: "cancel_unavailable" };
    const result = await runtime.cancel({ requestId });
    if (result?.ok || result?.interrupted || result?.canceled) {
      const summary = result.summary || "已中断。本次进度和上下文已保留，后续可以继续。";
      state.interruptRunProgress(requestId, summary);
      state.setLastRun({
        ...snap.lastRun,
        ...result,
        requestId,
        phase: "interrupted",
        ok: null,
        finishedAt: Date.now()
      });
      state.setBusy(false);
    }
    return result;
  }

  function enqueueUserTurn(message, attachments = [], runOptions = {}) {
    const text = String(message || "").trim();
    const cleanAttachments = Array.isArray(attachments)
      ? attachments.slice()
      : [];
    if (!text && !cleanAttachments.length) {
      return { ok: false, error: "empty_queued_turn" };
    }
    const snapshot = state.snapshot();
    const sessionId = String(snapshot.activeSessionId || "");
    const userMessage = state.addMessage(
      "user",
      text || "请阅读我上传的文件。",
      false,
      { attachments: cleanAttachments }
    );
    pendingUserTurns.push({
      sessionId,
      messageId: userMessage.id,
      text: text || "请阅读我上传的文件。",
      attachments: cleanAttachments,
      runOptions: { ...runOptions },
    });
    const position = pendingUserTurns.filter((item) => item.sessionId === sessionId).length;
    return { ok: true, queued: true, position, sessionId, messageId: userMessage.id };
  }

  async function drainPendingUserTurns() {
    if (drainingPendingTurns) return;
    drainingPendingTurns = true;
    try {
      while (pendingUserTurns.length) {
        const snapshot = state.snapshot();
        if (snapshot.busy) break;
        const activeSessionId = String(snapshot.activeSessionId || "");
        const nextIndex = pendingUserTurns.findIndex(
          (item) => String(item?.sessionId || "") === activeSessionId
        );
        if (nextIndex < 0) break;
        const [next] = pendingUserTurns.splice(nextIndex, 1);
        await sendMessage(next.text, next.attachments, {
          ...next.runOptions,
          __dequeuedTurn: true,
          __queuedMessageId: next.messageId,
          useStream: next.runOptions.useStream !== false,
        });
      }
    } finally {
      drainingPendingTurns = false;
    }
  }

  async function handleRunInput(text, attachments = []) {
    const parsed = classifyRunInput(text);
    const snapshot = state.snapshot();
    if (!snapshot.busy) {
      // With no active run, "纠偏：" is ordinary user text rather than an
      // instruction to a non-existent request. Preserve it verbatim.
      return sendMessage(String(text || "").trim(), attachments, {
        useStream: true,
        useKnowledgeReference: true,
      });
    }
    if (parsed.kind === "guide") {
      if (!parsed.text) return { ok: false, error: "纠偏内容不能为空" };
      if (Array.isArray(attachments) && attachments.length) {
        return { ok: false, error: "纠偏不接收附件；请作为普通消息排队发送" };
      }
      const result = await guideRun(parsed.text);
      return { ...(result || {}), guided: true };
    }
    return enqueueUserTurn(parsed.text, attachments, {
      useStream: true,
      useKnowledgeReference: true,
    });
  }

  async function guideRun(text) {
    const message = String(text || "").trim();
    const snap = state.snapshot();
    const requestId = snap.runProgress.requestId || snap.lastRun.requestId || "";
    if (!message) return { ok: false, error: "empty_guidance" };
    if (!requestId || !runtime?.guide) return { ok: false, error: "guide_unavailable" };
    state.addMessage("user", `【运行中纠偏】${message}`, false);
    const result = await runtime.guide({ requestId, message });
    if (!result?.ok) {
      state.addMessage("assistant", result?.error || "纠偏发送失败。", true);
    }
    return result;
  }

  function recentBackendAttachments(attachments) {
    return Array.isArray(attachments)
      ? attachments.map((item) => ({
          name: String(item?.name || item?.file_name || ""),
          path: String(item?.path || ""),
          ext: String(item?.ext || "").toLowerCase(),
          kind: String(item?.kind || ""),
          size: Number(item?.size || 0),
          documentId: String(item?.documentId || item?.document_id || ""),
          status: String(item?.status || ""),
          summary: String(item?.summary || ""),
          citationCount: Number(item?.citationCount || item?.citation_count || 0),
          error: String(item?.error || ""),
          importError: String(item?.importError || item?.import_error || "")
        })).filter((item) => item.name || item.path)
      : [];
  }

  function compactWorkText(value, limit = 1200) {
    const text = String(value || "").replace(/\u0000/g, "").trim();
    if (text.length <= limit) return text;
    return text.slice(-limit);
  }

  function recentWorkContext(snapshot) {
    const activeSessionId = String(snapshot?.activeSessionId || "");
    const rawRun = snapshot?.lastRun || {};
    const rawProgress = snapshot?.runProgress || {};
    const runSessionId = String(rawRun.sessionId || rawRun.session_id || "");
    const progressSessionId = String(rawProgress.sessionId || rawProgress.session_id || "");
    const includeRun = !activeSessionId || !runSessionId || runSessionId === activeSessionId;
    const includeProgress = !activeSessionId || !progressSessionId || progressSessionId === activeSessionId;
    const run = includeRun ? rawRun : {};
    const progress = includeProgress ? rawProgress : {};
    const stdout = compactWorkText(run.stdout, 5000);
    const stderr = compactWorkText(run.stderr, 3000);
    const isInternalStep = (step) => String(
      step?.visibility
      || step?.meta?.visibility
      || ""
    ).trim().toLowerCase() === "internal";
    const parsedSteps = includeRun ? parseBackendSteps(run.stdout || "")
      .filter((step) => !isInternalStep(step))
      .map((step) => ({
        tool: compactWorkText(step.tool, 80),
        status: compactWorkText(step.status, 40),
        summary: compactWorkText(step.summary, 500)
      })) : [];
    const progressSteps = includeProgress && Array.isArray(progress.steps) ? progress.steps
      .filter((step) => !isInternalStep(step))
      .map((step) => ({
        id: compactWorkText(step.id || step.stepId, 80),
        title: compactWorkText(step.title, 120),
        status: compactWorkText(step.status, 40),
        summary: compactWorkText(step.summary, 500),
        toolName: compactWorkText(step.toolName, 80),
        ts: Number(step.ts || 0)
      })) : [];
    const steps = [...parsedSteps, ...progressSteps]
      .filter((step) => step.tool || step.title || step.summary)
      .slice(-12);
    const hasRun = Boolean(run.requestId || stdout || stderr || run.phase === "finished" || run.phase === "running");
    const hasProgress = Boolean(progress.requestId || steps.length);
    if (!hasRun && !hasProgress) return null;
    return {
      schema: "tiangong.frontend.work_context.v1",
      capturedAt: Date.now(),
      lastRun: {
        requestId: compactWorkText(run.requestId, 80),
        phase: compactWorkText(run.phase, 40),
        ok: run.ok === null || typeof run.ok === "undefined" ? null : Boolean(run.ok),
        code: typeof run.code === "undefined" ? "" : String(run.code),
        mode: compactWorkText(run.mode, 40),
        elapsedMs: Number(run.elapsedMs || 0),
        startedAt: Number(run.startedAt || 0),
        finishedAt: Number(run.finishedAt || 0),
        stdout,
        stderr
      },
      runProgress: {
        requestId: compactWorkText(progress.requestId, 80),
        sessionId: compactWorkText(progress.sessionId || progress.session_id, 80),
        phase: compactWorkText(progress.phase, 40),
        ok: progress.ok === null || typeof progress.ok === "undefined" ? null : Boolean(progress.ok),
        startedAt: Number(progress.startedAt || 0),
        finishedAt: Number(progress.finishedAt || 0),
        anchorAt: Number(progress.anchorAt || 0)
      },
      steps
    };
  }

  async function rememberComposition(item, mode = "accept") {
    if (!runtime?.compositionFeedback || !item?.meta?.gatewayRequestId) return { ok: false, error: "没有可保存的执行记录" };
    const sessionId = item.sessionId || state.snapshot().activeSessionId;
    const request_id = item.meta.gatewayRequestId;
    const inspected = await runtime.compositionFeedback({ request_id, mode: "inspect" });
    const result = await runtime.compositionFeedback({ request_id, mode,
      result_version: inspected.result_version, event_id: `feedback_${crypto.randomUUID()}` });
    if (result.ok && result.saved) {
      state.replaceMessageById({ sessionId, messageId: item.id, text: item.content, error: item.error,
        meta: { ...item.meta, compositionRemembered: result.status === "accepted", compositionExperienceId: result.experience_id } });
    }
    return result;
  }

  async function sendMessage(text, attachments = [], runOptions = {}) {
    const cleanAttachments = Array.isArray(attachments) ? attachments.slice() : [];
    const message = String(text || "").trim() || (cleanAttachments.length ? "请阅读我上传的文件。" : "");
    if ((!message && !cleanAttachments.length) || !runtime?.send) return;
    const beforeSend = state.snapshot();
    const isAutoContinuation = Boolean(runOptions.__autoContinuation);
    const isDequeuedTurn = Boolean(runOptions.__dequeuedTurn);
    if (beforeSend.busy && !isAutoContinuation && !isDequeuedTurn) {
      return enqueueUserTurn(message, cleanAttachments, runOptions);
    }
    const feedbackTarget = [...(beforeSend.messages || [])].reverse().find(item => item.role === "assistant" && item.meta?.gatewayRequestId);
    if (!isAutoContinuation && !cleanAttachments.length && feedbackTarget && runtime?.compositionFeedback
        && /记住|记下|认可|满意|以后|今后|复用|撤销|别再|不要再|remember|reuse|approve/i.test(message)) {
      state.setBusy(beforeSend.activeSessionId, true);
      try {
        const feedback = await runtime.compositionFeedback({ request_id: feedbackTarget.meta.gatewayRequestId,
          mode: "interpret", user_text: message, event_id: `feedback_${crypto.randomUUID()}` });
        if (feedback.saved) {
          state.replaceMessageById({ sessionId: beforeSend.activeSessionId, messageId: feedbackTarget.id, text: feedbackTarget.content, error: feedbackTarget.error,
            meta: { ...feedbackTarget.meta, compositionRemembered: feedback.status === "accepted", compositionExperienceId: feedback.experience_id } });
          if (feedback.feedback_only) {
            if (state.snapshot().activeSessionId === beforeSend.activeSessionId) {
              if (!isDequeuedTurn) state.addMessage("user", message);
              state.addMessage("assistant", feedback.message, false, { meta: { origin: "composition_feedback" } });
            }
            queueMicrotask(() => { void drainPendingUserTurns(); });
            return feedback;
          }
        }
      } catch (error) {
        if (state.snapshot().activeSessionId === beforeSend.activeSessionId)
          state.addMessage("assistant", "这次做法尚未保存，可以使用结果旁的“认可并记住”重试。", true);
      } finally {
        state.setBusy(beforeSend.activeSessionId, false);
      }
      if (state.snapshot().activeSessionId !== beforeSend.activeSessionId) return { ok: false, error: "会话已切换，请在目标会话重发指令。" };
    }
    // GF 门（草案 §8）：上一轮结果待对账时，禁止"继续/重发"类操作。
    // 全新指令不受限；续作类输入（含隐藏的自动续作）一律拒绝并提示等待对账。
    const priorRunPhase = String(beforeSend.lastRun?.phase || "");
    if (priorRunPhase === "reconcile_required" && (isAutoContinuation || isContinuationRequest(message))) {
      const notice = "上一轮结果待对账，禁止继续或重发。请等待网关对账完成后再处理。";
      state.addMessage("assistant", notice, true);
      return { ok: false, code: "reconcile_required", stdout: "", stderr: notice };
    }
    const directLearningRequested = shouldUseDirectLearning(message);

    const kernelStatus = kernel ? (kernel.snapshot?.() || beforeSend.kernelStatus || {}) : null;
    const lifeStatus = kernelStatus?.life || {};
    if (kernelStatus && (
      kernelStatus.phase !== "ready"
      || kernelStatus.compatible !== true
      || lifeStatus.ready !== true
      || lifeStatus.available !== true
    )) {
      const setupRequired = kernelStatus.phase === "setup_required";
      const detail = String(kernelStatus.lastError?.message || lifeStatus.error || "").trim();
      const error = setupRequired
        ? "生命身份尚未完成迁移或绑定。系统已停止发送以保护原身份，请在设置中完成生命身份修复后重试。"
        : detail || "运行内核尚未就绪。请等待启动完成；若持续失败，请打开系统设置查看具体错误。";
      return { ok: false, code: setupRequired ? "life_setup_required" : "runtime_not_ready", stdout: "", stderr: error };
    }
    const { settings } = beforeSend;
    // Historical fixed-Skill selections cannot constrain task-generated programs.
    const selectedSkills = [];
    const continuationRequest = isAutoContinuation || isContinuationRequest(message);
    const inheritedRootGoal = String(
      runOptions.rootGoal
      || (continuationRequest ? findParentGoal(beforeSend.messages) : message)
      || ""
    ).trim();
    const baseExecutionMessage = continuationRequest && inheritedRootGoal
      ? `${message}\n\n【必须继承且仍未完成的原始总目标】\n${inheritedRootGoal}\n\n本轮不得只按“继续”验收；必须按上述原始总目标检查真实产物、运行和验证证据。`
      : message;
    const workspaceBound = Boolean(runOptions.workspaceBound);
    const workspaceExecutionMessage = workspaceBound && settings.workspace
      ? `${baseExecutionMessage}\n\n【本轮唯一默认工作区】\n${settings.workspace}\n所有新建、修改、运行和验收的项目文件必须位于这个目录的独立子目录内；不得把其父目录、应用数据目录或其他旧项目当成项目根目录。`
      : baseExecutionMessage;
    const projectRootEvidence = continuationRequest
      ? [
          ...beforeSend.messages.slice(-40).map((item) => String(item?.content || item?.text || "")),
          JSON.stringify(recentWorkContext(beforeSend) || {})
        ].join("\n")
      : [message, inheritedRootGoal].join("\n");
    const activeProjectRoot = inferActiveProjectRoot(
      message,
      settings.workspace,
      inheritedRootGoal,
      runOptions.projectRoot,
      projectRootEvidence
    );
    const rawExecutionMessage = activeProjectRoot
      ? `${workspaceExecutionMessage}\n\n【本轮活跃项目根】\n${activeProjectRoot}\n这是本轮项目目录。工具的相对路径仍以工作区为基准，必须包含项目的相对路径；也可以使用该目录下的绝对路径。`
      : workspaceExecutionMessage;
    const sendMode = inferSendMode(message, settings, selectedSkills, runOptions);
    const executionContract = sendMode === "work"
      ? "\n\n【连续执行契约】\nA1-A4 工作在平台执行预算内连续执行（轮次、时长、工具数有硬上限）。复用已有 source_text_map 和成功工具证据，不重复副作用；在预算内持续执行到结果检查通过、用户主动停止或命中 A5；达到预算仍未完成时，保留已完成产物并如实给出未完成清单，不得谎报完成。"
      : "";
    const executionMessageBase = normalizeBackendDeliveryIntent(`${rawExecutionMessage}${executionContract}`);
    const executionMessageWithSkills = executionMessageBase;
    // 确认重放：授权标记只附加在传输层执行消息上，用户气泡保持原始指令文本
    const confirmGrantId = String(runOptions.__confirmGrantId || "").trim();
    const executionMessage = confirmGrantId
      ? `${executionMessageWithSkills}\n[confirm_grant:${confirmGrantId}]`
      : executionMessageWithSkills;
    const requestId = makeRequestId();
    const queuedUserMessage = isDequeuedTurn
      ? beforeSend.messages.find((item) => String(item?.id || "") === String(runOptions.__queuedMessageId || ""))
      : null;
    const userMessage = isAutoContinuation
      ? null
      : queuedUserMessage || state.addMessage("user", message, false, { attachments: cleanAttachments });
    const recordCompletedTurn = (assistantText, runResult = {}) => {
      if (runResult.gatewayRequestId && runResult.ok) {
        // The user may have switched conversations while this task ran.
        state.replaceMessageById({ sessionId: targetSessionId, messageId: targetMessageId, text: assistantText, error: false,
          meta: { gatewayRequestId: runResult.gatewayRequestId } });
      }
      if (isAutoContinuation || typeof runtime?.recordConversationTurn !== "function") return;
      const response = String(assistantText || "").trim();
      if (!response) return;
      void runtime.recordConversationTurn(message, response, {
        turn_id: requestId,
        conversation_id: activeSessionId || "desktop"
      }).catch(() => {});
    };
    if (directLearningRequested && !isAutoContinuation && typeof runtime.decideLearning === "function") {
      void runtime.decideLearning(message).then((result) => {
        if (result?.ok !== true) return;
        const report = String(result?.report?.text || result?.learning?.summary || "").trim();
        if (report) state.addMessage("assistant", report, false, { kind: "learning", learningId: result?.learning?.learning_id || "" });
      }).catch(() => {});
    }
    const currentSnapshot = state.snapshot();
    const activeSessionId = String(currentSnapshot.activeSessionId || "");
    // 在 startRunProgress 之前创建 assistant 工作卡气泡（绑定 requestId）
    const assistantMsg = state.addMessage("assistant", "", false, { kind: "work", requestId });
    const targetSessionId = activeSessionId;
    const targetMessageId = assistantMsg.id;
    const workContext = recentWorkContext({
      ...currentSnapshot,
      lastRun: beforeSend.lastRun,
      runProgress: beforeSend.runProgress
    });
    const showRunProgress = true;
    if (showRunProgress) {
      state.startRunProgress(targetSessionId, requestId, { anchorAt: userMessage?.at || Date.now(), anchorMessageId: assistantMsg.id });
    }
    state.setBusy(targetSessionId, true);
      state.setLastRun(targetSessionId, {
        requestId,
        sessionId: activeSessionId,
        phase: "running",
        ok: null,
        mode: sendMode
      });

    try {
      let knowledgeReferences = [];
      let knowledgeReferenceScan = null;
      if (runOptions.useKnowledgeReference) {
        try {
          knowledgeReferenceScan = await searchKnowledge({ query: message, top_k: 6, per_doc: 3 });
          knowledgeReferences = Array.isArray(knowledgeReferenceScan?.cards)
            ? knowledgeReferenceScan.cards.slice(0, 6)
            : [];
        } catch (error) {
          knowledgeReferenceScan = { ok: false, error: error?.message || String(error) };
        }
      }
      const sendPayload = {
        requestId,
        sessionId: activeSessionId,
        activeSessionId,
        // The desktop inbound contract currently transports one text field.
        // Preserve the original utterance inside the renderer-compiled execution
        // message so hidden auto-continuations do not lose the root goal,
        // workspace/project boundary, or tool-batch contract at the 7184 edge.
        message: executionMessage,
        taskContext: {
          raw_user_text: message,
          root_goal: inheritedRootGoal,
          project_root_hint: activeProjectRoot || "",
          selected_skill_ids: selectedSkills.map((item) => item.id).filter(Boolean),
        },
        rootGoal: inheritedRootGoal,
        projectRoot: activeProjectRoot,
        continuation: continuationRequest,
        mode: sendMode,
        personaName: settings.personaName,
        soulPrompt: settings.soulPrompt,
        modelService: settings.modelService,
        modelProvider: settings.modelProvider,
        modelBaseUrl: settings.modelBaseUrl,
        modelName: settings.modelName,
        modelApiKey: settings.modelApiKey,
        modelThinkingEnabled: settings.modelThinkingEnabled,
        modelThinkingDepth: settings.modelThinkingDepth,
        modelMultimodalInput: settings.modelMultimodalInput,
        modelImageInput: settings.modelImageInput,
        modelVideoInput: settings.modelVideoInput,
        modelAudioInput: settings.modelAudioInput,
        webSearchProvider: settings.webSearchProvider,
        imageGenerationMode: settings.imageGenerationMode,
        attachments: cleanAttachments,
        selectedSkills,
        selectedSkillNames: selectedSkills.map((item) => item.name || item.id).filter(Boolean),
        useKnowledgeReference: Boolean(runOptions.useKnowledgeReference),
        knowledgeReferences,
        forceSelectedSkills: Boolean(runOptions.forceSelectedSkills) && selectedSkills.length > 0,
        workContext,
        learningAction: runOptions.learningAction || "",
        learningId: runOptions.learningId || ""
      };

      if (runOptions.useStream && runtime.sendStream) {
        let toolSeq = 0;
        let confirmPending = false;
        const toolStepByCallId = new Map();
        let missingCallIdSeq = 0;
        function mergedAttachments(result = {}) {
          const items = [];
          const seen = new Set();
          for (const source of [result.attachments, result.generated_attachments]) {
            for (const item of Array.isArray(source) ? source : []) {
              const key = String(item?.artifact_revision_id || item?.path || item?.url || item?.dataUrl || item?.documentId || item?.document_id || item?.name || JSON.stringify(item || {}));
              if (!key || seen.has(key)) continue;
              seen.add(key);
              items.push(item);
            }
          }
          return items;
        }
        function toolCallStepId(data = {}) {
          const callId = String(data.call_id || data.callId || "").trim();
          if (callId) {
            if (!toolStepByCallId.has(callId)) {
              toolStepByCallId.set(callId, `tool_${requestId}_${callId}`);
            }
            return toolStepByCallId.get(callId);
          }
          toolSeq++;
          const id = `tool_${requestId}_${toolSeq}_${data.name || "tool"}`;
          return id;
        }
        function toolResultStepId(data = {}) {
          const callId = String(data.call_id || data.callId || "").trim();
          if (callId) {
            if (!toolStepByCallId.has(callId)) {
              toolStepByCallId.set(callId, `tool_${requestId}_${callId}`);
            }
            return toolStepByCallId.get(callId);
          }
          missingCallIdSeq++;
          return `tool_${requestId}_missing_call_id_${missingCallIdSeq}_${data.name || "tool"}`;
        }
        state.applyRunProgress(targetSessionId, {
          requestId,
          id: "backend_execution",
          title: "思考中",
          status: "running",
          summary: "思考中"
        });
        let liveVisibleText = "";
        const streamResult = await runtime.sendStream(sendPayload, {
          onText: (chunk) => {
            liveVisibleText += String(chunk || "");
            state.streamAppendById({ sessionId: targetSessionId, messageId: targetMessageId, delta: chunk });
          },
          onStageText: (snapshot) => {
            const visibleText = spokenBackendText(snapshot);
            if (!visibleText) return;
            // Polling is recovery for a disconnected/missed SSE path.  Once the
            // renderer has newer live content, an older run snapshot must not
            // replace it.
            if (liveVisibleText && !visibleText.startsWith(liveVisibleText)) return;
            liveVisibleText = visibleText;
            state.replaceMessageById({
              sessionId: targetSessionId,
              messageId: targetMessageId,
              text: visibleText,
              error: false
            });
          },
          onToolCall: (data) => {
            const id = toolCallStepId(data);
            state.applyRunProgress(targetSessionId, { requestId, id, title: data.label || data.name, status: "running", summary: `执行 ${data.action || data.name}` });
          },
          onToolResult: (data) => {
            const id = toolResultStepId(data);
            state.applyRunProgress(targetSessionId, { requestId, id, title: data.label || data.name, status: data.ok ? "done" : "failed", summary: data.summary || (data.ok ? "已结束" : "失败") });
          },
          onBiaoxian: (data) => {
            try { window.dispatchEvent(new CustomEvent("tiangong-biaoxian", { detail: data })); } catch {}
          },
          onConfirmRequired: (data) => {
            // 后端命中确认通道：记录标记以跳过自动续作，并渲染确认卡片
            confirmPending = true;
            registerConfirmCard(data, message);
          }
        });
        // 用后端返回的干净回复替换流式累积的文本；没有最终文本时也必须给用户可见错误
        const finalText = extractFinalReplyText(streamResult.stdout);
        const errorText = String(streamResult.stderr || "").trim();
        let displayText = finalText
          || (errorText ? humanizeBackendError(errorText) : "")
          || "后端本轮没有返回最终内容。任务可能卡在工具链收口阶段，请查看后端 trace / zongdiaodu 日志。";
        let productVerification = null;
        if (claimsCompleted(streamResult, displayText)
          && requiresDeterministicWebQa(inheritedRootGoal, activeProjectRoot)
          && runtime?.verifyWebProject) {
          try {
            productVerification = await runtime.verifyWebProject({
              workspace: settings.workspace,
              projectRoot: activeProjectRoot
            });
          } catch (error) {
            productVerification = { ok: false, error: error?.message || String(error), issues: [error?.message || String(error)] };
          }
          displayText = `${displayText}\n\n${webQaSummary(productVerification)}`;
        }
        // GF 门：待对账/部分完成/矛盾/未知相位一律不触发自动续作
        const gfPhase = String(streamResult?.phase || "");
        const gfBlocksContinuation = ["reconcile_required", "partial", "incident", "unknown"].includes(gfPhase);
        const continuation = confirmPending || gfBlocksContinuation
          ? { shouldContinue: false, recoverable: false, stopReason: gfBlocksContinuation ? "gateway_non_success_phase" : "" }
          : autoContinuationDecision({
            result: streamResult,
            displayText,
            sendMode,
            runOptions: { ...runOptions, rootGoal: inheritedRootGoal, productVerification }
          });
        if (continuation.shouldContinue) {
          const continuationIndex = continuation.nextState.count;
          state.replaceMessageById({
            sessionId: targetSessionId,
            messageId: targetMessageId,
            text: `${displayText}\n\n已保存本轮检查点，正在自动续作（第 ${continuationIndex} 轮）。无需回复“继续”。`,
            error: false,
            attachments: mergedAttachments(streamResult),
            meta: { origin: String(streamResult?.origin || "model") }
          });
          try { window.dispatchEvent(new CustomEvent("tiangong-chat-final-render", { detail: { sessionId: targetSessionId, messageId: targetMessageId } })); } catch {}
          if (showRunProgress) state.finishRunProgress(targetSessionId, requestId, true);
          state.setLastRun(targetSessionId, {
            ...streamResult,
            requestId,
            sessionId: activeSessionId,
            phase: "checkpoint",
            finishedAt: Date.now(),
            autoContinuation: continuationIndex
          });
          state.setBusy(targetSessionId, false);
          await new Promise((resolve) => window.setTimeout(resolve, 500));
          return await sendMessage(
            autoContinuationPrompt(continuation),
            [],
            {
              ...runOptions,
              mode: "work",
              useStream: true,
              __autoContinuation: true,
              rootGoal: inheritedRootGoal,
              projectRoot: activeProjectRoot,
              autoContinueState: continuation.nextState
            }
          );
        }
        // 终局消息不变量：每轮最多一条 assistant 收尾；自然回复一律黑色，
        // 红色只允许在“完全没有自然回复”的兜底场景出现。
        const turnFinal = findTurnFinalMessage(requestId, finalText);
        if (turnFinal) {
          const attachments = mergedAttachments(streamResult);
          if (attachments.length && String(turnFinal.content || "") === String(finalText || "")) {
            state.replaceMessageById({
              sessionId: targetSessionId,
              messageId: turnFinal.id,
              text: turnFinal.content,
              error: false,
              attachments,
              meta: { origin: String(streamResult?.origin || turnFinal.meta?.origin || "model"), runId: turnFinal.meta?.runId || requestId }
            });
          }
        } else {
          state.replaceMessageById({
            sessionId: targetSessionId,
            messageId: targetMessageId,
            text: displayText,
            error: !finalText,
            attachments: mergedAttachments(streamResult),
            meta: { origin: String(streamResult?.origin || "model"), runId: requestId }
          });
        }
        recordCompletedTurn(displayText, streamResult);
        // 派发最终渲染事件（独立于进度条状态）
        try { window.dispatchEvent(new CustomEvent("tiangong-chat-final-render", { detail: { sessionId: targetSessionId, messageId: targetMessageId } })); } catch {}
        if (showRunProgress) state.finishRunProgress(targetSessionId, requestId, Boolean(streamResult.ok));
        // GF 门：透传网关终态相位（reconcile_required/partial/incident/unknown），
        // 供顶部状态聚合与会话卡片使用；无相位信息时保持原 finished 语义
        state.setLastRun(targetSessionId, mergeTerminalIntoLastRun({ ...streamResult, requestId, sessionId: activeSessionId, phase: streamResult.phase || "finished", finishedAt: Date.now() }));
        // 延迟清进度，让工具结果短暂可见。新消息发出后不再清除旧进度
        const _rid = requestId;
        setTimeout(() => {
          if (showRunProgress && state.snapshot().runProgress.requestId === _rid) {
            state.clearRunProgress(targetSessionId, _rid);
          }
        }, 1500);
        state.setBusy(targetSessionId, false);
        try { await refreshStatus(); } catch {}
        return;
      }

      const result = await runtime.send(sendPayload);
      if (result?.interrupted || result?.canceled) {
        if (showRunProgress) state.interruptRunProgress(targetSessionId, requestId, result.stderr || result.summary || "");
        state.setLastRun(targetSessionId, { ...result, phase: "interrupted", ok: null, finishedAt: Date.now() });
        // 系统中断提示不进聊天框：保留已流式的模型文本，原因由状态条展示。
        return;
      }
      if (showRunProgress) state.finishRunProgress(targetSessionId, requestId, Boolean(result.ok));
      // GF 门：非流式路径同样透传网关终态相位
      state.setLastRun(targetSessionId, mergeTerminalIntoLastRun({ ...result, requestId, sessionId: activeSessionId, phase: result.phase || "finished", finishedAt: Date.now() }));
      const reply = backendReply(result);
      let displayText = spokenBackendText(reply.text) || (reply.error ? reply.text : "已结束。");
      let productVerification = null;
      if (claimsCompleted(result, displayText)
        && requiresDeterministicWebQa(inheritedRootGoal, activeProjectRoot)
        && runtime?.verifyWebProject) {
        try {
          productVerification = await runtime.verifyWebProject({
            workspace: settings.workspace,
            projectRoot: activeProjectRoot
          });
        } catch (error) {
          productVerification = { ok: false, error: error?.message || String(error), issues: [error?.message || String(error)] };
        }
        displayText = `${displayText}\n\n${webQaSummary(productVerification)}`;
      }
      // GF 门：非流式路径的非成功相位同样禁止自动续作
      const resultGfPhase = String(result?.phase || "");
      const resultGfBlocks = ["reconcile_required", "partial", "incident", "unknown"].includes(resultGfPhase);
      const continuation = resultGfBlocks
        ? { shouldContinue: false, recoverable: false, stopReason: "gateway_non_success_phase" }
        : autoContinuationDecision({ result, displayText, sendMode, runOptions: { ...runOptions, rootGoal: inheritedRootGoal, productVerification } });
      if (continuation.shouldContinue) {
        const continuationIndex = continuation.nextState.count;
        if (showRunProgress) state.clearRunProgress(targetSessionId, requestId);
        state.replaceMessageById({
          sessionId: targetSessionId,
          messageId: targetMessageId,
          text: `${displayText}\n\n已保存本轮检查点，正在自动续作（第 ${continuationIndex} 轮）。无需回复“继续”。`,
          error: false,
          attachments: Array.isArray(result?.attachments) ? result.attachments : []
        });
        state.setLastRun(targetSessionId, {
          ...result,
          requestId,
          sessionId: activeSessionId,
          phase: "checkpoint",
          finishedAt: Date.now(),
          autoContinuation: continuationIndex
        });
        state.setBusy(targetSessionId, false);
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        return await sendMessage(
          autoContinuationPrompt(continuation),
          [],
          {
            ...runOptions,
            mode: "work",
            __autoContinuation: true,
            rootGoal: inheritedRootGoal,
            projectRoot: activeProjectRoot,
            autoContinueState: continuation.nextState
          }
        );
      }
      // 系统停止提示不再追加进聊天文本（用户不希望在对话里看到系统模板）；
      // 停止原因与下一步由后端自然语言收尾回复承载。
      // 派发 biaoxian 到 VRM 面板
      if (reply.biaoxian && typeof reply.biaoxian === "object") {
        console.log("[biaoxian] dispatching", reply.biaoxian.expression, reply.biaoxian.gesture);
        try { window.dispatchEvent(new CustomEvent("tiangong-biaoxian", { detail: reply.biaoxian })); } catch {}
      } else {
        console.log("[biaoxian] not in reply, result keys:", Object.keys(result||{}).join(","));
      }
      if (showRunProgress) state.clearRunProgress(targetSessionId, requestId);
      const finalAttachments = [];
      const seenFinalAttachments = new Set();
      for (const source of [result?.attachments, result?.generated_attachments]) {
        for (const item of Array.isArray(source) ? source : []) {
          const key = String(item?.artifact_revision_id || item?.path || item?.url || item?.dataUrl || item?.documentId || item?.document_id || item?.name || JSON.stringify(item || {}));
          if (!key || seenFinalAttachments.has(key)) continue;
          seenFinalAttachments.add(key);
          finalAttachments.push(item);
        }
      }
      const nonStreamTerminal = String(result?.simple_chain_status || "").trim();
      const nonStreamExpected = ["force_stopped", "interrupted", "incomplete", "complete"].includes(nonStreamTerminal);
      const nonStreamAlready = (state.snapshot().messages || []).some(
        (item) => item.role === "assistant" && String(item.meta?.runId || "") === requestId,
      );
      if (!nonStreamAlready) {
        state.replaceMessageById({ sessionId: targetSessionId, messageId: targetMessageId, text: displayText, error: reply.error && !nonStreamExpected, attachments: finalAttachments, meta: { origin: String(result?.origin || "model"), runId: requestId } });
      }
      recordCompletedTurn(displayText, result);
      try { window.dispatchEvent(new CustomEvent("tiangong-chat-final-render", { detail: { sessionId: targetSessionId, messageId: targetMessageId } })); } catch {}
    } catch (error) {
      const message = error.message || String(error);
      if (showRunProgress) state.finishRunProgress(targetSessionId, requestId, false);
      const isInterrupt = /请求已中断|已中断|aborted|user_cancelled|用户已停止/i.test(message);
      state.setLastRun(targetSessionId, {
        requestId,
        sessionId: activeSessionId,
        phase: isInterrupt ? "interrupted" : "finished",
        ok: false,
        stderr: message,
        finishedAt: Date.now()
      });
      if (showRunProgress) state.clearRunProgress(targetSessionId, requestId);
      // 系统中断提示不进聊天框：保留已流式的模型文本，原因由状态条展示。
      if (!isInterrupt) {
        state.replaceMessageById({ sessionId: targetSessionId, messageId: targetMessageId, text: humanizeBackendError(message) || "执行失败。", error: true, meta: { origin: "template" } });
      }
    } finally {
      state.setBusy(targetSessionId, false);
      try {
        await refreshStatus();
      } catch {
        // Status refresh must not turn a completed response into a failed send.
      }
      // The outer user turn owns queue draining. Hidden recursive
      // auto-continuations unwind through this frame, so queued work cannot
      // interleave between continuation checkpoints.
      if (!isAutoContinuation) {
        queueMicrotask(() => {
          void drainPendingUserTurns();
        });
      }
    }
  }

  async function learnLearningExperience(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    if (runtime?.confirmLearningCard) {
      const result = await runtime.confirmLearningCard({ card_id: id, learningId: id, actor: "desktop_user" }, item);
      await refreshStatus();
      if (result?.ok) return result;
    }
    const summary = String(item?.summary || item?.task_preview || "").replace(/\s+/g, " ").trim().slice(0, 240);
    const message = [
      summary ? `前台主动学习卡 ${id}：${summary}` : `前台主动学习卡 ${id}`,
      "执行要求：按学习卡 SOP 完成筛选、去重、优先级、学习、质检和归类；不得随机选择学习内容。"
    ].join("\n");
    await sendMessage(message, [], { learningAction: "learn", learningId: id });
    return { ok: true, id };
  }

  async function processLearningCard(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    if (!runtime?.processLearningCard) return { ok: false, error: "后端未提供能力沙盘构建接口" };
    const result = await runtime.processLearningCard({ card_id: id, learningId: id, actor: "desktop_user", reason: "desktop_user_requested_sandbox_build" }, item);
    await refreshStatus();
    return result;
  }

  async function requestLearningActivation(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    if (!runtime?.requestLearningActivation) return { ok: false, error: "后端未提供激活学习接口" };
    const result = await runtime.requestLearningActivation({ card_id: id, learningId: id, actor: "desktop_user" }, item);
    await refreshStatus();
    return result;
  }

  async function activateLearningCard(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    if (!runtime?.activateLearningCard) return { ok: false, error: "后端未提供激活接口" };
    const result = await runtime.activateLearningCard({ card_id: id, learningId: id, actor: "desktop_user" }, item);
    await refreshStatus();
    return result;
  }

  async function releaseLearningCard(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "missing_learning_card_id" };
    if (!runtime?.releaseLearningCard) return { ok: false, error: "learning_release_api_unavailable" };
    const result = await runtime.releaseLearningCard({
      card_id: id,
      learningId: id,
      actor: "desktop_user",
      reason: "desktop_user_review_release"
    }, item);
    await refreshStatus();
    return result;
  }

  async function discardLearningCard(experienceId, item = {}) {
    const id = String(experienceId || "").trim();
    if (!id) return { ok: false, error: "缺少学习卡编号" };
    if (!runtime?.discardLearningCard) return { ok: false, error: "后端未提供放弃学习卡接口" };
    const result = await runtime.discardLearningCard({ card_id: id, learningId: id, actor: "desktop_user", reason: "user_discarded_from_lifecycle" }, item);
    await refreshStatus();
    return result;
  }

  async function clearConversation() {
    await detachActiveRun("clear_conversation");
    const activeSessionId = String(state.snapshot().activeSessionId || "");
    discardPendingUserTurns(activeSessionId);
    // Local conversation history is authoritative for the desktop view. The
    // backend event is best-effort audit synchronization and must not make the
    // user's clear action appear to fail after the local state is removed.
    let clearResult = { ok: true, action: "clear", localOnly: false };
    if (activeSessionId) {
      if (runtime?.conversationEvents) {
        clearResult = await runtime.conversationEvents({
          action: "clear",
          sessionId: activeSessionId,
          reason: "user_cleared_conversation"
        }).catch((error) => ({
          ok: false,
          error: error?.message || String(error),
          localOnly: true
        }));
        if (clearResult?.ok === false) clearResult.localOnly = true;
      } else {
        clearResult = {
          ok: false,
          error: "后端未提供会话清空接口",
          localOnly: true
        };
      }
    }
    state.clearMessages();
    state.setLastRun({
      phase: "idle",
      ok: true,
      mode: "auto"
    });
    return { ...clearResult, ok: true, sessionId: activeSessionId };
  }

  return {
    loadSettings,
    setActivePage,
    startNewConversation,
    switchConversation,
    deleteConversation,
    setActiveSkillCategory,
    toggleSelectedSkill,
    clearSelectedSkills,
    setMode,
    saveSettings,
    deleteProviderApiKey,
    chooseWorkspace,
    chooseWorkspaceRoot,
    chooseStorageRoot,
    chooseKnowledgeRoot,
    choosePersonaAvatar,
    chooseVoiceSample,
    chooseUserAvatar,
    openWorkspace,
    openPath,
    openArtifact,
    saveTargetAs,
    listDailyLogs,
    openDailyLog,
    deleteDailyLog,
    listSkills,
    deleteSkill,
    activateSkill,
    hotSwitchSettings,
    refreshStatus,
    refreshConfig,
    messageChannelStatus,
    connectMessageChannel,
    gatewayLinksStatus,
    saveGatewayLinks,
    gatewayLinksAction,
    listKnowledge,
    importKnowledgeFiles,
    chooseChatFiles,
    pasteChatFiles,
    queryKnowledge,
    searchKnowledge,
    organizeKnowledge,
    exportKnowledge,
    removeKnowledge,
    confirmLifecycleUpdate,
    denyLifecycleUpdate,
    resolvePolicyConfirmation,
    learnLearningExperience,
    processLearningCard,
    requestLearningActivation,
    activateLearningCard,
    releaseLearningCard,
    discardLearningCard,
    deleteLearningExperience,
    cancelRun,
    guideRun,
    handleRunInput,
    sendMessage,
    rememberComposition,
    clearConversation
  };
}
