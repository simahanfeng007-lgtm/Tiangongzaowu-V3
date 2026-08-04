const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif", "tif", "tiff"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "ogv", "mov", "mkv", "avi", "m4v", "wmv", "flv", "mpeg", "mpg", "3gp", "ts", "m2ts"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "m4a", "flac", "aac", "opus", "wma"]);
const URL_RE = /https?:\/\/[^\s<>()]+/i;
const URL_RE_GLOBAL = /https?:\/\/[^\s<>()]+/gi;
const WINDOWS_PATH_RE = /^[a-zA-Z]:[\\/][^\n\r<>"]+$/;
const WINDOWS_MEDIA_PATH_RE = /[a-zA-Z]:[\\/][^\n\r<>"]+?\.(?:png|jpe?g|gif|webp|svg|bmp|ico|avif|tiff?|mp4|webm|ogv|mov|mkv|avi|m4v|wmv|flv|mpe?g|3gp|m2?ts|mp3|wav|ogg|m4a|flac|aac|opus|wma)(?=$|[\s"'`，。；;、)\]}])/gi;
const FILE_URL_RE_GLOBAL = /file:\/\/\/?[^\s<>()"'`]+/gi;
const FILE_URL_RE = /^file:\/\/\/?/i;

function create(tag, className = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function extensionOf(value) {
  const clean = String(value || "").split(/[?#]/)[0].replace(/["')\]}.,;:]+$/, "");
  const match = clean.match(/\.([a-zA-Z0-9]+)$/);
  return match ? match[1].toLowerCase() : "";
}

function isAbsoluteWindowsPath(value) {
  return WINDOWS_PATH_RE.test(String(value || "").trim());
}

function windowsPathToFileUrl(value) {
  const normalized = String(value || "").trim().replace(/\\/g, "/");
  return `file:///${encodeURI(normalized).replace(/%3A/i, ":")}`;
}

function normalizeUrl(value, { media = false } = {}) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (isAbsoluteWindowsPath(raw)) return windowsPathToFileUrl(raw);
  if (/^(https?:|mailto:)/i.test(raw)) return raw;
  if (FILE_URL_RE.test(raw)) return raw;
  if (media && /^data:image\//i.test(raw)) return raw;
  if (/^\.{0,2}\//.test(raw)) return raw;
  return "";
}

function configureLink(link, target, label = "") {
  const raw = String(target || "").trim();
  const href = normalizeUrl(raw);
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  if (isAbsoluteWindowsPath(raw)) {
    link.dataset.openPath = raw;
    link.title = raw;
  }
  if (/^https?:\/\//i.test(href)) {
    link.addEventListener("click", (event) => {
      const bridge = window.tiangongDesktop;
      if (!bridge?.openExternal) return;
      event.preventDefault();
      Promise.resolve(bridge.openExternal(href)).catch(() => {});
    });
  } else if (/^mailto:/i.test(href)) {
    // P2-08: mailto links must actually open the mail client instead of being
    // recognized but inert.
    link.addEventListener("click", (event) => {
      const bridge = window.tiangongDesktop;
      if (!bridge?.openExternal) return;
      event.preventDefault();
      Promise.resolve(bridge.openExternal(href)).catch(() => {});
    });
  } else if (isAbsoluteWindowsPath(raw) || FILE_URL_RE.test(raw)) {
    // P2-08: local file links open through the trusted openPath bridge; they
    // must never fall through to Electron's blocked new-window path.
    const targetPath = isAbsoluteWindowsPath(raw)
      ? raw
      : decodeURIComponent(raw.replace(FILE_URL_RE, "")).replace(/\//g, "\\");
    link.addEventListener("click", (event) => {
      const bridge = window.tiangongDesktop;
      if (!bridge?.openPath) return;
      event.preventDefault();
      Promise.resolve(bridge.openPath(targetPath)).catch(() => {});
    });
  } else if (/^\.{0,2}\//.test(raw)) {
    // Relative links have no unambiguous base; keep them visible but inert.
    link.removeAttribute("href");
    link.title = "相对链接不可直接打开";
    link.style.cursor = "default";
  }
  if (label) appendInline(link, label);
  else link.textContent = raw;
}

function mediaKind(value) {
  const ext = extensionOf(value);
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  return "";
}

function mediaTargetKey(value) {
  const normalized = normalizeUrl(value, { media: true }) || String(value || "").trim();
  return normalized.replace(/\\/g, "/").toLowerCase();
}

function decodeJsonPath(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    return JSON.parse(`"${raw.replace(/"/g, '\\"')}"`);
  } catch {
    return raw.replace(/\\\\/g, "\\");
  }
}

function cleanupMediaTarget(value) {
  let text = String(value || "").trim();
  text = text.replace(/^["'`]+|["'`]+$/g, "");
  text = text.replace(/[，。；;、]+$/g, "");
  text = text.replace(/[)\]}]+$/g, "");
  return text.trim();
}

function collectGeneratedMediaTargets(sourceText, renderedMedia = new Set()) {
  const source = String(sourceText || "");
  const candidates = [];

  for (const match of source.matchAll(URL_RE_GLOBAL)) {
    candidates.push(cleanupMediaTarget(match[0]));
  }
  for (const match of source.matchAll(FILE_URL_RE_GLOBAL)) {
    candidates.push(cleanupMediaTarget(match[0]));
  }
  for (const match of source.matchAll(WINDOWS_MEDIA_PATH_RE)) {
    candidates.push(cleanupMediaTarget(match[0]));
  }
  for (const match of source.matchAll(/["'](?:lujing|path|url|file|video|image)["']\s*:\s*["']([^"'\n\r]+)["']/gi)) {
    candidates.push(cleanupMediaTarget(decodeJsonPath(match[1])));
  }

  const labelPattern = /(?:路径|地址|文件|图片|图像|视频|输出|保存|生成结果|result|path|url|file|image|video)\s*[:：]\s*([^\n\r]+)/gi;
  for (const match of source.matchAll(labelPattern)) {
    candidates.push(cleanupMediaTarget(match[1]));
  }

  const unique = [];
  const seen = new Set(renderedMedia);
  for (const candidate of candidates) {
    const target = cleanupMediaTarget(candidate);
    if (!target || !mediaKind(target) || !normalizeUrl(target, { media: true })) continue;
    const key = mediaTargetKey(target);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(target);
  }
  return unique.slice(0, 8);
}

function buttonState(button, text, resetText = "") {
  if (!button) return;
  const original = resetText || button.dataset.defaultText || button.textContent || "";
  button.textContent = text;
  window.setTimeout(() => {
    button.textContent = original;
  }, 1400);
}

async function writeClipboardText(text) {
  const value = String(text || "");
  if (window.tiangongDesktop?.writeClipboardText) {
    const result = await window.tiangongDesktop.writeClipboardText(value);
    if (result?.ok === false) throw new Error(result.error || "desktop clipboard failed");
    return true;
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const ok = document.execCommand("copy");
  textarea.remove();
  if (!ok) throw new Error("clipboard unavailable");
  return true;
}

async function copyMediaTarget(target, kind, copyAs, button) {
  const runtime = window.tiangongRuntime;
  try {
    if (runtime?.copyMedia) {
      const result = await runtime.copyMedia({ target, kind, copyAs });
      if (result?.ok || result?.copiedAs) {
        const copiedAs = String(result.copiedAs || copyAs || "");
        if (copiedAs === "image") buttonState(button, "已复制图片");
        else if (copiedAs === "file") buttonState(button, "已复制文件");
        else if (copiedAs === "url") buttonState(button, "已复制链接");
        else buttonState(button, "已复制路径");
        return;
      }
      buttonState(button, "复制失败");
      return;
    }
    await writeClipboardText(target);
    buttonState(button, "已复制路径");
  } catch {
    buttonState(button, "复制失败");
  }
}

async function openMediaTarget(target, button) {
  const runtime = window.tiangongRuntime;
  try {
    if (runtime?.openPath) {
      const result = await runtime.openPath(target);
      if (result?.ok) {
        buttonState(button, "已打开");
        return;
      }
      if (isAbsoluteWindowsPath(target) || FILE_URL_RE.test(String(target || ""))) {
        buttonState(button, "打开失败");
        return;
      }
    }
    const href = normalizeUrl(target, { media: true });
    const opened = href ? window.open(href, "_blank", "noreferrer") : null;
    buttonState(button, opened ? "已打开" : "打开失败");
  } catch {
    buttonState(button, "打开失败");
  }
}

async function saveTarget(target, button) {
  const runtime = window.tiangongRuntime;
  try {
    if (runtime?.saveTargetAs) {
      const result = await runtime.saveTargetAs(target, { name: String(target || "").split(/[\\/]/).pop() });
      if (result?.ok && !result?.canceled) buttonState(button, "已保存");
      else if (result?.canceled) buttonState(button, "已取消");
      else buttonState(button, result?.error || "保存失败");
      return;
    }
    const href = normalizeUrl(target, { media: true });
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.download = "";
      link.click();
      buttonState(button, "已保存");
      return;
    }
    buttonState(button, "保存失败");
  } catch {
    buttonState(button, "保存失败");
  }
}

function createMediaActions(target, kind, compact = false) {
  const actions = create(compact ? "span" : "div", compact ? "md-media-actions compact" : "md-media-actions");
  if (kind === "image") {
    const copyMedia = create("button", "md-media-action");
    copyMedia.type = "button";
    copyMedia.dataset.defaultText = "复制图片";
    copyMedia.textContent = "复制图片";
    copyMedia.addEventListener("click", () => copyMediaTarget(target, kind, "media", copyMedia));
    actions.appendChild(copyMedia);
  }

  const copyPath = create("button", "md-media-action");
  copyPath.type = "button";
  copyPath.dataset.defaultText = kind === "video" ? "复制视频路径" : kind === "audio" ? "复制音频路径" : "复制路径";
  copyPath.textContent = copyPath.dataset.defaultText;
  copyPath.addEventListener("click", () => copyMediaTarget(target, kind, "path", copyPath));

  const open = create("button", "md-media-action");
  open.type = "button";
  open.dataset.defaultText = "打开";
  open.textContent = "打开";
  open.addEventListener("click", () => openMediaTarget(target, open));

  const save = create("button", "md-media-action");
  save.type = "button";
  save.dataset.defaultText = "保存";
  save.textContent = "保存";
  save.addEventListener("click", () => saveTarget(target, save));

  actions.appendChild(copyPath);
  actions.appendChild(open);
  actions.appendChild(save);
  return actions;
}

function trimTrailingUrlPunctuation(value) {
  return String(value || "").replace(/[.,;:!?]+$/, "");
}

function parseLinkAt(text, offset, image = false) {
  const start = image ? offset + 2 : offset + 1;
  const closeLabel = text.indexOf("]", start);
  if (closeLabel < 0 || text[closeLabel + 1] !== "(") return null;
  let depth = 1;
  let index = closeLabel + 2;
  while (index < text.length) {
    const char = text[index];
    if (char === "(") depth += 1;
    if (char === ")") {
      depth -= 1;
      if (depth === 0) {
        return {
          end: index + 1,
          label: text.slice(start, closeLabel),
          target: text.slice(closeLabel + 2, index).trim()
        };
      }
    }
    index += 1;
  }
  return null;
}

function appendText(parent, value) {
  if (value) parent.appendChild(document.createTextNode(value));
}

function appendInline(parent, text, context = {}) {
  const source = String(text || "");
  let index = 0;

  while (index < source.length) {
    const rest = source.slice(index);

    const urlMatch = rest.match(URL_RE);
    const nextUrlIndex = urlMatch ? index + urlMatch.index : -1;
    const nextSpecialIndex = ["![", "[", "`", "**", "__", "~~", "*", "_"]
      .map((token) => source.indexOf(token, index))
      .filter((pos) => pos >= 0)
      .sort((a, b) => a - b)[0] ?? -1;
    const nextIndex = [nextUrlIndex, nextSpecialIndex]
      .filter((pos) => pos >= 0)
      .sort((a, b) => a - b)[0];

    if (nextIndex > index) {
      appendText(parent, source.slice(index, nextIndex));
      index = nextIndex;
      continue;
    }

    if (source.startsWith("`", index)) {
      const end = source.indexOf("`", index + 1);
      if (end > index + 1) {
        const code = create("code", "md-inline-code");
        code.textContent = source.slice(index + 1, end);
        parent.appendChild(code);
        index = end + 1;
        continue;
      }
    }

    if (source.startsWith("![", index)) {
      const parsed = parseLinkAt(source, index, true);
      if (parsed) {
        const src = normalizeUrl(parsed.target, { media: true });
        if (src) {
          const wrap = create("span", "md-inline-media");
          const img = create("img", "md-inline-image");
          img.src = src;
          img.alt = parsed.label || "";
          img.loading = "lazy";
          wrap.appendChild(img);
          wrap.appendChild(createMediaActions(parsed.target, "image", true));
          parent.appendChild(wrap);
          context.renderedMedia?.add(mediaTargetKey(parsed.target));
        } else {
          appendText(parent, source.slice(index, parsed.end));
        }
        index = parsed.end;
        continue;
      }
    }

    if (source.startsWith("[", index)) {
      const parsed = parseLinkAt(source, index, false);
      if (parsed) {
        const href = normalizeUrl(parsed.target);
        if (href) {
          const link = create("a", "md-link");
          configureLink(link, parsed.target, parsed.label || parsed.target);
          parent.appendChild(link);
        } else {
          appendText(parent, source.slice(index, parsed.end));
        }
        index = parsed.end;
        continue;
      }
    }

    const paired = [
      ["**", "strong"],
      ["__", "strong"],
      ["~~", "s"],
      ["*", "em"],
      ["_", "em"]
    ].find(([token]) => source.startsWith(token, index));
    if (paired) {
      const [token, tag] = paired;
      const end = source.indexOf(token, index + token.length);
      if (end > index + token.length) {
        const node = document.createElement(tag);
        appendInline(node, source.slice(index + token.length, end), context);
        parent.appendChild(node);
        index = end + token.length;
        continue;
      }
    }

    if (nextUrlIndex === index && urlMatch) {
      const url = trimTrailingUrlPunctuation(urlMatch[0]);
      const link = create("a", "md-link");
      configureLink(link, url);
      parent.appendChild(link);
      index += urlMatch[0].length;
      continue;
    }

    appendText(parent, source[index]);
    index += 1;
  }
}

function parseTableCells(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableDelimiter(line) {
  const cells = parseTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function tableAlignments(line) {
  return parseTableCells(line).map((cell) => {
    if (/^:-+:$/.test(cell)) return "center";
    if (/^-+:$/.test(cell)) return "right";
    if (/^:-+$/.test(cell)) return "left";
    return "";
  });
}

function appendParagraph(parent, lines, context = {}) {
  const text = lines.join("\n").trim();
  if (!text) return;
  const paragraph = create("p", "md-paragraph");
  appendInline(paragraph, text, context);
  parent.appendChild(paragraph);
}

function appendCodeBlock(parent, code, language = "") {
  const block = create("div", "md-code");
  const header = create("div", "md-code-header");
  const label = create("span", "md-code-lang");
  label.textContent = language || "text";
  const button = create("button", "md-code-copy");
  button.type = "button";
  button.textContent = "复制";
  button.addEventListener("click", async () => {
    try {
      await writeClipboardText(code);
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    } catch {
      button.textContent = "复制失败";
      setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    }
  });
  header.appendChild(label);
  header.appendChild(button);
  const pre = create("pre", "md-pre");
  const codeNode = create("code", language ? `language-${language}` : "");
  codeNode.textContent = String(code || "");
  pre.appendChild(codeNode);
  block.appendChild(header);
  block.appendChild(pre);
  parent.appendChild(block);
}

function appendTable(parent, headerLine, delimiterLine, bodyLines, context = {}) {
  const wrap = create("div", "md-table-wrap");
  const table = create("table", "md-table");
  const headerCells = parseTableCells(headerLine);
  const alignments = tableAlignments(delimiterLine);
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headerCells.forEach((cell, index) => {
    const th = document.createElement("th");
    if (alignments[index]) th.style.textAlign = alignments[index];
    appendInline(th, cell, context);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const line of bodyLines) {
    const row = document.createElement("tr");
    parseTableCells(line).forEach((cell, index) => {
      const td = document.createElement("td");
      if (alignments[index]) td.style.textAlign = alignments[index];
      appendInline(td, cell, context);
      row.appendChild(td);
    });
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  parent.appendChild(wrap);
}

function appendList(parent, lines, ordered = false, context = {}) {
  const list = document.createElement(ordered ? "ol" : "ul");
  list.className = "md-list";
  for (const line of lines) {
    const match = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?([\s\S]*)$/);
    if (!match) continue;
    const item = document.createElement("li");
    item.className = match[1] ? "md-task-item" : "";
    if (match[1]) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.disabled = true;
      checkbox.checked = /x/i.test(match[1]);
      item.appendChild(checkbox);
    }
    appendInline(item, match[2], context);
    list.appendChild(item);
  }
  parent.appendChild(list);
}

function appendQuote(parent, lines, context = {}) {
  const quote = create("blockquote", "md-quote");
  renderMarkdownInto(quote, lines.map((line) => line.replace(/^>\s?/, "")).join("\n"), context);
  parent.appendChild(quote);
}

function appendMedia(parent, value, options = {}, context = {}) {
  const src = normalizeUrl(value, { media: true });
  const kind = mediaKind(value);
  if (!src || !kind) return false;
  context.renderedMedia?.add(mediaTargetKey(value));
  const figure = create("figure", "md-media");
  if (kind === "image") {
    const img = document.createElement("img");
    img.src = src;
    img.alt = value;
    img.loading = "lazy";
    figure.appendChild(img);
  } else if (kind === "video") {
    const video = document.createElement("video");
    video.src = src;
    video.controls = true;
    figure.appendChild(video);
  } else if (kind === "audio") {
    const audio = document.createElement("audio");
    audio.src = src;
    audio.controls = true;
    figure.appendChild(audio);
  }
  figure.appendChild(createMediaActions(value, kind));
  const caption = create("figcaption", "md-media-caption");
  caption.textContent = options.caption || value;
  figure.appendChild(caption);
  parent.appendChild(figure);
  return true;
}

export function renderMediaAttachment(container, value, options = {}) {
  return appendMedia(container, value, options);
}

function appendFileLink(parent, value) {
  const href = normalizeUrl(value);
  if (!href) return false;
  const wrap = create("div", "md-file-link");
  const link = create("a", "md-link");
  configureLink(link, value);
  wrap.appendChild(link);
  const actions = create("div", "md-media-actions compact");
  const save = create("button", "md-media-action");
  save.type = "button";
  save.dataset.defaultText = "保存";
  save.textContent = "保存";
  save.addEventListener("click", () => saveTarget(value, save));
  actions.appendChild(save);
  wrap.appendChild(actions);
  parent.appendChild(wrap);
  return true;
}

function lineLooksLikeMedia(line) {
  const text = String(line || "").trim();
  return Boolean(text && (isAbsoluteWindowsPath(text) || /^https?:\/\//i.test(text) || FILE_URL_RE.test(text)) && mediaKind(text));
}

function lineLooksLikeFileLink(line) {
  const text = String(line || "").trim();
  return Boolean(text && isAbsoluteWindowsPath(text) && !mediaKind(text));
}

function renderMarkdownInto(parent, sourceText, context = {}) {
  const lines = String(sourceText || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  let paragraph = [];

  const flushParagraph = () => {
    appendParagraph(parent, paragraph, context);
    paragraph = [];
  };

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      index += 1;
      continue;
    }

    const mediaDirective = trimmed.match(/^MEDIA\s*[:：]\s*(.+)$/i);
    if (mediaDirective) {
      flushParagraph();
      const target = cleanupMediaTarget(mediaDirective[1]);
      if (!appendMedia(parent, target, {}, context) && !appendFileLink(parent, target)) {
        appendParagraph(parent, [trimmed], context);
      }
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```([A-Za-z0-9_+.-]*)\s*$/);
    if (fence) {
      flushParagraph();
      const language = fence[1] || "";
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      appendCodeBlock(parent, body.join("\n"), language);
      continue;
    }

    if (/^( {4}|\t)/.test(line)) {
      flushParagraph();
      const body = [];
      while (index < lines.length && (/^( {4}|\t)/.test(lines[index]) || !lines[index].trim())) {
        body.push(lines[index].replace(/^( {4}|\t)/, ""));
        index += 1;
      }
      appendCodeBlock(parent, body.join("\n").replace(/\n+$/, ""), "");
      continue;
    }

    if (lineLooksLikeMedia(trimmed)) {
      flushParagraph();
      appendMedia(parent, trimmed, {}, context);
      index += 1;
      continue;
    }

    if (lineLooksLikeFileLink(trimmed)) {
      flushParagraph();
      appendFileLink(parent, trimmed);
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = Math.min(6, heading[1].length);
      const node = create(`h${level}`, `md-heading md-heading-${level}`);
      appendInline(node, heading[2].replace(/\s+#+\s*$/, ""), context);
      parent.appendChild(node);
      index += 1;
      continue;
    }

    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      parent.appendChild(create("hr", "md-hr"));
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      flushParagraph();
      const block = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        block.push(lines[index]);
        index += 1;
      }
      appendQuote(parent, block, context);
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && isTableDelimiter(lines[index + 1])) {
      flushParagraph();
      const headerLine = line;
      const delimiterLine = lines[index + 1];
      const body = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        body.push(lines[index]);
        index += 1;
      }
      appendTable(parent, headerLine, delimiterLine, body, context);
      continue;
    }

    const listMatch = line.match(/^\s*(?:([-*+])|(\d+[.)]))\s+/);
    if (listMatch) {
      flushParagraph();
      const ordered = Boolean(listMatch[2]);
      const block = [];
      while (index < lines.length) {
        const current = lines[index];
        const currentMatch = current.match(/^\s*(?:([-*+])|(\d+[.)]))\s+/);
        if (!currentMatch || Boolean(currentMatch[2]) !== ordered) break;
        block.push(current);
        index += 1;
      }
      appendList(parent, block, ordered, context);
      continue;
    }

    paragraph.push(line);
    index += 1;
  }

  flushParagraph();
}

export function renderMessageContent(container, text) {
  container.innerHTML = "";
  container.classList.add("rich-text");
  const context = { renderedMedia: new Set() };
  renderMarkdownInto(container, text, context);
  const extraMedia = collectGeneratedMediaTargets(text, context.renderedMedia);
  if (extraMedia.length) {
    const mediaWrap = create("div", "message-generated-media");
    for (const target of extraMedia) {
      appendMedia(mediaWrap, target, {}, context);
    }
    container.appendChild(mediaWrap);
  }
  if (!container.childNodes.length) {
    const empty = create("p", "md-paragraph");
    empty.textContent = String(text || "");
    container.appendChild(empty);
  }
}
