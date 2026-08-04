const CATEGORY_PURPOSES = {
  code: ["代码工程", "理解、检查、修复、验证和交付代码项目。"],
  knowledge: ["知识检索", "检索知识库、资料和上下文，用于创作或任务参考。"],
  research: ["资料研究", "搜索、阅读、归纳公开资料并形成可引用结论。"],
  document: ["文档处理", "读取、解析、整理和生成文档内容。"],
  file: ["文件操作", "读取、写入、移动、整理和检查本地文件。"],
  data: ["数据表格", "处理表格、结构化数据、统计和分析任务。"],
  media: ["多媒体处理", "处理图片、音频、视频或其他媒体素材。"],
  office: ["办公协作", "处理办公文档、表格、会议和协作工具任务。"],
  web: ["网页与网络", "打开网页、搜索信息、下载或提取网页内容。"],
  quality: ["质量检查", "执行项目质检、验收、风险扫描和交付前检查。"],
  learning: ["自学习", "沉淀经验、生成学习卡并更新长期能力。"],
  other: ["通用能力", "按任务需要选择合适工具完成工作。"]
};

const KEYWORD_PURPOSES = [
  [/code[-_\s]*x|codex|代码|源码|repo|repository|bug|repair|fix|quality|test|pytest|scan_project/i, CATEGORY_PURPOSES.code],
  [/knowledge|memory|context|document_context|知识|记忆|上下文/i, CATEGORY_PURPOSES.knowledge],
  [/research|search|web|browser|download|readability|搜索|网页|下载/i, CATEGORY_PURPOSES.web],
  [/doc|document|pdf|word|markdown|office|文档|材料/i, CATEGORY_PURPOSES.document],
  [/file|folder|workspace|directory|path|文件|目录/i, CATEGORY_PURPOSES.file],
  [/sheet|excel|csv|table|data|xlsx|数据|表格/i, CATEGORY_PURPOSES.data],
  [/image|video|audio|media|图片|视频|音频|媒体/i, CATEGORY_PURPOSES.media],
  [/lark|feishu|wechat|mail|calendar|meeting|飞书|微信|邮件|会议/i, CATEGORY_PURPOSES.office],
  [/learn|experience|skill|card|学习|经验|技能/i, CATEGORY_PURPOSES.learning]
];

const LEGACY_TOKEN = "v" + "2";
const LEGACY_TOOL_PATTERN = new RegExp(`\\b${LEGACY_TOKEN}\\b\\s*工具[:：]?\\s*`, "gi");
const LEGACY_TOKEN_PATTERN = new RegExp(`\\b${LEGACY_TOKEN}\\b`, "gi");

export function cleanSkillDisplayText(value) {
  return String(value ?? "")
    .replace(LEGACY_TOOL_PATTERN, "工具：")
    .replace(LEGACY_TOKEN_PATTERN, "")
    .replace(/\s+([:：])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function hasChinese(text) {
  return /[\u3400-\u9fff]/.test(String(text || ""));
}

function wordsFromIdentifier(value) {
  return cleanSkillDisplayText(value)
    .replace(/^backend_(virtual_)?skill_/, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_./:-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function categoryPurpose(ability = {}) {
  const category = String(ability.category || "other").toLowerCase();
  if (CATEGORY_PURPOSES[category]) return CATEGORY_PURPOSES[category];
  const haystack = [
    ability.id,
    ability.name,
    ability.description,
    ability.category,
    ...(Array.isArray(ability.toolNames) ? ability.toolNames : []),
    ...(Array.isArray(ability.taskIntents) ? ability.taskIntents : []),
    ...(Array.isArray(ability.tags) ? ability.tags : [])
  ].map(cleanSkillDisplayText).join(" ");
  for (const [pattern, purpose] of KEYWORD_PURPOSES) {
    if (pattern.test(haystack)) return purpose;
  }
  return CATEGORY_PURPOSES.other;
}

export function skillDisplayName(ability = {}) {
  const name = cleanSkillDisplayText(ability.displayName || ability.name || ability.title || ability.id || "");
  if (hasChinese(name)) return name;
  const [label] = categoryPurpose(ability);
  const raw = wordsFromIdentifier(name || ability.id);
  return raw ? `${label}：${raw}` : label;
}

export function skillDisplayDescription(ability = {}) {
  const explicit = cleanSkillDisplayText(ability.displayDescription || "");
  if (explicit) return explicit;
  const description = cleanSkillDisplayText(ability.description || "");
  if (hasChinese(description)) return description;
  const [, purpose] = categoryPurpose(ability);
  const raw = wordsFromIdentifier(description || ability.name || ability.id);
  return raw ? `${purpose} 原始说明：${raw}` : purpose;
}

export function withSkillDisplay(ability = {}) {
  return {
    ...ability,
    displayName: skillDisplayName(ability),
    displayDescription: skillDisplayDescription(ability)
  };
}
