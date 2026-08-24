"use strict";

const crypto = require("crypto");
const fs = require("fs");
const fsp = fs.promises;
const https = require("https");
const path = require("path");
const { spawn, execFile } = require("child_process");
const { URL } = require("url");
const { compareSemver } = require("./lib/release-binding");

const MAX_METADATA_BYTES = 1024 * 1024;
const MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024;

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

// P2-20: reuse the authoritative SemVer comparator from release-binding.
// The previous local implementation treated "3.0.3-beta" as newer than
// "3.0.3" and lost precision on huge version numbers.
const compareVersion = compareSemver;

function atomicWriteJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  const fd = fs.openSync(temp, "w", 0o600);
  try {
    fs.writeFileSync(fd, JSON.stringify(data, null, 2), "utf8");
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  fs.renameSync(temp, filePath);
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolve(hash.digest("hex")));
  });
}

function boundedHttpsGet(rawUrl, maxBytes, allowedOrigins, onChunk) {
  return new Promise((resolve, reject) => {
    let parsed;
    try { parsed = new URL(rawUrl); } catch { reject(new Error("update_url_invalid")); return; }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || !allowedOrigins.has(parsed.origin)) {
      reject(new Error("update_url_not_trusted")); return;
    }
    const req = https.get(parsed, { timeout: 30000, headers: { "User-Agent": "TiangongV3-SecureUpdater/1" } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400) { res.resume(); reject(new Error("update_redirect_forbidden")); return; }
      if (res.statusCode !== 200) { res.resume(); reject(new Error(`update_http_${res.statusCode}`)); return; }
      const contentLength = Number(res.headers["content-length"] || 0);
      if (contentLength && contentLength > maxBytes) { res.resume(); reject(new Error("update_response_too_large")); return; }
      const chunks = []; let total = 0;
      res.on("data", (chunk) => {
        total += chunk.length;
        if (total > maxBytes) { req.destroy(new Error("update_response_too_large")); return; }
        if (onChunk) {
          // onChunk 在流事件回调内同步执行（writeSync 落盘）；磁盘满等
          // 异常若任其冒泡会成为主进程 uncaughtException 且外层 Promise
          // 永不 settle。统一转成 req.destroy -> reject。
          try { onChunk(chunk, total, contentLength); }
          catch (error) { req.destroy(error); }
        } else chunks.push(chunk);
      });
      res.on("end", () => resolve(onChunk ? { total, contentLength } : Buffer.concat(chunks)));
      res.on("error", reject);
    });
    req.on("timeout", () => req.destroy(new Error("update_timeout")));
    req.on("error", reject);
  });
}

function verifyManifestEnvelope({ envelope, trust, state = {}, currentVersion = "0.0.0", now = Date.now() }) {
  if (!envelope || typeof envelope !== "object" || !envelope.signed || !envelope.signature) throw new Error("update_manifest_shape_invalid");
  const allowedOrigins = new Set((trust.allowed_origins || []).map((item) => new URL(item).origin));
  if (!trust.public_key_pem || !trust.key_id || !allowedOrigins.size) throw new Error("update_trust_root_incomplete");
  const signedBytes = Buffer.from(canonicalJson(envelope.signed), "utf8");
  if (String(envelope.signature.key_id || "") !== String(trust.key_id)) throw new Error("update_signing_key_mismatch");
  const signature = Buffer.from(String(envelope.signature.sig || ""), "base64");
  if (!crypto.verify(null, signedBytes, trust.public_key_pem, signature)) throw new Error("update_signature_invalid");
  const signed = envelope.signed;
  if (signed.schema !== "tiangong.update.manifest.v1") throw new Error("update_manifest_schema_invalid");
  const metadataVersion = Number(signed.metadata_version);
  // 只拒绝更旧的 metadata_version：相同的必然出自签名方（签名/过期/
  // 版本三重校验在前），是幂等重查而非重放。旧实现用 <= 会把"服务器
  // 尚未发布新 manifest 时的再次检查"判成 replay，已就绪的更新被
  // recordError 打成 ERROR 后永远无法下载。
  if (!Number.isSafeInteger(metadataVersion) || metadataVersion < Number(state.highest_metadata_version || 0)) throw new Error("update_metadata_rollback_or_replay");
  const expires = Date.parse(String(signed.expires || ""));
  if (!Number.isFinite(expires) || expires <= now) throw new Error("update_manifest_expired");
  const releaseVersion = String(signed.release_version || "");
  if (compareVersion(releaseVersion, currentVersion) <= 0 || compareVersion(releaseVersion, String(state.highest_release_version || "0")) < 0) throw new Error("update_release_rollback_or_not_newer");
  const target = signed.target || {};
  const targetUrl = new URL(String(target.url || ""));
  if (targetUrl.protocol !== "https:" || targetUrl.username || targetUrl.password || !allowedOrigins.has(targetUrl.origin)) throw new Error("update_target_origin_not_trusted");
  if (!/^[0-9a-f]{64}$/i.test(String(target.sha256 || ""))) throw new Error("update_target_hash_invalid");
  if (!Number.isSafeInteger(Number(target.size)) || Number(target.size) <= 0 || Number(target.size) > MAX_PACKAGE_BYTES) throw new Error("update_target_size_invalid");
  return signed;
}

class SecureUpdater {
  constructor({ app, userData, currentVersion, trustPath, onStatus = () => {}, onProgress = () => {}, soulBackup = null }) {
    this.app = app;
    this.userData = userData;
    this.currentVersion = String(currentVersion || "0.0.0");
    this.trustPath = trustPath;
    this.onStatus = onStatus;
    this.onProgress = onProgress;
    this.soulBackup = soulBackup;
    this.root = path.join(userData, "secure-update");
    this.statePath = path.join(this.root, "state.json");
    this.downloadRoot = path.join(this.root, "downloads");
    this.healthRoot = path.join(this.root, "health");
    fs.mkdirSync(this.downloadRoot, { recursive: true });
    fs.mkdirSync(this.healthRoot, { recursive: true });
    this.state = this._loadState();
    this.trust = this._loadTrust();
    this._seedLastKnownGoodInstaller();
  }

  _loadState() {
    try { const value = JSON.parse(fs.readFileSync(this.statePath, "utf8")); return value && typeof value === "object" ? value : {}; }
    catch { return { phase: "IDLE", highest_metadata_version: 0, highest_release_version: this.currentVersion }; }
  }
  _save(patch = {}) { this.state = { ...this.state, ...patch, updated_at: new Date().toISOString() }; atomicWriteJson(this.statePath, this.state); this.onStatus(this.status()); }
  _loadTrust() {
    try { const value = JSON.parse(fs.readFileSync(this.trustPath, "utf8")); return value && typeof value === "object" ? value : {}; }
    catch { return {}; }
  }
  _seedLastKnownGoodInstaller() {
    if (this.state.last_known_good_installer && fs.existsSync(this.state.last_known_good_installer)) return;
    const bundled = path.join(process.resourcesPath || "", "update-baseline", "TiangongV3-current.exe");
    if (!bundled || !fs.existsSync(bundled)) return;
    const target = path.join(this.root, "last-known-good", `TiangongV3-${this.currentVersion}.exe`);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    if (!fs.existsSync(target)) fs.copyFileSync(bundled, target);
    this._save({ last_known_good_installer: target, highest_release_version: this.currentVersion });
  }

  _origins() { return new Set((this.trust.allowed_origins || []).map((item) => new URL(item).origin)); }
  status() { return { ok: true, enabled: this.trust.enabled === true, currentVersion: this.currentVersion, ...this.state, downloaded: Boolean(this.state.download_path && fs.existsSync(this.state.download_path)) }; }

  recordError(error) {
    const message = error?.message || String(error || "update_failed");
    // DOWNLOADED/APPLYING/COMMITTED 是资产已就绪状态：检查/网络失败只
    // 记录错误，绝不作废已下载（哈希已验证）或已提交的更新，否则
    // "下载完再点一次检查且失败"就会让 apply() 的前置条件永远不满足。
    const assetPhase = ["DOWNLOADED", "APPLYING", "COMMITTED"].includes(this.state.phase);
    this._save({ phase: assetPhase ? this.state.phase : "ERROR", error: message });
    return { ok: false, error: message, status: this.status() };
  }

  async check() {
    if (this.trust.enabled !== true) return { ok: false, error: "update_not_configured", status: this.status() };
    const manifestUrl = String(this.trust.manifest_url || "");
    const allowedOrigins = this._origins();
    if (!manifestUrl || !this.trust.public_key_pem || !this.trust.key_id || !this.trust.expected_publisher || !allowedOrigins.size) throw new Error("update_trust_root_incomplete");
    // 已就绪的下载不因再次检查而作废：失败时 recordError 保留资产
    // phase；成功且仍是同一版本时保持 DOWNLOADED，只有服务器发布
    // 新版本才回到 AVAILABLE。
    const previouslyDownloaded = this.state.phase === "DOWNLOADED" ? this.state.available : null;
    this._save({ phase: this.state.phase === "DOWNLOADED" ? "DOWNLOADED" : "CHECKING", error: "" });
    const raw = await boundedHttpsGet(manifestUrl, MAX_METADATA_BYTES, allowedOrigins);
    let envelope;
    try { envelope = JSON.parse(raw.toString("utf8")); } catch { throw new Error("update_manifest_json_invalid"); }
    const signed = verifyManifestEnvelope({
      envelope,
      trust: this.trust,
      state: this.state,
      currentVersion: this.currentVersion,
    });
    const sameRelease = previouslyDownloaded
      && compareVersion(String(signed.release_version), String(previouslyDownloaded.release_version)) === 0;
    this._save({
      phase: sameRelease ? "DOWNLOADED" : "AVAILABLE",
      available: signed,
      highest_metadata_version: Number(signed.metadata_version),
      error: "",
    });
    return { ok: true, available: signed, status: this.status() };
  }

  async download() {
    const signed = this.state.available;
    if (!signed || this.state.phase !== "AVAILABLE") throw new Error("update_not_available");
    const target = signed.target;
    const destination = path.join(this.downloadRoot, `TiangongV3-${signed.release_version}.exe`);
    const partial = `${destination}.partial`;
    await fsp.rm(partial, { force: true });
    const fd = fs.openSync(partial, "w", 0o600); let written = 0;
    const writeAll = (buffer) => {
      // writeSync 对大 buffer 可能部分写，循环到写完。
      let offset = 0;
      while (offset < buffer.length) offset += fs.writeSync(fd, buffer, offset);
    };
    try {
      try {
        await boundedHttpsGet(String(target.url), Number(target.size), this._origins(), (chunk, total) => {
          writeAll(chunk); written = total;
          this.onProgress({ phase: "DOWNLOADING", written, total: Number(target.size), percent: Math.min(100, Math.round(written * 100 / Number(target.size))) });
        });
        fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
    } catch (error) {
      // 网络中断/磁盘满等任何失败都清掉半成品，不留无法通过哈希校验的 partial。
      await fsp.rm(partial, { force: true });
      throw error;
    }
    if (written !== Number(target.size)) { await fsp.rm(partial, { force: true }); throw new Error("update_download_size_mismatch"); }
    const digest = await sha256File(partial);
    if (digest.toLowerCase() !== String(target.sha256).toLowerCase()) { await fsp.rm(partial, { force: true }); throw new Error("update_download_hash_mismatch"); }
    await fsp.rename(partial, destination);
    this._save({ phase: "DOWNLOADED", download_path: destination, download_sha256: digest, error: "" });
    return { ok: true, path: destination, sha256: digest, status: this.status() };
  }

  async _verifyAuthenticode(filePath) {
    if (process.platform !== "win32") return { status: "platform_not_windows" };
    return new Promise((resolve, reject) => {
      const script = `$s=Get-AuthenticodeSignature -LiteralPath '${String(filePath).replace(/'/g, "''")}'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; @{Status=$s.Status.ToString();Subject=($s.SignerCertificate.Subject)} | ConvertTo-Json -Compress`;
      execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], { windowsHide: true, timeout: 30000 }, (error, stdout) => {
        if (error) { reject(new Error("update_authenticode_check_failed")); return; }
        try {
          const result = JSON.parse(stdout.trim());
          if (result.Status !== "Valid") throw new Error("update_authenticode_invalid");
          if (this.trust.expected_publisher && !String(result.Subject || "").includes(String(this.trust.expected_publisher))) throw new Error("update_publisher_mismatch");
          resolve(result);
        } catch (exc) { reject(exc); }
      });
    });
  }

  async _sha256(filePath) {
    const digest = crypto.createHash("sha256");
    digest.update(await fs.promises.readFile(filePath));
    return digest.digest("hex").toLowerCase();
  }

  async apply() {
    if (process.platform !== "win32") throw new Error("update_apply_requires_windows");
    if (this.state.phase !== "DOWNLOADED" || !fs.existsSync(this.state.download_path || "")) throw new Error("update_not_downloaded");
    await this._verifyAuthenticode(this.state.download_path);
    let backup = null;
    if (this.soulBackup) backup = await this.soulBackup();
    const token = crypto.randomBytes(32).toString("hex");
    const marker = path.join(this.healthRoot, `${token}.json`);
    const helper = path.join(__dirname, "scripts", "update-transaction.ps1");
    const previousInstaller = String(this.state.last_known_good_installer || "");
    if (!previousInstaller || !fs.existsSync(previousInstaller)) throw new Error("update_rollback_baseline_missing");
    // P2-21: re-verify the rollback installer before executing it.  The hash
    // was frozen when the previous installer was committed as last-known-good.
    const recordedHash = String(this.state.last_known_good_installer_sha256 || "").toLowerCase();
    if (recordedHash) {
      const actualHash = await this._sha256(previousInstaller);
      if (actualHash !== recordedHash) throw new Error("update_rollback_hash_mismatch");
    } else {
      // No recorded hash (legacy state): re-verify Authenticode and refuse a
      // rollback that is neither validly signed nor hash-bound.
      try {
        await this._verifyAuthenticode(previousInstaller);
      } catch (error) {
        if (String(error?.message || "").includes("update_authenticode_invalid")) {
          throw new Error("update_rollback_not_verified");
        }
        throw error;
      }
    }
    this._save({ phase: "APPLYING", transaction_token: token, health_marker: marker, soul_backup: backup, target_version: this.state.available.release_version });
    const args = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", helper,
      "-Installer", this.state.download_path, "-AppExe", process.execPath, "-HealthMarker", marker, "-Token", token,
      "-ParentPid", String(process.pid), "-RollbackInstaller", previousInstaller,
      "-RollbackInstallerSha256", String(this.state.last_known_good_installer_sha256 || "")];
    const child = spawn("powershell.exe", args, { detached: true, windowsHide: true, stdio: "ignore", env: { ...process.env, TIANGONG_UPDATE_TRANSACTION: "1" } });
    child.unref();
    return { ok: true, restartRequired: true, token, backup, status: this.status() };
  }

  markHealthy(token) {
    if (!token || token !== this.state.transaction_token || !this.state.health_marker) return false;
    atomicWriteJson(this.state.health_marker, { ok: true, version: this.currentVersion, observed_at: new Date().toISOString() });
    const installer = this.state.download_path;
    this._sha256(installer).then((installerHash) => {
      this._save({
        phase: "COMMITTED",
        highest_release_version: this.currentVersion,
        last_known_good_installer: installer,
        last_known_good_installer_sha256: installerHash,
        transaction_token: "",
        health_marker: "",
        error: "",
      });
    }).catch(() => {
      // 新安装包哈希不可得（文件被杀毒隔离/删除等）：绝不能把基线指向
      // 无法验证的文件而保留旧哈希——那会让之后每次 apply() 的基线复核
      // 恒抛 update_rollback_hash_mismatch，永久无法升级。旧基线仍完整
      // 时保留旧基线；旧基线也缺失则清空，让 _seedLastKnownGoodInstaller
      // 从打包基线重建。
      const patch = {
        phase: "COMMITTED",
        highest_release_version: this.currentVersion,
        transaction_token: "",
        health_marker: "",
        error: "update_baseline_hash_unavailable",
      };
      const previousInstaller = String(this.state.last_known_good_installer || "");
      if (previousInstaller && fs.existsSync(previousInstaller)) {
        this._save(patch);
      } else {
        this._save({ ...patch, last_known_good_installer: "", last_known_good_installer_sha256: "" });
      }
    });
    return true;
  }
}

module.exports = { SecureUpdater, canonicalJson, compareVersion, verifyManifestEnvelope };
