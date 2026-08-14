// P6b 启动组装：ThreeVrmEngine → AvatarRuntime 引擎适配器（鸭子类型，见 avatar-runtime.mjs 头注释）。
//
// 核心事实：ThreeVrmEngine 是单模型槽引擎（loadModel 先 dispose 前一模型，§11.4），
// 而 AvatarRuntime 的事务切换语义（§11.2/§18.5）要求候选与旧 current 在
// provisional-present 期间双驻留、可回滚。本适配器在**不修改引擎**的前提下用
// 可见性槽 + 字节保留桥接两者：
//
//   loadCandidate     引擎解析（addToScene:true 后立刻 visible=false）→ staging 离屏。
//                     引擎单槽约束：旧模型在此刻被引擎释放，适配器保留其字节供回滚重建。
//   renderCandidateFrame  候选置可见 → 渲染一帧 → 返回 { drawCalls }；
//                     引擎 FIRST_RENDERABLE_FRAME 无 attemptId，桥接时按当前候选补挂（§19.1.6）。
//   present/conceal   可见性切换（不重建、不重解析）。
//   promoteCandidate  候选转正为 presented（字节继续保留，供下一次切换的回滚）。
//   restorePresented  回滚：若旧模型已被引擎释放（stale），从保留字节异步重建并呈现；
//                     同步路径只登记归属，重建完成后自然出现在后续渲染帧。
//   disposeModel      幂等释放；stale 句柄（引擎早已释放）仅做记账清理。
//   discardInvalidatedModel  context-lost 引用级剔除：不触 GL，只丢引用与字节。
//
// 已知取舍（单槽引擎的固有降级，不改变运行语义与终态正确性）：
//   1. 候选解析期间旧模型已被释放，RAF 渲染短暂空场（候选 visible=false），
//      直至 provisional-present（通常 < 1s）。回滚重建同理（字节重解析期间空场）。
//   2. 内存中最多保留 presented + candidate 两份源字节（内置双模型约 126MB），
//      由 §11.1 资源预算（默认 512MB）覆盖。
//
// 纪律：不暴露 THREE 对象（§7.2——返回句柄为不透明记录，vrm 引用仅供适配器内部
// 可见性操作）；所有引擎 loadModel 经单串行队列，杜绝切换竞态。

import { EngineEvent, createEngineEventSink } from "./engines/avatar-engine-contract.mjs";

export const THREE_VRM_RUNTIME_ADAPTER_VERSION = "three-vrm-3.5.5";

export class ThreeVrmRuntimeAdapterError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ThreeVrmRuntimeAdapterError";
    this.code = code;
  }
}

export function createThreeVrmRuntimeAdapter({
  engine,
  engineVersion = THREE_VRM_RUNTIME_ADAPTER_VERSION,
  onEngineError = null,
} = {}) {
  if (engine === null || typeof engine !== "object" || typeof engine.loadModel !== "function") {
    throw new ThreeVrmRuntimeAdapterError("engine_invalid", "适配器需要 ThreeVrmEngine 实例（loadModel/renderFrame/on）");
  }

  const sink = createEngineEventSink();
  const handles = new Map(); // handleId → 内部记录（即对外不透明句柄）
  let handleSeq = 0;
  let presentedHandleId = null; // 已转正模型（回滚目标）
  let engineOwnerHandleId = null; // 当前占用引擎单模型槽的句柄
  let firstFrameHandleId = null; // 等待桥接 FIRST_RENDERABLE_FRAME 的候选
  let loadChain = Promise.resolve(); // 引擎 loadModel 单串行队列

  function reportEngineError(stage, error) {
    try {
      onEngineError?.(Object.freeze({ stage, code: error?.code ?? "engine_error", message: String(error?.message ?? error) }));
    } catch (_error) {
      // 诊断回调异常不阻断渲染路径
    }
  }

  // 引擎 loadModel 单串行化：selectModel latest-wins 与回滚重建不得交错进入引擎。
  function enqueueEngineLoad(fn) {
    const run = loadChain.then(fn, fn);
    loadChain = run.catch(() => {});
    return run;
  }

  function asRecord(handle) {
    if (handle === null || typeof handle !== "object") return null;
    const record = handles.get(handle.handleId);
    return record !== undefined && record === handle ? record : null;
  }

  function setVisible(record, visible) {
    if (record === null || record.disposed || record.stale) return;
    try {
      if (record.vrm?.scene) record.vrm.scene.visible = visible;
    } catch (_error) {
      // 可见性操作失败不阻断流程（引擎事件/探针会暴露真实渲染态）
    }
  }

  // ── 引擎事件桥（§19.1.6：FIRST_RENDERABLE_FRAME 必须带 attemptId 归属）──
  engine.on?.(EngineEvent.FIRST_RENDERABLE_FRAME, (payload) => {
    const record = firstFrameHandleId !== null ? handles.get(firstFrameHandleId) : null;
    if (record && record.expectsFirstFrame) {
      record.expectsFirstFrame = false;
      firstFrameHandleId = null;
      sink.emit(EngineEvent.FIRST_RENDERABLE_FRAME, Object.freeze({
        attemptId: record.attemptId,
        label: payload?.label ?? record.label,
        specVersion: payload?.specVersion ?? null,
      }));
      return;
    }
    // 无 attempt 归属的发射（回滚重建等）：转发但不带 attemptId，Runtime 按 §19.1.7 不采信。
    sink.emit(EngineEvent.FIRST_RENDERABLE_FRAME, Object.freeze({
      label: payload?.label ?? null,
      specVersion: payload?.specVersion ?? null,
    }));
  });
  engine.on?.(EngineEvent.CONTEXT_LOST, (payload) => sink.emit(EngineEvent.CONTEXT_LOST, payload));
  engine.on?.(EngineEvent.CONTEXT_RESTORED, (payload) => sink.emit(EngineEvent.CONTEXT_RESTORED, payload));
  engine.on?.(EngineEvent.MODEL_LOADED, (payload) => sink.emit(EngineEvent.MODEL_LOADED, payload));
  engine.on?.(EngineEvent.MODEL_DISPOSED, (payload) => sink.emit(EngineEvent.MODEL_DISPOSED, payload));
  engine.on?.(EngineEvent.GESTURE_SET_LOADED, (payload) => sink.emit(EngineEvent.GESTURE_SET_LOADED, payload));
  engine.on?.(EngineEvent.ENGINE_DISPOSED, (payload) => sink.emit(EngineEvent.ENGINE_DISPOSED, payload));

  function readHostViewport(host) {
    try {
      const viewport = typeof host?.getViewport === "function" ? host.getViewport() : null;
      if (viewport && Number.isFinite(viewport.width) && Number.isFinite(viewport.height) &&
          viewport.width >= 1 && viewport.height >= 1) {
        return { width: viewport.width, height: viewport.height };
      }
    } catch (_error) {
      // 宿主视口不可读：交给引擎既有视口
    }
    return null;
  }

  const adapter = {
    engineVersion,

    // ── 事件 ──
    on: (event, listener) => sink.on(event, listener),
    off: (event, listener) => sink.off(event, listener),

    // ── 候选加载（staging，不呈现）──
    async loadCandidate(bytes, { label, attemptId } = {}) {
      if (typeof attemptId !== "string" || attemptId.length === 0) {
        throw new ThreeVrmRuntimeAdapterError("attempt_id_invalid", "loadCandidate 需要非空 attemptId（§19.1.6 归属）");
      }
      const record = await enqueueEngineLoad(async () => {
        // 引擎单槽：loadModel 无条件先释放旧模型（含 presented）——无论解析成败，
        // 旧模型在引擎侧都已失效，先标 stale（字节已在旧句柄上保留供回滚）。
        const previous = engineOwnerHandleId !== null ? handles.get(engineOwnerHandleId) : null;
        if (previous && !previous.disposed) previous.stale = true;
        let result;
        try {
          result = await engine.loadModel(bytes, { label, addToScene: true });
        } catch (error) {
          engineOwnerHandleId = null; // 引擎槽已空（旧模型已释放、新模型未建成）
          firstFrameHandleId = null;
          throw error;
        }
        handleSeq += 1;
        const next = {
          kind: "three-vrm-candidate-handle",
          handleId: `cand_${handleSeq}`,
          attemptId,
          label: typeof label === "string" ? label : "VRM",
          vrm: result.vrm ?? null,
          bytes, // 保留源字节：该候选转正后成为回滚重建材料
          presented: false,
          disposed: false,
          gpuInvalidated: false,
          stale: false, // 引擎槽被后续加载取代后置 true
          expectsFirstFrame: true,
        };
        handles.set(next.handleId, next);
        engineOwnerHandleId = next.handleId;
        firstFrameHandleId = next.handleId;
        setVisible(next, false); // staging：在场景内但不可见（renderCandidateFrame 才上屏探针）
        return next;
      });
      return record;
    },

    // GPU 上传：three.js 在首次渲染时惰性上传纹理，无独立上传相位（空操作，语义对齐）。
    async uploadCandidate(handle) {
      asRecord(handle); // 句柄形状校验（外来句柄静默忽略，鸭子语义幂等）
    },

    // 离屏首帧（renderability-probe）：候选置可见 → 渲染一帧 → drawCalls。
    renderCandidateFrame(handle) {
      const record = asRecord(handle);
      if (record === null || record.disposed) {
        throw new ThreeVrmRuntimeAdapterError("handle_stale", "renderCandidateFrame 需要活动候选句柄");
      }
      if (engineOwnerHandleId !== record.handleId) {
        throw new ThreeVrmRuntimeAdapterError("handle_stale", "候选句柄已被后续加载取代");
      }
      setVisible(record, true);
      engine.renderFrame();
      return { drawCalls: engine.getStats?.().drawCalls ?? 0 };
    },

    // provisional-present 上屏 / 撤下（仅可见性，不重建不重解析）。
    presentCandidate(handle) {
      setVisible(asRecord(handle), true);
    },

    concealCandidate(handle) {
      setVisible(asRecord(handle), false);
    },

    // 回滚：恢复 rollbackTarget 呈现。stale（引擎槽已被候选取代）时从保留字节异步重建；
    // 同步路径只登记归属，不阻塞 Runtime 的回滚临界区。
    restorePresented(handle) {
      const record = asRecord(handle);
      if (record === null || record.disposed) return;
      presentedHandleId = record.handleId;
      if (!record.stale && engineOwnerHandleId === record.handleId) {
        setVisible(record, true);
        return;
      }
      if (record.bytes === null) {
        reportEngineError("restore-reload-unavailable", new ThreeVrmRuntimeAdapterError(
          "restore_bytes_missing", `回滚句柄 ${record.handleId} 无保留字节，无法重建`,
        ));
        return;
      }
      void enqueueEngineLoad(async () => {
        // 与 loadCandidate 同一纪律：loadModel 无条件先释放当前占用者（候选/旧模型）。
        const previous = engineOwnerHandleId !== null ? handles.get(engineOwnerHandleId) : null;
        if (previous && !previous.disposed && previous !== record) previous.stale = true;
        try {
          const result = await engine.loadModel(record.bytes, { label: record.label, addToScene: true });
          if (record.disposed) {
            // 重建期间句柄被释放：引擎里的新模型无人认领，立即清理防泄漏。
            try { engine.disposeModel?.(); } catch (_error) { /* 幂等 */ }
            engineOwnerHandleId = null;
            return;
          }
          record.vrm = result.vrm ?? null;
          record.stale = false;
          engineOwnerHandleId = record.handleId;
          setVisible(record, true);
        } catch (error) {
          engineOwnerHandleId = null;
          reportEngineError("restore-reload-failed", error);
        }
      });
    },

    // committed 后候选转正：成为新的 presented（字节保留为下次切换的回滚材料）。
    promoteCandidate(handle) {
      const record = asRecord(handle);
      if (record === null || record.disposed) return;
      record.presented = true;
      presentedHandleId = record.handleId;
    },

    // 模型级释放（幂等）：只有仍占用引擎槽的句柄才触发引擎 dispose；stale 仅记账。
    disposeModel(handle) {
      const record = asRecord(handle);
      if (record === null || record.disposed) return;
      record.disposed = true;
      record.bytes = null;
      if (engineOwnerHandleId === record.handleId) {
        engineOwnerHandleId = null;
        try {
          engine.disposeModel?.();
        } catch (error) {
          reportEngineError("dispose-model-failed", error);
        }
      }
      if (presentedHandleId === record.handleId) presentedHandleId = null;
      handles.delete(record.handleId);
    },

    // GPU 失效引用级剔除（§18.4/§20.3）：不触 GL。
    discardInvalidatedModel(handle) {
      const record = asRecord(handle);
      if (record === null) return;
      record.gpuInvalidated = true;
      record.disposed = true;
      record.stale = true;
      record.bytes = null;
      if (engineOwnerHandleId === record.handleId) engineOwnerHandleId = null;
      if (presentedHandleId === record.handleId) presentedHandleId = null;
      handles.delete(record.handleId);
    },

    // ── 帧推进与统计 ──
    renderFrame() {
      return engine.renderFrame();
    },

    update(dtSeconds) {
      engine.update?.(dtSeconds);
      engine.updateGesture?.(dtSeconds); // VRMA mixer 与 vrm.update 同链推进
    },

    getStats() {
      return engine.getStats();
    },

    isContextLost() {
      return engine.isContextLost();
    },

    recreateRenderer() {
      return engine.recreateRenderer();
    },

    // ── 表面管理：Runtime attachSurface({host,leaseId}) / service.rehostSurface 共用 ──
    attachSurface({ host, leaseId } = {}) {
      return engine.attachSurface({ host, viewport: readHostViewport(host) });
    },

    detachSurface() {
      return engine.detachSurface();
    },

    // ── 语义命令（§7.2：统一经 applyPerformanceSemantics 规范化/降级）──
    applyPosture(input) {
      return engine.applyPerformanceSemantics({ posture: input });
    },

    applyExpression(input) {
      return engine.applyPerformanceSemantics({ expression: input });
    },

    applyGaze(input) {
      // Runtime 发送 { target: <wire.gaze> }；wire.gaze 本身是 { target } 或 { x,y,z } 语义对象。
      return engine.applyPerformanceSemantics({ gaze: input?.target ?? input });
    },

    playGesture(semanticId) {
      return engine.playGesture(semanticId);
    },

    setSpeaking(speaking) {
      return engine.setSpeaking(speaking);
    },

    setConversationState(conversationState) {
      return engine.setConversationState?.(conversationState) ?? false;
    },

    applyVisemeTarget(targets) {
      return engine.applyVisemeTarget(targets);
    },

    // ── Legacy 表现驱动透传（§15 聊天互动：biaoxian wire → 驱动语义）──
    applyBodyPerformance(wire) {
      const gesture = typeof wire?.gesture === "string" ? wire.gesture : wire?.gesture?.semanticId ?? null;
      const data = {
        channel: typeof wire?.channel === "string" ? wire.channel : null,
        expression: typeof wire?.expression?.name === "string" ? wire.expression.name : null,
        gaze: typeof wire?.gaze?.target === "string" ? wire.gaze.target : null,
        posture: typeof wire?.posture === "string" ? wire.posture : null,
        gesture,
        tail: typeof wire?.extras?.tail === "string" ? wire.extras.tail : null,
        intensity: wire?.intensity,
        duration: Number.isFinite(wire?.durationMs) ? wire.durationMs / 1000 : undefined,
        source: typeof wire?.extras?.source === "string" ? wire.extras.source : "client",
      };
      return engine.applyBodyPerformance?.(data) ?? false;
    },

    loadGestures(gestureBytesByKey, options) {
      return engine.loadGesturesFromBytes?.(gestureBytesByKey, options);
    },

    setQinggan(qinggan) {
      return engine.setQinggan?.(qinggan) ?? false;
    },

    beginSpeech(text, speechPlan = null) {
      return engine.beginSpeech?.(text, speechPlan) ?? false;
    },

    applySpeechBoundary(boundary) {
      return engine.applySpeechBoundary?.(boundary) ?? false;
    },

    setSpeechEnergy(energy) {
      return engine.setSpeechEnergy?.(energy) ?? false;
    },

    applyPerformanceSemantics(semantics) {
      return engine.applyPerformanceSemantics(semantics);
    },

    applyCameraPresentation(presentation) {
      return engine.applyCameraPresentation?.(presentation);
    },

    applyLighting(presentation) {
      return engine.applyLighting?.(presentation);
    },

    // ── 探针采样鸭子（§19.1：默认实现，探针证据以渲染统计为准）──
    candidateBoundsIntersectViewport() {
      return true;
    },

    hasFatalRendererError() {
      return false;
    },

    disposeEngine() {
      for (const record of [...handles.values()]) {
        record.disposed = true;
        record.bytes = null;
      }
      handles.clear();
      presentedHandleId = null;
      engineOwnerHandleId = null;
      firstFrameHandleId = null;
      return engine.disposeEngine?.();
    },
  };

  return adapter;
}
