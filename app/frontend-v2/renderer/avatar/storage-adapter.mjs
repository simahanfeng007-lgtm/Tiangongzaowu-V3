// §22.2/§22.3/§22.4 userData 存储：布局常量、配额常量、原子写原语与可注入 backend。
// 渲染端不直接持路径权限：backend 由宿主注入（主进程 IPC / 内存测试实现 / 文件实现）。

export const STORAGE_LAYOUT_SCHEMA_VERSION = 1;

// §22.2 正式布局（相对 userData 根）。
export const AVATAR_STORAGE_LAYOUT = Object.freeze({
  schemaVersion: STORAGE_LAYOUT_SCHEMA_VERSION,
  rootDir: "avatar-models",
  registryFile: "avatar-models/registry-v1.json",
  modelsDir: "avatar-models/models",
  quarantineDir: "avatar-models/quarantine",
  tempDir: "avatar-models/temp",
  receiptsDir: "avatar-models/receipts",
  stateDir: "avatar-models/state",
  pendingLoadJournalFile: "avatar-models/state/pending-load-v1.json",
  quarantineStateFile: "avatar-models/state/quarantine-policy-state-v1.json",
});

export const STORAGE_QUOTA_SCHEMA_VERSION = 1;

// §22.4 配额（初始值，随版本校准）。
export const DEFAULT_STORAGE_QUOTAS = Object.freeze({
  schemaVersion: STORAGE_QUOTA_SCHEMA_VERSION,
  maxModelBytes: 256 * 1024 * 1024, // 单模型上限（与 §9.3 maxFileBytes 对齐）
  maxModelCount: 64, // 模型总数量
  maxTotalDiskBytes: 4 * 1024 * 1024 * 1024, // 总磁盘上限
  maxTempBytes: 512 * 1024 * 1024, // 临时区上限
  quarantineRetentionMs: 30 * 24 * 3600 * 1000, // quarantine 保留期
});

export class StorageError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "StorageError";
    this.code = code;
  }
}

// schemaVersion 安全失败：未知更高版本拒绝读取，绝不覆盖（§22.3 末尾）。
export function assertSchemaVersionSupported(doc, knownSchemaVersion, what) {
  if (doc !== null && typeof doc === "object" && Number.isInteger(doc.schemaVersion) && doc.schemaVersion > knownSchemaVersion) {
    throw new StorageError(
      "schema_version_unsupported",
      `${what} schemaVersion=${doc.schemaVersion} 高于已知 ${knownSchemaVersion}，安全失败不覆盖`,
    );
  }
}

// ── backend 接口（鸭子类型，全部 async）────────────────────
// readBytes(path) → Uint8Array | null
// writeBytesAtomic(path, bytes, opts?) → void：temp → flush → 校验 → 原子替换；
//   任何失败不得留下半成品目标文件，临时文件必须清理。
// exists(path) → boolean；remove(path) → void；listPaths(prefix) → string[]

// 内存 backend（测试与纯渲染端模拟）。
export function createMemoryStorageBackend() {
  const files = new Map();
  let tempCounter = 0;
  let injectedFailures = 0;
  return {
    kind: "memory",
    async readBytes(path) {
      const stored = files.get(path);
      return stored ? new Uint8Array(stored) : null;
    },
    async writeBytesAtomic(path, bytes) {
      const tmp = `${path}.tmp-${tempCounter += 1}`;
      // temp → flush（内存即落）→ 校验 → 原子替换；注入失败模拟 flush/替换前崩溃。
      files.set(tmp, new Uint8Array(bytes));
      if (injectedFailures > 0) {
        injectedFailures -= 1;
        files.delete(tmp);
        throw new StorageError("write_injected_failure", `注入的写入失败：${path}`);
      }
      files.set(path, files.get(tmp));
      files.delete(tmp);
    },
    async exists(path) {
      return files.has(path);
    },
    async remove(path) {
      files.delete(path);
    },
    async listPaths(prefix = "") {
      return [...files.keys()].filter((key) => key.startsWith(prefix));
    },
    // 测试钩子：注入接下来 n 次原子写失败；检查是否有临时文件残留。
    failNextWrites(count = 1) {
      injectedFailures = count;
    },
    hasTempLeftovers() {
      return [...files.keys()].some((key) => key.includes(".tmp-"));
    },
  };
}

// 生产 sandbox renderer 的持久化 backend。renderer 只把固定枚举 key 交给
// preload 窄桥；绝不把 userData 路径、相对路径或目录枚举请求送进主进程。
export const AVATAR_STORAGE_IPC_KEY_BY_PATH = Object.freeze({
  [AVATAR_STORAGE_LAYOUT.registryFile]: "registry-v1",
  [AVATAR_STORAGE_LAYOUT.pendingLoadJournalFile]: "pending-load-v1",
  [AVATAR_STORAGE_LAYOUT.quarantineStateFile]: "quarantine-state-v1",
});

function ipcStorageKeyForPath(logicalPath) {
  if (
    typeof logicalPath !== "string"
    || !Object.hasOwn(AVATAR_STORAGE_IPC_KEY_BY_PATH, logicalPath)
  ) {
    throw new StorageError("path_not_allowed", `IPC avatar storage 不允许逻辑路径: ${String(logicalPath)}`);
  }
  return AVATAR_STORAGE_IPC_KEY_BY_PATH[logicalPath];
}

function normalizeIpcBytes(value, logicalPath) {
  if (value === null) return null;
  if (value instanceof Uint8Array) return new Uint8Array(value);
  if (value instanceof ArrayBuffer) return new Uint8Array(value.slice(0));
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
  }
  throw new StorageError("bridge_response_invalid", `IPC avatar storage 返回非法 bytes: ${logicalPath}`);
}

export function createIpcStorageBackend({ bridge } = {}) {
  if (
    bridge === null
    || typeof bridge !== "object"
    || typeof bridge.read !== "function"
    || typeof bridge.writeAtomic !== "function"
  ) {
    throw new StorageError("bridge_invalid", "createIpcStorageBackend 需要 read/writeAtomic 窄桥");
  }

  const readBytes = async (logicalPath) => {
    const key = ipcStorageKeyForPath(logicalPath);
    return normalizeIpcBytes(await bridge.read(key), logicalPath);
  };

  return Object.freeze({
    kind: "ipc",
    async readBytes(logicalPath) {
      return readBytes(logicalPath);
    },
    async writeBytesAtomic(logicalPath, bytes) {
      const key = ipcStorageKeyForPath(logicalPath);
      if (!(bytes instanceof ArrayBuffer) && !ArrayBuffer.isView(bytes)) {
        throw new StorageError("bytes_invalid", "IPC avatar storage 只接受 Uint8Array/ArrayBuffer");
      }
      const result = await bridge.writeAtomic(key, bytes);
      if (result?.ok !== true || result.key !== key || result.byteLength !== bytes.byteLength) {
        throw new StorageError("bridge_response_invalid", `IPC avatar storage 写入回执非法: ${logicalPath}`);
      }
    },
    async exists(logicalPath) {
      return (await readBytes(logicalPath)) !== null;
    },
    async remove(logicalPath) {
      ipcStorageKeyForPath(logicalPath);
      throw new StorageError("operation_unsupported", "IPC avatar storage 不开放删除能力");
    },
    async listPaths(prefix = "") {
      if (typeof prefix !== "string") {
        throw new StorageError("path_not_allowed", "IPC avatar storage prefix 必须是字符串");
      }
      const found = [];
      for (const logicalPath of Object.keys(AVATAR_STORAGE_IPC_KEY_BY_PATH)) {
        if (logicalPath.startsWith(prefix) && (await readBytes(logicalPath)) !== null) {
          found.push(logicalPath);
        }
      }
      return found;
    },
  });
}

// 文件 backend（Node/Electron 主进程侧；渲染端经 IPC 注入同形接口）。
export async function createFileStorageBackend({ rootDir }) {
  if (typeof rootDir !== "string" || rootDir.length === 0) {
    throw new StorageError("root_dir_invalid", "createFileStorageBackend 需要非空 rootDir");
  }
  const fs = await import("node:fs/promises");
  const pathModule = await import("node:path");
  const root = pathModule.resolve(rootDir);
  const resolveInside = (logicalPath) => {
    const full = pathModule.resolve(root, logicalPath);
    if (full !== root && !full.startsWith(root + pathModule.sep)) {
      throw new StorageError("path_escape", `逻辑路径越出存储根: ${logicalPath}`);
    }
    return full;
  };
  let tempCounter = 0;
  return {
    kind: "file",
    rootDir: root,
    async readBytes(logicalPath) {
      try {
        return new Uint8Array(await fs.readFile(resolveInside(logicalPath)));
      } catch (error) {
        if (error && error.code === "ENOENT") return null;
        throw error;
      }
    },
    async writeBytesAtomic(logicalPath, bytes, { expectedSha256 = null, sha256 = null } = {}) {
      const target = resolveInside(logicalPath);
      const tmp = `${target}.tmp-${process.pid}-${tempCounter += 1}`;
      await fs.mkdir(pathModule.dirname(target), { recursive: true });
      let handle = null;
      try {
        handle = await fs.open(tmp, "w");
        await handle.writeFile(bytes);
        await handle.sync(); // flush 落盘后再校验、再替换
        await handle.close();
        handle = null;
        const stat = await fs.stat(tmp);
        if (stat.size !== bytes.byteLength) {
          throw new StorageError("write_verify_failed", `写入大小 ${stat.size} != 期望 ${bytes.byteLength}`);
        }
        if (expectedSha256 !== null) {
          if (typeof sha256 !== "function") {
            throw new StorageError("write_verify_failed", "expectedSha256 校验需要注入 sha256");
          }
          const reread = new Uint8Array(await fs.readFile(tmp));
          if (sha256(reread) !== expectedSha256) {
            throw new StorageError("write_verify_failed", "写入后哈希校验不一致");
          }
        }
        await fs.rename(tmp, target); // 同目录原子替换
      } finally {
        if (handle !== null) await handle.close().catch(() => {});
        await fs.rm(tmp, { force: true }).catch(() => {}); // 不留下半成品
      }
    },
    async exists(logicalPath) {
      try {
        await fs.access(resolveInside(logicalPath));
        return true;
      } catch {
        return false;
      }
    },
    async remove(logicalPath) {
      await fs.rm(resolveInside(logicalPath), { force: true });
    },
    async listPaths(prefix = "") {
      try {
        const entries = await fs.readdir(root, { recursive: true, withFileTypes: true });
        return entries
          .filter((entry) => entry.isFile())
          .map((entry) => pathModule.relative(root, pathModule.join(entry.parentPath ?? entry.path, entry.name)).split(pathModule.sep).join("/"))
          .filter((name) => name.startsWith(prefix));
      } catch (error) {
        if (error && error.code === "ENOENT") return [];
        throw error;
      }
    },
  };
}

// JSON 助手： UTF-8 写读 + 原子替换。
export async function readJsonFile(backend, logicalPath) {
  const bytes = await backend.readBytes(logicalPath);
  if (bytes === null) return null;
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new StorageError("json_corrupted", `JSON 文件损坏: ${logicalPath}`);
  }
}

export async function writeJsonAtomic(backend, logicalPath, value) {
  await backend.writeBytesAtomic(logicalPath, new TextEncoder().encode(JSON.stringify(value)));
}
