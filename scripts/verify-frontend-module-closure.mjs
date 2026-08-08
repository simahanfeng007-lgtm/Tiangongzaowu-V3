import { posix } from "node:path";

const STATIC_MODULE_REFERENCE = /(?:^|[;\n])\s*(?:import|export)\s+(?:[^"'`;]*?\s+from\s+)?["']([^"']+)["']/gm;
const DYNAMIC_MODULE_REFERENCE = /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;

function normalizeArchivePath(value) {
  return `/${posix.normalize(String(value || "").replaceAll("\\", "/")).replace(/^\/+/, "")}`;
}

function parseScriptAttributes(tag) {
  const attributes = new Map();
  for (const match of tag.matchAll(/([:\w-]+)\s*=\s*(["'])(.*?)\2/g)) {
    attributes.set(match[1].toLowerCase(), match[3]);
  }
  return attributes;
}

function resolveImportMapTarget(specifier, importMap) {
  if (Object.hasOwn(importMap, specifier)) return importMap[specifier];
  const prefix = Object.keys(importMap)
    .filter((key) => key.endsWith("/") && specifier.startsWith(key))
    .sort((left, right) => right.length - left.length)[0];
  if (!prefix) return null;
  return `${importMap[prefix]}${specifier.slice(prefix.length)}`;
}

function resolveUrlPath(specifier, importerPath, importMap, importMapBasePath = importerPath) {
  const value = String(specifier || "").trim();
  if (!value || /^(?:data|blob|https?):/i.test(value) || value.startsWith("node:")) return null;
  const usesImportMap = !value.startsWith(".") && !value.startsWith("/");
  const mapped = usesImportMap ? resolveImportMapTarget(value, importMap) : value;
  if (mapped === null) {
    throw new Error(`packaged frontend has an unmapped bare module import: ${value} from ${importerPath}`);
  }
  const withoutSuffix = String(mapped).split(/[?#]/, 1)[0];
  if (/^(?:data|blob|https?):/i.test(withoutSuffix)) return null;
  if (withoutSuffix.startsWith("/")) return normalizeArchivePath(withoutSuffix);
  const basePath = usesImportMap ? importMapBasePath : importerPath;
  return normalizeArchivePath(posix.join(posix.dirname(basePath), withoutSuffix));
}

function parseFrontendEntrypoint(html, htmlPath) {
  const moduleEntries = [];
  let importMap = {};
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    const attributes = parseScriptAttributes(match[1]);
    const type = String(attributes.get("type") || "").toLowerCase();
    if (type === "module" && attributes.has("src")) {
      moduleEntries.push(resolveUrlPath(attributes.get("src"), htmlPath, {}));
    } else if (type === "importmap") {
      let parsed;
      try {
        parsed = JSON.parse(match[2]);
      } catch (error) {
        throw new Error(`packaged frontend import map is invalid JSON: ${error.message}`);
      }
      importMap = parsed?.imports && typeof parsed.imports === "object" ? parsed.imports : {};
    }
  }
  if (moduleEntries.length === 0) {
    throw new Error(`packaged frontend has no module entry scripts: ${htmlPath}`);
  }
  return { moduleEntries, importMap };
}

function moduleReferences(source) {
  const references = [];
  for (const pattern of [STATIC_MODULE_REFERENCE, DYNAMIC_MODULE_REFERENCE]) {
    pattern.lastIndex = 0;
    for (const match of source.matchAll(pattern)) references.push(match[1]);
  }
  return references;
}

export function verifyFrontendModuleClosure({
  htmlPath = "/frontend-v2/index.html",
  packagedFiles,
  readText,
}) {
  if (!(packagedFiles instanceof Set) || typeof readText !== "function") {
    throw new TypeError("packagedFiles Set and readText function are required");
  }
  const normalizedFiles = new Set([...packagedFiles].map(normalizeArchivePath));
  const normalizedHtmlPath = normalizeArchivePath(htmlPath);
  if (!normalizedFiles.has(normalizedHtmlPath)) {
    throw new Error(`packaged frontend entry is missing: ${normalizedHtmlPath}`);
  }
  const html = readText(normalizedHtmlPath);
  const { moduleEntries, importMap } = parseFrontendEntrypoint(html, normalizedHtmlPath);
  const visited = new Set();
  const pending = [...moduleEntries];
  while (pending.length > 0) {
    const modulePath = pending.pop();
    if (modulePath === null || visited.has(modulePath)) continue;
    if (!normalizedFiles.has(modulePath)) {
      throw new Error(`packaged frontend module dependency is missing: ${modulePath}`);
    }
    visited.add(modulePath);
    const source = readText(modulePath);
    for (const specifier of moduleReferences(source)) {
      const resolved = resolveUrlPath(specifier, modulePath, importMap, normalizedHtmlPath);
      if (resolved !== null && !visited.has(resolved)) pending.push(resolved);
    }
  }
  return Object.freeze({ entryCount: moduleEntries.length, moduleCount: visited.size });
}
