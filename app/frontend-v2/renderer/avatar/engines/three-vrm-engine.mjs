// Avatar P3 共享引擎：ThreeVrmEngine（方案 §13，从 桌面宠物.html 提取）。
// 职责（§13.1）：Renderer/Scene/Camera/Light、VRM 加载与版本适配、VRMA、
// 姿态/表情/视线、SpringBone（three-vrm 接管）、资源统计、
// FIRST_RENDERABLE_FRAME 输入信号、context lost/restored、资源释放。
// 禁止职责（§13.2）：Electron IPC、文件选择、用户身份、后端 API、TTS 播放、
// 身体设置 UI、本地存储类业务读写、模型许可决策——本模块一律不实现。
// 加载入口只接受已验证 ArrayBuffer/Uint8Array（§8.2/§29），不提供 raw URL/路径加载。

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
// OrbitControls 用相对路径（不经 importmap 的 three/addons 别名），
// 保证预览调试页（appdeps 无 controls 目录）与桌面端都能解析。
import { OrbitControls } from "../../../../node_modules/three/examples/jsm/controls/OrbitControls.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  VRMLookAtQuaternionProxy,
  createVRMAnimationClip,
} from "@pixiv/three-vrm-animation";
import {
  createLegacyPerformanceDriver,
  VRMA_GESTURE_KEYS,
} from "./legacy-performance-driver.mjs";

import {
  AVATAR_ENGINE_CONTRACT_VERSION,
  EngineEvent,
  THREE_VRM_ENGINE_CAPABILITIES,
  VrmSpecVersion,
  createEngineEventSink,
} from "./avatar-engine-contract.mjs";
import {
  adaptVrm0Runtime,
  analyzeVrm0GltfJson,
  detectVrmSpecVersion,
  projectVrm0LicenseRecord,
  vrm0ExpressionAliases,
} from "../compatibility/vrm0-adapter.mjs";
import {
  adaptVrm1Runtime,
  analyzeVrm1GltfJson,
  projectVrm1LicenseRecord,
  vrm1ExpressionAliases,
} from "../compatibility/vrm1-adapter.mjs";

export class AvatarEngineError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarEngineError";
    this.code = code;
  }
}

// ── 与提取前 桌面宠物.html 逐值一致的渲染基线 ────────────────
export const LIGHTING_BASE = Object.freeze({ key: 5.5, ambient: 0.18, hemi: 0.28, rim: 0.18, warm: 0.20 });

const DEFAULT_EXPOSURE = 0.78;
const DEFAULT_CAMERA_PRESET = Object.freeze({
  fov: 36,
  near: 0.05,
  far: 50,
  focus: Object.freeze([0, 1.52, 0.10]),
  distance: 2.05,
  side: 0.01,
  lift: 0.14,
});
const DEFAULT_FOG = Object.freeze({ color: 0xf4dfc8, density: 0.012 });

// VRMA 动作默认播放速度（parity：clapping 0.72 / lookAround 0.82 / 其余 0.62）。
const GESTURE_DEFAULT_TIMESCALES = Object.freeze({ clapping: 0.72, lookAround: 0.82 });
const GESTURE_FALLBACK_TIMESCALE = 0.62;

// P6a 姿态语义槽（§15.2 posture 状态型）：语义名 → humanoid 骨骼旋转槽。
// neutral 为恒等槽（不写任何骨骼 = 保持当前姿态）；更多槽由 registerPostureSlot 登记。
export const POSTURE_SEMANTIC_SLOTS = Object.freeze({
  neutral: Object.freeze({ bones: Object.freeze({}) }),
});

// P6a 视线语义目标：命名目标一律解析为引擎相机（vrm.lookAt.target 缺省绑定）。
const GAZE_SEMANTIC_TARGETS = Object.freeze(["camera", "user", "front", "reset"]);

// P6a 校准诊断环上限（超出丢弃最旧）。
const PERFORMANCE_DIAGNOSTICS_LIMIT = 128;

// 口型 viseme 字符映射（parity：原 visemeForChar）。
const VISEME_CYCLE = Object.freeze(["aa", "ih", "ou", "ee", "oh"]);
export function mapVisemeChar(ch, index = 0) {
  const c = String(ch || "").toLowerCase();
  if (/[a]/.test(c)) return "aa";
  if (/[i]/.test(c)) return "ih";
  if (/[u]/.test(c)) return "ou";
  if (/[e]/.test(c)) return "ee";
  if (/[o]/.test(c)) return "oh";
  const code = c.charCodeAt(0) || 0;
  if (code >= 0x4e00 && code <= 0x9fff) {
    return VISEME_CYCLE[(code + index) % VISEME_CYCLE.length];
  }
  return VISEME_CYCLE[index % VISEME_CYCLE.length];
}

// ── GLB/glTF 容器结构读取（纯字节级，Node 可测）──────────────
const GLB_MAGIC = 0x46546c67; // "glTF"
const GLB_CHUNK_JSON = 0x4e4f534a; // "JSON"

// 从 ArrayBuffer/Uint8Array 读取 glTF JSON（GLB 容器或裸 JSON）。不触碰 three.js。
export function sniffGltfJsonBytes(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (view.byteLength < 12) throw new AvatarEngineError("gltf_container_invalid", "字节长度不足，不是 glTF/GLB");
  const header = new DataView(view.buffer, view.byteOffset, view.byteLength);
  if (header.getUint32(0, true) === GLB_MAGIC) {
    const version = header.getUint32(4, true);
    const declaredLength = header.getUint32(8, true);
    if (declaredLength !== view.byteLength) {
      throw new AvatarEngineError("glb_length_mismatch", `GLB declaredLength=${declaredLength} 与实际 ${view.byteLength} 不一致`);
    }
    const chunkLength = header.getUint32(12, true);
    const chunkType = header.getUint32(16, true);
    if (chunkType !== GLB_CHUNK_JSON || 20 + chunkLength > view.byteLength) {
      throw new AvatarEngineError("glb_json_chunk_missing", "GLB 首个 chunk 不是 JSON");
    }
    const jsonBytes = view.subarray(20, 20 + chunkLength);
    return Object.freeze({
      container: "glb",
      glbVersion: version,
      json: JSON.parse(new TextDecoder().decode(jsonBytes)),
    });
  }
  // 裸 JSON glTF。
  try {
    return Object.freeze({ container: "json", glbVersion: null, json: JSON.parse(new TextDecoder().decode(view)) });
  } catch (err) {
    throw new AvatarEngineError("gltf_container_invalid", `无法解析 glTF JSON: ${err.message}`);
  }
}

function isAcceptedModelBytes(value) {
  return value instanceof ArrayBuffer || ArrayBuffer.isView(value);
}

function toArrayBuffer(value) {
  if (value instanceof ArrayBuffer) return value;
  if (ArrayBuffer.isView(value)) {
    return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
  }
  throw new AvatarEngineError("model_bytes_invalid", "loadModel 只接受已验证 ArrayBuffer/Uint8Array（§8.2）");
}

// 材质贴图槽：dispose 时遍历释放 Texture。
const MATERIAL_TEXTURE_SLOTS = Object.freeze([
  "map", "normalMap", "bumpMap", "roughnessMap", "metalnessMap", "emissiveMap",
  "specularMap", "envMap", "aoMap", "alphaMap", "lightMap", "displacementMap",
  "gradientMap", "matcap", "transmissionMap", "thicknessMap", "sheenColorMap",
  "sheenRoughnessMap", "iridescenceMap", "iridescenceThicknessMap", "clearcoatMap",
  "clearcoatNormalMap", "clearcoatRoughnessMap", "anisotropyMap",
]);

function defaultRendererFactory({ canvas }) {
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
}

// P6a §14.3 rehost 宿主解析：宿主可以是 DOM 元素（appendChild）或带 element 的包装。
function resolveHostElement(host) {
  if (host !== null && typeof host === "object") {
    if (typeof host.appendChild === "function") return host;
    if (host.element !== null && typeof host.element === "object" && typeof host.element.appendChild === "function") {
      return host.element;
    }
  }
  return null;
}

function defaultParseGltf(arrayBuffer) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  return new Promise((resolve, reject) => loader.parse(arrayBuffer, "", resolve, reject));
}

function defaultParseVrma(arrayBuffer) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
  return new Promise((resolve, reject) => loader.parse(arrayBuffer, "", resolve, reject));
}

// ── P6a 语义规范化（biaoxian/WireBodyAction 语义 → 引擎目标；纯函数）────────
function normalizeExpressionSemantic(raw) {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "string") return raw.length > 0 ? { name: raw, intensity: 1 } : null;
  if (typeof raw === "object") {
    const name = typeof raw.name === "string" ? raw.name : typeof raw.expression === "string" ? raw.expression : null;
    if (name === null || name.length === 0) return null;
    const num = Number(raw.intensity);
    return { name, intensity: Number.isFinite(num) ? Math.max(0, Math.min(1, num)) : 1 };
  }
  return null;
}

function normalizeGazeSemantic(raw) {
  if (raw === null || raw === undefined) return null;
  const target = typeof raw === "string" ? raw : typeof raw === "object" ? raw.target ?? raw.name : null;
  if (target === null || target === undefined) return null;
  if (typeof target === "string") return target.length > 0 ? { kind: "named", name: target } : null;
  if (typeof target === "object") {
    const { x, y, z } = target;
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) return { kind: "point", point: { x, y, z } };
  }
  return null;
}

function normalizePostureSemantic(raw) {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "string") return raw.length > 0 ? { name: raw, bones: null } : null;
  if (typeof raw === "object") {
    const name = typeof raw.name === "string" && raw.name.length > 0 ? raw.name : null;
    const bones = raw.bones !== null && typeof raw.bones === "object" ? raw.bones : null;
    if (name === null && bones === null) return null;
    return { name, bones };
  }
  return null;
}

// ── ThreeVrmEngine ──────────────────────────────────────────
// options:
//   canvas             目标 canvas（必填，attachSurface 的初始形态）
//   viewport           { width, height } 初始视口（必填）
//   pixelRatio         默认 min(devicePixelRatio, 2)
//   cameraPreset       覆盖 DEFAULT_CAMERA_PRESET
//   fog                覆盖 DEFAULT_FOG；fog:null 表示不设置雾
//   exposure           初始 toneMappingExposure（默认 0.78）
//   deps               可替换依赖（测试注入点）：rendererFactory / parseGltf / parseVrma
export function createThreeVrmEngine(options = {}) {
  const { canvas, viewport } = options;
  if (canvas === null || typeof canvas !== "object" || typeof canvas.addEventListener !== "function") {
    throw new AvatarEngineError("canvas_invalid", "createThreeVrmEngine 需要可监听事件的 canvas");
  }
  if (!viewport || !Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) || viewport.width < 1 || viewport.height < 1) {
    throw new AvatarEngineError("viewport_invalid", "createThreeVrmEngine 需要有效 viewport");
  }
  const deps = options.deps ?? {};
  const rendererFactory = deps.rendererFactory ?? defaultRendererFactory;
  const parseGltf = deps.parseGltf ?? defaultParseGltf;
  const parseVrma = deps.parseVrma ?? defaultParseVrma;
  const cameraPreset = { ...DEFAULT_CAMERA_PRESET, ...(options.cameraPreset ?? {}) };
  const fogPreset = options.fog === undefined ? DEFAULT_FOG : options.fog;
  const initialExposure = Number.isFinite(options.exposure) ? options.exposure : DEFAULT_EXPOSURE;
  // 镜像显示（照镜子/自拍约定）：画面水平翻转，角色右手显示在观众右侧；
  // 拖拽方向同步反转，保证“往右拖 = 往右转”的直觉。
  const mirrorView = Boolean(options.mirrorView);

  const events = createEngineEventSink();
  const state = {
    disposed: false,
    contextLost: false,
    speaking: false,
    canvas, // P6a §14.3：当前渲染 canvas（rehost DOM 迁移的对象）
    renderer: null,
    scene: null,
    camera: null,
    lights: null,
    // 当前模型记录；null 表示无模型。
    model: null,
    // P6a 语义校准状态：latest-wins 的上一语义表情名（切换前先清零）。
    semanticExpression: null,
    // P6a 姿态语义槽注册表（name → { bones }）。
    postureSlots: new Map(Object.entries(POSTURE_SEMANTIC_SLOTS)),
    // P6a 校准诊断环（有界，最新在尾）。
    performanceDiagnostics: [],
    // 取景基线（frameCameraToModel 写入）与展示偏移（applyCameraPresentation 写入）。
    // 相机最终位姿 = 基线 + 偏移，语义与旧面板四项控件逐值一致。
    cameraFraming: null,
    cameraPresentation: { focus: 0, height: 0, distance: 0, side: 0 },
    // 交互（旧 桌面宠物.html 的 OrbitControls parity）：鼠标拖拽旋转/缩放视角。
    controls: null,
    cameraManual: false, // 用户拖拽后进入手动视角；设置/恢复默认时退出
    prevControlsTarget: null, // 镜头惯性联动 SpringBone 的上帧目标
  };
  applyMirrorView();

  function applyRendererBaseline(renderer) {
    const ratio = Number.isFinite(options.pixelRatio)
      ? options.pixelRatio
      : Math.min(Number(globalThis.devicePixelRatio) || 1, 2);
    renderer.setPixelRatio(ratio);
    renderer.setSize(viewport.width, viewport.height, false);
    renderer.shadowMap.enabled = false;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = initialExposure;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
  }

  function buildScene() {
    const scene = new THREE.Scene();
    if (fogPreset) scene.fog = new THREE.FogExp2(fogPreset.color, fogPreset.density);
    return scene;
  }

  function buildCamera() {
    const camera = new THREE.PerspectiveCamera(cameraPreset.fov, viewport.width / viewport.height, cameraPreset.near, cameraPreset.far);
    const focus = new THREE.Vector3(...cameraPreset.focus);
    camera.position.copy(focus).add(new THREE.Vector3(cameraPreset.side, cameraPreset.lift, cameraPreset.distance));
    camera.lookAt(focus);
    return camera;
  }

  // ── 鼠标交互（旧 桌面宠物.html OrbitControls parity）────────────
  // 左键拖拽旋转视角、滚轮缩放、右键平移；阻尼与距离/极角限制逐值沿用旧版。
  function createOrbitControls() {
    try {
      if (typeof globalThis.window === "undefined") return null;
      const element = state.canvas;
      if (!element || typeof element.addEventListener !== "function" || typeof element.style !== "object") {
        return null; // Node/stub 环境不创建真实交互
      }
      const controls = new OrbitControls(state.camera, element);
      controls.enableDamping = true;
      controls.dampingFactor = 0.07;
      controls.minDistance = 0.45;
      controls.maxDistance = 5.8;
      controls.maxPolarAngle = Math.PI * 0.64;
      if (mirrorView) {
        // 画面已镜像：拖拽方向取反，保持“右拖=右转”的直觉。
        controls.rotateSpeed = -1;
        controls.panSpeed = -1;
      }
      const focus = state.cameraFraming?.focus ?? new THREE.Vector3(...cameraPreset.focus);
      controls.target.copy(focus);
      controls.addEventListener("start", () => {
        state.cameraManual = true;
      });
      controls.update();
      return controls;
    } catch (_error) {
      return null; // 交互不可用不阻断渲染
    }
  }

  function ensureOrbitControls() {
    applyMirrorView();
    if (state.controls) {
      if (state.controls.domElement === state.canvas) return;
      try { state.controls.dispose(); } catch (_error) { /* 幂等 */ }
      state.controls = null;
    }
    state.controls = createOrbitControls();
  }

  function applyMirrorView() {
    if (!mirrorView || !state.canvas || typeof state.canvas.style !== "object") return;
    state.canvas.style.transform = "scaleX(-1)";
  }

  function syncOrbitTarget(focus) {
    if (!state.controls || !focus) return;
    state.controls.target.copy(focus);
    if (state.prevControlsTarget) state.prevControlsTarget.copy(focus);
    state.controls.update();
  }

  // 镜头惯性联动 SpringBone：拖拽时给模型施加反向微旋转并缓回 0，
  // 让头发/衣服动态在视角转动时可见（旧 loop 的逐值 parity）。
  function updateCameraInertia(dt) {
    const model = state.model;
    if (!state.controls || !model || model.disposed || !model.vrm?.scene) return;
    if (!state.prevControlsTarget) state.prevControlsTarget = state.controls.target.clone();
    const target = state.controls.target;
    const camDeltaX = target.x - state.prevControlsTarget.x;
    const camDeltaY = target.y - state.prevControlsTarget.y;
    state.prevControlsTarget.copy(target);
    const rotX = THREE.MathUtils.clamp(camDeltaX * 1.8, -0.04, 0.04);
    const rotZ = THREE.MathUtils.clamp(-camDeltaY * 1.5, -0.04, 0.04);
    model.vrm.scene.rotation.x += rotZ * 0.6;
    model.vrm.scene.rotation.z += rotX * 0.6;
    model.vrm.scene.rotation.x += (0 - model.vrm.scene.rotation.x) * dt * 3.5;
    model.vrm.scene.rotation.z += (0 - model.vrm.scene.rotation.z) * dt * 3.5;
  }

  function buildLights(scene) {
    // 与提取前逐值一致的五灯 rig（§13.1.1 / relighting 能力）。
    const mainLight = new THREE.SpotLight(0xfff5ec, 5.5, 10, Math.PI / 3.2, 0.55, 0.7);
    mainLight.position.set(0.5, 3.2, 3.5);
    mainLight.target.position.set(0, 1.15, 0);
    mainLight.castShadow = false;
    scene.add(mainLight);
    scene.add(mainLight.target);
    const ambientLight = new THREE.AmbientLight(0xfff0e5, 0.18);
    scene.add(ambientLight);
    const hemiLight = new THREE.HemisphereLight(0xe8eeff, 0xc8b098, 0.28);
    scene.add(hemiLight);
    const rimLight = new THREE.PointLight(0xc8d8ff, 0.18, 5);
    rimLight.position.set(2.2, 2.0, -1.5);
    scene.add(rimLight);
    const warmSideLight = new THREE.PointLight(0xffe8d0, 0.20, 4);
    warmSideLight.position.set(-2.2, 1.6, 0.6);
    scene.add(warmSideLight);
    return Object.freeze({ mainLight, ambientLight, hemiLight, rimLight, warmSideLight });
  }

  // ── 展示调谐（自 桌面宠物.html 逐值移植：模型正向 / 取景 / MToon 材质 / 曝光）──
  // 与调试页 tuneVRMMaterials 同标准；已调谐模型带 materialsTunedByEngine 标记，
  // 调试页据此跳过自身重复调谐，避免双重上色。
  const EXPOSURE_BY_LABEL = Object.freeze([
    [/Milklatte|Sugarlatte/i, 0.42],
    [/Sophina/i, 0.40],
    [/NEKONA/i, 0.52],
  ]);
  const DEFAULT_MODEL_EXPOSURE = 0.68;
  const DISPLAY_YAW = Math.PI; // VRM 模型正面朝相机（与 portraitYawForVRMLabel 一致）

  function exposureForLabel(label = "") {
    for (const [pattern, value] of EXPOSURE_BY_LABEL) if (pattern.test(label)) return value;
    return DEFAULT_MODEL_EXPOSURE;
  }

  function tuneModelMaterials(root, label = "") {
    // Sophina/Milklatte/Sugarlatte 特殊分支保留给调试页既有路径；引擎只承担通用调谐。
    if (/Sophina|Milklatte|Sugarlatte/i.test(label)) return;
    root.userData.materialsTunedByEngine = true;
    root.traverse((node) => {
      const materials = node.material ? (Array.isArray(node.material) ? node.material : [node.material]) : [];
      for (const material of materials) {
        const name = (material.name || "").toLowerCase();
        material.toneMapped = true;
        // MToon → 降 toony 感，接近 PBR
        if (material.shadingToonyFactor !== undefined) {
          material.shadingToonyFactor = Math.max(0.3, material.shadingToonyFactor * 0.6);
        }
        if (material.shadingShiftFactor !== undefined) {
          material.shadingShiftFactor = material.shadingShiftFactor * 0.5;
        }
        // 皮肤：暖色调 + 柔光
        if (/skin|body|face|head|arm|leg|hand/i.test(name) && !/hair|eye|brow|lash|mouth|tongue/i.test(name)) {
          material.roughness = Math.min(0.55, material.roughness || 0.6);
          if (material.color && material.color.isColor) {
            const { r, g, b } = material.color;
            material.color.setRGB(r * 1.04, g * 0.96, b * 0.92);
            material.needsUpdate = true;
          }
        }
        // 衣服/布料：提高粗糙度
        if (/cloth|dress|skirt|shirt|coat|jacket|outfit|top|bottom|pant|sock|shoe/i.test(name)) {
          material.roughness = Math.max(0.7, material.roughness || 0.5);
        }
        // 头发：维持光泽但不过亮
        if (/hair/i.test(name)) {
          material.roughness = Math.max(0.5, material.roughness || 0.4);
        }
      }
    });
  }

  function frameCameraToModel(vrm) {
    // 依据模型包围盒决定取景（调试页 portraitFocusForActor 同语义：头部偏上聚焦）。
    const box = new THREE.Box3().setFromObject(vrm.scene);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    if (!Number.isFinite(size.y) || size.y <= 0) return;
    const focus = new THREE.Vector3(center.x, box.min.y + size.y * 0.82, center.z + 0.04);
    const fitHeight = Math.max(0.6, size.y * 0.62); // 头部到腰的肖像比例
    const fovRad = (state.camera.fov * Math.PI) / 180;
    const distance = THREE.MathUtils.clamp(
      fitHeight / (2 * Math.tan(fovRad / 2)),
      cameraPreset.near * 4,
      cameraPreset.far * 0.4,
    );
    state.cameraFraming = { focus, distance };
    composeFramedCamera();
  }

  // 相机最终位姿 = 取景基线 + 展示偏移。与旧面板 setMainCameraSetting 同语义：
  // focus→焦点高低、height→镜头升降、distance→远近增量、side→左右平移（焦点反向）。
  function composeFramedCamera() {
    const framing = state.cameraFraming;
    if (!framing || !state.camera) return false;
    const prefs = state.cameraPresentation;
    const focus = framing.focus.clone();
    focus.x -= prefs.side;
    focus.y += prefs.focus;
    const distance = THREE.MathUtils.clamp(
      framing.distance + prefs.distance,
      cameraPreset.near * 4,
      cameraPreset.far * 0.4,
    );
    state.camera.position.copy(focus).add(
      new THREE.Vector3(cameraPreset.side, cameraPreset.lift + prefs.height, distance),
    );
    state.camera.lookAt(focus);
    state.camera.updateProjectionMatrix();
    // 展示设置/重新取景时退出手动拖拽视角（与旧面板滑块=恢复主镜头一致）。
    state.cameraManual = false;
    syncOrbitTarget(focus);
    return true;
  }

  function orientAndPresentModel(vrm, label) {
    vrm.scene.rotation.y = DISPLAY_YAW;
    tuneModelMaterials(vrm.scene, label);
    state.renderer.toneMappingExposure = exposureForLabel(label);
    frameCameraToModel(vrm);
  }

  // ── context lost/restored（§20.3：引擎只暴露事件与重建原语）──
  function onContextLost(event) {
    // §20.3.1 preventDefault：允许浏览器后续触发 restored。
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    if (state.contextLost) return;
    state.contextLost = true;
    // §20.3.2/.4：停 RAF/禁止提交 GPU 命令——renderFrame 在 contextLost 期间直接跳过。
    events.emit(EngineEvent.CONTEXT_LOST, Object.freeze({ at: Date.now() }));
  }

  function onContextRestored() {
    if (!state.contextLost) return;
    state.contextLost = false;
    // 恢复流程（重建 Renderer、RecoveryLoadAttempt、状态快照恢复）由 Runtime 主导，
    // 引擎只发事件；需要整体重建时由调用方使用 recreateRenderer() 原语（§20.3）。
    events.emit(EngineEvent.CONTEXT_RESTORED, Object.freeze({ at: Date.now() }));
  }

  function bindContextListeners(targetCanvas) {
    targetCanvas.addEventListener("webglcontextlost", onContextLost);
    targetCanvas.addEventListener("webglcontextrestored", onContextRestored);
    return () => {
      targetCanvas.removeEventListener("webglcontextlost", onContextLost);
      targetCanvas.removeEventListener("webglcontextrestored", onContextRestored);
    };
  }

  let unbindContextListeners = bindContextListeners(canvas);

  state.renderer = rendererFactory({ canvas });
  applyRendererBaseline(state.renderer);
  state.scene = buildScene();
  state.camera = buildCamera();
  state.lights = buildLights(state.scene);

  function assertEngineAlive() {
    if (state.disposed) throw new AvatarEngineError("engine_disposed", "引擎已 disposeEngine");
  }

  // ── §11.4 模型级 dispose（幂等）─────────────────────────────
  function disposeModelRecord(record) {
    if (record === null || record.disposed) return;
    record.disposed = true;
    // 1. AnimationMixer.stopAllAction() + clip/action 引用解除。
    if (record.mixer) {
      try {
        record.mixer.stopAllAction();
        if (record.vrm?.scene) record.mixer.uncacheRoot(record.vrm.scene);
      } catch (_) { /* 幂等释放：忽略重复释放异常 */ }
      record.mixer = null;
    }
    record.gestureClips = {};
    record.gestureActions = {};
    record.currentGesture = "";
    // 2. Object URL revoke（引擎只接受 ArrayBuffer，不创建 Object URL；保留撤销钩子）。
    for (const url of record.objectUrls) {
      try {
        URL.revokeObjectURL(url);
      } catch (_) { /* 忽略 */ }
    }
    record.objectUrls = [];
    // 3. 模型级事件订阅/临时回调。
    for (const unsubscribe of record.subscriptions) {
      try {
        unsubscribe();
      } catch (_) { /* 忽略 */ }
    }
    record.subscriptions = [];
    // 4. 模型级 RenderTarget（当前实现无模型级 RT，保留清单）。
    for (const target of record.renderTargets) {
      try {
        target.dispose();
      } catch (_) { /* 忽略 */ }
    }
    record.renderTargets = [];
    // 5. SpringBone：three-vrm 3.x 的 springBone 关节挂在场景图内，随场景图遍历
    //    与 VRMUtils.deepDispose 一并释放；此处显式断开引用。
    // 6. Geometry/Material/Texture dispose + Skeleton 引用解除（场景图遍历）。
    const root = record.vrm?.scene ?? null;
    if (root) {
      root.traverse((obj) => {
        if (obj.geometry && typeof obj.geometry.dispose === "function") {
          try { obj.geometry.dispose(); } catch (_) { /* 忽略 */ }
        }
        const materials = Array.isArray(obj.material) ? obj.material : obj.material ? [obj.material] : [];
        for (const material of materials) {
          for (const slot of MATERIAL_TEXTURE_SLOTS) {
            const texture = material[slot];
            if (texture && typeof texture.dispose === "function") {
              try { texture.dispose(); } catch (_) { /* 忽略 */ }
            }
          }
          if (typeof material.dispose === "function") {
            try { material.dispose(); } catch (_) { /* 忽略 */ }
          }
        }
        if (obj.skeleton) obj.skeleton = null;
      });
      // 7. VRMUtils.deepDispose 兜底释放 VRM 内部资源（SpringBone 管理器等）。
      try {
        VRMUtils.deepDispose(root);
      } catch (_) { /* 忽略 */ }
      if (root.parent) root.parent.remove(root);
    }
    // 8. 临时 ArrayBuffer 引用（加载期间持有的源字节）。
    record.sourceBytes = null;
    record.vrm = null;
    record.lookAtProxy = null;
    record.performanceDriver = null;
  }

  const engine = {
    contractVersion: AVATAR_ENGINE_CONTRACT_VERSION,
    capabilities: THREE_VRM_ENGINE_CAPABILITIES,

    on: (event, listener) => events.on(event, listener),
    off: (event, listener) => events.off(event, listener),

    // ── 表面管理 ──
    setViewport(width, height) {
      assertEngineAlive();
      if (!Number.isFinite(width) || !Number.isFinite(height) || width < 1 || height < 1) {
        throw new AvatarEngineError("viewport_invalid", "setViewport 需要正数宽高");
      }
      state.camera.aspect = width / Math.max(1, height);
      state.camera.updateProjectionMatrix();
      state.renderer.setSize(width, height, false);
    },

    attachSurface({ canvas: nextCanvas, viewport: nextViewport, host } = {}) {
      assertEngineAlive();
      // P6a §14.3 rehost：给出宿主时优先做 DOM 节点迁移——同一 canvas 元素搬到
      // 新宿主，不重建 renderer、不重解析模型（loadModel 计数与模型记录不变）。
      if (host !== null && host !== undefined) {
        const hostElement = resolveHostElement(host);
        let moved = false;
        if (hostElement !== null) {
          try {
            hostElement.appendChild(state.canvas);
            // 真实 DOM 会维护 parentNode；stub DOM 不维护时以"未抛错"为迁移成功。
            moved = state.canvas.parentNode === hostElement || state.canvas.parentNode == null;
          } catch (_error) {
            moved = false; // 迁入失败落入降级路径
          }
        }
        if (!moved) {
          // 降级：detach+attach（仅重绑 context 监听与视口），renderer 保持单例。
          unbindContextListeners();
          unbindContextListeners = bindContextListeners(state.canvas);
        }
        if (nextViewport) engine.setViewport(nextViewport.width, nextViewport.height);
        ensureOrbitControls();
        return Object.freeze({ moved, fallback: moved ? null : "detach-attach", rendererRebuilt: false });
      }
      if (nextCanvas !== null && nextCanvas !== undefined && nextCanvas !== state.canvas) {
        if (typeof nextCanvas !== "object" || typeof nextCanvas.addEventListener !== "function") {
          throw new AvatarEngineError("canvas_invalid", "attachSurface 需要可监听事件的 canvas");
        }
        // 表面级重建：canvas 元素本身更换时重建 Renderer（这不是普通模型切换）。
        unbindContextListeners();
        state.renderer.dispose();
        unbindContextListeners = bindContextListeners(nextCanvas);
        state.canvas = nextCanvas;
        state.renderer = rendererFactory({ canvas: nextCanvas });
        applyRendererBaseline(state.renderer);
        if (nextViewport) engine.setViewport(nextViewport.width, nextViewport.height);
        ensureOrbitControls();
        return Object.freeze({ moved: false, fallback: "canvas-rebuild", rendererRebuilt: true });
      }
      // 同一 canvas/无新宿主：幂等，仅按需更新视口。
      if (nextViewport) engine.setViewport(nextViewport.width, nextViewport.height);
      ensureOrbitControls();
      return Object.freeze({ moved: false, fallback: null, rendererRebuilt: false });
    },

    detachSurface() {
      assertEngineAlive();
      // 仅解除 canvas 事件绑定与 RAF 提交；Renderer 随 disposeEngine 释放。
      unbindContextListeners();
      unbindContextListeners = () => {};
    },

    // ── §20.3 重建原语：浏览器无法恢复 context 时由 Runtime 决定是否整体重建 ──
    recreateRenderer() {
      assertEngineAlive();
      unbindContextListeners();
      state.renderer.dispose();
      unbindContextListeners = bindContextListeners(state.canvas);
      state.renderer = rendererFactory({ canvas: state.canvas });
      applyRendererBaseline(state.renderer);
      state.contextLost = false;
    },

    isContextLost() {
      return state.contextLost;
    },

    // ── 展示取景（§13.1）：四项偏移钳制后按“基线+偏移”重写相机位姿 ──
    applyCameraPresentation({ focus = 0, height = 0, distance = 0, side = 0 } = {}) {
      assertEngineAlive();
      const clamp = (value, min, max) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return 0;
        return THREE.MathUtils.clamp(number, min, max);
      };
      state.cameraPresentation = {
        focus: clamp(focus, -0.5, 0.5),
        height: clamp(height, -0.5, 0.5),
        distance: clamp(distance, -2, 2),
        side: clamp(side, -1, 1),
      };
      return composeFramedCamera();
    },

    // 恢复主镜头：按当前模型包围盒重新取景，并退出用户拖拽的手动视角。
    // 与旧面板“主镜头/恢复默认”按钮 parity（restoreMainCamera）。
    restoreMainCamera() {
      assertEngineAlive();
      state.cameraManual = false;
      const model = state.model;
      if (model && !model.disposed && model.vrm) {
        frameCameraToModel(model.vrm);
      } else {
        state.cameraFraming = {
          focus: new THREE.Vector3(...cameraPreset.focus),
          distance: cameraPreset.distance,
        };
        composeFramedCamera();
      }
      return true;
    },

    isCameraManual() {
      return Boolean(state.cameraManual);
    },

    // ── relighting（§13.1/§7.3）：与提取前 applyLightingRig 相同的强度/位置数学 ──
    applyLighting({ key = 1, angle = 0, ambient = 1, exposure = 1, baseExposure = 0.68, target } = {}) {
      assertEngineAlive();
      const focus = target && Number.isFinite(target.x) && Number.isFinite(target.y) && Number.isFinite(target.z)
        ? target
        : { x: cameraPreset.focus[0], y: cameraPreset.focus[1], z: cameraPreset.focus[2] };
      const { mainLight, ambientLight, hemiLight, rimLight, warmSideLight } = state.lights;
      mainLight.intensity = LIGHTING_BASE.key * (0.3 + key * 0.85);
      ambientLight.intensity = LIGHTING_BASE.ambient * (0.3 + ambient * 0.85);
      hemiLight.intensity = LIGHTING_BASE.hemi * (0.3 + ambient * 0.85);
      rimLight.intensity = LIGHTING_BASE.rim * (0.3 + key * 0.7 + Math.abs(angle) * 0.12);
      warmSideLight.intensity = LIGHTING_BASE.warm * (0.3 + ambient * 0.4 + key * 0.25);
      const appliedExposure = baseExposure * exposure * (0.94 + key * 0.03);
      state.renderer.toneMappingExposure = appliedExposure;
      mainLight.target.position.copy(focus);
      mainLight.position.set(focus.x + 0.2 + angle * 3.4, 3.25 + key * 0.18, focus.z + 3.05 - Math.abs(angle) * 0.18);
      rimLight.position.set(focus.x + 2.7 - angle * 0.72, 2.2, focus.z - 2.1);
      warmSideLight.position.set(focus.x - 2.6 + angle * 0.48, 1.35, focus.z + 0.9);
      return Object.freeze({ key, angle, ambient, exposure, exposureApplied: appliedExposure });
    },

    setExposure(value) {
      assertEngineAlive();
      if (!Number.isFinite(value) || value <= 0) throw new AvatarEngineError("exposure_invalid", "exposure 必须为正数");
      state.renderer.toneMappingExposure = value;
    },

    // ── 模型加载（只接受已验证 ArrayBuffer；加载前 dispose 前一模型）──
    async loadModel(modelBytes, { label = "VRM", lookAtTarget, normalizeForward = false, cleanup = false, addToScene = true } = {}) {
      assertEngineAlive();
      if (!isAcceptedModelBytes(modelBytes)) {
        throw new AvatarEngineError("model_bytes_invalid", "loadModel 只接受已验证 ArrayBuffer/Uint8Array，禁止 raw URL/路径（§8.2/§29）");
      }
      // 加载前一模型完整释放（§11.4）；普通切换不销毁共享 Renderer/Scene/Camera。
      engine.disposeModel();
      const arrayBuffer = toArrayBuffer(modelBytes);
      // 结构级路由：读 extensions.VRM / VRMC_vrm（§12.2），先验版本再全量解析。
      const sniffed = sniffGltfJsonBytes(arrayBuffer);
      const specVersion = detectVrmSpecVersion(sniffed.json);
      if (specVersion === null) {
        throw new AvatarEngineError("vrm_spec_unknown", "glTF extensions 中既无 VRM 也无 VRMC_vrm，拒绝加载");
      }
      const adapterApi = specVersion === VrmSpecVersion.VRM0
        ? { analyze: analyzeVrm0GltfJson, adapt: adaptVrm0Runtime, projectLicense: projectVrm0LicenseRecord, aliases: vrm0ExpressionAliases }
        : { analyze: analyzeVrm1GltfJson, adapt: adaptVrm1Runtime, projectLicense: projectVrm1LicenseRecord, aliases: vrm1ExpressionAliases };
      const structuralReport = adapterApi.analyze(sniffed.json);
      const licenseRecord = adapterApi.projectLicense(structuralReport.meta);
      const gltf = await parseGltf(arrayBuffer);
      const vrm = gltf?.userData?.vrm;
      if (!vrm) throw new AvatarEngineError("vrm_parse_failed", "GLTF 解析结果缺少 userData.vrm");
      const adaptResult = adapterApi.adapt(vrm, {
        VRMUtils,
        lookAtProxyClass: VRMLookAtQuaternionProxy,
        normalizeForward,
        cleanup,
      });
      // 视线目标默认绑定引擎相机（parity：vrm.lookAt.target=camera）。
      try {
        if (vrm.lookAt) vrm.lookAt.target = lookAtTarget ?? state.camera;
      } catch (_) { /* lookAt 绑定失败不阻断加载 */ }
      state.model = {
        vrm,
        label,
        specVersion,
        adapter: adapterApi,
        structuralReport,
        licenseRecord,
        adaptResult,
        mixer: null,
        gestureClips: {},
        gestureActions: {},
        currentGesture: "",
        objectUrls: [],
        subscriptions: [],
        renderTargets: [],
        sourceBytes: arrayBuffer,
        lookAtProxy: null,
        performanceDriver: createLegacyPerformanceDriver({
          vrm,
          applyExpression: (name, value) => engine.applyExpression(name, value),
          mapViseme: mapVisemeChar,
        }),
        firstFrameEmitted: false,
        disposed: false,
      };
      if (addToScene) state.scene.add(vrm.scene);
      // 展示调谐：正向朝向 + MToon 材质 + 按模型曝光 + 包围盒取景（自 桌面宠物.html 移植）。
      if (addToScene) orientAndPresentModel(vrm, label);
      // 源字节引用只保留到解析完成（§11.4 临时 ArrayBuffer 释放）。
      state.model.sourceBytes = null;
      events.emit(EngineEvent.MODEL_LOADED, Object.freeze({
        label,
        specVersion,
        nodeCount: structuralReport.nodeCount,
        meshCount: structuralReport.meshCount,
      }));
      return Object.freeze({
        vrm,
        label,
        specVersion,
        adapterKind: structuralReport.kind,
        structuralReport,
        licenseRecord,
        adaptResult,
      });
    },

    // ── VRMA：调用方提供已读字节（引擎不做文件选择/IPC/网络）──
    async loadGesturesFromBytes(gestureBytesByKey, { timeScales = GESTURE_DEFAULT_TIMESCALES } = {}) {
      assertEngineAlive();
      const model = state.model;
      if (!model || model.disposed) throw new AvatarEngineError("model_missing", "loadGesturesFromBytes 需要先加载模型");
      const mixer = new THREE.AnimationMixer(model.vrm.scene);
      const clips = {};
      const actions = {};
      try {
        for (const [key, bytes] of Object.entries(gestureBytesByKey)) {
          if (!isAcceptedModelBytes(bytes)) {
            throw new AvatarEngineError("gesture_bytes_invalid", `VRMA ${key} 只接受 ArrayBuffer/Uint8Array`);
          }
          const gltf = await parseVrma(toArrayBuffer(bytes));
          const animation = gltf?.userData?.vrmAnimations?.[0];
          if (!animation) throw new AvatarEngineError("vrma_missing", `${key} 缺少 VRMAnimation`);
          const clip = createVRMAnimationClip(animation, model.vrm);
          clip.name = key;
          const action = mixer.clipAction(clip);
          action.enabled = true;
          action.setLoop(THREE.LoopRepeat, Infinity);
          action.clampWhenFinished = false;
          action.timeScale = timeScales[key] ?? GESTURE_FALLBACK_TIMESCALE;
          clips[key] = clip;
          actions[key] = action;
        }
      } catch (err) {
        try { mixer.stopAllAction(); } catch (_) { /* 忽略 */ }
        throw err;
      }
      model.mixer = mixer;
      model.gestureClips = clips;
      model.gestureActions = actions;
      model.currentGesture = "";
      events.emit(EngineEvent.GESTURE_SET_LOADED, Object.freeze({ keys: Object.freeze(Object.keys(actions)) }));
      return Object.freeze({ clips, actions, ready: true });
    },

    // 交叉淡入（parity：fadeIn/fadeOut 0.35，instant 0.01，空 key 全部 fadeOut 0.25）。
    playGesture(key, { instant = false } = {}) {
      const model = state.model;
      if (!model || model.disposed) return false;
      if (!key) {
        for (const action of Object.values(model.gestureActions)) {
          if (!action) continue;
          if (instant) {
            action.stop();
            action.enabled = false;
          } else {
            action.fadeOut(0.25);
          }
        }
        model.currentGesture = "";
        return true;
      }
      if (!model.mixer || !model.gestureActions[key]) return false;
      if (model.currentGesture === key) return true;
      const prev = model.gestureActions[model.currentGesture];
      const next = model.gestureActions[key];
      next.enabled = true;
      next.reset().play();
      next.fadeIn(instant ? 0.01 : 0.35);
      if (prev) prev.fadeOut(instant ? 0.01 : 0.35);
      model.currentGesture = key;
      return true;
    },

    currentGesture() {
      return state.model?.currentGesture ?? "";
    },

    hasGestureMixer() {
      return !!(state.model && !state.model.disposed && state.model.mixer);
    },

    updateGesture(dt) {
      const model = state.model;
      if (model && !model.disposed && model.mixer) model.mixer.update(dt);
    },

    // ── 表情：统一语义名 → 适配器别名 → expressionManager（parity 四级匹配）──
    applyExpression(name, value) {
      const model = state.model;
      const manager = model && !model.disposed ? model.vrm?.expressionManager : null;
      if (!manager) return Object.freeze({ matched: false, availableKeys: Object.freeze([]), tried: name });
      const clamped = THREE.MathUtils.clamp(Number(value) || 0, 0, 1);
      // ① 精确匹配
      if (manager.expressionMap?.[name]) {
        manager.setValue(name, clamped);
        return Object.freeze({ matched: true, availableKeys: Object.freeze(Object.keys(manager.expressionMap ?? {})), tried: name });
      }
      const aliasList = model.adapter.aliases(name);
      // ② 别名精确匹配
      for (const alias of aliasList) {
        if (manager.expressionMap?.[alias]) {
          manager.setValue(alias, clamped);
          return Object.freeze({ matched: true, availableKeys: Object.freeze(Object.keys(manager.expressionMap ?? {})), tried: name });
        }
      }
      // ③ 模糊匹配：忽略大小写和 Fcl_/Jnt_ 等前缀。
      // parity：保留原实现的正则形态（原字符类为 [_\\s]，bug-for-bug 一致）。
      const clean = (s) => s.toLowerCase().replace(/^[a-z]{3}_/, "").replace(/[_\\s]/g, "");
      const target = clean(name);
      const keys = Object.keys(manager.expressionMap || {});
      for (const key of keys) {
        if (clean(key) === target || clean(key).includes(target) || target.includes(clean(key))) {
          manager.setValue(key, clamped);
          return Object.freeze({ matched: true, availableKeys: Object.freeze(keys), tried: name });
        }
      }
      // ④ 别名模糊匹配
      for (const alias of aliasList) {
        const aliasClean = clean(alias);
        for (const key of keys) {
          if (clean(key) === aliasClean || clean(key).includes(aliasClean)) {
            manager.setValue(key, clamped);
            return Object.freeze({ matched: true, availableKeys: Object.freeze(keys), tried: name });
          }
        }
      }
      return Object.freeze({ matched: false, availableKeys: Object.freeze(keys), tried: name });
    },

    // viseme 语义命令：口型目标（aa/ih/ou/ee/oh）直写 expressionManager。
    applyVisemeTarget(targets) {
      const model = state.model;
      const manager = model && !model.disposed ? model.vrm?.expressionManager : null;
      if (!manager) return false;
      for (const viseme of VISEME_CYCLE) {
        if (manager.expressionMap?.[viseme]) {
          manager.setValue(viseme, THREE.MathUtils.clamp(Number(targets[viseme]) || 0, 0, 1));
        }
      }
      return true;
    },

    setSpeaking(speaking) {
      state.speaking = !!speaking;
      state.model?.performanceDriver?.setSpeaking(state.speaking);
      if (!state.speaking) engine.applyVisemeTarget({ aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 });
    },

    isSpeaking() {
      return state.speaking;
    },

    mapViseme: mapVisemeChar,

    // ── Legacy 表现驱动（自 桌面宠物.html 移植：自然站姿/手势/表情/口型/尾巴）──
    applyBodyPerformance(data) {
      const model = state.model;
      const driver = model && !model.disposed ? model.performanceDriver : null;
      if (!driver) return false;
      driver.applyBodyPerformance(data);
      const gesture = typeof data?.gesture === "string"
        ? data.gesture
        : data?.gesture?.semanticId ?? null;
      // VRMA 语义键 → 动作播放；程序化手势（nod/挥手等）→ 自然站姿驱动接管。
      if (gesture !== null && VRMA_GESTURE_KEYS.includes(gesture) && model.gestureActions?.[gesture]) {
        engine.playGesture(gesture);
      } else {
        engine.playGesture(null);
      }
      return true;
    },

    setQinggan(qinggan) {
      const driver = state.model && !state.model.disposed ? state.model.performanceDriver : null;
      return driver ? driver.setQinggan(qinggan) : false;
    },

    beginSpeech(text) {
      const driver = state.model && !state.model.disposed ? state.model.performanceDriver : null;
      return driver ? driver.markTalking(text) : false;
    },

    setSpeechEnergy(energy) {
      const driver = state.model && !state.model.disposed ? state.model.performanceDriver : null;
      return driver ? driver.setSpeechEnergy(energy) : false;
    },

    // ── 视线（§13.1.4）──
    applyGaze({ target } = {}) {
      const model = state.model;
      if (!model || model.disposed || !model.vrm?.lookAt) return false;
      try {
        model.vrm.lookAt.target = target ?? state.camera;
        return true;
      } catch (_) {
        return false;
      }
    },

    // ── 姿态语义命令：按 humanoid 归一化骨骼名写四元数 ──
    applyPosture({ bones = {} } = {}) {
      const model = state.model;
      const humanoid = model && !model.disposed ? model.vrm?.humanoid : null;
      if (!humanoid || typeof humanoid.getNormalizedBoneNode !== "function") return false;
      let applied = 0;
      for (const [boneName, rotation] of Object.entries(bones)) {
        const node = humanoid.getNormalizedBoneNode(boneName);
        if (!node || !rotation) continue;
        if (Array.isArray(rotation) && rotation.length === 4) {
          node.quaternion.set(rotation[0], rotation[1], rotation[2], rotation[3]);
          applied += 1;
        } else if (Number.isFinite(rotation.x) && Number.isFinite(rotation.y) && Number.isFinite(rotation.z)) {
          node.rotation.set(rotation.x, rotation.y, rotation.z);
          applied += 1;
        }
      }
      return applied > 0;
    },

    // ── P6a biaoxian→引擎映射校准（§15.1/§15.2 语义命令统一入口）────────────
    // semantics = { expression?: string|{name,intensity?}, gaze?: string|{target},
    //               posture?: string|{name?, bones?} }。
    //   expression 名称 → expressionManager 目标（含 intensity，latest-wins：切换前先清零旧目标）
    //   gaze 目标       → lookAt（命名目标=引擎相机；{x,y,z} 点目标=Vector3）
    //   posture         → 姿态语义槽（registerPostureSlot 登记；显式 bones 直写）
    // 未知名一律降级 neutral（expression→neutral 表情 / gaze→camera / posture→neutral 槽）
    // 并记录诊断（本返回值的 diagnostics + 引擎级有界诊断环）。
    applyPerformanceSemantics(semantics = {}) {
      assertEngineAlive();
      if (semantics === null || typeof semantics !== "object") {
        throw new AvatarEngineError("semantics_invalid", "applyPerformanceSemantics 需要语义对象 { expression?, gaze?, posture? }");
      }
      const diagnostics = [];
      const recordDiagnostic = (entry) => {
        const frozen = Object.freeze({ ...entry });
        diagnostics.push(frozen);
        state.performanceDiagnostics.push(frozen);
        if (state.performanceDiagnostics.length > PERFORMANCE_DIAGNOSTICS_LIMIT) {
          state.performanceDiagnostics.splice(0, state.performanceDiagnostics.length - PERFORMANCE_DIAGNOSTICS_LIMIT);
        }
      };
      const report = { expression: null, gaze: null, posture: null };

      // expression：名称 → expressionManager（含 intensity）
      const expression = normalizeExpressionSemantic(semantics.expression);
      if (expression !== null) {
        if (state.semanticExpression !== null && state.semanticExpression !== expression.name) {
          engine.applyExpression(state.semanticExpression, 0); // latest-wins：先清上一语义目标
        }
        const applied = engine.applyExpression(expression.name, expression.intensity);
        if (applied.matched) {
          state.semanticExpression = expression.name;
          report.expression = Object.freeze({ name: expression.name, intensity: expression.intensity, matched: true, degraded: false });
        } else {
          const fallback = engine.applyExpression("neutral", 1);
          state.semanticExpression = fallback.matched ? "neutral" : null;
          recordDiagnostic({ channel: "expression", reason: "unknown_expression", requested: expression.name, degradedTo: fallback.matched ? "neutral" : null });
          report.expression = Object.freeze({ name: expression.name, intensity: expression.intensity, matched: false, degraded: true, degradedTo: fallback.matched ? "neutral" : null });
        }
      }

      // gaze：目标 → lookAt
      const gaze = normalizeGazeSemantic(semantics.gaze);
      if (gaze !== null) {
        if (gaze.kind === "point") {
          const ok = engine.applyGaze({ target: new THREE.Vector3(gaze.point.x, gaze.point.y, gaze.point.z) });
          if (!ok) recordDiagnostic({ channel: "gaze", reason: "lookat_unavailable", requested: gaze.point, degradedTo: null });
          report.gaze = Object.freeze({ kind: "point", applied: ok, degraded: !ok });
        } else if (GAZE_SEMANTIC_TARGETS.includes(gaze.name)) {
          const ok = engine.applyGaze({}); // 命名目标 = 引擎相机（lookAt 缺省绑定）
          report.gaze = Object.freeze({ kind: "named", name: gaze.name, applied: ok, degraded: false });
        } else {
          const ok = engine.applyGaze({}); // 未知名降级 camera
          recordDiagnostic({ channel: "gaze", reason: "unknown_gaze_target", requested: gaze.name, degradedTo: "camera" });
          report.gaze = Object.freeze({ kind: "named", name: gaze.name, applied: ok, degraded: true, degradedTo: "camera" });
        }
      }

      // posture：姿态语义槽（或显式 bones 直写）
      const posture = normalizePostureSemantic(semantics.posture);
      if (posture !== null) {
        if (posture.bones !== null) {
          const ok = engine.applyPosture({ bones: posture.bones });
          report.posture = Object.freeze({ name: posture.name, slot: "explicit-bones", applied: ok, degraded: false });
        } else if (state.postureSlots.has(posture.name)) {
          const ok = engine.applyPosture({ bones: state.postureSlots.get(posture.name).bones });
          report.posture = Object.freeze({ name: posture.name, slot: posture.name, applied: ok, degraded: false });
        } else {
          const neutral = state.postureSlots.get("neutral") ?? POSTURE_SEMANTIC_SLOTS.neutral;
          const ok = engine.applyPosture({ bones: neutral.bones }); // 未知名降级 neutral 槽
          recordDiagnostic({ channel: "posture", reason: "unknown_posture_slot", requested: posture.name, degradedTo: "neutral" });
          report.posture = Object.freeze({ name: posture.name, slot: "neutral", applied: ok, degraded: true, degradedTo: "neutral" });
        }
      }

      return Object.freeze({ ...report, diagnostics: Object.freeze(diagnostics.slice()) });
    },

    // 姿态语义槽登记：name → { bones }（bones 键为 humanoid 归一化骨骼名，值为四元数/欧拉角）。
    registerPostureSlot(name, slot) {
      assertEngineAlive();
      if (typeof name !== "string" || name.length === 0) {
        throw new AvatarEngineError("posture_slot_invalid", "registerPostureSlot 需要非空语义名");
      }
      if (slot === null || typeof slot !== "object" || slot.bones === null || typeof slot.bones !== "object") {
        throw new AvatarEngineError("posture_slot_invalid", "registerPostureSlot 需要 { bones } 槽对象");
      }
      state.postureSlots.set(name, Object.freeze({ bones: Object.freeze({ ...slot.bones }) }));
      return true;
    },

    // 校准诊断环（有界，最新在尾；applyPerformanceSemantics 的降级记录）。
    getPerformanceSemanticsDiagnostics() {
      return Object.freeze(state.performanceDiagnostics.slice());
    },

    // ── 帧推进 ──
    update(dt) {
      const model = state.model;
      if (model && !model.disposed && model.vrm && typeof model.vrm.update === "function") {
        // Legacy 表现驱动：VRMA 动作播放时由动作驱动全身，否则自然站姿接管。
        updateCameraInertia(Number(dt) || 0);
        model.performanceDriver?.update(dt, { gestureActive: model.currentGesture !== "" });
        model.vrm.update(dt);
      }
    },

    renderFrame() {
      if (state.disposed || state.contextLost) return false; // §20.3.4 context lost 期间禁止提交 GPU 命令
      if (state.controls) state.controls.update(); // 阻尼/惯性每帧推进
      state.renderer.render(state.scene, state.camera);
      const model = state.model;
      if (model && !model.disposed && !model.firstFrameEmitted) {
        model.firstFrameEmitted = true;
        // FIRST_RENDERABLE_FRAME：供 Runtime 探针消费的输入信号（§13.1.7）。
        events.emit(EngineEvent.FIRST_RENDERABLE_FRAME, Object.freeze({
          label: model.label,
          specVersion: model.specVersion,
        }));
      }
      return true;
    },

    // ── 资源统计（§13.1.6）──
    getStats() {
      const info = state.renderer?.info;
      const model = state.model && !state.model.disposed ? state.model : null;
      return Object.freeze({
        drawCalls: info?.render?.calls ?? 0,
        triangles: info?.render?.triangles ?? 0,
        geometries: info?.memory?.geometries ?? 0,
        textures: info?.memory?.textures ?? 0,
        programs: Array.isArray(info?.programs) ? info.programs.length : 0,
        model: model
          ? Object.freeze({
              label: model.label,
              specVersion: model.specVersion,
              nodeCount: model.structuralReport.nodeCount,
              meshCount: model.structuralReport.meshCount,
              materialCount: model.structuralReport.materialCount,
              textureCount: model.structuralReport.textureCount,
            })
          : null,
      });
    },

    // ── §11.4 模型级释放（幂等）；普通切换不销毁共享 Renderer ──
    disposeModel() {
      const record = state.model;
      if (!record || record.disposed) {
        state.model = null;
        return;
      }
      state.model = null;
      disposeModelRecord(record);
      events.emit(EngineEvent.MODEL_DISPOSED, Object.freeze({ label: record.label }));
    },

    // ── 引擎级释放（§11.4：额外含 WebGLRenderer.dispose 与 context 事件监听）──
    disposeEngine() {
      if (state.disposed) return;
      state.disposed = true;
      engine.disposeModel();
      unbindContextListeners();
      unbindContextListeners = () => {};
      try {
        state.renderer.dispose();
      } catch (_) { /* 幂等释放 */ }
      events.emit(EngineEvent.ENGINE_DISPOSED, Object.freeze({}));
      events.clear();
    },

    // ── 调试壳专用内部访问（§25 EngineHarness）。
    // 注意：这不是 §7.2 公共接口；正式 AvatarRuntime/UI 不得使用，
    // 仅独立调试页 EngineHarness 用于房间场景挂载、OrbitControls 绑定等调试用途。
    debugInternals() {
      return Object.freeze({
        renderer: state.renderer,
        scene: state.scene,
        camera: state.camera,
        lights: state.lights,
        controls: state.controls,
        LIGHTING_BASE,
      });
    },
  };

  return engine;
}
