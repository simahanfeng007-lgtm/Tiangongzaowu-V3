// P6b 启动组装根（composition root）：把 P1~P6a 全部模块在真实前端启动时接线运行。
//
// 组装顺序（谁建谁先谁后，依赖只能指向已建项）：
//   ① nowMonotonic/flagStorage/诊断记账
//   ② storage backend（生产 sandbox renderer 走 preload 的固定 key IPC 窄桥；
//      Node 测试可用文件 backend，内存 backend 只允许测试显式注入）
//   ③ AssetRegistry（加载 builtin-models.json 两条记录；缺失则按清单原子登记
//      admitted/registryEntryVersion=1；清单漂移则按 §8.2 白名单字段更新并 +1）
//   ④ TokenIssuer + AssetProvider(channelFactory) + BuiltinAssetSource
//   ⑤ QuarantineTracker / PendingLoadJournal / SuspensionGuard / RenderSurfaceController
//   ⑥ ThreeVrmEngine（动态 import：three 依赖 importmap，加载失败只降级 direct，
//      不拖垮前端）+ ThreeVrmRuntimeAdapter；surface viewport 事件 → engine.setViewport
//   ⑦ AvatarRuntime（safeModelId=tiangong-z1，registry 登记 "avatar-runtime"）
//   ⑧ BodyCommandScheduler + BiaoxianAdapter + AvatarStore（业务链默认实例；
//      面板另有 per-mount 实例，见 avatar-panel.mjs，二者幂等键共存不冲突——boot 实例
//      不订阅 biaoxian 事件，避免双路执行）
//   ⑨ AvatarService（registry 登记 "avatar-service"）→ startMode(readFlagMode())
//   ⑩ direct 激活时 queueMicrotask 自动 selectModel(safeModelId)： Surface 未挂接时
//      探针进入 suspended-probe（预算 5min/15min），面板 attachSurface 后自然恢复。
//
// 失败纪律：任何一步抛错 → 记启动诊断（boot 行 + writeDiagnostic IPC）→ 回退
// legacy-iframe（不阻断前端启动；getBootstrappedAvatarService 仍返回服务外观）。

import { createAvatarRuntime } from "./avatar-runtime.mjs";
import {
  AVATAR_SELECTED_MODEL_FLAG_KEY,
  createAvatarService,
  AvatarRenderMode,
} from "./avatar-service.mjs";
import {
  registerService as defaultRegisterService,
  getService as defaultGetService,
  hasService as defaultHasService,
} from "./service-registry.mjs";
import { createAssetRegistry, AssetScope, AdmissionState, computeAuthorizationFingerprint } from "./asset-registry.mjs";
import { createFileStorageBackend, createIpcStorageBackend } from "./storage-adapter.mjs";
import { createTokenIssuer } from "./validated-asset-token.mjs";
import { createAssetProvider } from "./asset-provider.mjs";
import { createBuiltinAssetSource, normalizeBuiltinManifest } from "./builtin-asset-source.mjs";
import { VALIDATOR_VERSION } from "./model-admission-gate.mjs";
import { createQuarantineTracker } from "./model-quarantine.mjs";
import { createPendingLoadJournal } from "./pending-load-journal.mjs";
import { createSuspensionGuard } from "./suspension-guard.mjs";
import { createRenderSurfaceController } from "./render-surface-controller.mjs";
import { createDiagnostics } from "./diagnostics.mjs";
import { createThreeVrmRuntimeAdapter } from "./three-vrm-runtime-adapter.mjs";
import { EngineEvent } from "./engines/avatar-engine-contract.mjs";
import { createBodyCommandScheduler } from "./body-command-scheduler.mjs";
import { createBiaoxianAdapter } from "./body-performance-adapter.mjs";
import { createAvatarStore } from "./avatar-store.mjs";
import { sha256HexSync } from "./canonical-hash.mjs";
import { installAvatarImportBridge } from "./avatar-import-controller.mjs";

export const AVATAR_BOOT_VERSION = "avatar-boot-1.0.0";
export const AVATAR_SAFE_MODEL_ID = "tiangong-z1";
const BUILTIN_MANIFEST_URL = "../assets/avatar/builtin-models.json";
const BUILTIN_RECEIPT_ID = "arec_builtin_manifest_v1";
const DEFAULT_ENGINE_VIEWPORT = Object.freeze({ width: 640, height: 360 });

let bootHandle = null;

/** 诊断用：最近一次 bootstrapAvatar 的服务外观（未启动为 null）。 */
export function getBootstrappedAvatarService() {
  return bootHandle?.service ?? null;
}

/** 诊断用：完整 boot 句柄（service/runtime/store/scheduler/组装记账行）。 */
export function getAvatarBootHandle() {
  return bootHandle;
}

// P2 §22.2/§22.3：生产必须跨 renderer/应用重启持久化 registry、pending journal
// 与 quarantine state。IPC 桥存在时优先使用；无桥的 Node 测试环境才尝试文件
// backend。两者都不可用时安全失败并进入 legacy，而不是静默退化为会话内存。
async function createDefaultStorageBackend(storageRootDir, windowRef) {
  const ipcBridge = windowRef?.tiangongDesktop?.avatarStorage ?? null;
  if (
    ipcBridge !== null
    && typeof ipcBridge === "object"
    && typeof ipcBridge.read === "function"
    && typeof ipcBridge.writeAtomic === "function"
  ) {
    return createIpcStorageBackend({ bridge: ipcBridge });
  }
  try {
    return await createFileStorageBackend({ rootDir: storageRootDir });
  } catch (error) {
    throw new Error(
      `avatar_storage_backend_unavailable: ${String(error?.message ?? error)}`,
    );
  }
}

async function fetchBuiltinManifest(fetchImpl) {
  if (typeof fetchImpl !== "function") {
    throw new Error("manifest_fetch_unavailable: 缺少 fetch 实现，无法读取内置模型清单");
  }
  const response = await fetchImpl(BUILTIN_MANIFEST_URL);
  if (!response?.ok) throw new Error(`manifest_fetch_failed: HTTP ${response?.status ?? "?"}`);
  return response.json();
}

function readSelectedModelFlag(flagStorage) {
  try {
    const raw = flagStorage?.getItem?.(AVATAR_SELECTED_MODEL_FLAG_KEY);
    return typeof raw === "string" && raw.length > 0 ? raw : null;
  } catch (_error) {
    return null;
  }
}

async function loadDefaultEngineFactory() {
  const engineModule = await import("./engines/three-vrm-engine.mjs");
  if (typeof engineModule?.createThreeVrmEngine !== "function") {
    throw new Error("engine_module_invalid: three-vrm-engine 未导出 createThreeVrmEngine");
  }
  return engineModule.createThreeVrmEngine;
}

export async function bootstrapAvatar({
  document,
  window,
  navigator = null,
  flagStorage = null,
  storageBackend = null,
  storageRootDir = "avatar-models",
  channelFactory = null,
  manifest = null,
  serviceRegistry = null,
  nowMonotonic = null,
  engineFactory = null,
  engineModuleLoader = null,
  fetchImpl = null,
  autoSelectSafeModel = true,
} = {}) {
  const clock = nowMonotonic ?? (() => window?.performance?.now?.() ?? 0);
  const flags = flagStorage ?? window?.localStorage ?? null;
  const registryForServices = serviceRegistry ?? {
    registerService: defaultRegisterService,
    getService: defaultGetService,
    hasService: defaultHasService,
  };
  // 组装记账行：诊断事件目录（§23.1）之外的 boot 相位记录，随 bootHandle 暴露。
  const bootLog = [];
  const note = (stage, ok, detail = null) => {
    bootLog.push(Object.freeze({ stage, ok, detail }));
  };

  let service = null;
  let runtime = null;
  let store = null;
  let scheduler = null;
  let adapter = null;
  let avatarImportBridge = null;

  try {
    // ② storage
    const storage = storageBackend ?? (await createDefaultStorageBackend(storageRootDir, window));
    note("storage", true, `kind=${storage.kind ?? "unknown"}`);

    // ③ AssetRegistry + 内置清单登记
    const registry = await createAssetRegistry({ storage, issuerEpoch: 0 });
    const manifestDoc = manifest ?? (await fetchBuiltinManifest(fetchImpl ?? window?.fetch?.bind(window)));
    const builtinModels = normalizeBuiltinManifest(manifestDoc);
    if (builtinModels.length === 0) {
      // 制品可按许可策略排除全部内置 VRM。此时 direct 仍是合法的 import-only
      // 运行态：空 catalog + 可用导入按钮，不得误判为引擎故障并回退 legacy。
      note("manifest", true, "empty import-only catalog");
    }
    for (const model of builtinModels) {
      const authorizationFingerprint = computeAuthorizationFingerprint({
        licenseRecord: null,
        admissionLimits: null,
        uriPolicy: null,
        validatorVersion: VALIDATOR_VERSION,
        contentHash: model.contentHash,
        byteLength: model.byteLength,
      });
      const existing = registry.getRecord(model.id);
      if (existing === null) {
        // 缺失：按清单原子登记 admitted，registryEntryVersion=1（§22.3 登记顺序）。
        await registry.registerAsset({
          assetId: model.id,
          scope: AssetScope.BUILTIN,
          contentHash: model.contentHash,
          byteLength: model.byteLength,
          validationReceiptId: BUILTIN_RECEIPT_ID,
          validatorVersion: VALIDATOR_VERSION,
          authorizationFingerprint,
          admissionState: AdmissionState.ADMITTED,
          displayName: model.displayName,
          reason: "builtin-manifest",
        });
        note("registry", true, `${model.id} registered v1`);
      } else if (existing.contentHash !== model.contentHash || existing.byteLength !== model.byteLength) {
        // 清单漂移：按 §8.2 白名单字段更新，registryEntryVersion 恰 +1。
        await registry.updateAssetFields(model.id, {
          contentHash: model.contentHash,
          byteLength: model.byteLength,
          authorizationFingerprint,
        }, { reason: "builtin-manifest-refresh" });
        note("registry", true, `${model.id} refreshed v${existing.registryEntryVersion + 1}`);
      } else {
        note("registry", true, `${model.id} present v${existing.registryEntryVersion}`);
      }
    }

    // ④ TokenIssuer / AssetProvider / BuiltinAssetSource
    const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: registry.issuerEpoch });
    const openChannel = channelFactory ?? window?.tiangongDesktop?.avatarAsset?.openChannel ?? null;
    if (typeof openChannel !== "function") {
      throw new Error("channel_factory_missing: 缺少 avatarAsset.openChannel 通道（preload 未暴露或未注入）");
    }
    const provider = createAssetProvider({ channelFactory: openChannel, registry, issuerEpoch: registry.issuerEpoch });
    const assetSource = createBuiltinAssetSource({
      manifest: manifestDoc,
      provider,
      tokenIssuer,
      registry,
      sha256: sha256HexSync,
    });
    note("asset-source", true, `models=${builtinModels.length}`);

    // ⑤ Quarantine / Journal / SuspensionGuard / SurfaceController
    const quarantineTracker = await createQuarantineTracker({ storage });
    const journal = await createPendingLoadJournal({ storage });
    const suspensionGuard = createSuspensionGuard({ nowMonotonic: clock });
    const surfaceController = createRenderSurfaceController({ nowMonotonic: clock });

    // ⑥ Engine + Adapter。真实引擎保持在可捕获的动态边界内：依赖缺失、
    // asar 资源不完整或模块求值失败时，异常由本函数 catch 并持久回退 legacy，
    // 不得在 avatar-boot 模块求值阶段拖垮整个 frontend。
    let createEngine = engineFactory;
    if (createEngine === null) {
      createEngine = engineModuleLoader === null
        ? await loadDefaultEngineFactory()
        : (await engineModuleLoader())?.createThreeVrmEngine;
      if (typeof createEngine !== "function") {
        throw new Error("engine_module_invalid: three-vrm-engine 未导出 createThreeVrmEngine");
      }
    }
    const engine = await createEngine({
      document,
      window,
      navigator,
      canvas: document.createElement("canvas"),
      viewport: { ...DEFAULT_ENGINE_VIEWPORT },
    });
    adapter = createThreeVrmRuntimeAdapter({ engine });
    // VRMA 动作资产（§15 聊天互动）：主进程资产通道读取 7 个内置动作字节，
    // 每次模型加载后喂给引擎（mixer 绑定当前模型；回滚重建会再次触发加载）。
    const VRMA_ASSET_PATHS = Object.freeze({
      thinking: "assets/animations/vrma/Thinking.vrma",
      relax: "assets/animations/vrma/Relax.vrma",
      sad: "assets/animations/vrma/Sad.vrma",
      surprised: "assets/animations/vrma/Surprised.vrma",
      lookAround: "assets/animations/vrma/LookAround.vrma",
      angry: "assets/animations/vrma/Angry.vrma",
      clapping: "assets/animations/vrma/Clapping.vrma",
    });
    let gestureBytesPromise = null;
    const readGestureAssets = () => {
      if (gestureBytesPromise !== null) return gestureBytesPromise;
      const readProjectAsset = window?.tiangongDesktop?.readProjectAsset ?? null;
      if (typeof readProjectAsset !== "function") {
        gestureBytesPromise = Promise.resolve(null);
        return gestureBytesPromise;
      }
      gestureBytesPromise = (async () => {
        const entries = await Promise.all(
          Object.entries(VRMA_ASSET_PATHS).map(async ([key, relPath]) => {
            const data = await readProjectAsset(relPath);
            const bytes = data instanceof ArrayBuffer
              ? data
              : data && ArrayBuffer.isView(data)
                ? data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
                : null;
            return [key, bytes];
          }),
        );
        const map = {};
        for (const [key, bytes] of entries) {
          if (bytes !== null) map[key] = bytes;
        }
        return Object.keys(map).length > 0 ? map : null;
      })().catch(() => null);
      return gestureBytesPromise;
    };
    adapter.on?.(EngineEvent.MODEL_LOADED, () => {
      void readGestureAssets()
        .then((bytes) => {
          if (bytes !== null) return adapter.loadGestures?.(bytes);
          return null;
        })
        .catch(() => { /* 动作资产加载失败不阻断模型渲染 */ });
    });
    // E2E/诊断逃生口（只读引用；不参与公共接口，§7.2 不放宽）：
    // 面板/调试不得经此操作引擎对象，仅用于诊断采样与材质核验。
    window.__avatarDebugEngine = engine;
    // 面板 ResizeObserver → surface viewport 事件 → 引擎视口（§14/§19 探针尺寸依据）。
    surfaceController.onDidChange((event) => {
      if (event?.type === "viewport" && event.viewport) {
        try {
          engine.setViewport(event.viewport.width, event.viewport.height);
        } catch (_error) {
          // 引擎已释放或无活动渲染：视口更新落空不阻断
        }
      }
    });
    note("engine", true, adapter.engineVersion);

    // ⑦ AvatarRuntime
    const diagnostics = createDiagnostics({ nowMonotonic: clock });
    runtime = createAvatarRuntime({
      engineAdapter: adapter,
      assetSource,
      nowMonotonic: clock,
      requestAnimationFrame: (cb) => window.requestAnimationFrame(cb),
      cancelAnimationFrame: (id) => window.cancelAnimationFrame(id),
      registry: registryForServices,
      surfaceController,
      diagnostics,
      quarantineTracker,
      journal,
      suspensionGuard,
      env: {
        isDocumentHidden: () => document?.visibilityState === "hidden",
        isWindowMinimized: () => false,
      },
      gpuFingerprint: null, // 渲染端无 GPU 五要素来源；runtime 键按纪律跳过计数（不降级键语义）
      safeModelId: AVATAR_SAFE_MODEL_ID,
    });

    // ⑧ 业务链默认实例（不订阅 biaoxian 事件：面板 per-mount 实例承担，避免双路执行）
    scheduler = createBodyCommandScheduler({
      nowMonotonic: clock,
      sink: runtime,
      onModelLoad: (wire) => {
        if (typeof wire?.modelId === "string") runtime.selectModel(wire.modelId);
      },
    });
    const biaoxianAdapter = createBiaoxianAdapter({
      getBackendInstanceId: () => window?.tiangongBackendInstanceId ?? null,
      getSessionEpoch: () => window?.tiangongSessionEpoch ?? null,
    });
    store = createAvatarStore({ nowMonotonic: clock, mode: AvatarRenderMode.LEGACY_IFRAME });

    // ⑨ AvatarService + 按 flag 启动
    service = createAvatarService({
      registry: registryForServices,
      flagStorage: flags,
      nowMonotonic: clock,
      createDirectRuntime: () => ({ runtime, surfaceController, engineAdapter: adapter }),
      createLegacyBridge: () => ({ note: "vrm-inspector-panel 承担" }),
    });
    const startedMode = service.startMode(service.readFlagMode());
    store.setMode(startedMode);
    store.bindRuntime(service.getRuntime());
    note("service", true, `mode=${startedMode}`);

    // ⑩ 自定义导入桥只在完整 preload 窄桥存在时安装。测试/普通浏览器环境
    // 缺少任一 desktop 能力只记 skip，不得拖累 direct 启动或触发整套 fallback。
    const desktop = window?.tiangongDesktop ?? null;
    const importBridgeReady =
      typeof desktop?.avatarImport?.chooseFile === "function" &&
      typeof desktop?.avatarImport?.commitCandidate === "function" &&
      typeof desktop?.avatarImport?.deleteModelFile === "function" &&
      typeof desktop?.avatarAsset?.issueCandidateGrant === "function" &&
      typeof desktop?.avatarAsset?.openChannel === "function";
    if (importBridgeReady && window !== null && typeof window === "object") {
      try {
        avatarImportBridge = installAvatarImportBridge(window, {
          desktop,
          registry,
          tokenIssuer,
          getRuntime: () => service?.getRuntime?.() ?? null,
        });
        note("import-bridge", true, "installed");
      } catch (error) {
        note("import-bridge", false, String(error?.message ?? error));
      }
    } else {
      note("import-bridge", true, "skipped: controlled desktop bridge unavailable");
    }

    // ⑪ direct 激活：优先恢复上次选择的模型（仍登记且 admitted），
    // 否则回退初始模型；空制品保持 import-only。
    const safeModelAvailable = builtinModels.some((model) => model.id === AVATAR_SAFE_MODEL_ID);
    const savedModelId = readSelectedModelFlag(flags);
    const savedModelAvailable =
      savedModelId !== null &&
      (() => {
        const record = registry.getRecord(savedModelId);
        return record !== null && record.admissionState === AdmissionState.ADMITTED;
      })();
    const initialModelId = savedModelAvailable
      ? savedModelId
      : autoSelectSafeModel && safeModelAvailable
        ? AVATAR_SAFE_MODEL_ID
        : null;
    if (startedMode === AvatarRenderMode.DIRECT && initialModelId !== null) {
      queueMicrotask(() => {
        try {
          service.getRuntime()?.selectModel(initialModelId);
        } catch (error) {
          note("auto-select", false, String(error?.message ?? error));
        }
      });
      note(
        "auto-select",
        true,
        savedModelAvailable ? `restore:${savedModelId}` : `safe:${AVATAR_SAFE_MODEL_ID}`,
      );
    } else if (startedMode === AvatarRenderMode.DIRECT && autoSelectSafeModel) {
      note("auto-select", true, `skipped: ${AVATAR_SAFE_MODEL_ID} absent`);
    }

    bootHandle = Object.freeze({
      version: AVATAR_BOOT_VERSION,
      service,
      runtime,
      store,
      scheduler,
      biaoxianAdapter,
      engineAdapter: adapter,
      surfaceController,
      assetSource,
      assetRegistry: registry,
      avatarImportBridge,
      diagnostics,
      bootLog: Object.freeze(bootLog.slice()),
      fallback: false,
    });
    return bootHandle;
  } catch (error) {
    const message = String(error?.message ?? error);
    note("boot", false, message);
    try {
      window?.tiangongDesktop?.writeDiagnostic?.("avatar-boot-failed", message.slice(0, 500));
    } catch (_error) { /* 诊断通道不可用时静默 */ }

    // 回退：legacy-iframe（不阻断前端启动）。avatar-service 已注册则复用，否则补建。
    try {
      if (typeof registryForServices.hasService === "function" && registryForServices.hasService("avatar-service")) {
        service = registryForServices.getService("avatar-service");
      } else {
        service = createAvatarService({
          registry: registryForServices,
          flagStorage: flags,
          nowMonotonic: clock,
          createDirectRuntime: null,
          createLegacyBridge: () => ({ note: "vrm-inspector-panel 承担" }),
        });
      }
      // 统一走 setMode：先清理可能已启动的 direct，再持久化诊断 flag，
      // 随后启动 legacy。下次前端启动不会再次踩同一个坏依赖。
      service.setMode(AvatarRenderMode.LEGACY_IFRAME);
      note("fallback", true, "legacy-iframe");
    } catch (fallbackError) {
      service = null;
      note("fallback", false, String(fallbackError?.message ?? fallbackError));
    }
    bootHandle = Object.freeze({
      version: AVATAR_BOOT_VERSION,
      service,
      runtime: null,
      store: null,
      scheduler: null,
      biaoxianAdapter: null,
      engineAdapter: null,
      surfaceController: null,
      assetSource: null,
      assetRegistry: null,
      avatarImportBridge: null,
      diagnostics: null,
      bootLog: Object.freeze(bootLog.slice()),
      fallback: true,
    });
    return bootHandle;
  }
}
