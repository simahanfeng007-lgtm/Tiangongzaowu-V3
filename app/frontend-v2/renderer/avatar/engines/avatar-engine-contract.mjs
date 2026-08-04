// Avatar P3 引擎能力契约（方案 §7.3/§13）。
// 纯数据、常量与校验函数：无 three.js 依赖、无 DOM/时钟副作用，可在 Node 端直接测试。
// 引擎实现只暴露语义命令；§7.2 禁止向 UI 公开 THREE.Scene/WebGLRenderer/VRM 实例等。

export const AVATAR_ENGINE_CONTRACT_VERSION = "avatar-engine-contract-1.0.0";

// ── §7.3 引擎能力键 ─────────────────────────────────────────
export const EngineCapability = Object.freeze({
  HUMANOID: "humanoid",
  EXPRESSION: "expression",
  LOOK_AT: "lookAt",
  SPRING_BONE: "springBone",
  VRMA: "vrma",
  LIP_SYNC: "lipSync",
  RELIGHTING: "relighting",
  MULTI_VIEW: "multiView",
  WEBGL: "webgl",
  WEBGPU: "webgpu",
  GAUSSIAN: "gaussian",
});

export const ENGINE_CAPABILITY_KEYS = Object.freeze(Object.values(EngineCapability));

// ThreeVrmEngine 能力声明：当前仅 WebGL 单 Canvas（§1.12 单 Canvas 为实现约束）。
// multiView/webgpu/gaussian 必须显式为 false，未来引擎另作声明（§13.3）。
export const THREE_VRM_ENGINE_CAPABILITIES = Object.freeze({
  humanoid: true,
  expression: true,
  lookAt: true,
  springBone: true,
  vrma: true,
  lipSync: true,
  relighting: true,
  multiView: false,
  webgl: true,
  webgpu: false,
  gaussian: false,
});

// 能力声明结构校验：键必须恰好覆盖 §7.3 全集，值必须为布尔。返回错误码列表，空数组通过。
export function validateEngineCapabilities(capabilities) {
  const errors = [];
  if (capabilities === null || typeof capabilities !== "object" || Array.isArray(capabilities)) {
    return ["capabilities_not_object"];
  }
  for (const key of ENGINE_CAPABILITY_KEYS) {
    if (typeof capabilities[key] !== "boolean") errors.push(`capability_${key}_invalid`);
  }
  for (const key of Object.keys(capabilities)) {
    if (!ENGINE_CAPABILITY_KEYS.includes(key)) errors.push(`capability_unknown:${key}`);
  }
  return errors;
}

// ── 语义命令接口（§7.2：上层只能发送语义命令）────────────────
export const ENGINE_SEMANTIC_COMMANDS = Object.freeze([
  "applyPosture",
  "applyExpression",
  "applyGaze",
  "playGesture",
  "setSpeaking",
  "viseme",
  "applyCameraPresentation",
  "applyLighting",
  "attachSurface",
  "detachSurface",
  "loadModel",
  "disposeModel",
  "disposeEngine",
]);

// ── 引擎事件 ────────────────────────────────────────────────
// FIRST_RENDERABLE_FRAME：引擎提交首个可渲染帧后发出的输入信号，
// 供 Runtime 的 renderability-probe / FIRST_VISIBLE_FRAME 探针消费（§18.2），引擎自身不做可见性判定。
// CONTEXT_LOST/CONTEXT_RESTORED：仅事件通知；恢复流程由 Runtime 主导（§20.3），引擎只暴露事件与重建原语。
export const EngineEvent = Object.freeze({
  FIRST_RENDERABLE_FRAME: "first-renderable-frame",
  CONTEXT_LOST: "context-lost",
  CONTEXT_RESTORED: "context-restored",
  MODEL_LOADED: "model-loaded",
  MODEL_DISPOSED: "model-disposed",
  GESTURE_SET_LOADED: "gesture-set-loaded",
  ENGINE_DISPOSED: "engine-disposed",
});

// VRM 规范版本标识（§12.2：只读 extensions.VRM / extensions.VRMC_vrm，禁止按文件名判断）。
export const VrmSpecVersion = Object.freeze({
  VRM0: "0.x",
  VRM1: "1.0",
});

// 极简事件槽：引擎内部使用；off 幂等，emit 同步逐个调用，监听器异常不阻断后续监听器。
export function createEngineEventSink() {
  const listeners = new Map();
  return {
    on(event, listener) {
      if (typeof listener !== "function") throw new Error("引擎事件监听器必须是函数");
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(listener);
      return () => this.off(event, listener);
    },
    off(event, listener) {
      listeners.get(event)?.delete(listener);
    },
    emit(event, payload) {
      for (const listener of [...(listeners.get(event) ?? [])]) {
        try {
          listener(payload);
        } catch (err) {
          // 事件消费者异常不得击穿引擎渲染路径。
          queueMicrotask(() => { throw err; });
        }
      }
    },
    listenerCount(event) {
      return listeners.get(event)?.size ?? 0;
    },
    clear() {
      listeners.clear();
    },
  };
}
