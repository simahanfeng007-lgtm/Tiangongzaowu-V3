// EngineHarness：独立桌宠页调试壳（方案 §25）。
// 只实例化并包装共享 ThreeVrmEngine，供 VRM 兼容测试、镜头/灯光调参、
// 动作/表情/视线验证、VRMA 验证、context lost 测试与资源估算展示。
// 本调试壳不注册为正式 AvatarRuntime，不连接 BodyCommandScheduler，
// 不取得 TTS 所有权，也不向 service-registry 登记（§25）。

import { EngineEvent } from "./engines/avatar-engine-contract.mjs";
import { createThreeVrmEngine } from "./engines/three-vrm-engine.mjs";

export function createEngineHarness(options = {}) {
  const engine = createThreeVrmEngine(options);
  const canvas = options.canvas ?? null;

  const harness = {
    engine,
    contractVersion: engine.contractVersion,
    capabilities: engine.capabilities,

    on: (event, listener) => engine.on(event, listener),
    off: (event, listener) => engine.off(event, listener),

    // ── 模型/动作加载（字节由调用方经业务资产通道取得，调试壳不经手 IPC）──
    loadModelFromBytes: (bytes, meta = {}) => engine.loadModel(bytes, meta),
    loadGesturesFromBytes: (map, opts = {}) => engine.loadGesturesFromBytes(map, opts),

    // ── 动作/表情/视线/口型验证 ──
    playGesture: (key, opts = {}) => engine.playGesture(key, opts),
    stopGestures: (opts = {}) => engine.playGesture(null, opts),
    currentGesture: () => engine.currentGesture(),
    hasGestureMixer: () => engine.hasGestureMixer(),
    updateGesture: (dt) => engine.updateGesture(dt),
    applyExpression: (name, value) => engine.applyExpression(name, value),
    applyVisemeTarget: (targets) => engine.applyVisemeTarget(targets),
    applyGaze: (gaze = {}) => engine.applyGaze(gaze),
    applyPosture: (posture = {}) => engine.applyPosture(posture),
    setSpeaking: (speaking) => engine.setSpeaking(speaking),
    mapViseme: (ch, index = 0) => engine.mapViseme(ch, index),

    // ── 镜头/灯光调参 ──
    applyCameraPresentation: (params = {}) => engine.applyCameraPresentation(params),
    applyLighting: (params = {}) => engine.applyLighting(params),
    setExposure: (value) => engine.setExposure(value),
    setViewport: (width, height) => engine.setViewport(width, height),

    // ── 帧推进 ──
    update: (dt) => engine.update(dt),
    renderFrame: () => engine.renderFrame(),

    // ── 资源估算展示 ──
    getResourceEstimate: () => engine.getStats(),

    // ── context lost 测试（调试壳受控故障注入：向 canvas 派发合成 DOM 事件，
    //    走与真实 webglcontextlost/restored 相同的引擎监听路径；引擎自身不主动丢失 context）──
    simulateContextLost() {
      if (!canvas || typeof canvas.dispatchEvent !== "function") return false;
      return canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    },
    simulateContextRestored() {
      if (!canvas || typeof canvas.dispatchEvent !== "function") return false;
      return canvas.dispatchEvent(new Event("webglcontextrestored"));
    },
    isContextLost: () => engine.isContextLost(),
    // §20.3 恢复原语（Runtime 主导恢复流程时调用）。
    recreateRenderer: () => engine.recreateRenderer(),

    // ── 释放 ──
    disposeModel: () => engine.disposeModel(),
    disposeEngine: () => engine.disposeEngine(),

    // 调试壳内部访问（非 §7.2 公共接口）：房间场景挂载、OrbitControls 绑定、
    // 灯光调试滑杆等独立页遗留业务需要直接操作 scene/camera/renderer 时使用。
    debugInternals() {
      const internals = engine.debugInternals();
      return Object.freeze({
        renderer: internals.renderer,
        scene: internals.scene,
        camera: internals.camera,
      });
    },
    debugLights() {
      const internals = engine.debugInternals();
      return Object.freeze({
        mainLight: internals.lights.mainLight,
        ambientLight: internals.lights.ambientLight,
        hemiLight: internals.lights.hemiLight,
        rimLight: internals.lights.rimLight,
        warmSideLight: internals.lights.warmSideLight,
        LIGHTING_BASE: internals.LIGHTING_BASE,
      });
    },
  };

  return harness;
}

export { EngineEvent };
