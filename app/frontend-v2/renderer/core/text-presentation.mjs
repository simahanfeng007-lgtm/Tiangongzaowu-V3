// Transport/model markup is meaningful only in prose. Code and file examples
// are data, including tag names and whitespace that resemble internal markup.
export function mapProse(text, transform) {
  let output = "";
  let prose = "";
  let fence = null;
  const flush = () => {
    let cursor = 0;
    for (const match of prose.matchAll(/(?<!`)(`+)(?!`)[^\n]*?\1(?!`)/g)) {
      output += transform(prose.slice(cursor, match.index)) + match[0];
      cursor = match.index + match[0].length;
    }
    output += transform(prose.slice(cursor));
    prose = "";
  };
  for (const line of String(text ?? "").match(/[^\n]*(?:\n|$)/g) || []) {
    if (fence) {
      if (fence.data) output += line;
      else prose += line;
      const close = line.match(/^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$/);
      if (close && close[1][0] === fence.char && close[1].length >= fence.length) fence = null;
      continue;
    }
    const open = line.match(/^ {0,3}(`{3,}|~{3,})([^\n]*)(?:\n|$)/);
    if (open) {
      const data = !/^think(?:ing)?\s*$/i.test(open[2]);
      fence = { char: open[1][0], length: open[1].length, data };
      if (data) { flush(); output += line; }
      else prose += line;
    } else prose += line;
  }
  flush();
  return output;
}

export function cleanChatDisplayText(text) {
  return mapProse(text, (part) => part
    .replace(/<\s*system-reminder\b[^>]*>[\s\S]*?<\s*\/\s*system-reminder\s*>/gi, "")
    .replace(/<\s*\/?\s*system-reminder\b[^>]*>/gi, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")).trim();
}

export const MESSAGE_MAX_CONTENT = 1024 * 1024;
export function boundedMessageContent(value) {
  const text = String(value ?? "");
  if (text.length <= MESSAGE_MAX_CONTENT) return text;
  const notice = "\n\n[消息较长，当前显示已截断；完整内容请查看本次运行记录或产物文件。]";
  return text.slice(0, MESSAGE_MAX_CONTENT - notice.length) + notice;
}
