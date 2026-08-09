export const LIFE_API_CONTRACT_ID = "tiangong.life.api.v2";

function route(method, path, capability) {
  return Object.freeze({ method, path, capability });
}

export const LIFE_API_ROUTES = Object.freeze({
  health: route("GET", "/api/v1/v3/life/health", "life.health"),
  identities: route("GET", "/api/v1/v3/life/identities", "life.identity"),
  identityActive: route("GET", "/api/v1/v3/life/identity/active", "life.identity"),
  identityAudit: route("GET", "/api/v1/v3/life/identity/audit", "life.identity.audit"),
  identityCreate: route("POST", "/api/v1/v3/life/identity/create", "life.identity.create"),
  identityBind: route("POST", "/api/v1/v3/life/identity/bind", "life.identity.bind"),
  identityActivate: route("POST", "/api/v1/v3/life/identity/activate", "life.identity.activate"),
  identityUnbind: route("POST", "/api/v1/v3/life/identity/unbind", "life.identity.unbind"),
  identityDelete: route("POST", "/api/v1/v3/life/identity/delete", "life.identity.delete"),
  soulGet: route("GET", "/api/v1/v3/life/soul", "life.soul"),
  temperamentGet: route("GET", "/api/v1/v3/life/temperament", "life.temperament"),
  soulUpdate: route("POST", "/api/v1/v3/life/soul/update", "life.soul.update"),
  journalVerify: route("GET", "/api/v1/v3/life/journal/verify", "life.journal.verify"),
  journalMigrate: route("POST", "/api/v1/v3/life/journal/migrate", "life.journal.migrate"),
  projectionRebuild: route("POST", "/api/v1/v3/life/projection/rebuild", "life.projection.rebuild"),
  projectionSnapshot: route("POST", "/api/v1/v3/life/projection/snapshot", "life.projection.snapshot"),
  memoryStats: route("GET", "/api/v1/v3/life/memory/stats", "life.memory"),
  memoryCandidates: route("POST", "/api/v1/v3/life/memory/candidates", "life.memory.candidate"),
  memoryAssert: route("POST", "/api/v1/v3/life/memory/assert", "life.memory.write"),
  memoryTurn: route("POST", "/api/v1/v3/life/memory/turn", "life.memory.turn"),
  memoryCorrect: route("POST", "/api/v1/v3/life/memory/correct", "life.memory.write"),
  memoryStatus: route("POST", "/api/v1/v3/life/memory/status", "life.memory.write"),
  memoryRelation: route("POST", "/api/v1/v3/life/memory/relation", "life.memory.write"),
  memorySearch: route("POST", "/api/v1/v3/life/memory/search", "life.memory.recall"),
  memoryDelete: route("POST", "/api/v1/v3/life/memory/delete", "life.memory.erase"),
  memoryRebuild: route("POST", "/api/v1/v3/life/memory/rebuild-index", "life.memory.repair"),
  affectGet: route("GET", "/api/v1/v3/life/affect", "life.affect"),
  affectAppraise: route("POST", "/api/v1/v3/life/affect/appraise", "life.affect.appraise"),
  affectDecay: route("POST", "/api/v1/v3/life/affect/decay", "life.affect.decay"),
  affectOutcome: route("POST", "/api/v1/v3/life/affect/outcome", "life.affect.outcome"),
  affectExpression: route("GET", "/api/v1/v3/life/affect/expression", "life.affect.expression"),
  contextCompile: route("POST", "/api/v1/v3/life/context/compile", "life.context.compile"),
  contextCompileAuthorize: route("POST", "/api/v1/v3/life/context/compile-and-authorize", "life.context.authorize"),
  contextLatest: route("GET", "/api/v1/v3/life/context/latest", "life.context.replay"),
  contextReplay: route("POST", "/api/v1/v3/life/context/replay", "life.context.replay"),
  contextVerify: route("POST", "/api/v1/v3/life/context/verify", "life.context.verify"),
  executionPrepare: route("POST", "/api/v1/v3/life/execution/prepare", "life.execution.authorize"),
  executionRecover: route("POST", "/api/v1/v3/life/execution/recover", "life.execution.verify"),
  executionStatus: route("POST", "/api/v1/v3/life/execution/status", "life.execution.status"),
  heartbeat: route("POST", "/api/v1/v3/life/heartbeat", "life.heartbeat"),
  autonomyTasks: route("GET", "/api/v1/v3/life/autonomy/tasks", "life.autonomy.read"),
  autonomyTick: route("POST", "/api/v1/v3/life/autonomy/tick", "life.autonomy.propose"),
  autonomyTaskStatus: route("POST", "/api/v1/v3/life/autonomy/task/status", "life.autonomy.review"),
  learningActivityScope: route("GET", "/api/v1/v3/life/learning/activity-scope", "life.learning.scope"),
  capabilityOverlay: route("GET", "/api/v1/v3/life/capabilities/overlay", "life.capability.overlay"),
  learningDraft: route("POST", "/api/v1/v3/life/learning/draft", "life.learning.draft"),
  learningUserRequest: route("POST", "/api/v1/v3/life/learning/user-request", "life.learning.direct"),
  learningDecide: route("POST", "/api/v1/v3/life/learning/decide", "life.learning.model_router"),
  executionCommit: route("POST", "/api/v1/v3/life/execution/commit", "life.execution.bridge"),
  state: route("GET", "/api/v1/v3/state", "life.state"),
  panel: route("GET", "/api/v1/v3/life/panel", "life.panel"),
  inboxRead: route("POST", "/api/v1/v3/life/inbox/read", "life.inbox"),
  inboxDelete: route("POST", "/api/v1/v3/life/inbox/delete", "life.inbox"),
  proactiveChatPending: route("GET", "/api/v1/v3/life/proactive-chat/pending", "life.proactive_chat"),
  proactiveChatAck: route("POST", "/api/v1/v3/life/proactive-chat/ack", "life.proactive_chat"),
  settingsUpdate: route("POST", "/api/v1/v3/life/settings", "life.settings"),
  upgradeConfirm: route("POST", "/api/v1/v3/life/upgrade/confirm", "life.upgrade"),
  upgradeCancel: route("POST", "/api/v1/v3/life/upgrade/cancel", "life.upgrade"),
  upgradeComplete: route("POST", "/api/v1/v3/life/upgrade/complete", "life.upgrade.complete"),
  capabilityPropose: route("POST", "/api/v1/v3/life/capability/propose", "life.capability.candidate"),
  capabilityApprove: route("POST", "/api/v1/v3/life/capability/approve", "life.capability.approve"),
  capabilityBuild: route("POST", "/api/v1/v3/life/capability/build", "life.capability.build"),
  capabilityPublish: route("POST", "/api/v1/v3/life/capability/publish", "life.capability.publish"),
  capabilityActivate: route("POST", "/api/v1/v3/life/capability/activate", "life.capability.activate"),
  capabilityDiscard: route("POST", "/api/v1/v3/life/capability/discard", "life.capability.discard"),
  capabilityInvoke: route("POST", "/api/v1/v3/life/capability/invoke", "life.capability.invoke"),
  capabilityOutcome: route("POST", "/api/v1/v3/life/capability/outcome", "life.capability.outcome"),
  capabilityPatchPropose: route("POST", "/api/v1/v3/life/capability/patch/propose", "life.capability.patch.propose"),
  capabilityPatchVerify: route("POST", "/api/v1/v3/life/capability/patch/verify", "life.capability.patch.verify"),
  capabilityRollback: route("POST", "/api/v1/v3/life/capability/rollback", "life.capability.rollback"),
  capabilityReactivate: route("POST", "/api/v1/v3/life/capability/reactivate", "life.capability.reactivate"),
  capabilityUsage: route("POST", "/api/v1/v3/life/capability/usage", "life.capability.usage"),
  learningConfirm: route("POST", "/api/v1/v3/learning/confirm", "learning.review"),
  learningProcessApproved: route("POST", "/api/v1/v3/learning/process-approved", "learning.processing"),
  learningRequestActivation: route("POST", "/api/v1/v3/learning/request-activation", "learning.activation"),
  learningActivate: route("POST", "/api/v1/v3/learning/activate", "learning.activation"),
  learningRelease: route("POST", "/api/v1/v3/learning/release", "learning.release"),
  learningDiscard: route("POST", "/api/v1/v3/learning/discard", "learning.processing")
});

const LEARNING_ROUTE_BY_ACTION = Object.freeze({
  confirm: "learningConfirm",
  process: "learningProcessApproved",
  requestActivation: "learningRequestActivation",
  activate: "learningActivate",
  release: "learningRelease",
  discard: "learningDiscard"
});

export class LifeApiContractError extends Error {
  constructor(message, code = "life_api_contract_error") {
    super(message);
    this.name = "LifeApiContractError";
    this.code = code;
  }
}

function defaultRequest(path, options) {
  const kernel = typeof window !== "undefined" ? window.tiangongFrontendKernel : null;
  if (!kernel?.request) {
    throw new LifeApiContractError("天工造物 v3.0 完整版前端内核尚未初始化", "frontend_kernel_unavailable");
  }
  return kernel.request(path, options);
}

export function createLifeApiClient({ request = defaultRequest, timeoutMs = 9000 } = {}) {
  async function call(routeName, options = {}) {
    const spec = LIFE_API_ROUTES[routeName];
    if (!spec) throw new LifeApiContractError(`Unknown life API route: ${routeName}`, "unknown_life_route");
    const { body, ...rest } = options;
    return request(spec.path, {
      timeoutMs,
      ...rest,
      method: spec.method,
      ...(typeof body === "undefined" ? {} : { body })
    });
  }

  return Object.freeze({
    contractId: LIFE_API_CONTRACT_ID,
    routes: LIFE_API_ROUTES,
    call,
    getHealth: (options = {}) => call("health", options),
    listIdentities: (options = {}) => call("identities", options),
    getActiveIdentity: (options = {}) => call("identityActive", options),
    getIdentityAudit: (options = {}) => call("identityAudit", options),
    createIdentity(name, extra = {}) {
      return call("identityCreate", { body: { name: String(name || "起源").trim() || "起源", ...extra } });
    },
    bindIdentity(root, extra = {}) {
      const value = String(root || "").trim();
      if (!value) throw new LifeApiContractError("root is required", "empty_root");
      return call("identityBind", { body: { root: value, ...extra } });
    },
    activateIdentity(lifeId, extra = {}) {
      const life_id = String(lifeId || "").trim();
      if (!life_id) throw new LifeApiContractError("life_id is required", "empty_life_id");
      return call("identityActivate", { body: { life_id, ...extra } });
    },
    unbindIdentity(lifeId, extra = {}) {
      const life_id = String(lifeId || "").trim();
      if (!life_id) throw new LifeApiContractError("life_id is required", "empty_life_id");
      return call("identityUnbind", { body: { life_id, ...extra } });
    },
    deleteIdentity(lifeId, extra = {}) {
      const life_id = String(lifeId || "").trim();
      if (!life_id) throw new LifeApiContractError("life_id is required", "empty_life_id");
      return call("identityDelete", { body: { life_id, ...extra } });
    },
    getTemperament: (options = {}) => call("temperamentGet", options),
    heartbeat(reason = "manual") {
      return call("heartbeat", { body: { reason } });
    },
    listAutonomyTasks(options = {}) {
      return call("autonomyTasks", options);
    },
    runAutonomyTick(reason = "frontend_manual") {
      return call("autonomyTick", { body: { reason } });
    },
    updateAutonomyTaskStatus(task_id, status, extra = {}) {
      const normalizedTaskId = String(task_id || "").trim();
      const normalizedStatus = String(status || "").trim();
      if (!normalizedTaskId) throw new LifeApiContractError("task_id is required", "empty_task_id");
      if (!normalizedStatus) throw new LifeApiContractError("status is required", "empty_task_status");
      return call("autonomyTaskStatus", { body: { task_id: normalizedTaskId, status: normalizedStatus, ...extra } });
    },
    getLearningActivityScope: (options = {}) => call("learningActivityScope", options),
    createLearningDraft(decision, extra = {}) {
      if (!decision || typeof decision !== "object" || Array.isArray(decision)) throw new LifeApiContractError("decision is required", "invalid_learning_decision");
      return call("learningDraft", { body: { decision, actor: "llm_learning_router", ...extra } });
    },
    learnFromUserRequest(decision, extra = {}) {
      if (!decision || typeof decision !== "object" || Array.isArray(decision)) throw new LifeApiContractError("decision is required", "invalid_learning_decision");
      return call("learningUserRequest", { body: { decision, actor: "user", ...extra } });
    },
    decideLearning(request, { source = "user_direct", ...extra } = {}) {
      const value = String(request || "").trim();
      if (!value && source === "user_direct") throw new LifeApiContractError("request is required", "empty_learning_request");
      return call("learningDecide", { body: { request: value, source, ...extra } });
    },
    getSoul: (options = {}) => call("soulGet", options),
    updateSoul(soul, extra = {}) {
      return call("soulUpdate", { body: { soul: soul || {}, actor: "user", ...extra } });
    },
    verifyJournal: (options = {}) => call("journalVerify", options),
    migrateJournal({ dry_run = false, ...extra } = {}) {
      return call("journalMigrate", { body: { dry_run: Boolean(dry_run), ...extra } });
    },
    rebuildProjection({ dry_run = false, reason = "frontend", ...extra } = {}) {
      return call("projectionRebuild", { body: { dry_run: Boolean(dry_run), reason, ...extra } });
    },
    snapshotProjection(reason = "frontend") {
      return call("projectionSnapshot", { body: { reason } });
    },
    getMemoryStats: (options = {}) => call("memoryStats", options),
    validateMemoryCandidates(candidates, source_event_ids = []) {
      return call("memoryCandidates", { body: { candidates: Array.isArray(candidates) ? candidates : [], source_event_ids } });
    },
    assertMemory(memory_type, content, provenance, extra = {}) {
      return call("memoryAssert", { body: { memory_type, content: content || {}, provenance: provenance || {}, ...extra } });
    },
    recordConversationTurn(user_text, assistant_text, extra = {}) {
      return call("memoryTurn", { body: { user_text: String(user_text || ""), assistant_text: String(assistant_text || ""), actor: "frontend", ...extra } });
    },
    correctMemory(target_memory_id, content, provenance, extra = {}) {
      return call("memoryCorrect", { body: { target_memory_id, content: content || {}, provenance: provenance || {}, ...extra } });
    },
    setMemoryStatus(memory_id, status, extra = {}) {
      return call("memoryStatus", { body: { memory_id, status, ...extra } });
    },
    addMemoryRelation(memory_id, target_memory_id, kind, extra = {}) {
      return call("memoryRelation", { body: { memory_id, target_memory_id, kind, ...extra } });
    },
    searchMemory(query, extra = {}) {
      return call("memorySearch", { body: { query: String(query || ""), ...extra } });
    },
    deleteMemory(memory_id, extra = {}) {
      return call("memoryDelete", { body: { memory_id, ...extra } });
    },
    rebuildMemoryIndex: (extra = {}) => call("memoryRebuild", { body: extra }),
    getAffect: (options = {}) => call("affectGet", options),
    appraiseAffect(appraisal, source_event_ids, extra = {}) {
      return call("affectAppraise", { body: { appraisal: appraisal || {}, source_event_ids: Array.isArray(source_event_ids) ? source_event_ids : [], ...extra } });
    },
    decayAffect(force = false) {
      return call("affectDecay", { body: { force: Boolean(force) } });
    },
    integrateExecutionAffect(source_event_id, extra = {}) {
      return call("affectOutcome", { body: { source_event_id, ...extra } });
    },
    getAffectExpression: (options = {}) => call("affectExpression", options),
    compileContext(current_request, extra = {}) {
      const value = String(current_request || "").trim();
      if (!value) throw new LifeApiContractError("current_request is required", "empty_current_request");
      return call("contextCompile", { body: { current_request: value, ...extra } });
    },
    getLatestContext: (options = {}) => call("contextLatest", options),
    replayContext(context_hash, extra = {}) {
      const value = String(context_hash || "").trim();
      if (!value) throw new LifeApiContractError("context_hash is required", "empty_context_hash");
      return call("contextReplay", { body: { context_hash: value, ...extra } });
    },
    verifyContext(envelope, extra = {}) {
      return call("contextVerify", { body: { envelope: envelope || {}, ...extra } });
    },
    prepareExecution(contextHash, requestId, extra = {}) {
      const context_hash = String(contextHash || "").trim();
      const request_id = String(requestId || "").trim();
      if (!context_hash) throw new LifeApiContractError("context_hash is required", "empty_context_hash");
      if (!request_id) throw new LifeApiContractError("request_id is required", "empty_request_id");
      return call("executionPrepare", { body: { context_hash, request_id, decision_action: "execute", ...extra } });
    },
    recoverExecution(requestId, extra = {}) {
      const request_id = String(requestId || "").trim();
      const cycle_id = String(extra.cycle_id || "").trim();
      if (!request_id && !cycle_id) throw new LifeApiContractError("request_id or cycle_id is required", "empty_execution_reference");
      return call("executionRecover", { body: { request_id, ...extra } });
    },
    getExecutionStatus(requestId, extra = {}) {
      const request_id = String(requestId || "").trim();
      const cycle_id = String(extra.cycle_id || "").trim();
      if (!request_id && !cycle_id) throw new LifeApiContractError("request_id or cycle_id is required", "empty_execution_reference");
      return call("executionStatus", { body: { request_id, ...extra } });
    },
    getState: (options = {}) => call("state", options),
    getPanel: (options = {}) => call("panel", options),
    markInboxRead(messageId, extra = {}) {
      const message_id = String(messageId || "").trim();
      if (!message_id) throw new LifeApiContractError("message_id is required", "empty_message_id");
      return call("inboxRead", { body: { message_id, ...extra } });
    },
    deleteInboxMessage(messageId, extra = {}) {
      const message_id = String(messageId || "").trim();
      if (!message_id) throw new LifeApiContractError("message_id is required", "empty_message_id");
      return call("inboxDelete", { body: { message_id, ...extra } });
    },
    getPendingProactiveChats: (options = {}) => call("proactiveChatPending", options),
    ackProactiveChat(messageId, extra = {}) {
      const message_id = String(messageId || "").trim();
      if (!message_id) throw new LifeApiContractError("message_id is required", "empty_message_id");
      return call("proactiveChatAck", { body: { message_id, actor: "frontend", ...extra } });
    },
    updateSettings(settings, extra = {}) {
      return call("settingsUpdate", { body: { settings: settings || {}, actor: "user", ...extra } });
    },
    getCapabilityOverlay(extra = {}) {
      return call("capabilityOverlay", { query: extra });
    },
    proposeCapability(card, extra = {}) {
      if (!card || typeof card !== "object" || Array.isArray(card)) throw new LifeApiContractError("card is required", "invalid_capability_candidate");
      return call("capabilityPropose", { body: { card, actor: "user", ...extra } });
    },
    approveCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityApprove", { body: { artifact_id, actor: "user", ...extra } });
    },
    buildCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityBuild", { body: { artifact_id, actor: "user", ...extra } });
    },
    publishCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityPublish", { body: { artifact_id, actor: "user", ...extra } });
    },
    discardCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityDiscard", { body: { artifact_id, actor: "user", ...extra } });
    },
    invokeCapability(artifactId, inputs = {}, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) throw new LifeApiContractError("inputs must be an object", "invalid_capability_inputs");
      return call("capabilityInvoke", { body: { artifact_id, inputs, actor: "user", ...extra } });
    },
    rollbackCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityRollback", { body: { artifact_id, actor: "user", ...extra } });
    },
    reactivateCapability(artifactId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      return call("capabilityReactivate", { body: { artifact_id, actor: "user", ...extra } });
    },
    recordCapabilityUsage(artifactId, executionEventId, extra = {}) {
      const artifact_id = String(artifactId || "").trim();
      const execution_event_id = String(executionEventId || "").trim();
      if (!artifact_id) throw new LifeApiContractError("artifact_id is required", "empty_artifact_id");
      if (!execution_event_id) throw new LifeApiContractError("execution_event_id is required", "empty_execution_event_id");
      return call("capabilityUsage", { body: { artifact_id, execution_event_id, actor: "life_evaluator", ...extra } });
    },
    decideUpgrade(decision, cardId, extra = {}) {
      const card_id = String(cardId || "").trim();
      if (!card_id) throw new LifeApiContractError("card_id is required", "empty_card_id");
      const routeName = decision === "confirm" ? "upgradeConfirm" : decision === "cancel" ? "upgradeCancel" : "";
      if (!routeName) throw new LifeApiContractError(`Unknown upgrade decision: ${decision}`, "unknown_upgrade_decision");
      return call(routeName, { body: { card_id, actor: "user", ...extra } });
    },
    completeUpgrade(cardId, extra = {}) {
      const card_id = String(cardId || "").trim();
      if (!card_id) throw new LifeApiContractError("card_id is required", "empty_card_id");
      return call("upgradeComplete", { body: { card_id, actor: "execution_bridge", ...extra } });
    },
    transitionLearning(action, cardId, extra = {}) {
      const routeName = LEARNING_ROUTE_BY_ACTION[action];
      if (!routeName) throw new LifeApiContractError(`Unknown learning action: ${action}`, "unknown_learning_action");
      const card_id = String(cardId || "").trim();
      if (!card_id) throw new LifeApiContractError("card_id is required", "empty_card_id");
      return call(routeName, { body: { card_id, actor: "user", ...extra } });
    }
  });
}

export const lifeApi = createLifeApiClient();
