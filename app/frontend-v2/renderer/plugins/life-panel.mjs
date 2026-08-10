import { lifeApi } from "../runtime/life-api.mjs";
import { buildLifeViewModel } from "../runtime/life-view-model.mjs";

const REFRESH_INTERVAL_MS = 30000;
let lifePanelTimer = null;

const LIFE_TABS = [
  { id: "overview", label: "总览" },
  { id: "identity", label: "身份" },
  { id: "organism", label: "生命状态" },
  { id: "memory", label: "记忆" },
  { id: "context", label: "上下文" },
  { id: "schedule", label: "日程" },
  { id: "will", label: "自主意志" },
  { id: "reflection", label: "反思" },
  { id: "capabilities", label: "生命自产能力" },
  { id: "iteration", label: "迭代" },
  { id: "boundaries", label: "边界" },
  { id: "settings", label: "设置" }
];

const ICONS = {
  sprout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21v-8"/><path d="M12 13c-4.2 0-7-2.9-7-7 4.2 0 7 2.9 7 7Z"/><path d="M12 13c4.2 0 7-2.9 7-7-4.2 0-7 2.9-7 7Z"/></svg>`,
  heart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.4 6.8a5 5 0 0 0-7.1 0L12 8.1l-1.3-1.3a5 5 0 1 0-7.1 7.1L12 22l8.4-8.1a5 5 0 0 0 0-7.1Z"/><path d="M7.4 12h2.4l1.4-2.6 2 5.2 1.4-2.6h2"/></svg>`,
  compass: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15.6 8.4-2.1 5.1-5.1 2.1 2.1-5.1 5.1-2.1Z"/><path d="M12 3v2"/><path d="M12 19v2"/><path d="M3 12h2"/><path d="M19 12h2"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 20 6v6c0 4.8-3.1 7.7-8 9-4.9-1.3-8-4.2-8-9V6l8-3Z"/><path d="M8.5 12.2 11 14.7l4.7-5"/></svg>`,
  scroll: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10a3 3 0 0 1 3 3v13H8a4 4 0 0 1-4-4V7a3 3 0 0 1 3-3Z"/><path d="M8 20a3 3 0 0 0 0-6H4"/><path d="M10 8h6"/><path d="M10 12h5"/></svg>`
};

const STATUS_TEXT = {
  done: "已完成",
  finished: "已完成",
  completed: "已完成",
  success: "已完成",
  ok: "已完成",
  synced: "已同步",
  ready: "就绪",
  active: "已生效",
  enabled: "已开启",
  accepted: "已确认",
  approved: "已通过",
  failed: "失败",
  error: "失败",
  blocked: "已阻止",
  rejected: "已取消",
  cancelled: "已取消",
  canceled: "已取消",
  discarded: "已放弃",
  skipped: "已跳过",
  pending: "等待中",
  pending_card: "待确认学习",
  processing_approved: "已确认待加工",
  pending_learning: "等待学习",
  pending_approval: "等待确认",
  planned: "计划中",
  waiting: "等待中",
  idle: "待机",
  suspended: "已暂停",
  running: "进行中",
  in_progress: "进行中",
  proposed: "建议",
  suggestion: "建议",
  candidate: "候选",
  awaiting_user: "等待你确认",
  confirmed: "已确认待执行",
  executing: "执行中",
  model_review: "模型复审",
  draft: "草稿",
  draft_ready: "草案待激活",
  sandbox_passed: "沙盒通过",
  building: "沙盘构建中",
  tested: "沙盘测试通过",
  released: "已发布",
  rolled_back: "已回滚",
  degraded: "已降级",
  review_ready: "等待复审",
  quarantined: "已隔离",
  duplicate_removed: "重复移除",
  no_value: "价值不足",
  disabled: "已停用",
  unknown: "未知"
};

const TERM_TEXT = {
  soul: "灵魂",
  scheduled: "已计划",
  standard: "标准",
  embedded: "内置",
  embedded_life_runtime: "内置生命运行时",
  life_state: "生命状态",
  "life state": "生命状态",
  encrypted_sqlite_payload: "加密数据库载荷",
  "encrypted sqlite payload": "加密数据库载荷",
  gateway_atomic_context_authority: "网关原子上下文权威",
  "gateway atomic context authority": "网关原子上下文权威",
  inspect_life_context: "检查生命上下文",
  "inspect life context": "检查生命上下文",
  a0_a4_auto_a5_signed_lease: "A0-A4 自动，A5 签名租约",
  not_implemented: "尚未实现",
  "not implemented": "尚未实现",
  canonical_life_kernel: "权威生命内核",
  idle: "待机",
  scheduled_autonomy: "计划式自主",
  api_not_configured: "未连接模型接口",
  waiting_first_tick: "等待首次调度",
  auto: "自动",
  autonomous: "自主",
  manual: "手动",
  card: "卡片",
  card_only: "只生成卡片",
  suggest_only: "只给建议",
  suggestion: "建议",
  candidate: "候选",
  schedule: "日程",
  task: "任务",
  dream: "做梦",
  dreaming: "做梦",
  dream_summary: "梦境总结",
  reflect: "反思",
  reflection: "反思",
  learn: "学习",
  learning: "学习",
  pending_learning: "等待学习",
  share: "分享",
  life_share: "行动心得分享",
  self_clean: "自洁",
  self_clean_runtime_garbage: "自洁：清理临时垃圾",
  clean: "整理",
  cleanup: "整理",
  report: "报告",
  plan: "规划",
  daily_plan: "每日日程",
  daily_plan_generation: "生成每日日程",
  morning_daily_plan_review: "早晨整理今日闲时日程",
  review: "复审",
  tool_gap: "工具缺口",
  skill_candidate: "技能候选",
  identify_root_cause: "定位根因",
  complete_causal_link: "补全因果链",
  establish_memory_baseline: "建立记忆基线",
  verify_memory_hypothesis: "验证记忆假设",
  resolve_memory_contradiction: "解决记忆矛盾",
  review_learning_candidate: "复核学习候选",
  review_capability_candidate: "复核能力候选",
  daily_planning: "今日规划",
  self_reflection: "行动反思",
  goal_progress: "目标推进",
  relationship_care: "关系关怀",
  knowledge_organization: "知识整理",
  learning_review: "学习复盘",
  capability_inventory: "能力盘点",
  system_health: "生命自检",
  workspace_hygiene: "工作区整理建议",
  creative_exploration: "好奇探索",
  end_of_day_summary: "今日小结",
  "life_activity.daily_planning": "今日规划",
  "life_activity.self_reflection": "行动反思",
  "life_activity.goal_progress": "目标推进",
  "life_activity.relationship_care": "关系关怀",
  "life_activity.knowledge_organization": "知识整理",
  "life_activity.learning_review": "学习复盘",
  "life_activity.capability_inventory": "能力盘点",
  "life_activity.system_health": "生命自检",
  "life_activity.workspace_hygiene": "工作区整理建议",
  "life_activity.creative_exploration": "好奇探索",
  "life_activity.end_of_day_summary": "今日小结",
  life_activity_catalog: "生命自主活动目录",
  model_internal: "内部思考执行",
  inspect_prior_events: "检查既有事件",
  inspect_related_memories: "检查相关记忆",
  "Identify evidence-backed causes for an observed outcome.": "定位已观察结果的证据化原因",
  "Find or verify the effect associated with a recorded cause.": "查找并验证已记录原因对应的结果",
  "Establish a minimal verified memory baseline for the active life identity.": "为当前生命建立最小可验证记忆基线",
  learning_route: "学习路线",
  context_memory: "上下文记忆",
  knowledge_memory_tidy: "知识与记忆梳理",
  domestic_web_search: "国内联网搜索",
  desktop_avatar_3d: "桌面身体体验",
  life_autonomous_learning: "生命自主学习",
  free_will: "自由意志",
  self_healing: "自我愈合",
  self_healing_check: "自我愈合检查",
  self_learning: "自主学习",
  self_learning_skill_review: "自主学习：记忆与技能核对",
  self_iteration: "自我迭代",
  self_iteration_direction: "自我迭代方向",
  self_code_review_upgrade_card: "自我迭代：代码审查与升级卡",
  connection: "关系维护",
  idle_connection_ping: "闲时关系维护",
  curiosity: "好奇心",
  growth: "成长",
  safety: "安全",
  security: "安全",
  order: "秩序",
  achievement: "成就",
  rest: "休息",
  mastery: "掌握感",
  purpose: "目标感",
  creation: "创造",
  survival: "生存",
  belonging: "归属感",
  thoughtfulness: "思考",
  usefulness: "有用性",
  user_value: "用户价值",
  novelty: "新鲜度",
  confidence: "信心",
  risk: "风险",
  alignment: "对齐",
  memory: "记忆",
  boundary: "边界",
  privacy: "隐私",
  file_system: "文件系统",
  normal: "普通",
  low: "低",
  medium: "中",
  high: "高",
  enabled: "开启",
  disabled: "关闭",
  true: "是",
  false: "否",
  none: "无",
  null: "无",
  unknown: "未知",
  schedule_required: "日程必做",
  required_daily_schedule: "日程必做",
  recent_failures: "近期失败",
  user_active_low_risk_healing: "用户活跃时低风险自愈",
  user_not_idle_enough: "用户未足够空闲",
  user_run_active: "用户任务运行中",
  failed_recently: "近期失败过",
  night_window: "夜间窗口",
  risk_A5_blocked: "A5 风险已阻止",
  idle_life_schedule: "闲时生命日程",
  model_review: "模型复审",
  sandbox_passed: "沙盒通过",
  review_ready: "等待复审",
  draft: "草稿",
  learned: "已学习",
  working: "工作记忆",
  episodic: "情节记忆",
  semantic: "语义记忆",
  relational: "关系记忆",
  procedural: "程序记忆",
  prospective: "前瞻记忆",
  reflective: "反思记忆",
  observed: "直接观察",
  user_asserted: "用户陈述",
  execution_verified: "执行验证",
  summarized: "摘要",
  inferred: "模型推断",
  calm: "平静",
  joy: "喜悦",
  interest: "兴趣",
  hope: "期待",
  warmth: "温暖",
  concern: "担忧",
  sadness: "低落",
  fear: "担忧",
  anger: "受阻",
  frustration: "生气与受挫",
  disappointment: "失望",
  vigilance: "警觉",
  fatigue: "疲惫",
  surprise: "惊讶",
  worry: "挂念",
  caution: "谨慎",
  urgency: "紧迫",
  persistence: "坚持",
  stability: "稳定",
  exploration: "探索",
  communication: "交流",
  protection: "保护",
  repair: "修复",
  relationship: "关系",
  trust: "信任",
  familiarity: "熟悉度",
  tension: "紧张度",
  gratitude: "感激",
  transient_affect: "临时情绪",
  innate_temperament: "天生人格基线",
  bounded_affect_expression: "有界情绪表达",
  canonical_life_affect_projection: "权威生命情绪投影",
  identity_soul_safety_goal_are_mandatory: "身份、灵魂配置、安全边界和当前目标强制保留",
  current_explicit_request_has_priority: "用户当前明确要求优先",
  tool_call_result_pairs_are_atomic: "工具调用与结果成对保留",
  only_active_skills_are_model_visible: "仅已激活技能对模型可见",
  only_released_tools_are_model_visible: "仅已发布工具对模型可见",
  affect_modulates_attention_not_facts: "情感只调节注意和表达，不改写事实",
  current_relationship_selected: "已选择当前主要关系状态",
  memory_ranked_by_relevance_evidence_confidence_recency_and_bounded_affect: "记忆按相关性、证据、置信、时效和有界情感排序",
  recent_non_tool_conversation_selected_newest_first: "近期非工具对话按时间倒序纳入",
  memory_budget: "记忆因令牌预算未纳入",
  conversation_budget: "部分对话因令牌预算未纳入",
  duplicate_memory: "重复记忆已去重",
  orphan_tool_result: "孤立工具结果已排除",
  active_skill: "技能因预算未纳入",
  released_tool: "工具因预算未纳入",
  derived: "派生索引",
  canonical_life_affect_and_memory_projection: "当前生命情感与记忆投影"
};

const RISK_TEXT = {
  A0: "A0 · 观察记录",
  A1: "A1 · 本地只读/记忆整理",
  A2: "A2 · 知识草稿/流程沉淀",
  A3: "A3 · 文件写入/配置建议",
  A4: "A4 · 工具注册/代码或安装动作",
  A5: "A5 · 系统级不可逆动作"
};

const KEY_TEXT = {
  extraversion: "外向性",
  agreeableness: "宜人性",
  conscientiousness: "尽责性",
  openness: "开放性",
  neuroticism: "情绪敏感性",
  emotional_stability: "情绪稳定性",
  "emotional stability": "情绪稳定性",
  arousal_set_point: "唤醒基准",
  "arousal set point": "唤醒基准",
  dominance_set_point: "掌控基准",
  "dominance set point": "掌控基准",
  emotional_reactivity: "情绪反应强度",
  "emotional reactivity": "情绪反应强度",
  recovery_tendency: "情绪恢复倾向",
  "recovery tendency": "情绪恢复倾向",
  valence_set_point: "愉悦度基准",
  "valence set point": "愉悦度基准",
  body_preset: "身体预设",
  "body preset": "身体预设",
  energy_milli: "能量水平",
  "energy milli": "能量水平",
  load_milli: "负载水平",
  "load milli": "负载水平",
  constraints: "约束条件",
  goals: "目标",
  outcomes: "结果",
  heartbeat_enabled: "是否启用心跳",
  "heartbeat enabled": "是否启用心跳",
  confirm_high_risk: "高风险需要确认",
  "confirm high risk": "高风险需要确认",
  risk_max: "风险上限",
  "risk max": "风险上限",
  task_generation_enabled: "是否生成任务",
  "task generation enabled": "是否生成任务",
  permission_mode: "权限模式",
  model_may_grant_permission: "模型可授予权限",
  a5_hard_gate: "A5 强制门禁",
  available: "是否可用",
  protected_memory_layers: "受保护记忆层",
  memory_content_exposed: "记忆内容是否暴露",
  model_summary_authoritative: "模型摘要是否权威",
  workspace: "工作区",
  workspace_enforced: "是否强制工作区边界",
  empty_target_fail_closed: "空目标是否关闭式拒绝",
  share_enabled: "允许生命链分享",
  share_probability: "分享概率",
  share_hourly_limit: "每小时分享上限",
  share_daily_limit: "每日分享上限",
  share_dnd: "分享免打扰",
  "share_dnd.start": "免打扰开始",
  "share_dnd.end": "免打扰结束",
  primary_emotion: "主情绪",
  tone: "表达语气",
  intensity: "情绪强度",
  allostatic_load: "稳态负荷",
  regulation: "调节能力",
  revision: "灵魂修订",
  revision_id: "灵魂修订签名",
  relational_memory_count: "关系记忆数",
  index_status: "记忆索引",
  token_budget: "令牌预算",
  estimated_tokens: "已使用令牌估算",
  selected_context_tokens: "已选生命上下文",
  current_context_tokens: "当前对话上下文",
  context_utilization_milli: "上下文压力千分比",
  mandatory_blocks: "强制上下文块",
  memory_cards: "召回记忆",
  conversation_messages: "近期对话",
  active_skills: "已激活技能",
  released_tools: "已发布工具",
  affect: "情感状态",
  context_hash: "上下文哈希",
  cycle_id: "生命周期标识",
  llm_daily_budget: "每日模型成功预算",
  llm_daily_attempt_budget: "每日模型尝试预算",
  self_clean_delete: "允许自洁删除文件",
  heavy_interval_minutes: "重心跳间隔（分钟）",
  autonomous_judgment_enabled: "启用自主判断",
  daily_plan_enabled: "启用每日计划",
  daily_plan_hour: "每日计划时间（小时）",
  dream_enabled: "启用夜间梦境整理",
  dream_hour: "梦境整理时间（小时）",
  dream_catchup_until_hour: "梦境补偿截止（小时）",
  max_jobs_per_tick: "每次心跳最多任务",
  user_idle_seconds: "用户闲置秒数",
  llm_timeout_seconds: "模型超时秒数",
  readonly: "只读",
  source: "来源",
  override_path: "用户覆盖文件",
  user_overrides: "用户覆盖项",
  autonomy: "自主等级",
  share: "分享策略",
  privacy: "隐私",
  file_system: "文件系统",
  learned_rules: "学习到的边界规则",
  enabled: "是否开启",
  level: "等级",
  mode: "模式",
  max_level: "最高等级",
  require_confirm: "需要确认",
  auto_share: "自动分享",
  allow_share: "允许分享",
  redact_llm: "模型调用脱敏",
  redact_share: "分享内容脱敏",
  allow_read: "允许读取",
  allow_write: "允许写入",
  allow_delete: "允许删除",
  allowed_roots: "允许根目录",
  blocked_roots: "禁止根目录",
  allowlist: "允许清单",
  denylist: "禁止清单",
  user_confirm_required: "需要用户确认",
  reason: "原因",
  status: "状态",
  kind: "类型",
  title: "标题",
  summary: "摘要",
  description: "说明",
  created_at: "创建时间",
  updated_at: "更新时间",
  started_at: "开始时间",
  finished_at: "完成时间",
  risk_level: "风险等级",
  promotion_stage: "推进阶段",
  next_action: "下一步",
  score: "分数",
  value_score: "价值分",
  total_score: "总价值分",
  "auto allowed risks": "允许自动执行的风险等级",
  auto_allowed_risks: "允许自动执行的风险等级",
  "card only risks": "只生成卡片的风险等级",
  card_only_risks: "只生成卡片的风险等级",
  "never auto risks": "永不自动执行的风险等级",
  never_auto_risks: "永不自动执行的风险等级",
  daily_limit: "每日上限",
  "daily limit": "每日上限",
  hourly_limit: "每小时上限",
  "hourly limit": "每小时上限",
  dnd_start: "免打扰开始",
  "dnd start": "免打扰开始",
  dnd_end: "免打扰结束",
  "dnd end": "免打扰结束",
  min_interval_seconds: "最小间隔秒数",
  "min interval seconds": "最小间隔秒数",
  quiet_if_user_active: "用户活跃时保持安静",
  "quiet if user active": "用户活跃时保持安静",
  suppress_until: "暂停到",
  "suppress until": "暂停到",
  learned_level: "学习等级",
  "learned level": "学习等级",
  external_search_minimal_redaction: "外部搜索最小脱敏",
  "external search minimal redaction": "外部搜索最小脱敏",
  do_not_touch_user_data: "不触碰用户数据",
  "do not touch user data": "不触碰用户数据",
  rollback_whitelist_required: "回滚需要白名单",
  "rollback whitelist required": "回滚需要白名单",
  external_effects_require_gateway_grant: "外部影响需要网关授权",
  recursive_delete_requires_explicit_user: "递归删除需要用户明确确认",
  outside_installation_uses_dynamic_workspace: "安装目录外使用动态工作区",
  minimum_evidence_count: "最少重复证据",
  minimum_confidence: "最低置信度",
  may_override_explicit_user_rule: "能否覆盖用户明确规则",
  budget_exempt: "不计入模型预算",
  schedule_required: "日程必做",
  llm_adjusted: "模型调整过",
  task_count: "任务数量",
  plan_date: "计划日期",
  window: "计划时间",
  risk: "风险"
};

const SETTING_FIELDS = [
  {
    key: "permission_mode",
    label: "权限模式",
    type: "select",
    options: [
      ["autonomous_low_risk", "低风险自动执行，高风险需确认"],
      ["confirm_high_risk", "高风险操作需确认"],
      ["confirm_all", "所有自主操作均需确认"]
    ],
    help: "控制自主行动何时必须请求用户确认。"
  },
  {
    key: "autonomous_risk_max",
    label: "自主风险上限",
    type: "select",
    options: [
      ["A0", "A0 · 只读观察"],
      ["A1", "A1 · 低风险内部整理"],
      ["A2", "A2 · 可回退的受限操作"],
      ["A3", "A3 · 必须确认的重要操作"],
      ["A4", "A4 · 必须确认的高风险操作"]
    ],
    help: "超过上限的行动不会进入自主执行链。"
  },
  { key: "autonomy_enabled", label: "启用自主意志", type: "checkbox", help: "控制当前生命是否参与自主调度。" },
  { key: "autonomy_task_generation_enabled", label: "生成自主任务", type: "checkbox", help: "允许单一心跳根据生命状态和已选活动生成候选任务。" },
  { key: "autonomy_activity_types", label: "允许的自主活动", type: "multi-check", help: "可多选。这里只决定生命体可以做哪些低风险内部活动；文件、消息和工具仍受统一权限约束。" },
  { key: "heartbeat_enabled", label: "启用生命心跳", type: "checkbox", help: "控制当前生命的单一权威心跳。" },
  { key: "llm_daily_budget", label: "每日模型成功预算", type: "number", min: 0, max: 1000, step: 1, help: "自主生命活动每天最多成功调用模型的次数；0 表示不设成功次数上限。" },
  { key: "llm_daily_attempt_budget", label: "每日模型尝试预算", type: "number", min: 0, max: 2000, step: 1, help: "包括成功、失败和超时；0 表示不设尝试次数上限。" },
  { key: "share_enabled", label: "允许生命主动分享", type: "checkbox", help: "仅影响进入生命信箱的主动分享，不绕过消息权限。" },
  { key: "share_quiet_if_user_active", label: "用户活跃时保持安静", type: "checkbox", help: "用户正在操作时不主动打断。" },
  { key: "share_min_interval_seconds", label: "主动分享最小间隔（秒）", type: "number", min: 60, max: 604800, step: 60, help: "限制主动分享频率。" },
  { key: "share_hourly_limit", label: "每小时分享上限", type: "number", min: 0, max: 60, step: 1, help: "0 表示该小时不主动分享。" },
  { key: "share_daily_limit", label: "每日分享上限", type: "number", min: 0, max: 1000, step: 1, help: "0 表示当天不主动分享。" },
  { key: "share_dnd_start", label: "免打扰开始", type: "time", help: "进入免打扰时段后不主动推送。" },
  { key: "share_dnd_end", label: "免打扰结束", type: "time", help: "离开免打扰时段后恢复正常策略。" },
  { key: "privacy.redact_llm", label: "模型调用脱敏", type: "checkbox", help: "发送给模型前隐藏受保护内容。" },
  { key: "privacy.redact_share", label: "分享内容脱敏", type: "checkbox", help: "生成对外分享内容前隐藏受保护内容。" }
];

const SETTING_GROUPS = [
  {
    id: "autonomy",
    title: "权限与自主行动",
    description: "决定生命可以自主思考什么，以及执行到哪一级必须询问用户。",
    fields: ["permission_mode", "autonomous_risk_max", "autonomy_enabled", "autonomy_task_generation_enabled", "autonomy_activity_types"]
  },
  {
    id: "runtime",
    title: "心跳与模型预算",
    description: "所有数值均来自当前生命的权威后端设置，保存后写回同一生命作用域。",
    fields: ["heartbeat_enabled", "llm_daily_budget", "llm_daily_attempt_budget"]
  },
  {
    id: "sharing",
    title: "主动分享",
    description: "控制生命信箱总结的发送条件、频率与免打扰时段。",
    fields: ["share_enabled", "share_quiet_if_user_active", "share_min_interval_seconds", "share_hourly_limit", "share_daily_limit", "share_dnd_start", "share_dnd_end"]
  },
  {
    id: "privacy",
    title: "隐私保护",
    description: "脱敏只改变进入模型或分享内容的副本，不修改生命原始数据。",
    fields: ["privacy.redact_llm", "privacy.redact_share"]
  }
];

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function safeObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

const AVAILABILITY_REASON_NAMES = {
  PANEL_UNAVAILABLE: "生命面板当前不可用，所有子系统均保持关闭式只读。",
  SCHEDULER_UNAVAILABLE: "当前后端未挂载权威日程调度系统。",
  FREE_WILL_SCHEDULER_UNAVAILABLE: "当前后端未挂载权威自由意志调度器。",
  REFLECTION_PARTIAL: "当前仅展示真实学习提案边界；反思与行动价值系统尚未挂载。",
  ITERATION_QUEUE_UNAVAILABLE: "当前没有可由前端确认的权威升级卡队列。",
  LIFE_SETTINGS_WRITE_UNAVAILABLE: "生命链设置尚无独立后端写入契约，本页保持只读。",
  LIFE_INBOX_UNAVAILABLE: "当前后端未挂载权威生命信箱。",
  MODEL_BUDGET_PROJECTION_UNAVAILABLE: "当前后端没有权威模型预算投影。",
  model_budget_projection_unavailable: "当前运行时尚未提供权威模型预算，因此不会用零值冒充预算。",
  context_not_compiled: "当前生命尚未生成统一上下文；完成一次进入生命执行链的请求后才会产生。",
  autonomous_judgment_projection_unavailable: "当前只提供心跳与任务队列，尚未生成可校验的自主判断记录。",
  reflection_projection_unavailable: "当前反思与行动价值记录尚未接入权威面板，本页不会用学习卡冒充反思。",
  share_and_file_policy_unavailable: "分享与文件系统边界尚未挂载；已显示当前可验证的自主、隐私与灵魂声明边界。",
  "authoritative scheduler unavailable": "当前后端未挂载权威日程调度系统。",
  "authoritative life inbox unavailable": "当前后端未挂载权威生命信箱。",
  "no authoritative daily model budget projection": "当前后端没有权威模型预算投影。",
  "no authoritative free-will scheduler is mounted in this backend build": "当前后端未挂载权威自由意志调度器。"
};

function availabilityReason(code, reason, fallback) {
  return AVAILABILITY_REASON_NAMES[String(code || "")]
    || AVAILABILITY_REASON_NAMES[String(reason || "")]
    || String(reason || fallback || "当前系统不可用。");
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, min = 0, max = 100) {
  return Math.max(min, Math.min(max, value));
}

function percent(value, total = 1) {
  const denominator = numberValue(total, 0);
  if (denominator <= 0) return 0;
  return clamp(Math.round((numberValue(value, 0) / denominator) * 100));
}

function compact(value, limit = 120, fallback = "暂无") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return fallback;
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function firstText(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const EXTRA_PHRASE_TEXT = {
  "self healing": "自我愈合",
  "self learning": "自主学习",
  "self iteration": "自我迭代",
  "knowledge memory tidy": "知识与记忆梳理",
  "daily plan": "每日日程",
  "daily limit": "每日上限",
  "hourly limit": "每小时上限",
  "dnd start": "免打扰开始",
  "dnd end": "免打扰结束",
  "min interval seconds": "最小间隔秒数",
  "quiet if user active": "用户活跃时保持安静",
  "suppress until": "暂停到",
  "learned level": "学习等级",
  "auto allowed risks": "允许自动执行的风险等级",
  "card only risks": "只生成卡片的风险等级",
  "never auto risks": "永不自动执行的风险等级",
  "external search minimal redaction": "外部搜索最小脱敏",
  "do not touch user data": "不触碰用户数据",
  "rollback whitelist required": "回滚需要白名单",
  "risk gated model review learning": "按风险分级，并经过模型复审",
  "heartbeat running action guarded": "心跳运行中，行动受保护",
  "candidate only": "候选模式",
  "value score": "价值分",
  "base score": "基础分",
  "risk penalty": "风险扣分",
  "night window": "夜间窗口",
  "recent failures": "近期失败",
  "user not idle enough": "用户未足够空闲",
  "user run active": "用户任务运行中",
  "failed recently": "近期失败过"
};

function phraseMap() {
  return { ...TERM_TEXT, ...STATUS_TEXT, ...KEY_TEXT, ...EXTRA_PHRASE_TEXT };
}

function translateEnglishFragments(value) {
  let text = String(value ?? "").trim();
  if (!text) return "";
  const directKey = text.toLowerCase().replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  if (EXTRA_PHRASE_TEXT[directKey]) return EXTRA_PHRASE_TEXT[directKey];

  const entries = Object.entries(phraseMap())
    .map(([key, label]) => [String(key).toLowerCase().replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim(), label])
    .filter(([key]) => key && /[a-z]/i.test(key))
    .sort((a, b) => b[0].length - a[0].length);

  let output = text.replace(/[_.-]+/g, " ");
  for (const [key, label] of entries) {
    const pattern = new RegExp(`\\b${escapeRegExp(key).replace(/\\s+/g, "\\s+")}\\b`, "gi");
    output = output.replace(pattern, label);
  }
  output = output
    .replace(/\btrue\b/gi, "是")
    .replace(/\bfalse\b/gi, "否")
    .replace(/\bnone\b/gi, "无")
    .replace(/\bnull\b/gi, "无")
    .replace(/\bLLM\b/g, "模型")
    .replace(/\bAPI\b/gi, "接口")
    .replace(/\s+/g, " ")
    .trim();
  return output;
}

function zhTerm(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  const normalized = lower.replace(/[ .-]+/g, "_");
  const spaced = lower.replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  return TERM_TEXT[text]
    || TERM_TEXT[lower]
    || TERM_TEXT[normalized]
    || TERM_TEXT[spaced]
    || STATUS_TEXT[text]
    || STATUS_TEXT[lower]
    || STATUS_TEXT[normalized]
    || EXTRA_PHRASE_TEXT[spaced]
    || translateEnglishFragments(text);
}

function labelForKey(key) {
  const text = String(key ?? "").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  const normalized = lower.replace(/[ .-]+/g, "_");
  const spaced = lower.replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  const translated = KEY_TEXT[text]
    || KEY_TEXT[lower]
    || KEY_TEXT[normalized]
    || KEY_TEXT[spaced]
    || EXTRA_PHRASE_TEXT[spaced]
    || translateEnglishFragments(text.replace(/\./g, " · "));
  return /[A-Za-z]{2,}/.test(translated) ? "系统字段" : translated;
}

function labelForStatus(status) {
  const text = String(status || "").trim();
  return STATUS_TEXT[text] || STATUS_TEXT[text.toLowerCase()] || zhTerm(text) || "未知";
}

function labelForRisk(risk = "") {
  const value = String(risk || "").toUpperCase().trim();
  return RISK_TEXT[value] || value || "未标注";
}

function statusTone(status) {
  const text = String(status || "").toLowerCase();
  if (["done", "finished", "completed", "success", "ok", "accepted", "approved", "active", "synced"].includes(text)) return "done";
  if (["failed", "error", "blocked", "rejected", "cancelled", "canceled"].includes(text)) return "failed";
  if (["running", "in_progress", "review_ready", "model_review"].includes(text)) return "running";
  if (["skipped", "discarded", "disabled"].includes(text)) return "skipped";
  return "pending";
}

function statusIcon(status) {
  const tone = statusTone(status);
  if (tone === "done") return "✓";
  if (tone === "failed") return "✗";
  return "○";
}

function formatScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(1);
}

function formatCount(value, fallback = "0") {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : fallback;
}

function formatMinutes(value) {
  if (value === null || typeof value === "undefined" || value === "") return "未提供";
  const number = Number(value);
  if (!Number.isFinite(number)) return "未知";
  if (number <= 0) return "即将触发";
  if (number < 60) return `${Math.round(number)} 分钟`;
  const hours = Math.floor(number / 60);
  const minutes = Math.round(number % 60);
  return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
}

function formatDate(value) {
  const text = String(value || "").trim();
  if (!text) return "暂无";
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return text;
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    }).format(new Date(parsed));
  } catch {
    return text;
  }
}

function formatTimeOnly(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^\d{1,2}:\d{2}$/.test(text)) return text;
  if (/^\d{1,2}$/.test(text)) return `${text.padStart(2, "0")}:00`;
  const parsed = Date.parse(text);
  if (Number.isFinite(parsed)) {
    try {
      return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(parsed));
    } catch {
      return text;
    }
  }
  return text;
}

function displayValue(value) {
  if (value === null || typeof value === "undefined" || value === "") return "—";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (typeof value === "number") return Number.isFinite(value) ? (Math.abs(value) < 1 ? value.toFixed(1) : String(value)) : "—";
  if (Array.isArray(value)) return value.length ? value.map((item) => displayValue(item)).join("、") : "—";
  if (typeof value === "object") {
    const rows = Object.entries(value).slice(0, 6).map(([key, item]) => `${labelForKey(key)}：${displayValue(item)}`);
    return rows.length ? rows.join("；") : "—";
  }
  const text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}T/.test(text) || /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}/.test(text)) {
    return formatDate(text);
  }
  return compact(zhTerm(text), 80, "—");
}

function humanizeText(value, limit = 96, fallback = "暂无简述") {
  let text = String(value ?? "")
    .replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/gi, " ")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[{}[\]"'`]/g, " ")
    .replace(/\b(trace_id|task_id|value_score|created_at|updated_at|kind|status|summary|reflection|reason)\b[:：]*/gi, " ")
    .replace(/[,_]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return fallback;
  text = translateEnglishFragments(text);
  return compact(text, limit, fallback);
}

async function optionalGatewayPayload(path) {
  try {
    const request = window.tiangongFrontendKernel?.request;
    if (typeof request !== "function") return {};
    const result = await request(path, { method: "GET", timeoutMs: 9000 });
    return safeObject(result);
  } catch {
    return {};
  }
}

async function fetchLifePanelPayload(settings = {}) {
  const [panel, skills, tools] = await Promise.all([
    lifeApi.getPanel(),
    optionalGatewayPayload("/api/v1/v3/skills"),
    optionalGatewayPayload("/api/v1/v3/tools")
  ]);
  const skillSummary = safeObject(skills.summary);
  const toolSummary = safeObject(tools.summary);
  return buildLifeViewModel({
    ...safeObject(panel),
    system_capabilities: {
      skill_count: numberValue(skillSummary.skillCount ?? safeArray(skills.skills).length),
      ability_count: numberValue(skillSummary.abilityCount ?? safeArray(skills.abilities).length),
      runtime_tool_count: numberValue(toolSummary.runtimeToolCount ?? toolSummary.toolCount ?? safeArray(tools.tools).length),
      declared_tool_count: numberValue(toolSummary.total ?? skillSummary.declaredToolCount),
      unavailable_tool_count: numberValue(toolSummary.unavailable ?? skillSummary.unavailableToolCount),
      validated: tools.ok !== false && skills.ok !== false
    }
  }, settings);
}

function shellCard({ icon = "sprout", label = "", value = "", hint = "", tone = "" }) {
  return `
    <article class="life-metric-card ${esc(tone)}">
      <div class="life-card-icon">${ICONS[icon] || ICONS.sprout}</div>
      <div class="life-card-copy">
        <span>${esc(label)}</span>
        <strong class="life-fit-single-line" data-life-fit-text title="${esc(value)}">${esc(value)}</strong>
        <small>${esc(hint)}</small>
      </div>
    </article>
  `;
}

function fitLifeCardValues(root) {
  const elements = root?.querySelectorAll?.("[data-life-fit-text]") || [];
  for (const element of elements) {
    element.style.removeProperty("font-size");
    const availableWidth = element.clientWidth;
    if (!availableWidth) continue;

    const maximum = Number.parseFloat(window.getComputedStyle(element).fontSize) || 19;
    const minimum = Math.min(maximum, 10);
    element.style.fontSize = `${maximum}px`;
    if (element.scrollWidth <= availableWidth) {
      element.dataset.lifeFitSize = maximum.toFixed(2);
      continue;
    }

    let lower = minimum;
    let upper = maximum;
    for (let index = 0; index < 8; index += 1) {
      const candidate = (lower + upper) / 2;
      element.style.fontSize = `${candidate}px`;
      if (element.scrollWidth <= availableWidth) {
        lower = candidate;
      } else {
        upper = candidate;
      }
    }
    element.style.fontSize = `${lower.toFixed(2)}px`;
    element.dataset.lifeFitSize = lower.toFixed(2);
  }
}

function sectionTitle(title, meta = "") {
  return `
    <div class="life-section-title">
      <h3>${esc(title)}</h3>
      ${meta ? `<span>${esc(meta)}</span>` : ""}
    </div>
  `;
}

function emptyState(text = "暂无数据") {
  return `<div class="life-empty">${esc(text)}</div>`;
}

function kvRows(items = []) {
  return `
    <div class="life-kv-list">
      ${items.map(([key, value, tone = ""]) => `
        <div class="life-kv-row ${esc(tone)}">
          <span>${esc(labelForKey(key))}</span>
          <strong>${esc(displayValue(value))}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function progressBar(label, value, total, detail = "") {
  const pct = percent(value, total);
  return `
    <div class="life-progress">
      <div class="life-progress-head">
        <span>${esc(label)}</span>
        <strong>${pct}%</strong>
      </div>
      <div class="life-progress-track" aria-label="${esc(label)}"><span style="width:${pct}%"></span></div>
      <small>${esc(detail || `${displayValue(value)} / ${displayValue(total)}`)}</small>
    </div>
  `;
}

function renderTimeline(payload) {
  const state = safeObject(payload.state);
  const recent = safeObject(safeObject(payload.summary).recent_autonomous_action);
  const rows = [
    ["面板投影生成", payload.generated_at],
    ["最近生命心跳", state.last_heavy_tick_at],
    ["最近自主行动完成", recent.updated_at_ms ? new Date(numberValue(recent.updated_at_ms)).toISOString() : ""],
    ["最近真实执行", state.last_execution_at],
    ["生命状态更新", state.updated_at]
  ].filter(([, value]) => String(value || "").trim());

  if (!rows.length && !safeArray(payload.errors).length) return emptyState("暂无状态时间线。");

  return `
    <div class="life-timeline">
      ${rows.map(([label, value]) => `
        <div class="life-timeline-item">
          <span></span>
          <div>
            <strong>${esc(label)}</strong>
            <p>${esc(formatDate(value))}</p>
          </div>
        </div>
      `).join("")}
      ${safeArray(payload.errors).slice(0, 4).map((error) => `
        <div class="life-timeline-item warn">
          <span></span>
          <div>
            <strong>${esc(labelForKey(error.section || "数据警告"))}</strong>
            <p>${esc(compact(error.message, 180, "读取失败"))}</p>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderOverview(payload) {
  const summary = safeObject(payload.summary);
  const budget = safeObject(payload.budget);
  const settings = safeObject(payload.settings);
  const inbox = safeObject(payload.inbox);
  const inboxItems = safeArray(inbox.items);
  const unread = numberValue(inbox.unread_count);
  const completed = numberValue(summary.completed_tasks_today);
  const dailyBudget = numberValue(budget.success_limit, numberValue(settings.llm_daily_budget));
  const attemptBudget = numberValue(budget.attempt_limit, numberValue(settings.llm_daily_attempt_budget));
  const used = numberValue(budget.used);
  const attempts = numberValue(budget.attempts);
  const budgetTotal = dailyBudget > 0 ? dailyBudget : attemptBudget;
  const budgetValue = dailyBudget > 0 ? used : attempts;
  const budgetText = budgetTotal > 0 ? `${budgetValue}/${budgetTotal}` : displayValue(used || attempts || 0);
  const recent = safeObject(
    Object.keys(safeObject(summary.recent_autonomous_action)).length
      ? summary.recent_autonomous_action
      : summary.recent_action
  );
  const rawStatus = String(summary.today_status || "unknown").toLowerCase();
  const status = rawStatus === "active" ? "活跃" : rawStatus === "alive" ? "存活" : rawStatus === "unknown" ? "未知" : labelForStatus(rawStatus);
  const budgetAvailable = budget.available !== false;

  return `
    <div class="life-tab-view">
      <section class="life-overview-grid">
        ${shellCard({ icon: "sprout", label: "今日状态", value: status, hint: `已完成 ${completed} 项`, tone: completed ? "sprout" : "" })}
        ${shellCard({ icon: "heart", label: "下次心跳", value: formatMinutes(summary.next_heavy_tick_minutes), hint: humanizeText(safeObject(payload.state).last_heavy_reason || "重心跳调度", 40), tone: "heart" })}
        ${shellCard({ icon: "compass", label: "当前焦点", value: zhTerm(summary.current_focus || "idle"), hint: recent.title || "等待下一步行动", tone: "mind" })}
        ${shellCard({ icon: "scroll", label: "生命信箱", value: `${unread} 未读`, hint: `${safeArray(inbox.items).length} 条最近消息`, tone: unread ? "learn" : "" })}
      </section>

      <section class="life-card">
        ${sectionTitle("模型预算", "今日 · 不含必做日程")}
        ${budgetAvailable ? `
          ${progressBar("非日程调用预算", budgetValue, budgetTotal || Math.max(used, attempts, 1), budgetTotal > 0 ? budgetText : "未配置预算上限")}
          <div class="life-budget-grid">
            ${kvRows([
              ["成功", budget.successes ?? 0, "ok"],
              ["失败", budget.failures ?? 0, numberValue(budget.failures) ? "failed" : ""],
              ["超时", budget.timeouts ?? 0, numberValue(budget.timeouts) ? "warn" : ""],
              ["跳过", budget.skipped ?? 0]
            ])}
          </div>
        ` : emptyState(availabilityReason(budget.reason_code, budget.reason, "后端没有权威模型预算投影。"))} 
      </section>

      <section class="life-card">
        ${sectionTitle("生命信箱", unread ? `${unread} 条未读` : "今日消息")}
        <p class="life-card-hint">${esc(inboxItems.length ? "点击信件在弹窗中查看，并标记为已读。" : "今天还没有生命信箱消息。")}</p>
        ${inboxItems.length ? `
          <div class="life-inbox-list">
            ${inboxItems.map((item) => `
              <div class="life-inbox-row${item.read ? "" : " unread"}">
                <button type="button" class="life-inbox-item" data-life-inbox-message="${esc(String(item.message_id || ""))}">
                  <span class="life-inbox-dot">${item.read ? "" : "●"}</span>
                  <span class="life-inbox-title">${esc(item.title || "生命来信")}</span>
                  <span class="life-inbox-meta">${esc(formatDate(item.created_at))}</span>
                </button>
                <button type="button" class="life-inbox-delete" data-life-inbox-delete="${esc(String(item.message_id || ""))}" title="删除信件" aria-label="删除信件">🗑</button>
              </div>
            `).join("")}
          </div>
        ` : ""}
      </section>

      <section class="life-card">
        ${sectionTitle("状态时间线", payload.generated_at ? `生成 ${formatDate(payload.generated_at)}` : "")}
        ${renderTimeline(payload)}
      </section>
    </div>
  `;
}

function topNumberEntries(value = {}, limit = 8) {
  return Object.entries(safeObject(value))
    .map(([key, number]) => [key, numberValue(number)])
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit);
}

function renderOrganism(payload) {
  const soul = safeObject(payload.soul);
  const temperament = safeObject(payload.temperament);
  const traits = safeObject(temperament.current_traits);
  const disposition = safeObject(temperament.current_affective_disposition);
  const affect = safeObject(payload.affect);
  const state = safeObject(affect.state);
  const expression = safeObject(affect.expression);
  const body = safeObject(payload.body);
  const bodyProfile = safeObject(body.profile);
  const bodySignals = safeObject(body.signals);
  const emotions = topNumberEntries(state.emotions, 10);
  const drives = topNumberEntries(state.drives, 8);

  return `
    <div class="life-tab-view life-two-column">
      <section class="life-card life-soul-card">
        ${sectionTitle("灵魂", soul.revision ? `修订 ${soul.revision}` : "当前生命标识")}
        <form class="life-settings-form" data-life-soul-form>
          <label class="life-setting-field">
            <span>生命名称</span>
            <input name="name" type="text" maxlength="120" value="${esc(soul.name || "起源")}" />
            <small>名称可修改；生命标识永久不变。</small>
          </label>
          <label class="life-setting-field">
            <span>人格底稿</span>
            <textarea name="prompt" rows="16" maxlength="20000" placeholder="定义表达方式、价值取向和长期边界。">${esc(soul.prompt || "")}</textarea>
            <small>保存后签名写入当前生命标识，并进入下一次统一上下文。</small>
          </label>
          <div class="life-action-row"><button type="button" data-life-soul-save>保存灵魂配置</button></div>
        </form>
      </section>

      <section class="life-card">
        ${sectionTitle("当前情感", zhTerm(state.source || expression.tone || "有界调节"))}
        ${kvRows([
          ["愉悦度", state.valence ?? "—"],
          ["唤醒度", state.arousal ?? "—"],
          ["掌控感", state.dominance ?? "—"],
          ["主要情绪", expression.primary_emotion ? zhTerm(expression.primary_emotion) : "尚未形成离散情绪投影"],
          ["更新时间", state.updated_at || "—"]
        ])}
        ${/* HOTFIX-20260728: 后端情绪分为千分制(0-1000)，进度条与数值都需按 1000 归一化 */""}
        ${emotions.length ? `<div class="life-bar-list">${emotions.map(([key, value]) => `
          <div class="life-bar-row"><span>${esc(zhTerm(key))}</span><div class="life-bar-track"><i style="width:${percent(value, 1000)}%"></i></div><strong>${esc(formatScore(numberValue(value, 0) / 1000))}</strong></div>
        `).join("")}</div>` : emptyState("暂无情绪投影。")}
      </section>

      <section class="life-card">
        ${sectionTitle("驱动力", `${drives.length} 个维度`)}
        ${drives.length ? renderWeightBars(Object.fromEntries(drives), "驱动力") : emptyState("当前运行时尚未生成独立驱动力向量。")}
      </section>

      <section class="life-card">
        ${sectionTitle("天生人格", temperament.soul_influence === "none" ? "与灵魂配置解耦" : zhTerm(temperament.source || "生命底层人格"))}
        ${Object.keys(traits).length ? renderWeightBars(traits, "人格维度") : emptyState("尚未生成底层人格投影。")}
        ${Object.keys(disposition).length ? kvRows(Object.entries(disposition)) : ""}
        ${kvRows([
          ["人格修订", temperament.revision ?? "—"],
          ["完成沟通适应", temperament.completed_turn_evidence ?? 0],
          ["更新时间", temperament.updated_at || "—"]
        ])}
      </section>

      <section class="life-card">
        ${sectionTitle("身体状态", zhTerm(bodySignals.availability || "未标注"))}
        ${kvRows([
          ["身体预设", zhTerm(bodyProfile.body_preset || "standard")],
          ["能量水平", bodySignals.energy_milli ?? "—"],
          ["负载水平", bodySignals.load_milli ?? "—"],
          ["数据来源", zhTerm(body.source || "—")],
          ["更新时间", body.updated_at || "—"]
        ])}
      </section>
    </div>
  `;
}

function renderMemory(payload) {
  const memory = safeObject(payload.memory);
  const sourceTypes = {
    ...safeObject(memory.by_type),
    ...safeObject(memory.by_classified_type)
  };
  const memoryTypes = [
    ["episodic", "情节记忆"],
    ["semantic", "语义记忆"],
    ["procedural", "程序记忆"],
    ["preference", "偏好记忆"],
    ["relationship", "关系记忆"],
    ["goal", "目标记忆"],
    ["causal", "因果记忆"]
  ];
  const byType = memoryTypes.map(([key, label]) => [label, numberValue(sourceTypes[key])]);
  const records = Object.values(safeObject(memory.records));
  return `
    <div class="life-tab-view">
      <section class="life-overview-grid">
        ${shellCard({ icon: "scroll", label: "记忆总数", value: String(memory.total || 0), hint: "当前生命标识", tone: "learn" })}
        ${shellCard({ icon: "compass", label: "记忆类型", value: "7", hint: "七类统一记忆" })}
        ${shellCard({ icon: "shield", label: "索引状态", value: zhTerm(memory.index_status || "derived"), hint: "可从账本重建" })}
        ${shellCard({ icon: "heart", label: "关系记忆", value: String(sourceTypes.relationship || sourceTypes.relational || 0), hint: "与关系情感分权" })}
      </section>
      <section class="life-card life-memory-types">
        ${sectionTitle("七类记忆", `${byType.filter(([, value]) => numberValue(value) > 0).length} 类有记录`)}
        <div class="life-memory-types-grid">
          ${kvRows(byType)}
        </div>
      </section>
      <section class="life-card">
        ${sectionTitle("最近记忆断言", `${Math.min(records.length, 12)} / ${records.length}`)}
        ${records.length ? `<div class="life-learning-list">${records.slice(-12).reverse().map((item) => `
          <article class="life-learning-card">
            <div class="life-reflection-head"><strong>${esc(zhTerm(item.memory_type || "memory"))}</strong><span>${esc(labelForStatus(item.status || "active"))}</span></div>
            <p>${esc(humanizeText(firstText(item.summary, item.title, item.content_preview, item.memory_id), 140, "加密记忆断言"))}</p>
            <div class="life-tag-row">
              <span>${esc(zhTerm(item.evidence_class || safeObject(item.provenance).epistemic_class || "unknown"))}</span>
              ${typeof item.confidence !== "undefined" ? `<span>置信 ${esc(formatScore(item.confidence))}</span>` : ""}
              ${item.updated_at || item.created_at ? `<span>${esc(formatDate(item.updated_at || item.created_at))}</span>` : ""}
            </div>
          </article>
        `).join("")}</div>` : emptyState("当前生命还没有可展示的记忆断言。")}
      </section>
    </div>
  `;
}

function renderContext(payload) {
  const context = safeObject(payload.context);
  if (context.available !== true) {
    return `
      <div class="life-tab-view">
        <section class="life-card">${sectionTitle("统一上下文", "尚未编译")}${emptyState("发送第一条对话或触发生命判断后，将出现可校验的上下文解释。")}</section>
      </div>
    `;
  }
  const included = safeObject(context.included);
  const contextPressure = Number.isFinite(Number(context.context_utilization_milli))
    ? Math.max(0, Math.min(100, Number(context.context_utilization_milli || 0) / 10))
    : (Number(context.token_budget) > 0
      ? Math.max(0, Math.min(100, (Number(context.current_context_tokens || 0) / Number(context.token_budget)) * 100))
      : 0);
  return `
    <div class="life-tab-view">
      <section class="life-overview-grid">
        ${shellCard({
          icon: "shield",
          label: "上下文状态",
          value: context.verified
            ? (context.current ? "当前有效" : "已校验 · 需重编")
            : context.source === "live_activity_scope"
              ? "实时范围"
              : "校验失败",
          hint: compact(context.context_hash, 18, "无哈希"),
          tone: context.current ? "sprout" : ""
        })}
        ${shellCard({ icon: "compass", label: "上下文压力", value: `${Math.round(contextPressure)}%`, hint: `${context.current_context_tokens || 0}/${context.token_budget || 0} 当前对话令牌` })}
        ${shellCard({ icon: "scroll", label: "召回记忆", value: String(included.memory_cards || 0), hint: "经过证据和预算排序" })}
        ${shellCard({ icon: "heart", label: "生命能力", value: String((included.active_skills || 0) + (included.released_tools || 0)), hint: "仅已激活/已发布" })}
      </section>
      <section class="life-two-column life-tab-view">
        <article class="life-card">
          ${sectionTitle("纳入内容", `生命周期 ${context.cycle_id ? "已生成" : "—"}`)}
          ${kvRows(Object.entries(included))}
        </article>
        <article class="life-card">
          ${sectionTitle("证据层级", `${safeArray(context.evidence_classes).length} 类`)}
          ${safeArray(context.evidence_classes).length ? `<div class="life-rule-list">${safeArray(context.evidence_classes).map((item) => `<span>${esc(zhTerm(item))}</span>`).join("")}</div>` : emptyState("本轮没有可选证据块。")}
          ${kvRows([["context_hash", context.context_hash], ["生成时间", context.created_at], ["加密算法", safeObject(context.storage).algorithm || "—"]])}
        </article>
      </section>
    </div>
  `;
}

function capabilityArtifactRows(capabilities) {
  return Object.values(safeObject(capabilities.by_id))
    .filter((item) => item && typeof item === "object")
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

function renderCapabilities(payload) {
  const capabilities = safeObject(payload.capabilities);
  const activeSkills = safeArray(capabilities.active_skills);
  const releasedTools = safeArray(capabilities.released_tools);
  const allArtifacts = capabilityArtifactRows(capabilities);
  const artifacts = allArtifacts.filter((item) => !["discarded", "duplicate_removed", "no_value", "rejected"].includes(String(item.status || "").toLowerCase()));
  const candidates = artifacts.filter((item) => ["candidate", "proposed", "approved", "building", "draft", "draft_ready", "tested", "review_ready", "sandbox_passed"].includes(String(item.status || "").toLowerCase()));
  const formalArtifacts = artifacts.filter((item) => ["active", "released", "published", "accepted", "learned"].includes(String(item.status || "").toLowerCase()));
  const archivedCount = allArtifacts.length - artifacts.length;
  return `
    <div class="life-tab-view life-capability-layout">
      <section class="life-card life-capability-owned">
        ${sectionTitle("当前生命自产能力", "只计该生命自主提案、构建与发布的产物")}
        <div class="life-overview-grid life-overview-grid-auto">
          ${shellCard({ icon: "sprout", label: "自产已激活技能", value: String(activeSkills.length), hint: "可进入生命上下文", tone: "sprout" })}
          ${shellCard({ icon: "compass", label: "自产已发布工具", value: String(releasedTools.length), hint: "已核对真实执行工具" })}
          ${shellCard({ icon: "scroll", label: "正式能力版本", value: String(formalArtifacts.length), hint: formalArtifacts.length ? "已激活或已发布" : "当前尚无正式能力" })}
          ${shellCard({ icon: "compass", label: "待审能力候选", value: String(candidates.length), hint: archivedCount ? `尚未成为能力；另有 ${archivedCount} 个已归档` : "尚未成为能力" })}
        </div>
      </section>
      <section class="life-card life-capability-artifacts">
        ${sectionTitle("生命能力候选与正式产物", `${artifacts.length} 项`)}
        ${artifacts.length ? `<div class="life-learning-list life-artifact-grid">${artifacts.map((artifact) => {
          const artifactId = firstText(artifact.artifact_id, artifact.id);
          const activationStatus = String(artifact.activation_status || artifact.status || "").toLowerCase();
          const degraded = activationStatus === "degraded";
          const rollbackAllowed = Boolean(artifact.current && artifact.upgrade_of && ["active", "released", "degraded"].includes(activationStatus));
          const spec = safeObject(artifact.skill_spec);
          const steps = safeArray(spec.steps);
          const doc = safeObject(artifact.document);
          const publication = safeObject(artifact.publication);
          const workspacePath = String(publication.workspace_path || "").trim();
          return `
            <article class="life-learning-card life-artifact-card">
              <div class="life-reflection-head"><strong>${esc(firstText(artifact.name, artifact.title, artifactId, "未命名能力"))}</strong><span>${esc(labelForStatus(activationStatus || artifact.status || "candidate"))}</span></div>
              <p class="life-artifact-summary">${esc(firstText(artifact.summary, artifact.description, artifact.procedure, "暂无能力说明"))}</p>
              <div class="life-tag-row">
                <span>${esc(String(artifact.kind || "skill").toUpperCase())}</span>
                <span>版本 ${esc(artifact.version || "—")}</span>
                ${artifact.current ? "<span>当前版本</span>" : ""}
                ${artifact.updated_at ? `<span>${esc(formatDate(artifact.updated_at))}</span>` : ""}
              </div>
              ${degraded ? `<p class="life-artifact-degraded">已自动降级：${esc(artifact.degraded_reason || "连续失败且补丁未通过验证")}。不再进入模型工具列表，可手动重新激活。</p>` : ""}
              ${workspacePath ? `<div class="life-artifact-path">工作区文件：${esc(workspacePath)}</div>` : ""}
              ${steps.length ? `<div class="life-artifact-steps">${steps.map((step, stepIndex) => `
                <div class="life-artifact-step">
                  <span>${stepIndex + 1}</span>
                  <div><strong>${esc(firstText(step.step_id, `步骤 ${stepIndex + 1}`))}</strong>${step.on_failure ? `<small>${esc(step.on_failure)}</small>` : ""}</div>
                </div>
              `).join("")}</div>` : ""}
              ${doc.content ? `<details class="life-artifact-doc"><summary>完整文档</summary><pre>${esc(doc.content)}</pre></details>` : ""}
              <div class="life-action-row">
                ${degraded ? `<button type="button" data-life-capability-reactivate="${esc(artifactId)}">重新激活</button>` : ""}
                ${rollbackAllowed ? `<button type="button" class="danger" data-life-capability-rollback="${esc(artifactId)}">回滚到上一版本</button>` : ""}
                <button type="button" class="danger" data-life-capability-delete="${esc(artifactId)}">删除能力</button>
              </div>
            </article>
          `;
        }).join("")}</div>` : emptyState("尚无已确认、构建或发布的能力产物。")}
      </section>
    </div>
  `;
}

function taskTitle(item = {}) {
  const detail = safeObject(item.detail);
  return zhTerm(firstText(item.title, item.name, item.objective, detail.title, safeObject(detail.card).title, item.summary, detail.summary, item.task_kind, item.kind, "未命名任务"));
}

function taskSummary(item = {}) {
  const detail = safeObject(item.detail);
  const result = safeObject(item.result);
  return firstText(result.self_summary, result.reflection, result.summary, result.outcome, item.instruction, item.human_summary, item.llm_summary, item.summary, item.proposed_action, item.generation_reason, item.message, item.reason, item.objective, detail.instruction, detail.human_summary, detail.summary, detail.description, detail.reflection, detail.reason, "");
}

function taskKind(item = {}) {
  const detail = safeObject(item.detail);
  return firstText(item.task_kind, item.kind, detail.task_kind, detail.kind, item.source, detail.source, "");
}

function taskTime(item = {}) {
  const detail = safeObject(item.detail);
  const card = safeObject(detail.card);
  const start = firstText(item.start_time, item.start, item.planned_start, item.from, detail.start_time, detail.start, card.start_time);
  const end = firstText(item.end_time, item.end, item.planned_end, item.to, detail.end_time, detail.end, card.end_time);
  if (start && end) return `${formatTimeOnly(start)}–${formatTimeOnly(end)}`;
  const direct = firstText(item.window, item.time_range, item.time_window, item.planned_time, item.schedule_time, item.when, item.time, detail.window, detail.time_range, detail.time_window, detail.planned_time, detail.when, detail.time, card.window, card.time_range, card.time);
  if (direct) return direct.replace(/\s*-\s*/g, "–");
  const raw = `${taskTitle(item)} ${taskKind(item)} ${taskSummary(item)}`.toLowerCase();
  if (/做梦|梦境|dream/.test(raw)) return "23:00–24:00";
  return "";
}

function taskTags(item = {}) {
  const text = JSON.stringify(item).toLowerCase();
  const tags = [];
  if (/auto|autonomous|zizhu|自主|自动/.test(text)) tags.push("自动");
  if (/card|卡片/.test(text)) tags.push("卡片");
  if (/suggest|proposal|建议|推荐/.test(text)) tags.push("建议");
  const kind = taskKind(item);
  if (kind && tags.length < 3) tags.push(zhTerm(kind));
  return [...new Set(tags)].slice(0, 3);
}

function renderTaskRow(item = {}, index = 0) {
  const status = item.status || safeObject(item.detail).status || "pending";
  const tone = statusTone(status);
  const tags = taskTags(item);
  const time = taskTime(item);
  const timestamp = firstText(
    item.created_at,
    item.updated_at,
    item.at,
    item.updated_at_ms != null && Number.isFinite(Number(item.updated_at_ms)) ? new Date(Number(item.updated_at_ms)).toISOString() : "",
    item.created_at_ms != null && Number.isFinite(Number(item.created_at_ms)) ? new Date(Number(item.created_at_ms)).toISOString() : ""
  );
  return `
    <article class="life-task-row ${esc(tone)}">
      <span class="life-task-status" aria-label="${esc(labelForStatus(status))}">${statusIcon(status)}</span>
      <div class="life-task-main">
        <div class="life-task-title-line">
          <strong>${esc(taskTitle(item))}</strong>
          ${time ? `<em class="life-task-time">${esc(time)}</em>` : ""}
        </div>
        <p>${esc(humanizeText(taskSummary(item), 160, "暂无摘要"))}</p>
        <div class="life-tag-row">
          <span>${esc(labelForStatus(status))}</span>
          ${tags.map((tag) => `<span>${esc(tag)}</span>`).join("")}
          ${timestamp ? `<span>${esc(formatDate(timestamp))}</span>` : `<span>#${index + 1}</span>`}
        </div>
      </div>
    </article>
  `;
}

function renderSchedule(payload) {
  const schedule = safeObject(payload.schedule);
  const scheduledTasks = safeArray(schedule.tasks);

  return `
    <div class="life-tab-view life-schedule-layout">
      <section class="life-card life-schedule-plan">
        ${sectionTitle("今日计划", `${scheduledTasks.length} 项 · ${schedule.date || "今日"}`)}
        ${scheduledTasks.length ? `<div class="life-task-list">${scheduledTasks.map(renderTaskRow).join("")}</div>` : emptyState("今天还没有日程计划。")}
      </section>
    </div>
  `;
}

function goalScore(goal = {}) {
  return firstText(goal.score, goal.priority, goal.weight, goal.value, "");
}

function renderGoals(goals = []) {
  if (!goals.length) return emptyState("暂无长期目标。");
  return `
    <div class="life-goal-list">
      ${goals.map((goal) => `
        <article class="life-goal-card">
          <strong>${esc(zhTerm(firstText(goal.title, goal.name, goal.id, "未命名目标")))}</strong>
          <p>${esc(humanizeText(firstText(goal.description, goal.summary, goal.notes, ""), 130, "暂无目标描述"))}</p>
          <div class="life-tag-row">
            ${goalScore(goal) ? `<span>权重 ${esc(formatScore(goalScore(goal)))}</span>` : ""}
            ${goal.status ? `<span>${esc(labelForStatus(goal.status))}</span>` : ""}
            ${goal.updated_at ? `<span>${esc(formatDate(goal.updated_at))}</span>` : ""}
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

function renderWeightBars(weights = {}, title = "") {
  const entries = Object.entries(safeObject(weights))
    .map(([key, value]) => [labelForKey(key), numberValue(value)])
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  if (!entries.length) return emptyState(`${title || "权重"}暂无数据。`);
  const max = Math.max(...entries.map(([, value]) => value), 1);
  return `
    <div class="life-bar-list">
      ${entries.map(([key, value]) => `
        <div class="life-bar-row">
          <span>${esc(key)}</span>
          <div class="life-bar-track"><i style="width:${percent(value, max)}%"></i></div>
          <strong>${esc(formatScore(value))}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function renderDrift(drift = []) {
  if (!drift.length) return emptyState("暂无动机漂移记录。");
  return `
    <div class="life-drift-list">
      ${drift.map((item) => `
        <article class="life-drift-row">
          <strong>${esc(firstText(item.title, zhTerm(item.kind), item.reason, "动机漂移检测"))}</strong>
          <p>${esc(humanizeText(firstText(item.summary, item.description, item.message, item.reason, ""), 150, "暂无漂移说明"))}</p>
          <span>${esc(formatDate(item.created_at || item.updated_at || item.at || item.date))}</span>
        </article>
      `).join("")}
    </div>
  `;
}

function renderFreeWill(payload) {
  const goals = safeArray(payload.goals);
  const preferences = safeObject(payload.preferences);
  const drive = safeObject(preferences.drive_weights);
  const drift = safeArray(payload.drift);
  const freeWill = safeObject(payload.free_will);
  const scheduler = safeObject(payload.scheduler);
  const judgment = safeObject(scheduler.last_judgment);
  const latestAction = safeObject(freeWill.latest_autonomous_action);
  const recentActions = safeArray(freeWill.recent_autonomous_actions).length
    ? safeArray(freeWill.recent_autonomous_actions).slice(0, 2)
    : (Object.keys(latestAction).length ? [latestAction] : []);
  const latestActionId = firstText(latestAction.task_id, latestAction.request_id, latestAction.id);
  const actions = safeArray(payload.tasks);
  const schedulerState = firstText(scheduler.status, freeWill.heartbeat_state, "unknown");
  const decision = judgment.should_act === true
    ? "决定行动"
    : judgment.request_id
      ? "本轮不行动"
      : scheduler.running
        ? "心跳运行中"
        : "尚未判断";
  const skipReason = firstText(freeWill.skip_detail, freeWill.skip_reason, scheduler.suspended_reason, "");

  return `
    <div class="life-tab-view life-will-layout">
      <section class="life-overview-grid life-will-overview">
        ${shellCard({ icon: "compass", label: "自由意志运行", value: labelForStatus(schedulerState), hint: freeWill.enabled === false ? "已关闭" : zhTerm(freeWill.current_mode || "scheduled_autonomy") })}
        ${shellCard({ icon: "sprout", label: "是否准备行动", value: freeWill.ready_for_action ? "是" : "否", hint: freeWill.heartbeat_running ? "心跳运行中" : `心跳 ${labelForStatus(freeWill.heartbeat_state || "waiting")}` })}
        ${shellCard({ icon: "scroll", label: "最近判断", value: decision, hint: compact(judgment.title || judgment.task_id || judgment.reason, 44, "等待首次判断") })}
        ${shellCard({ icon: "heart", label: "最近自主行动", value: latestActionId ? labelForStatus(latestAction.status || "unknown") : actions.some((item) => ["pending", "running", "blocked"].includes(String(item.status || ""))) ? "待执行" : "暂无", hint: latestAction.title || (actions.length ? `已有 ${actions.length} 个自主活动候选` : "尚无可验证的自主执行记录") })}
      </section>
      <section class="life-card life-will-reason">
        ${sectionTitle("判断原因与跳过原因", judgment.at ? formatDate(judgment.at) : "当前状态")}
        ${kvRows([
          ["判断结果", decision],
          ["判断原因", judgment.reason || "暂无判断正文"],
          ["跳过 / 暂停原因", skipReason || "无"],
          ["最近行动结果", latestAction.human_summary || latestAction.summary || "暂无行动结果"],
          ["最近请求", latestActionId || judgment.request_id || "—"]
        ])}
      </section>
      <section class="life-card life-will-actions">
        ${sectionTitle("最近自主行动总结", `${recentActions.length} 条`)}
        ${recentActions.length ? `<div class="life-task-list life-recent-action-grid">${recentActions.map(renderTaskRow).join("")}</div>` : emptyState(actions.length ? "已有待执行活动，完成后会在这里显示模型对本次行动的自我总结。" : "暂无已完成的自主行动。")}
      </section>
      <section class="life-card life-will-goals">
        ${sectionTitle("长期目标", `${goals.length} 项`)}
        ${renderGoals(goals)}
      </section>
      <section class="life-card life-will-drive">
        ${sectionTitle("驱动力权重", "中文映射")}
        ${renderWeightBars(drive, "驱动力")}
      </section>
      <section class="life-card life-will-drift">
        ${sectionTitle("动机漂移检测", `${drift.length} 条`)}
        ${renderDrift(drift)}
      </section>
    </div>
  `;
}

function reflectionTitle(item = {}, index = 0) {
  return zhTerm(firstText(item.title, item.kind, item.action_title, item.task_title, `反思 ${index + 1}`));
}

function learningCardId(item = {}) {
  return firstText(item.card_id, item.id, item.learning_id, item.ability_id, item.title);
}

function riskValue(item = {}) {
  return firstText(item.risk_level, item.level, safeObject(item.card).risk_level, "");
}

function isA3Card(item = {}) {
  return String(riskValue(item)).toUpperCase().startsWith("A3");
}

function learningCardState(card = {}) {
  return String(card.status || card.promotion_stage || "candidate").toLowerCase();
}

function publishErrorText(code = "") {
  const text = String(code || "").trim();
  const known = {
    "life.learning.artifact_not_buildable": "产物构建未完成",
    "life.learning.materialization_not_complete": "学习素材尚未完成",
    "life.learning.publish_not_authorized": "发布未获授权",
    "life.learning.publisher_invalid": "发布通道异常",
    "life.learning.autonomous_risk_limit": "自主权限上限未放开",
    "life.learning.artifact.on_failure.too_large": "产物步骤参数超限"
  };
  return known[text] || text || "未知原因";
}

function markLearningCardConfirmed(cardEl, busyLabel = "确认学习中") {
  if (!cardEl) return;
  cardEl.classList.add("life-learning-confirmed");
  cardEl.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  const statusTag = cardEl.querySelector(".life-tag-row span");
  if (statusTag) statusTag.textContent = "已确认 · 学习队列";
  const confirmButton = cardEl.querySelector('[data-life-learning-action="confirm"]');
  if (confirmButton) confirmButton.textContent = busyLabel;
  const note = cardEl.querySelector(".life-action-muted");
  if (note) note.textContent = "已确认进入学习队列，正在自动发布产物…";
}

function learningCardStage(card = {}) {
  return String(card.promotion_stage || card.status || "candidate").toLowerCase();
}

function isVisibleLearningCard(card = {}) {
  const state = learningCardState(card);
  if (["discarded", "disabled", "duplicate_removed", "no_value"].includes(state)) return false;
  return isA3Card(card)
    || Boolean(card.can_confirm_learning)
    || Boolean(card.can_process_learning)
    || Boolean(card.can_activate_learning)
    || Boolean(card.can_release_learning)
    || ["candidate", "approved", "awaiting_user", "published", "building", "tested", "pending_card", "processing_approved", "draft_ready", "review_ready", "quarantined"].includes(state);
}

function learningCardActions(card = {}) {
  const id = learningCardId(card);
  if (!id) return [];
  const state = learningCardState(card);
  const stage = learningCardStage(card);
  const actions = [];
  if (card.can_confirm_learning && ["awaiting_user", "pending_card", "candidate"].includes(state)) {
    actions.push({ id: "confirm", label: "确认学习", busy: "确认学习中", reason: "用户在生命面板确认学习卡" });
  }
  if (card.can_process_learning && ["approved", "processing_approved"].includes(state)) {
    actions.push({ id: "process", label: "沙盘构建", busy: "沙盘构建中", reason: "用户在生命面板批准能力进入隔离沙盘构建" });
  }
  if (card.can_activate_learning && (["tested", "draft_ready"].includes(state) || ["tested", "review_ready", "sandbox_passed"].includes(stage))) {
    actions.push({ id: "activate", label: "激活技能", busy: "激活技能中", reason: "用户在生命面板激活学习能力" });
  }
  if (card.can_release_learning && ["tested", "review_ready", "sandbox_passed"].includes(stage)) {
    actions.push({ id: "release", label: "发布工具", busy: "发布工具中", reason: "用户在生命面板发布学习工具" });
  }
  if (card.can_discard_learning && !["active", "learned", "accepted"].includes(state)) {
    actions.push({ id: "discard", label: "取消学习", busy: "取消学习中", reason: "用户在生命面板取消学习卡", danger: true });
  }
  return actions;
}

function learningCardRowsHtml(payload) {
  const learning = safeObject(payload.learning);
  const cards = safeArray(learning.latest).filter(isVisibleLearningCard);
  if (!cards.length) return "";
  const governanceNoteText = (note = "") => {
    const legacyNotes = {
      "Preview only; no Skill or Tool is registered before user confirmation.": "当前仅为预览；在用户确认前不会注册任何技能或工具。",
      "Approved direct/low-risk learning is ready for publication.": "已批准的直通/低风险学习已具备发布条件。",
      "User confirmed the preview; publication may now write the artifact.": "用户已确认预览，发布流程现在可以写入产物。"
    };
    const key = String(note || "").trim();
    return legacyNotes[key] || note || "等待后端状态推进";
  };
  return `
    <div class="life-learning-list">
      ${cards.map((card) => {
        const id = learningCardId(card);
        const actions = learningCardActions(card);
        const state = learningCardState(card);
        const confirmed = !card.can_confirm_learning && ["approved", "processing_approved"].includes(state);
        const userAuthorized = card.requires_confirmation !== false;
        const publishError = String(card.last_publish_error || card.publish_error || "").trim();
        const retryExhausted = Boolean(card.publish_retry_exhausted);
        const queuedLabel = userAuthorized ? "已确认 · 学习队列" : "已批准 · 自动发布中";
        const statusText = confirmed
          ? queuedLabel
          : labelForStatus(card.status || card.promotion_stage || "candidate");
        const note = confirmed
          ? (retryExhausted
              ? `${queuedLabel}，但发布连续失败，自动重试已停止（${publishErrorText(publishError)}）。你可以取消这张卡。`
              : publishError
                ? `${queuedLabel}，发布暂未完成，系统自动重试中（${publishErrorText(publishError)}）`
                : `${queuedLabel}，正在自动发布产物…`)
          : governanceNoteText(card.governance_note);
        return `
          <article class="life-learning-card${confirmed ? " life-learning-confirmed" : ""}">
            <div class="life-learning-main">
              <div class="life-reflection-head">
                <strong>${esc(zhTerm(firstText(card.title, id, "未命名学习卡")))}</strong>
                <span>${esc(labelForRisk(riskValue(card)))}</span>
              </div>
              <p>${esc(firstText(card.summary, card.description, card.evidence_summary, card.reason, "暂无学习说明"))}</p>
              <div class="life-tag-row">
                <span>${esc(statusText)}</span>
                ${card.human_action_label ? `<span>下一步 ${esc(zhTerm(card.human_action_label))}</span>` : ""}
                ${card.kind ? `<span>${esc(zhTerm(card.kind))}</span>` : ""}
                ${card.score ? `<span>分数 ${esc(formatScore(card.score))}</span>` : ""}
                ${card.updated_at ? `<span>${esc(formatDate(card.updated_at))}</span>` : ""}
              </div>
              ${safeArray(card.learning_plan).length ? `
                <div class="life-learning-plan">
                  ${safeArray(card.learning_plan).map((planItem, planIndex) => `
                    <div class="life-learning-plan-row">
                      <span>${planIndex + 1}</span>
                      <div>
                        <strong>${esc(firstText(planItem.phase, `阶段 ${planIndex + 1}`))}</strong>
                        <p>${esc(firstText(planItem.goal, planItem.description, planItem.action, planItem.title, "暂无计划说明"))}</p>
                      </div>
                    </div>
                  `).join("")}
                </div>
              ` : ""}
            </div>
            <div class="life-action-row">
              ${actions.length ? actions.map((action) => `
                <button
                  type="button"
                  data-life-learning-action="${esc(action.id)}"
                  data-card-id="${esc(id)}"
                  data-busy-label="${esc(action.busy)}"
                  data-action-reason="${esc(action.reason)}"
                  class="${action.danger ? "danger" : ""}"
                >${esc(action.label)}</button>
              `).join("") : `<span class="life-action-muted">${esc(note)}</span>`}
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderLearningCards(payload) {
  const learning = safeObject(payload.learning);
  const cards = safeArray(learning.latest).filter(isVisibleLearningCard);
  return `
    <section class="life-card life-reflection-learning">
      ${sectionTitle("学习卡与能力草案", `${cards.length} 张`)}
      ${learningCardRowsHtml(payload) || emptyState("暂无需要处理的学习卡或能力草案。")}
    </section>
  `;
}

function renderReflections(payload) {
  const reflections = safeArray(payload.reflections);
  const values = safeArray(payload.action_values);

  return `
    <div class="life-tab-view life-reflection-layout">
      <section class="life-card life-slim-card life-reflection-values">
        ${sectionTitle("行动价值", `${values.length} 条`)}
        ${values.length ? `
          <div class="life-value-list">
            ${values.slice(0, 12).map((item) => `
              <div class="life-value-row">
                <span>${esc(compact(zhTerm(firstText(item.title, item.kind, item.action, "行动价值")), 68, "行动价值"))}</span>
                <strong>${esc(formatScore(item.total_score ?? item.value_score ?? item.score))}</strong>
              </div>
            `).join("")}
          </div>
        ` : emptyState("暂无行动价值评分。")}
      </section>
      <section class="life-card life-reflection-cards">
        ${sectionTitle("反思卡片", `${reflections.length} 条`)}
        ${reflections.length ? `
          <div class="life-reflection-list">
            ${reflections.map((item, index) => `
              <article class="life-reflection-card">
                <div class="life-reflection-head">
                  <strong>${esc(reflectionTitle(item, index))}</strong>
                  <span>${esc(formatScore(item.value_score ?? item.score))} 分</span>
                </div>
                <p>${esc(humanizeText(firstText(item.human_summary, item.llm_summary, item.reflection, item.summary, item.message, item.description, ""), 220, "暂无反思文本"))}</p>
                <div class="life-tag-row">
                  ${item.kind ? `<span>${esc(zhTerm(item.kind))}</span>` : ""}
                  ${item.created_at || item.updated_at || item.at ? `<span>${esc(formatDate(item.created_at || item.updated_at || item.at))}</span>` : ""}
                </div>
              </article>
            `).join("")}
          </div>
        ` : emptyState("暂无反思记录。")}
      </section>
    </div>
  `;
}

function riskTone(risk = "") {
  const value = String(risk || "").toUpperCase();
  if (value.startsWith("A4")) return "a4";
  if (value.startsWith("A3")) return "a3";
  if (value.startsWith("A2")) return "a2";
  return "a1";
}

function canActOnUpgrade(card = {}) {
  const status = String(card.status || "").toLowerCase();
  if (["accepted", "approved", "building", "tested", "active", "released", "failed", "rolled_back", "rejected", "cancelled", "canceled", "discarded", "applied", "confirmed", "executing", "completed"].includes(status)) return false;
  return Boolean(firstText(card.id, card.card_id, card.title));
}

function renderIteration(payload) {
  const cards = safeArray(payload.upgrade_cards);
  const learning = safeObject(payload.learning);
  const learningCards = safeArray(learning.latest).filter(isVisibleLearningCard);
  const learningRows = learningCardRowsHtml(payload);
  const capabilities = safeObject(payload.capabilities);
  const artifacts = capabilityArtifactRows(capabilities);
  const candidates = artifacts.filter((item) => ["candidate", "proposed", "draft"].includes(String(item.status || "").toLowerCase()));
  const pipelineState = cards.length || candidates.length || numberValue(learning.candidate_count) ? "有候选待处理" : "等待上游候选";

  return `
    <div class="life-tab-view">
      <section class="life-overview-grid">
        ${shellCard({ icon: "sprout", label: "学习候选", value: String(numberValue(learning.candidate_count)), hint: "等待审查的学习卡" })}
        ${shellCard({ icon: "compass", label: "能力候选", value: String(candidates.length), hint: "尚未发布的自产能力" })}
        ${shellCard({ icon: "scroll", label: "升级卡", value: String(cards.length), hint: "等待确认的迭代" })}
        ${shellCard({ icon: "shield", label: "迭代管线", value: pipelineState, hint: "候选 → 审核 → 沙盒 → 发布" })}
      </section>
      <section class="life-card">
        ${sectionTitle("升级卡", `${cards.length + learningCards.length} 张`)}
        ${cards.length ? `
          <div class="life-upgrade-list">
            ${cards.map((card) => {
              const id = firstText(card.id, card.card_id, card.title);
              const canAct = canActOnUpgrade(card);
              return `
                <article class="life-upgrade-card">
                  <div class="life-upgrade-risk ${esc(riskTone(card.risk_level))}">${esc(String(card.risk_level || "A?").toUpperCase())}</div>
                  <div class="life-upgrade-main">
                    <strong>${esc(zhTerm(firstText(card.title, id, "未命名升级卡")))}</strong>
                    <p>${esc(humanizeText(firstText(card.summary, card.source, safeArray(card.goals).join("、"), ""), 160, "暂无来源说明"))}</p>
                    <div class="life-tag-row">
                      <span>${esc(labelForRisk(card.risk_level))}</span>
                      <span>${esc(labelForStatus(card.status))}</span>
                      ${safeArray(card.changes).length ? `<span>代码变更 ${safeArray(card.changes).length} 处</span>` : ""}
                      ${card.review_level ? `<span>${esc(card.review_level === "CORE_REVIEW" ? "核心审查" : "人工审查")}</span>` : ""}
                      ${card.created_at ? `<span>${esc(formatDate(card.created_at))}</span>` : ""}
                      ${safeArray(card.tests).length ? `<span>测试 ${safeArray(card.tests).length}</span>` : ""}
                    </div>
                    ${card.error ? `<p class="life-upgrade-error">${esc(humanizeText(card.error, 200, ""))}</p>` : ""}
                    <div class="life-action-row">
                      <button type="button" data-life-upgrade-action="confirm" data-card-id="${esc(id)}" ${canAct ? "" : "disabled"}>确认升级</button>
                      <button type="button" data-life-upgrade-action="cancel" data-card-id="${esc(id)}" class="danger" ${canAct ? "" : "disabled"}>取消升级</button>
                    </div>
                  </div>
                </article>
              `;
            }).join("")}
          </div>
        ` : ""}
        ${learningRows}
        ${(!cards.length && !learningCards.length) ? emptyState("暂无自我迭代升级卡。") : ""}
      </section>
    </div>
  `;
}

function renderBoundarySection(title, data = {}, icon = "shield") {
  const entries = Object.entries(safeObject(data));
  return `
    <article class="life-boundary-card">
      <div class="life-boundary-head">
        <span>${ICONS[icon] || ICONS.shield}</span>
        <strong>${esc(title)}</strong>
      </div>
      ${entries.length ? kvRows(entries.map(([key, value]) => [key, value])) : emptyState("暂无配置")}
    </article>
  `;
}

function renderBoundaries(payload) {
  const boundaries = safeObject(payload.boundaries);
  return `
    <div class="life-tab-view life-boundary-view">
      <section class="life-boundary-grid">
        ${renderBoundarySection("自主等级", boundaries.autonomy, "compass")}
        ${renderBoundarySection("分享策略", boundaries.share, "heart")}
        ${renderBoundarySection("隐私", boundaries.privacy, "shield")}
        ${renderBoundarySection("文件系统", boundaries.file_system, "scroll")}
      </section>
      <section class="life-card">
        ${sectionTitle("灵魂声明边界", `${safeArray(boundaries.declared_rules).length} 条`)}
        ${safeArray(boundaries.declared_rules).length ? `
          <div class="life-rule-list">
            ${safeArray(boundaries.declared_rules).map((rule) => `<span>${esc(displayValue(rule))}</span>`).join("")}
          </div>
        ` : emptyState("灵魂配置尚未声明长期边界。")}
      </section>
    </div>
  `;
}

function getPathValue(obj, path) {
  const parts = String(path || "").split(".");
  let current = obj;
  for (const part of parts) {
    if (!current || typeof current !== "object") return undefined;
    current = current[part];
  }
  return current;
}

function renderSettingField(field, settings) {
  const rawValue = getPathValue(settings, field.key);
  const hasValue = rawValue !== null && typeof rawValue !== "undefined" && rawValue !== "";
  const value = hasValue ? rawValue : "";
  const name = field.key;
  if (field.type === "multi-check") {
    const selected = new Set(Array.isArray(value) ? value.map(String) : []);
    const options = safeArray(settings.autonomy_activity_catalog);
    return `
      <fieldset class="life-setting-field multi-check">
        <legend>
          <span>${esc(field.label)}</span>
          <em>当前：${esc(selected.size)} 项</em>
        </legend>
        <div class="life-setting-options">
          ${options.map((option) => `
            <label class="life-setting-option">
              <input
                name="${esc(name)}"
                type="checkbox"
                value="${esc(option.activity_id || "")}"
                ${selected.has(String(option.activity_id || "")) ? "checked" : ""}
              />
              <span><strong>${esc(option.label || option.activity_id || "自主活动")}</strong><small>${esc(option.description || "")}</small></span>
            </label>
          `).join("")}
          ${options.length ? "" : `<div class="life-setting-unavailable">后端未提供可选活动</div>`}
        </div>
        <small>${esc(field.help || "")}</small>
      </fieldset>
    `;
  }
  if (field.type === "checkbox") {
    return `
      <label class="life-setting-field checkbox">
        <span class="life-setting-head">
          <strong>${esc(field.label)}</strong>
          <em>当前：${hasValue ? (value ? "开启" : "关闭") : "后端未提供"}</em>
        </span>
        <span class="life-setting-toggle">
          <input name="${esc(name)}" type="checkbox" ${value ? "checked" : ""} ${hasValue ? "" : "disabled"} />
          <i aria-hidden="true"></i>
          <b>${value ? "已开启" : "已关闭"}</b>
        </span>
        <small>${esc(field.help || "")}</small>
      </label>
    `;
  }
  if (field.type === "select") {
    const selectedLabel = safeArray(field.options).find(([optionValue]) => String(value) === String(optionValue))?.[1] || "";
    return `
      <label class="life-setting-field">
        <span class="life-setting-head">
          <strong>${esc(field.label)}</strong>
          <em>当前：${esc(selectedLabel || (hasValue ? zhTerm(value) : "后端未提供"))}</em>
        </span>
        <span class="life-select-shell">
          <select name="${esc(name)}" ${hasValue ? "" : "disabled"}>
            ${hasValue ? "" : `<option value="" selected disabled>后端未提供</option>`}
            ${safeArray(field.options).map(([optionValue, optionLabel]) => `
              <option value="${esc(optionValue)}" ${String(value) === String(optionValue) ? "selected" : ""}>${esc(optionLabel)}</option>
            `).join("")}
          </select>
        </span>
        <small>${esc(field.help || "")}</small>
      </label>
    `;
  }
  return `
    <label class="life-setting-field">
      <span class="life-setting-head">
        <strong>${esc(field.label)}</strong>
        <em>当前：${hasValue ? esc(value) : "后端未提供"}</em>
      </span>
      <input
        name="${esc(name)}"
        type="${esc(field.type || "text")}"
        value="${esc(value)}"
        placeholder="${hasValue ? "" : "后端未提供"}"
        ${typeof field.min !== "undefined" ? `min="${esc(field.min)}"` : ""}
        ${typeof field.max !== "undefined" ? `max="${esc(field.max)}"` : ""}
        ${typeof field.step !== "undefined" ? `step="${esc(field.step)}"` : ""}
        ${hasValue ? "" : "disabled"}
        ${field.readonly ? "readonly" : ""}
      />
      <small>${esc(field.help || "")}</small>
    </label>
  `;
}

export function renderSettings(payload) {
  const settings = safeObject(payload.settings);
  const source = firstText(settings.source, "未挂载");
  if (settings.available === false || settings.editable === false || settings.readonly === true) {
    return `
      <div class="life-tab-view">
        <section class="life-card">
          ${sectionTitle("生命链设置", `来源 ${zhTerm(source)}`)}
          ${emptyState("后端当前没有生命链设置写入契约；此页保持只读，避免制造无法生效的配置。")}
        </section>
      </div>
    `;
  }

  return `
    <div class="life-tab-view">
      <section class="life-card life-settings-card">
        ${sectionTitle("生命链设置", `来源 ${zhTerm(source)}`)}
        <form class="life-settings-form" data-life-settings-form>
          <div class="life-settings-groups">
            ${SETTING_GROUPS.map((group) => {
              const groupFields = group.fields
                .map((key) => SETTING_FIELDS.find((field) => field.key === key))
                .filter(Boolean);
              return `
                <section class="life-settings-group" data-life-settings-group="${esc(group.id)}">
                  <header>
                    <h4>${esc(group.title)}</h4>
                    <p>${esc(group.description)}</p>
                  </header>
                  <div class="life-settings-grid">
                    ${groupFields.map((field) => renderSettingField(field, settings)).join("")}
                  </div>
                </section>
              `;
            }).join("")}
          </div>
          <div class="life-action-row life-settings-actions">
            <button type="button" data-life-settings-save>保存设置</button>
            <button type="button" data-life-settings-reset>恢复当前值</button>
          </div>
        </form>
      </section>
    </div>
  `;
}

function renderIdentity(payload) {
  const current = safeObject(payload.identity);
  const identities = safeArray(payload.identities);
  const auditEvents = safeArray(payload.identity_audit);
  const identityTone = (item) => {
    const value = Number(item?.soul_tone);
    return Number.isInteger(value) && value >= 0 && value <= 359 ? value : 168;
  };
  return `
    <div class="life-tab-view life-identity-view">
      <section class="life-card">
        ${sectionTitle("当前生命", current.life_id ? "身份校验有效" : "等待选择")}
        ${current.life_id ? kvRows([
          ["生命名称", current.name || "起源"],
          ["生命标识", current.life_id],
          ["创建时间", current.created_at || ""],
          ["数据目录", current.root || ""],
          ["状态", current.status || "active"],
          ["写入纪元", current.writer_epoch || "—"]
        ]) : emptyState("尚未激活生命标识。")}
      </section>
      <section class="life-card">
        ${sectionTitle("已绑定生命", `${identities.length} 个`)}
        <div class="life-identity-list">
          ${identities.length ? identities.map((item) => `
            <article class="life-identity-row ${item.active ? "active" : ""}" style="--life-tone: ${identityTone(item)}">
              <div class="life-identity-name">
                <strong>${esc(item.name || "起源")}</strong>
                <small class="life-identity-state">${esc(item.active ? "当前生命" : (item.integrity === "valid" ? "封印中" : "封印异常"))}</small>
              </div>
              <p class="life-identity-intro" title="${esc(item.soul_intro || "")}">${esc(item.soul_intro || "这个生命还没有写下自己的简介。")}</p>
              <p class="life-identity-persona">人格基线：${esc(Object.entries(safeObject(item.temperament_traits)).map(([key, value]) => `${zhTerm(key)} ${Number(value).toFixed(2)}`).join(" · ") || "尚未通过人格校验")}</p>
              <div class="life-action-row life-identity-action">
                ${item.active
                  ? `<span class="life-identity-active-state">激活中</span>`
                  : `
                  <button type="button" class="danger" data-life-identity-delete="${esc(item.life_id || "")}">删除</button>
                  <button type="button" data-life-identity-activate="${esc(item.life_id || "")}" ${item.integrity !== "valid" ? "disabled" : ""}>${item.integrity === "valid" ? "激活" : "不可激活"}</button>
                `}
              </div>
            </article>
          `).join("") : emptyState("本机还没有已绑定生命。")}
        </div>
      </section>
      <section class="life-card">
        ${sectionTitle("身份操作记录", `${auditEvents.length} 条最近记录`)}
        ${auditEvents.length ? `<div class="life-learning-list">${auditEvents.map((event) => `
          <article class="life-learning-card">
            <div class="life-reflection-head"><strong>${esc(zhTerm(event.action || "身份操作"))}</strong><span>${esc(formatDate(event.at))}</span></div>
            ${kvRows([["生命", event.name || event.life_id || "—"], ["操作者", event.actor || "user"]])}
          </article>
        `).join("")}</div>` : emptyState("尚无可显示的身份操作记录。")}
      </section>
    </div>
  `;
}

function renderTab(payload, activeTab) {
  if (activeTab === "identity") return renderIdentity(payload);
  if (payload.projection_status !== "authoritative") {
    return `
      <div class="life-tab-view">
        <section class="life-card">
          ${sectionTitle(LIFE_TABS.find((tab) => tab.id === activeTab)?.label || "系统状态", "等待权威投影")}
          ${emptyState("Gateway 尚未提供带 revision 与来源引用的生命投影；为避免展示臆造状态，本页不使用旧缓存拼接数据。")}
        </section>
      </div>
    `;
  }
  const section = safeObject(safeObject(payload.sections)[activeTab]);
  if (section.available !== true) {
    return `
      <div class="life-tab-view">
        <section class="life-card">
          ${sectionTitle(LIFE_TABS.find((tab) => tab.id === activeTab)?.label || "系统状态", "后端未挂载")}
          ${emptyState(availabilityReason(section.reason_code, section.reason, "当前后端没有这个系统的权威投影。"))} 
        </section>
      </div>
    `;
  }
  let content;
  switch (activeTab) {
    case "organism": content = renderOrganism(payload); break;
    case "memory": content = renderMemory(payload); break;
    case "context": content = renderContext(payload); break;
    case "schedule": content = renderSchedule(payload); break;
    case "will": content = renderFreeWill(payload); break;
    case "reflection": content = renderReflections(payload); break;
    case "capabilities": content = renderCapabilities(payload); break;
    case "iteration": content = renderIteration(payload); break;
    case "boundaries": content = renderBoundaries(payload); break;
    case "settings": content = renderSettings(payload); break;
    case "overview":
    default: content = renderOverview(payload); break;
  }
  return content;
}

function setNested(target, path, value) {
  const parts = String(path || "").split(".");
  let current = target;
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index];
    if (index === parts.length - 1) {
      current[part] = value;
      return;
    }
    if (!current[part] || typeof current[part] !== "object") current[part] = {};
    current = current[part];
  }
}

function collectSettings(form) {
  const payload = {};
  const fieldsByName = new Map(Array.from(form.elements || []).map((el) => [el.name, el]));
  for (const field of SETTING_FIELDS) {
    if (field.type === "multi-check") {
      const values = Array.from(form.elements || [])
        .filter((el) => el.name === field.key && el.checked)
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
      setNested(payload, field.key, values);
      continue;
    }
    const input = fieldsByName.get(field.key);
    if (!input || input.disabled) continue;
    let value = field.type === "checkbox" ? Boolean(input.checked) : input.value;
    if (field.type === "number") {
      const number = Number(value);
      value = Number.isFinite(number) ? number : 0;
    }
    setNested(payload, field.key, value);
  }
  return payload;
}

export const lifePanelPlugin = {
  id: "life-panel",
  slot: "conversation",
  order: 218,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel lifecycle-page life-page" data-page-panel="lifecycle">
        <header class="page-header life-page-header">
          <div class="title-group">
            <span class="caption">生命</span>
            <h2>生命链面板</h2>
          </div>
          <div class="commandbar-meta">
            <span id="lifePanelState" class="mini-pill">未读取</span>
            <button id="lifePanelRefresh" class="small-command life-refresh-command" type="button">刷新</button>
          </div>
        </header>

        <section class="page-body life-body">
          <nav class="life-tabs" role="tablist" aria-label="生命链内容">
            ${LIFE_TABS.map((tab) => `
              <button class="life-tab" data-life-tab="${tab.id}" type="button" role="tab" aria-selected="${tab.id === "overview" ? "true" : "false"}">${tab.label}</button>
            `).join("")}
          </nav>
          <section id="lifePanelContent" class="life-tab-content" aria-live="polite"></section>
        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="lifecycle"].life-page');
    const pill = panel.querySelector("#lifePanelState");
    const refresh = panel.querySelector("#lifePanelRefresh");
    const content = panel.querySelector("#lifePanelContent");
    const tabButtons = [...panel.querySelectorAll("[data-life-tab]")];

    let activeTab = "overview";
    let payload = null;
    let generation = 0;
    let lastLoadedAt = 0;
    let loading = false;
    let actionBusy = false;
    let activeLifeId = "";
    let fitFrame = 0;
    let latestInboxItems = [];

    panel.insertAdjacentHTML("beforeend", `
      <div class="life-inbox-modal" data-life-inbox-modal hidden>
        <div class="life-inbox-modal-card" role="dialog" aria-modal="true" aria-label="生命信箱信件">
          <div class="life-inbox-modal-head">
            <strong data-life-inbox-modal-title></strong>
            <button type="button" class="life-inbox-modal-close" data-life-inbox-modal-close aria-label="关闭">×</button>
          </div>
          <div class="life-inbox-modal-meta" data-life-inbox-modal-meta></div>
          <div class="life-inbox-modal-body" data-life-inbox-modal-body></div>
        </div>
      </div>
    `);
    const inboxModal = panel.querySelector("[data-life-inbox-modal]");
    const inboxModalTitle = panel.querySelector("[data-life-inbox-modal-title]");
    const inboxModalMeta = panel.querySelector("[data-life-inbox-modal-meta]");
    const inboxModalBody = panel.querySelector("[data-life-inbox-modal-body]");

    function showInboxModal(item = {}) {
      inboxModalTitle.textContent = item.title || "生命来信";
      inboxModalMeta.textContent = [
        formatDate(item.created_at),
        item.kind === "daily_life_summary" ? "今日生命总结" : String(item.kind || "")
      ].filter(Boolean).join(" · ");
      inboxModalBody.textContent = item.message || "（信件内容为空）";
      inboxModal.hidden = false;
    }

    function closeInboxModal() {
      inboxModal.hidden = true;
    }

    function scheduleLifeCardFit() {
      if (fitFrame) window.cancelAnimationFrame(fitFrame);
      fitFrame = window.requestAnimationFrame(() => {
        fitFrame = 0;
        fitLifeCardValues(content);
      });
    }

    const fitObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleLifeCardFit)
      : null;
    fitObserver?.observe(content);

    function setPill(text, tone = "") {
      pill.textContent = text;
      pill.className = `mini-pill ${tone}`.trim();
    }

    function renderPage(page) {
      const active = page === "lifecycle";
      panel.classList.toggle("active", active);
      if (active && (!payload || Date.now() - lastLoadedAt > REFRESH_INTERVAL_MS)) {
        void loadPanel("page");
      }
    }

    function renderTabs() {
      for (const button of tabButtons) {
        const active = button.dataset.lifeTab === activeTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      }
    }

    function renderContent() {
      renderTabs();
      content.dataset.lifeActiveTab = activeTab;
      if (!payload) {
        content.innerHTML = `
          <div class="life-loading-card">
            <div class="life-state-dot breathe"></div>
            <strong>${loading ? "正在读取生命链数据" : "等待生命链数据"}</strong>
            <p>${loading ? "接口 /api/v1/v3/life/panel 正在返回生命链数据。" : "切到生命页后会自动刷新。"}</p>
          </div>
        `;
        return;
      }
      content.classList.add("is-switching");
      window.setTimeout(() => {
        latestInboxItems = safeArray(payload.inbox?.items);
        content.innerHTML = renderTab(payload, activeTab);
        content.classList.toggle("is-action-busy", actionBusy);
        content.classList.remove("is-switching");
        scheduleLifeCardFit();
      }, 60);
    }

    function applyChatGateProjection(nextPayload) {
      const gate = safeObject(nextPayload?.chat_gate);
      if (gate.schema !== "tiangong.life.chat-gate.v1" || gate.authority !== "embedded_life_runtime") return;
      const current = safeObject(state.snapshot?.().kernelStatus);
      const currentLife = safeObject(current.life);
      const ready = gate.ready === true && gate.available === true;
      const compatible = current.compatible === false ? false : true;
      const phase = compatible ? (ready ? "ready" : "degraded") : "incompatible";
      const observedAt = Date.parse(String(gate.observed_at || ""));
      state.setKernelStatus?.({
        ...current,
        phase,
        compatible,
        backend: { ...safeObject(current.backend), connected: true },
        life: {
          ...currentLife,
          ready,
          available: gate.available === true,
          degraded: gate.degraded === true || !ready,
          error: String(gate.reason_code || ""),
          warning: "",
          phase: String(gate.life_phase || (ready ? "alive" : "not_ready")),
        },
        lastSuccessAt: Number.isFinite(observedAt) ? observedAt : Date.now(),
        lastError: compatible && !ready
          ? { code: String(gate.reason_code || "life_not_ready"), message: "生命内核尚未就绪" }
          : null,
      });
    }

    async function loadPanel(reason = "manual") {
      const current = ++generation;
      loading = true;
      setPill(reason === "timer" ? "自动刷新" : "读取中");
      refresh.classList.add("is-rotating");
      if (!payload) renderContent();
      try {
        const data = await fetchLifePanelPayload(state.snapshot?.().settings || {});
        if (current !== generation) return;
        payload = data && typeof data === "object" ? data : { ok: false, error: "invalid_payload" };
        applyChatGateProjection(payload);
        const nextLifeId = String(safeObject(payload.identity).life_id || "");
        if (nextLifeId && nextLifeId !== activeLifeId) {
          activeLifeId = nextLifeId;
          state.setLifeScope?.(nextLifeId);
          await actions?.loadSettings?.();
          try { window.dispatchEvent(new CustomEvent("tiangong-life-changed", { detail: { lifeId: nextLifeId } })); } catch {}
        }
        if (payload.setup_required === true) activeTab = "identity";
        lastLoadedAt = Date.now();
        loading = false;
        if (payload.ok === false) {
          setPill("读取失败", "failed");
        } else if (safeArray(payload.errors).length) {
          setPill(`已同步 · ${safeArray(payload.errors).length} 警告`, "warn");
        } else {
          setPill("已同步", "ok");
        }
        renderContent();
      } catch (error) {
        if (current !== generation) return;
        loading = false;
        payload = {
          ok: false,
          generated_at: new Date().toISOString(),
          error: error?.message || String(error),
          errors: [{ section: "api", message: error?.message || String(error) }],
          sections: {
            overview: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            organism: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            memory: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            context: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            schedule: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            will: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            reflection: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            capabilities: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            iteration: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            boundaries: { available: false, reason_code: "PANEL_UNAVAILABLE" },
            settings: { available: false, reason_code: "PANEL_UNAVAILABLE" }
          },
          summary: {},
          state: {},
          schedule: { available: false, reason_code: "PANEL_UNAVAILABLE" },
          inbox: { available: false, reason_code: "PANEL_UNAVAILABLE" },
          tasks: [],
          goals: [],
          preferences: {},
          drift: [],
          action_values: [],
          reflections: [],
          learning: {},
          boundaries: {},
          budget: { available: false, reason_code: "PANEL_UNAVAILABLE" },
          upgrade_cards: [],
          settings: { readonly: true, editable: false, available: false, source: "unavailable" }
        };
        setPill("离线", "failed");
        renderContent();
      } finally {
        window.setTimeout(() => refresh.classList.remove("is-rotating"), 620);
      }
    }

    async function runAction(label, operation, { syncSettings = false, reloadOnError = false } = {}) {
      if (actionBusy) return;
      actionBusy = true;
      setPill(label, "warn");
      content.classList.add("is-action-busy");
      try {
        const result = await operation();
        if (result?.ok === false) throw new Error(result.error || "操作失败");
        setPill("操作完成", "ok");
        await loadPanel("action");
        if (syncSettings && typeof actions?.loadSettings === "function") {
          await actions.loadSettings();
        }
      } catch (error) {
        setPill(error?.message || "操作失败", "failed");
        if (reloadOnError) await loadPanel("action_error");
      } finally {
        actionBusy = false;
        content.classList.remove("is-action-busy");
      }
    }

    function switchTab(nextTab) {
      if (!LIFE_TABS.some((tab) => tab.id === nextTab)) return;
      if (nextTab === activeTab) return;
      activeTab = nextTab;
      renderContent();
    }

    panel.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-life-tab]");
      if (tab) {
        switchTab(tab.dataset.lifeTab);
        return;
      }
      if (event.target.closest("#lifePanelRefresh")) {
        void loadPanel("manual");
        return;
      }

      if (event.target.closest("[data-life-inbox-message]")) {
        const messageId = String(event.target.closest("[data-life-inbox-message]").dataset.lifeInboxMessage || "");
        const item = latestInboxItems.find((row) => String(row.message_id || "") === messageId);
        if (item) {
          showInboxModal(item);
          if (!item.read) {
            item.read = true;
            void lifeApi.markInboxRead(messageId).then(() => loadPanel("inbox_read")).catch(() => {});
          }
        }
        return;
      }
      if (event.target.closest("[data-life-inbox-delete]")) {
        const messageId = String(event.target.closest("[data-life-inbox-delete]").dataset.lifeInboxDelete || "");
        if (messageId) {
          void lifeApi.deleteInboxMessage(messageId).then(() => loadPanel("inbox_delete")).catch(() => {});
        }
        return;
      }
      if (
        event.target.closest("[data-life-inbox-modal-close]")
        || (event.target.closest("[data-life-inbox-modal]") && event.target === inboxModal)
      ) {
        closeInboxModal();
        return;
      }

      if (event.target.closest("[data-life-identity-create]")) {
        const name = panel.querySelector("[data-life-create-name]")?.value || "起源";
        void runAction("创建生命中", () => lifeApi.createIdentity(name));
        return;
      }

      if (event.target.closest("[data-life-bind-choose]")) {
        const bridge = window.tiangongDesktop;
        void (async () => {
          try {
            if (!bridge?.chooseStorageRoot) throw new Error("桌面目录选择器不可用");
            const result = await bridge.chooseStorageRoot({ purpose: "lifeIdentity" });
            if (result?.ok === false) throw new Error(result.error || "目录选择失败");
            if (!result?.canceled && result?.path) {
              const input = panel.querySelector("[data-life-bind-root]");
              if (input) input.value = result.path;
            }
          } catch (error) {
            setPill(error?.message || "目录选择失败", "failed");
          }
        })();
        return;
      }

      if (event.target.closest("[data-life-identity-bind]")) {
        const root = panel.querySelector("[data-life-bind-root]")?.value || "";
        void runAction("绑定生命中", () => lifeApi.bindIdentity(root));
        return;
      }

      const activateIdentity = event.target.closest("[data-life-identity-activate]");
      if (activateIdentity) {
        void runAction("切换生命中", () => lifeApi.activateIdentity(activateIdentity.dataset.lifeIdentityActivate));
        return;
      }

      const deleteIdentity = event.target.closest("[data-life-identity-delete]");
      if (deleteIdentity) {
        const lifeId = String(deleteIdentity.dataset.lifeIdentityDelete || "");
        const identity = safeArray(payload?.identities).find((item) => item?.life_id === lifeId);
        const name = String(identity?.name || "这个生命");
        if (!window.confirm(`确定永久删除“${name}”吗？\n\n该生命目录下的全部文件和子目录都会被删除，无法恢复。`)) return;
        void runAction("删除生命中", () => lifeApi.deleteIdentity(lifeId));
        return;
      }

      const unbindIdentity = event.target.closest("[data-life-identity-unbind]");
      if (unbindIdentity) {
        void runAction("解除绑定中", () => lifeApi.unbindIdentity(unbindIdentity.dataset.lifeIdentityUnbind));
        return;
      }

      const learningButton = event.target.closest("[data-life-learning-action]");
      if (learningButton) {
        const cardId = learningButton.dataset.cardId || "";
        const action = learningButton.dataset.lifeLearningAction;
        if (!["confirm", "process", "activate", "release", "discard"].includes(action)) return;
        const label = learningButton.dataset.busyLabel || "处理中";
        const reason = learningButton.dataset.actionReason || "用户在生命面板处理学习卡";
        if (action === "confirm") markLearningCardConfirmed(learningButton.closest(".life-learning-card"), label);
        void runAction(label, () => lifeApi.transitionLearning(action, cardId, { reason }), { reloadOnError: true });
        return;
      }

      const upgradeButton = event.target.closest("[data-life-upgrade-action]");
      if (upgradeButton) {
        const cardId = upgradeButton.dataset.cardId || "";
        const action = upgradeButton.dataset.lifeUpgradeAction;
        const label = action === "confirm" ? "确认升级中" : "取消升级中";
        const reason = action === "confirm" ? "用户在生命面板确认升级卡" : "用户在生命面板取消升级卡";
        void runAction(label, () => lifeApi.decideUpgrade(action, cardId, { reason }));
        return;
      }

      const rollbackButton = event.target.closest("[data-life-capability-rollback]");
      if (rollbackButton) {
        const artifactId = rollbackButton.dataset.lifeCapabilityRollback || "";
        void runAction("回滚能力中", () => lifeApi.rollbackCapability(artifactId, { reason: "用户在生命面板回滚当前能力版本" }));
        return;
      }

      const reactivateButton = event.target.closest("[data-life-capability-reactivate]");
      if (reactivateButton) {
        const artifactId = reactivateButton.dataset.lifeCapabilityReactivate || "";
        void runAction("重新激活能力中", () => lifeApi.reactivateCapability(artifactId, { reason: "用户在生命面板重新激活已降级能力" }));
        return;
      }

      const deleteCapability = event.target.closest("[data-life-capability-delete]");
      if (deleteCapability) {
        const artifactId = String(deleteCapability.dataset.lifeCapabilityDelete || "");
        const record = safeObject(payload?.capabilities?.by_id)[artifactId] || {};
        const name = firstText(record.title, record.name, artifactId, "该能力");
        if (!window.confirm(`确定删除能力“${name}”吗？\n\n将删除能力记录、产物包与工作区映射文件，无法恢复。`)) return;
        void runAction("删除能力中", () => lifeApi.capabilityDiscard(artifactId, { reason: "用户在生命面板删除生命能力" }));
        return;
      }

      if (event.target.closest("[data-life-soul-save]")) {
        const form = panel.querySelector("[data-life-soul-form]");
        if (!form) return;
        const formData = new FormData(form);
        void runAction("保存灵魂配置中", () => lifeApi.updateSoul({
          name: String(formData.get("name") || "起源").trim() || "起源",
          prompt: String(formData.get("prompt") || "")
        }), { syncSettings: true });
        return;
      }

      if (event.target.closest("[data-life-settings-save]")) {
        const form = panel.querySelector("[data-life-settings-form]");
        if (!form) return;
        const settings = collectSettings(form);
        void runAction("保存设置中", () => lifeApi.updateSettings(settings), { syncSettings: true });
        return;
      }

      if (event.target.closest("[data-life-settings-reset]")) {
        renderContent();
      }
    });

    state.on("page", renderPage);

    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderContent();
    void loadPanel("mount");

    window.addEventListener("life:identity-changed", () => {
      if (state.snapshot().activePage === "lifecycle") loadPanel("identity_changed");
    });

    if (!lifePanelTimer) {
      lifePanelTimer = window.setInterval(() => {
        if (state.snapshot().activePage !== "lifecycle") return;
        const focused = document.activeElement;
        if (focused && (focused.tagName === "INPUT" || focused.tagName === "TEXTAREA" || focused.tagName === "SELECT")) return;
        void loadPanel("timer");
      }, REFRESH_INTERVAL_MS);
    }
  }
};
