export const modeNames = {
  auto: "自动",
  chat: "聊天",
  work: "工作"
};

export const permissionNames = {
  readonly: "只读",
  workspace_write: "读写",
  workspace_full: "完全权限"
};

const MODEL_VISIBLE_OUTPUT_MAX_CONTENT = 15000;

const labelNames = {
  provider: "模型服务",
  model_service: "模型服务",
  model_provider: "服务商标识",
  model: "模型",
  model_name: "模型名",
  base_url: "接口地址",
  api_key: "接口密钥",
  thinking_enabled: "思考模式",
  thinking_depth: "思考深度",
  timeout: "超时时间",
  endpoint: "接口状态",
  endpoint_state: "接口状态",
  credential_state: "密钥状态",
  SignalKind: "SignalKind",
  HealthState: "HealthState",
  signal_kind: "SignalKind",
  health_state: "HealthState",
  max_steps: "最大步数",
  permission_mode: "权限",
  permissionMode: "权限",
  workspace: "工作区",
  workspace_writable: "工作区可写",
  kernel_importable: "内核可导入",
  pending_confirmations: "待确认事项",
  session_id: "会话编号",
  messages_count: "消息数量",
  context_memory_records: "上下文记忆数",
  project_index_status: "项目索引状态",
  quality_gate_decision: "质量门状态",
  delivery_status: "交付状态",
  experience_status: "经验沉淀状态",
  skill_queue_status: "技能队列状态",
  tool_request_status: "工具请求状态",
  exoskeleton_status: "执行骨架状态",
  shell_mount_status: "外壳挂载状态",
  project_repair_status: "项目修复状态",
  delivery_standard_status: "交付标准状态",
  provider_adaptation_status: "模型服务适配状态",
  learning_convergence_status: "学习合流状态",
  recovery_coordination_status: "恢复协调状态",
  governance_execution_status: "治理执行状态",
  planner_context_status: "规划上下文状态",
  planner_execution_status: "规划执行状态",
  execution_chain_freeze_status: "执行链冻结状态",
  l6_38_p0_status: "核心系统接入状态",
  exit: "退出码",
  time: "耗时",
  mode: "模式",
  code: "退出码",
  stdout: "标准输出",
  stderr: "错误输出",
  status: "状态",
  phase: "阶段",
  error: "错误",
  message: "消息",
  tool: "工具",
  summary: "摘要",
  elapsedMs: "耗时",
  elapsed_ms: "耗时",
  file_type: "文件类型",
  document_id: "文档编号",
  citation_id: "引用编号",
  citation_count: "引用片段",
  created_at: "创建时间",
  updated_at: "更新时间"
};

const valueNames = {
  missing: "未配置",
  unavailable: "不可用",
  not_implemented: "尚未实现",
  openai_compatible: "兼容接口",
  openai: "OpenAI",
  deepseek: "DeepSeek",
  qwen: "通义千问",
  dashscope: "通义千问",
  zhipu: "智谱",
  glm: "智谱",
  minimax: "MiniMax",
  mimo: "MiMo",
  moonshot: "月之暗面",
  anthropic: "Anthropic",
  gemini: "Gemini",
  mock: "模拟模式",
  direct_file: "文件直读写",
  removed: "已移除",
  resource: "资源",
  healthy: "健康",
  model_required: "必须使用模型",
  enabled: "开启",
  disabled: "禁用",
  dry_run: "演练",
  auto: "自动",
  standard: "标准",
  deep: "深入",
  max: "极深",
  xhigh: "xhigh",
  chat: "聊天",
  work: "工作",
  readonly: "只读",
  workspace_write: "读写",
  workspace_full: "完全权限",
  idle: "空闲",
  running: "运行中",
  finished: "已完成",
  done: "完成",
  completed: "完成",
  completed_pass: "完成",
  pending: "等待中",
  success: "成功",
  skipped: "已跳过",
  timeout: "超时",
  blocked: "已阻断",
  loading: "读取中",
  true: "是",
  false: "否",
  True: "是",
  False: "否",
  not_enabled: "未启用",
  not_configured: "未配置",
  configured: "已配置",
  empty: "暂无",
  ready: "就绪",
  active: "运行中",
  ok: "正常",
  failed: "失败",
  queue_ready: "队列就绪",
  needs_attention: "需要处理",
  host_only: "本机模式",
  rewritten: "已重写",
  "<未配置>": "未配置",
  "<workspace-unavailable>": "工作区不可用",
  "Electron bridge unavailable": "桌面桥接不可用",
  "run_agent.py not found": "找不到后端运行代理",
  "找不到后端运行代理": "找不到后端运行代理"
};

export const STATUS_PAYLOAD_START = "TIANGONG_STATUS_JSON_START";
export const STATUS_PAYLOAD_END = "TIANGONG_STATUS_JSON_END";
export const STREAM_EVENT_PREFIX = "__TIANGONG_STREAM_EVENT__ ";

export function stripStreamEvents(text) {
  return String(text || "")
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith(STREAM_EVENT_PREFIX))
    .join("\n")
    .trim();
}

export function concisePath(value) {
  const text = String(value || "");
  if (!text) return "未选择";
  if (text.length <= 42) return text;
  return `${text.slice(0, 18)}...${text.slice(-21)}`;
}

export function extractStatusPayload(text) {
  const raw = String(text || "");
  const start = raw.indexOf(STATUS_PAYLOAD_START);
  if (start < 0) return null;
  const bodyStart = start + STATUS_PAYLOAD_START.length;
  const end = raw.indexOf(STATUS_PAYLOAD_END, bodyStart);
  if (end < 0) return null;
  return safeJsonParse(raw.slice(bodyStart, end).trim());
}

export function stripStatusPayload(text) {
  const raw = String(text || "");
  const start = raw.indexOf(STATUS_PAYLOAD_START);
  if (start < 0) return raw;
  const bodyStart = start + STATUS_PAYLOAD_START.length;
  const end = raw.indexOf(STATUS_PAYLOAD_END, bodyStart);
  if (end < 0) return raw.slice(0, start).trim();
  return `${raw.slice(0, start)}${raw.slice(end + STATUS_PAYLOAD_END.length)}`.trim();
}

export function parseKeyValueLines(text) {
  const rows = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.replace(/^-\s*/, "").trim();
    if (!line) continue;
    const separatorIndexes = [line.indexOf(":"), line.indexOf("=")].filter((index) => index > 0);
    const index = separatorIndexes.length ? Math.min(...separatorIndexes) : -1;
    if (index > 0) {
      rows.push({ key: line.slice(0, index).trim(), value: line.slice(index + 1).trim() });
    } else {
      rows.push({ key: "", value: line });
    }
  }
  return rows;
}

export function keyValueMap(text) {
  const data = {};
  for (const row of parseKeyValueLines(text)) {
    if (row.key) data[row.key] = row.value;
  }
  return data;
}

export function translateLabel(label) {
  const text = String(label || "").trim();
  if (labelNames[text]) return labelNames[text];
  const normalized = text.replace(/[-_]+/g, " ").trim();
  if (/^[a-z][a-z0-9 .-]*$/i.test(normalized)) return "字段";
  return normalized || "字段";
}

export function translateValue(value) {
  if (Array.isArray(value)) {
    const values = value.slice(0, 6).map((item) => translateValue(item)).filter(Boolean);
    return values.length ? values.join("、") : "暂无";
  }
  if (value && typeof value === "object") {
    const rows = Object.entries(value).slice(0, 6).map(([key, item]) => `${translateLabel(key)}：${translateValue(item)}`);
    return rows.length ? rows.join("；") : "暂无";
  }
  const text = String(value ?? "").trim();
  if (!text) return "未配置";
  if (valueNames[text]) return valueNames[text];
  return text
    .replace(/\b(\d+(?:\.\d+)?)ms\b/g, "$1毫秒")
    .replace(/\b(\d+(?:\.\d+)?)s\b/g, "$1秒")
    .replace(/\b(\d+(?:\.\d+)?)\s*KB\b/g, "$1 千字节")
    .replace(/\b(\d+(?:\.\d+)?)\s*MB\b/g, "$1 兆字节")
    .replace(/\b(\d+(?:\.\d+)?)\s*B\b/g, "$1 字节")
    .replace(/\bstdout\b/g, "标准输出")
    .replace(/\bstderr\b/g, "错误输出")
    .replace(/\bprovider\b/g, "模型服务")
    .replace(/\bmodel\b/g, "模型")
    .replace(/\bworkspace\b/g, "工作区")
    .replace(/\bRuntimeHost\b/g, "运行宿主")
    .replace(/\bRuntime\b/g, "运行内核")
    .replace(/\bElectron\b/g, "桌面端")
    .replace(/\bPlanner\b/g, "规划器");
}

export function formatElapsedMs(value) {
  const ms = Number(value || 0);
  if (!Number.isFinite(ms) || ms <= 0) return "0 毫秒";
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  return `${(ms / 1000).toFixed(1)} 秒`;
}

export function localizedRowsFromText(text) {
  return parseKeyValueLines(text).map((row) => [
    row.key ? translateLabel(row.key) : "状态",
    translateValue(row.value)
  ]);
}

export function humanizeBackendText(text) {
  const rows = localizedRowsFromText(text);
  if (!rows.length) return "";
  return rows.map(([label, value]) => `${label}：${value}`).join("\n");
}

export function humanizeBackendError(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const structured = parseStructuredLlmError(raw);
  if (structured) return structured;
  const jsonBoundaryError = humanizeJsonBoundaryError(raw);
  if (jsonBoundaryError) return jsonBoundaryError;
  const modelError = humanizeModelConnectionError(raw);
  if (modelError) return modelError;
  const translated = humanizeBackendText(raw) || translateValue(raw);
  if (translated && !/[A-Za-z]/.test(translated)) return translated;
  const compact = raw.replace(/\s+/g, " ").slice(0, 800);
  return compact ? `后端执行失败：${compact}` : "后端执行失败。";
}

function humanizeJsonBoundaryError(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const lower = raw.toLowerCase();
  const looksLikeJsonBoundary = /expecting value:\s*line 1 column 1/i.test(raw)
    || lower.includes("jsondecodeerror")
    || lower.includes("backend_non_json_response")
    || lower.includes("provider_non_json_response")
    || lower.includes("non_json_response")
    || lower.includes("invalid_json")
    || lower.includes("empty_json");
  if (!looksLikeJsonBoundary) return "";
  if (lower.includes("request_body") || lower.includes("invalid_json_body")) {
    return "后端没有收到合法 JSON 请求体，请重试本轮操作；如果仍然出现，请检查前端请求是否被拦截或截断。";
  }
  return "后端收到空响应或非 JSON 响应，已中止本轮执行。请检查模型服务、工具服务或文件解析器是否返回了空内容、网页错误页或异常文本。";
}

function humanizeModelConnectionError(text) {
  const raw = String(text || "").replace(/^\[错误\]\s*/, "").trim();
  if (!raw) return "";
  const lower = raw.toLowerCase();
  const looksLikeModelError = raw.includes("Provider")
    || raw.includes("模型连接失败")
    || raw.includes("模型接口")
    || raw.includes("模型服务")
    || lower.includes("api key")
    || lower.includes("base url")
    || lower.includes("provider");
  if (!looksLikeModelError) return "";
  if (/请求超时|timed out|timeout/i.test(raw)) {
    return "模型连接失败：Provider 请求超时（HTTP 408/超时）。请检查网络或服务商状态，稍后重试或切换模型。";
  }
  if (/服务异常|server error|bad gateway|internal server error|502|5\d\d/i.test(raw)) {
    return "模型连接失败：服务商服务异常（HTTP 5xx）。请稍后重试或切换模型。";
  }
  if (/鉴权失败|invalid api key|unauthorized|401|403|api key/i.test(raw)) {
    return "模型连接失败：API Key 或权限校验失败（HTTP 401/403）。请检查 API Key 是否正确、账号是否有权限或余额。";
  }
  if (/模型或地址不存在|model_not_found|not found|404/i.test(raw)) {
    return "模型连接失败：模型或接口地址不存在（HTTP 404）。请检查模型名和 Base URL 是否拼写正确。";
  }
  if (/限流|额度不足|rate limit|429/i.test(raw)) {
    return "模型连接失败：服务商限流或额度不足（HTTP 429）。常见原因：账号额度已用完、每分钟/每日调用次数超限、共享 Key 被占满。请到服务商控制台查看用量，稍后重试或切换模型。";
  }
  if (raw.startsWith("模型连接失败")) return raw;
  return `模型连接失败：${raw}`;
}

function parseStructuredLlmError(raw) {
  const match = String(raw || "").match(/^\[LLM错误:\s*([\s\S]*?)\]$/);
  if (!match) return "";
  const fields = {};
  for (const part of match[1].split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (key) fields[key] = value;
  }
  const reason = fields.hint || fields.reason || "模型接口调用失败";
  const lines = [`模型连接失败：${reason}`];
  if (fields.provider) lines.push(`模型厂家：${fields.provider}`);
  if (fields.model) lines.push(`模型名：${fields.model}`);
  if (fields.base_url) lines.push(`Base URL：${fields.base_url}`);
  const status = Number(fields.http_status || 0);
  if (status === 400) {
    lines.push("处理建议：请检查模型名与 Base URL 是否匹配该服务商。");
  } else if (status === 401 || status === 403) {
    lines.push("处理建议：请检查 API Key 是否正确、账号是否有权限或余额。");
  } else if (status === 404) {
    lines.push("处理建议：请检查模型名和 Base URL 是否拼写正确。");
  } else if (status === 429) {
    lines.push("处理建议：常见原因是账号额度已用完、每分钟/每日调用次数超限、共享 Key 被占满；请到服务商控制台查看用量，稍后重试或切换模型。");
  } else if (status >= 500) {
    lines.push("处理建议：服务商服务异常，请稍后重试或切换模型。");
  } else if (status) {
    lines.push(`HTTP：${status}`);
  }
  if (fields.retry) lines.push(`重试：${fields.retry}`);
  if (fields.response) lines.push(`响应摘要：${fields.response}`);
  return lines.join("\n");
}

export function terminalStatusLabel(simpleChainStatus, phase) {
  const byStatus = {
    force_stopped: "已强制停止",
    interrupted: "已中断",
    incomplete: "未完成",
    failed: "执行失败",
  };
  const scs = String(simpleChainStatus || "").trim();
  if (byStatus[scs]) return byStatus[scs];
  const byPhase = {
    force_stopped: "已强制停止",
    failed: "执行失败",
    interrupted: "已中断",
    cancelled: "已中断",
    reconcile_required: "待对账",
    partial: "部分完成",
    incident: "状态矛盾",
    unknown: "状态未知",
  };
  return byPhase[String(phase || "")] || "";
}

export function terminalStatus(result) {
  const terminal = result && typeof result.terminal === "object" && result.terminal ? result.terminal : null;
  const status = String(terminal?.status || result?.simple_chain_status || "").trim();
  return {
    status,
    reason: String(terminal?.reason || result?.terminal_reason || ""),
    source: String(terminal?.source || ""),
    at: String(terminal?.at || ""),
    label: terminalStatusLabel(status, result?.phase),
  };
}

function compactDisplayText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function splitThinkBlocks(text) {
  let spoken = stripStreamEvents(text);
  const thoughts = [];

  spoken = spoken.replace(/```think(?:ing)?\s*([\s\S]*?)```/gi, (_match, body) => {
    const value = compactDisplayText(body);
    if (value) thoughts.push(value);
    return "";
  });

  spoken = spoken.replace(/<think(?:ing)?\b[^>]*>([\s\S]*?)<\/think(?:ing)?>/gi, (_match, body) => {
    const value = compactDisplayText(body);
    if (value) thoughts.push(value);
    return "";
  });

  const openThink = spoken.search(/<think(?:ing)?\b[^>]*>/i);
  if (openThink >= 0) {
    const before = spoken.slice(0, openThink);
    const after = spoken.slice(openThink).replace(/^<think(?:ing)?\b[^>]*>/i, "");
    const value = compactDisplayText(after);
    if (value) thoughts.push(value);
    spoken = before;
  }

  return {
    spoken: compactDisplayText(spoken),
    thoughts: thoughts.join("\n\n")
  };
}

export function spokenBackendText(text) {
  return splitThinkBlocks(text).spoken.slice(0, MODEL_VISIBLE_OUTPUT_MAX_CONTENT);
}

export function runtimeStatusText(result) {
  if (!result?.ok) return result?.stderr || "运行内核状态检查失败";

  const data = keyValueMap(stripStatusPayload(result.stdout || ""));
  return [
    result?.degraded ? "后端已连接，生命内核降级" : "运行内核可用",
    data.provider ? `模型服务: ${translateValue(data.provider)}` : "",
    data.model ? `模型: ${translateValue(data.model)}` : "",
    data.endpoint_state ? `接口状态: ${translateValue(data.endpoint_state)}` : "",
    data.signal_kind ? `SignalKind: ${translateValue(data.signal_kind)}` : ""
  ]
    .filter(Boolean)
    .join("\n");
}

export function backendReply(result) {
  const output = stripStreamEvents(result?.stdout || "");
  const error = String(result?.stderr || "").trim();
  let biaoxian = null;
  try {
    const raw = result?.data || result;
    const nested = raw?.data || raw;
    if (raw?.biaoxian && typeof raw.biaoxian === "object") biaoxian = raw.biaoxian;
    else if (nested?.biaoxian && typeof nested.biaoxian === "object") biaoxian = nested.biaoxian;
  } catch { /* best-effort */ }
  if (result?.ok) return { text: output || "已完成。", error: false, biaoxian };
  const translated = humanizeBackendError(error) || humanizeBackendText(output) || output;
  return { text: translated || "执行失败。", error: true, biaoxian };
}

export function parseBackendSteps(stdout) {
  const lines = stripStreamEvents(stdout).split(/\r?\n/);
  const steps = [];
  const taskMap = new Map(); // idx → { status, text }

  // 先收集 [TASK] 行
  for (const line of lines) {
    const taskMatch = line.match(/^\[TASK\]\s+(\d+)\/(\d+):(\w+):(.+)$/);
    if (taskMatch) {
      taskMap.set(parseInt(taskMatch[1]), {
        tool: `步骤 ${taskMatch[1]}`,
        status: taskMatch[3],
        summary: taskMatch[4].trim()
      });
    }
  }

  // [TASK] 有值时直接返回
  if (taskMap.size > 0) {
    const sorted = [...taskMap.entries()].sort((a, b) => a[0] - b[0]);
    for (const [, step] of sorted) {
      steps.push(step);
    }
    return steps;
  }

  // 回退：旧格式 - tool: status|summary
  let current = null;
  for (const line of lines) {
    const match = line.match(/^-\s+([a-zA-Z0-9_]+):\s+([^｜|]+)[｜|](.*)$/);
    if (match) {
      current = {
        tool: match[1],
        status: match[2].trim(),
        summary: match[3].trim()
      };
      steps.push(current);
      continue;
    }
    if (current && line.trim()) {
      current.summary += `\n${line}`;
    }
  }
  return steps;
}

export function safeJsonParse(text) {
  const raw = String(text || "").trim();
  try {
    return JSON.parse(raw);
  } catch {
    const start = raw.indexOf("{");
    const end = raw.lastIndexOf("}");
    if (start < 0 || end <= start) return null;
    try {
      return JSON.parse(raw.slice(start, end + 1));
    } catch {
      return null;
    }
  }
}

// 统一可见文本清洗
const _RE_BIAOXIAN_BLOCK = /<\s*biaoxi[an][ng]\s*>[\s\S]*?<\s*\/\s*biaoxi[an][ng]\s*>/gi;
const _RE_BIAOXIAN_TAG = /<\s*\/?\s*biaoxi[an][ng][^>]*>/gi;
const _RE_TOOL_CALL = /<tool_call\b[^>]*>[\s\S]*?<\/tool_call>/gi;
const _RE_FUNCTION_CALL = /<function_?calls?\b[^>]*>[\s\S]*?(?:<\/function_?calls?>|$)/gi;

export function sanitizeVisibleText(text) {
  if (!text || typeof text !== "string") return "";
  return text
    .replace(_RE_BIAOXIAN_BLOCK, "")
    .replace(_RE_BIAOXIAN_TAG, "")
    .replace(_RE_TOOL_CALL, "")
    .replace(_RE_FUNCTION_CALL, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
