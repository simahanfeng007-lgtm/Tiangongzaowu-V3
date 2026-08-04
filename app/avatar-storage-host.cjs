"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { TextDecoder } = require("util");

const AVATAR_STORAGE_SCHEMA_VERSION = 1;

const AVATAR_STORAGE_DEFINITIONS = Object.freeze({
  "registry-v1": Object.freeze({
    relativeSegments: Object.freeze(["avatar-models", "registry-v1.json"]),
    maxBytes: 4 * 1024 * 1024,
  }),
  "pending-load-v1": Object.freeze({
    relativeSegments: Object.freeze(["avatar-models", "state", "pending-load-v1.json"]),
    maxBytes: 64 * 1024,
  }),
  "quarantine-state-v1": Object.freeze({
    relativeSegments: Object.freeze(["avatar-models", "state", "quarantine-policy-state-v1.json"]),
    maxBytes: 2 * 1024 * 1024,
  }),
});

const AVATAR_STORAGE_KEYS = Object.freeze(Object.keys(AVATAR_STORAGE_DEFINITIONS));
const TRANSIENT_RENAME_CODES = new Set(["EACCES", "EBUSY", "EEXIST", "EPERM"]);

class AvatarStorageHostError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarStorageHostError";
    this.code = code;
  }
}

function requireDefinition(key) {
  if (typeof key !== "string" || !Object.hasOwn(AVATAR_STORAGE_DEFINITIONS, key)) {
    throw new AvatarStorageHostError("storage_key_invalid", "avatar storage key 不在固定枚举内");
  }
  return AVATAR_STORAGE_DEFINITIONS[key];
}

function bytesToBuffer(bytes) {
  if (Buffer.isBuffer(bytes)) return Buffer.from(bytes);
  if (bytes instanceof ArrayBuffer) return Buffer.from(new Uint8Array(bytes));
  if (ArrayBuffer.isView(bytes)) {
    return Buffer.from(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength));
  }
  throw new AvatarStorageHostError("storage_bytes_invalid", "avatar storage bytes 必须是 Uint8Array 或 ArrayBuffer");
}

function validateJsonDocument(key, bytes) {
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (_error) {
    throw new AvatarStorageHostError("storage_json_invalid", `${key} 不是合法 UTF-8 JSON`);
  }
  let document;
  try {
    document = JSON.parse(text);
  } catch (_error) {
    throw new AvatarStorageHostError("storage_json_invalid", `${key} 不是合法 JSON`);
  }
  if (document === null || typeof document !== "object" || Array.isArray(document)) {
    throw new AvatarStorageHostError("storage_json_invalid", `${key} 顶层必须是 JSON 对象`);
  }
  if (
    !Number.isInteger(document.schemaVersion)
    || document.schemaVersion < 1
    || document.schemaVersion > AVATAR_STORAGE_SCHEMA_VERSION
  ) {
    throw new AvatarStorageHostError(
      "storage_schema_unsupported",
      `${key} schemaVersion 必须是 1..${AVATAR_STORAGE_SCHEMA_VERSION}`,
    );
  }
  return document;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function replaceFileWithRetry(source, target, {
  rename = fs.promises.rename.bind(fs.promises),
  maxAttempts = 5,
  retryDelayMs = 12,
} = {}) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      // libuv maps this to an atomic replace on Windows (MOVEFILE_REPLACE_EXISTING).
      // Never unlink target first: a failed replace must leave the old file intact.
      await rename(source, target);
      return;
    } catch (error) {
      lastError = error;
      if (!TRANSIENT_RENAME_CODES.has(error?.code) || attempt === maxAttempts) break;
      await delay(retryDelayMs * attempt);
    }
  }
  throw lastError;
}

function createAvatarStorageHost({
  userDataRoot,
  fsPromises = fs.promises,
  randomId = () => crypto.randomUUID(),
  replaceFile = null,
} = {}) {
  if (typeof userDataRoot !== "string" || userDataRoot.length === 0 || !path.isAbsolute(userDataRoot)) {
    throw new AvatarStorageHostError("storage_root_invalid", "avatar storage host 需要绝对 userDataRoot");
  }
  const root = path.resolve(userDataRoot);

  function targetFor(key) {
    const definition = requireDefinition(key);
    const target = path.resolve(root, ...definition.relativeSegments);
    if (!target.startsWith(`${root}${path.sep}`)) {
      throw new AvatarStorageHostError("storage_path_escape", "avatar storage 固定映射越出 userData");
    }
    return { definition, target };
  }

  async function read(key) {
    const { definition, target } = targetFor(key);
    let stat;
    try {
      stat = await fsPromises.stat(target);
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw error;
    }
    if (!stat.isFile()) {
      throw new AvatarStorageHostError("storage_target_invalid", `${key} 不是普通文件`);
    }
    if (stat.size <= 0 || stat.size > definition.maxBytes) {
      throw new AvatarStorageHostError("storage_size_invalid", `${key} 文件大小越界`);
    }
    const bytes = await fsPromises.readFile(target);
    if (bytes.byteLength !== stat.size || bytes.byteLength > definition.maxBytes) {
      throw new AvatarStorageHostError("storage_size_invalid", `${key} 读取期间大小发生变化或越界`);
    }
    validateJsonDocument(key, bytes);
    return new Uint8Array(bytes);
  }

  async function writeAtomic(key, bytes) {
    const { definition, target } = targetFor(key);
    const buffer = bytesToBuffer(bytes);
    if (buffer.byteLength <= 0 || buffer.byteLength > definition.maxBytes) {
      throw new AvatarStorageHostError(
        "storage_size_invalid",
        `${key} 写入大小必须在 1..${definition.maxBytes} 字节`,
      );
    }
    validateJsonDocument(key, buffer);

    const directory = path.dirname(target);
    await fsPromises.mkdir(directory, { recursive: true });
    const temporary = path.join(directory, `.${path.basename(target)}.tmp-${process.pid}-${randomId()}`);
    let handle = null;
    let replaced = false;
    try {
      handle = await fsPromises.open(temporary, "wx", 0o600);
      await handle.writeFile(buffer);
      await handle.sync();
      await handle.close();
      handle = null;

      const temporaryStat = await fsPromises.stat(temporary);
      if (!temporaryStat.isFile() || temporaryStat.size !== buffer.byteLength) {
        throw new AvatarStorageHostError("storage_write_verify_failed", `${key} 临时文件大小复核失败`);
      }
      const verifyBytes = await fsPromises.readFile(temporary);
      if (!Buffer.from(verifyBytes).equals(buffer)) {
        throw new AvatarStorageHostError("storage_write_verify_failed", `${key} 临时文件逐字节复核失败`);
      }
      validateJsonDocument(key, verifyBytes);

      if (replaceFile !== null) {
        await replaceFile(temporary, target);
      } else {
        await replaceFileWithRetry(temporary, target, { rename: fsPromises.rename.bind(fsPromises) });
      }
      replaced = true;
      return Object.freeze({ ok: true, key, byteLength: buffer.byteLength });
    } finally {
      if (handle !== null) await handle.close().catch(() => {});
      if (!replaced) await fsPromises.rm(temporary, { force: true }).catch(() => {});
    }
  }

  return Object.freeze({ read, writeAtomic });
}

module.exports = {
  AVATAR_STORAGE_DEFINITIONS,
  AVATAR_STORAGE_KEYS,
  AVATAR_STORAGE_SCHEMA_VERSION,
  AvatarStorageHostError,
  createAvatarStorageHost,
  replaceFileWithRetry,
  validateJsonDocument,
};
