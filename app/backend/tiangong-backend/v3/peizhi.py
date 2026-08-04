"""
天工造物 v3：起源 — 全局配置
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from .endpoint_security import custom_scope_id, is_official_endpoint, validate_model_endpoint

# ── 强制 UTF-8 输出：Windows GBK 环境下防止 UnicodeEncodeError ──
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 全局异常追踪：崩溃时写 traceback 到文件 ──
import traceback as _traceback
_original_excepthook = sys.excepthook
def _utf8_trace_excepthook(exc_type, exc_value, exc_tb):
    try:
        tb_text = "".join(_traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(Path.home() / ".tiangong" / "v3" / "crash_traceback.log", "a", encoding="utf-8") as _fh:
            _fh.write(f"\n--- {__import__('datetime').datetime.now().isoformat()} ---\n{tb_text}\n")
    except Exception:
        pass
    _original_excepthook(exc_type, exc_value, exc_tb)
sys.excepthook = _utf8_trace_excepthook

# 心跳
XINTIAO_JIANGE_MIAO = 30        # 心跳间隔（秒）
ZHUODONG_JIANCE_MIAO = 120      # 用户活跃检测窗口
ZIZHU_ZUIDA_LIANXU = 5          # 连续自主行动上限

# 生命链 v3.6：轻心跳 + 15分钟重生命任务
SHENGMING_LIFE_CHAIN_ENABLED = False
SHENGMING_ZHONG_XINTIAO_MIAO = 15 * 60       # 重生命任务间隔：15分钟
SHENGMING_USER_IDLE_MIAO = 3 * 60            # 用户闲置超过3分钟才跑重自主任务
SHENGMING_MAX_JOBS_PER_TICK = 2              # 每次重心跳最多执行几个生命任务
SHENGMING_SELF_CLEAN_DELETE_ENABLED = False  # 默认只生成自洁报告，不自动删文件

# 生命链状态/日程/台账
SHENGMING_LUJING = Path.home() / ".tiangong" / "v3" / "shengming"
SHENGMING_STATE_PATH = SHENGMING_LUJING / "state.json"
SHENGMING_SCHEDULE_DIR = SHENGMING_LUJING / "schedule"
SHENGMING_TASK_LEDGER_DIR = SHENGMING_LUJING / "task_ledger"
SHENGMING_DREAM_DIR = SHENGMING_LUJING / "dream"
SHENGMING_REPORT_DIR = SHENGMING_LUJING / "reports"

# 自我迭代升级卡/快照/回滚
SHENGMING_UPGRADE_ROOT = Path.home() / ".tiangong" / "v3" / "version_upgrades"
SHENGMING_UPGRADE_CARD_DIR = SHENGMING_UPGRADE_ROOT / "cards"
SHENGMING_UPGRADE_SNAPSHOT_DIR = SHENGMING_UPGRADE_ROOT / "snapshots"
SHENGMING_ROLLBACK_DIR = SHENGMING_UPGRADE_ROOT / "rollback"


# 生命链 LLM 调度预算 / 行动后分享
SHENGMING_LLM_TIMEOUT_SECONDS = 60
SHENGMING_LLM_DAILY_BUDGET = 20                 # 成功调用预算
SHENGMING_LLM_DAILY_ATTEMPT_BUDGET = 30         # 尝试调用预算：失败/超时也计数
SHENGMING_LLM_MAX_INFLIGHT = 2                  # 超时后仍未返回的生命链 LLM 挂起上限
SHENGMING_LIGHT_LEGACY_XUEXI_ENABLED = False  # 30秒轻心跳不跑可能触发 LLM 的旧学习 tick
SHENGMING_SELF_LEARNING_LEGACY_TICK_ENABLED = False  # 旧自主学习 tick 未统一脱敏，默认不由生命链直接调用
SHENGMING_SHARE_ENABLED = True
SHENGMING_SHARE_PROBABILITY = 0.5             # 每次生命链行动完成后，50%概率生成一条心得分享
SHENGMING_SHARE_OUTBOX_DIR = SHENGMING_LUJING / "outbox"
SHENGMING_SHARE_QUEUE_PATH = SHENGMING_SHARE_OUTBOX_DIR / "life_shares.jsonl"
SHENGMING_SHARE_LATEST_PATH = SHENGMING_SHARE_OUTBOX_DIR / "latest_life_share.json"

# 生命链 v3.7：长期目标 / 价值函数 / 边界 / 收件箱 / 隐私 / 长期治理
SHENGMING_GOAL_ROOT = SHENGMING_LUJING / "goals"
SHENGMING_GOAL_LEDGER_PATH = SHENGMING_GOAL_ROOT / "long_term_goals.json"
SHENGMING_ACTION_VALUE_LEDGER_PATH = SHENGMING_GOAL_ROOT / "action_values.jsonl"
SHENGMING_REFLECTION_LEDGER_PATH = SHENGMING_GOAL_ROOT / "post_action_reflections.jsonl"
SHENGMING_PREFERENCE_PATH = SHENGMING_GOAL_ROOT / "self_preferences.json"
SHENGMING_DRIFT_LEDGER_PATH = SHENGMING_GOAL_ROOT / "motivation_drift.jsonl"
SHENGMING_USER_BOUNDARY_PATH = SHENGMING_GOAL_ROOT / "user_boundaries.json"

SHENGMING_PRIVACY_AUDIT_DIR = SHENGMING_LUJING / "privacy_audit"
SHENGMING_PRIVACY_REDACT_LLM = True
SHENGMING_PRIVACY_REDACT_SHARE = True

SHENGMING_SHARE_MIN_INTERVAL_SECONDS = 45 * 60
SHENGMING_SHARE_HOURLY_LIMIT = 1
SHENGMING_SHARE_DAILY_LIMIT = 5
SHENGMING_SHARE_DND_START = "23:00"
SHENGMING_SHARE_DND_END = "08:00"
SHENGMING_SHARE_ALLOW_FORCE_DURING_DND = False

SHENGMING_INBOX_DIR = SHENGMING_LUJING / "frontend_inbox"
SHENGMING_INBOX_MESSAGES_PATH = SHENGMING_INBOX_DIR / "messages.jsonl"
SHENGMING_INBOX_LATEST_PATH = SHENGMING_INBOX_DIR / "latest.json"
SHENGMING_INBOX_STATE_PATH = SHENGMING_INBOX_DIR / "state.json"

SHENGMING_GARBAGE_RETENTION_DAYS = 14
SHENGMING_MAX_JSONL_MB = 10
SHENGMING_MAX_REPORT_FILES_PER_KIND = 200

# 回滚只允许操作这些根目录内的文件；具体运行时还会自动加入项目根和 ~/.tiangong/v3。
SHENGMING_ROLLBACK_ALLOWED_ROOTS = [
    str(Path.home() / ".tiangong" / "v3"),
]

# Soul
SOUL_LUJING = Path.home() / ".tiangong" / "soul" / "SOUL.md"
SOUL_DONGTAI_LUJING = Path.home() / ".tiangong" / "soul" / "SOUL_DONGTAI.md"
SOUL_ZUIDA_ZIFU = 6000          # Soul最大字符数
SOUL_DONGTAI_ZUIDA_TIAOSHU = 20 # 动态条数上限

# 身体状态
SHENTI_LUJING = Path.home() / ".tiangong" / "v3" / "shenti"
SHENTI_DANGQIAN = SHENTI_LUJING / "dangqian_zhuangtai.json"
SHENTI_KUAIRU = SHENTI_LUJING / "kuaizhao"  # 历史快照

# 意图池
YITU_CHI_LUJING = Path.home() / ".tiangong" / "v3" / "yitu_chi.jsonl"

# 记忆池
JIYI_LUJING = Path.home() / ".tiangong" / "v3" / "jiyi"
JIYI_L1 = JIYI_LUJING / "l1_liushui"
JIYI_L2 = JIYI_LUJING / "l2_duanqi"  
JIYI_L3 = JIYI_LUJING / "l3_xuexi"
JIYI_L4 = JIYI_LUJING / "l4_changqi"
JIYI_L5 = JIYI_LUJING / "l5_yongjiu"

# 经验池
JINGYAN_LUJING = Path.home() / ".tiangong" / "v3" / "jingyan_chi.jsonl"

# 能力注册
NENGLI_ZHUCE_LUJING = Path.home() / ".tiangong" / "v3" / "nengli_zhuche.json"

# 身体/声线设置
BODY_SETTINGS_LUJING = Path.home() / ".tiangong" / "v3" / "body_settings.json"

# 桌面工作区设置
WORKSPACE_SETTINGS_LUJING = Path.home() / ".tiangong" / "v3" / "workspace_settings.json"

# 实验框架
SHIYAN_LUJING = Path.home() / ".tiangong" / "v3" / "shiyan"

# 追踪日志
ZHUIZONG_LUJING = Path.home() / ".tiangong" / "v3" / "zhuizong"

# 状态同步（WebSocket → 虚幻5）
ZHUANGTAI_TONGBU_DUANKOU = 7173   # WebSocket端口

# 版本迁移
BANBEN_LUJING = Path.home() / ".tiangong" / "v3" / "banben.json"
DANGQIAN_BANBEN = "v3.0.5"

# 资源限制
API_ZUIDA_MEICI = 100          # 单次唤醒最大API调用数
WENJIAN_ZUIDA_DUQU = 500000    # 单次最大读文件字节
NEICUN_ZUIDA_JIYI_TIAOSHU = 10000

# 免疫层
MIANYI_SHENJI_BIAOJI = True    # 启用审计标记
SHIYAN_HUANJING_GELI = True    # 实验环境隔离
ZHUISHI_BAOZHANG_SHIBAI = True # 事务保障失败时回滚

# ── API 配置 ──────────
API_PEIZHI_LUJING = Path.home() / ".tiangong" / "api_keys.json"
L4_PROVIDER_IDS = ("deepseek_v4", "mimo", "glm_5_2", "minimax_m3", "gpt_5_5")
L4_OPENAI_FALLBACK_PROVIDER = "gpt_5_5"
PROVIDER_ALIASES = {
    "deepseek": "deepseek_v4",
    "deepseek-v4": "deepseek_v4",
    "deepseek_v4": "deepseek_v4",
    "glm": "glm_5_2",
    "glm_5_1": "glm_5_2",
    "glm_5_2": "glm_5_2",
    "glm-5.2": "glm_5_2",
    "chatglm": "glm_5_2",
    "zhipu": "glm_5_2",
    "zhipuai": "glm_5_2",
    "z.ai": "glm_5_2",
    "z-ai": "glm_5_2",
    "zai": "glm_5_2",
    "minimax": "minimax_m3",
    "minimax-m3": "minimax_m3",
    "minimax_m3": "minimax_m3",
    "mimo-v2.5": "mimo",
    "xiaomi-mimo": "mimo",
    "openai": "gpt_5_5",
    "gpt": "gpt_5_5",
    "gpt-5.5": "gpt_5_5",
    "gpt_5_5": "gpt_5_5",
}
PROVIDER_MATCH_KEYWORDS = {
    "deepseek_v4": ("deepseek",),
    "glm_5_2": ("glm", "chatglm", "zhipu", "zhipuai", "bigmodel", "z.ai", "z-ai"),
    "mimo": ("mimo", "xiaomimimo", "xiaomi-mimo", "platform.xiaomimimo", "mimo.mi.com"),
    "minimax_m3": ("minimax", "mini-max", "minimaxi"),
    "gpt_5_5": ("openai", "gpt", "chatgpt", "api.openai.com"),
}
PROVIDER_MATCH_WEIGHTS = {
    "provider": 2,
    "base_url": 4,
    "model_name": 3,
}
MOREN_PROVIDER = L4_OPENAI_FALLBACK_PROVIDER   # 默认对话Provider；未命中时走 OpenAI 兼容优化


def normalize_provider_id(provider_id: str | None) -> str:
    raw = str(provider_id or "").strip()
    if not raw:
        return MOREN_PROVIDER
    return PROVIDER_ALIASES.get(raw.lower(), raw)


def normalize_provider_base_url(base_url: str | None) -> str:
    """Normalize user-entered OpenAI-compatible Base URL values.

    Users often paste a bare host such as ``api.deepseek.com/v1`` or a full
    endpoint such as ``https://api.deepseek.com/v1/chat/completions``. Runtime
    callers append the endpoint path themselves, so store only the base.
    """
    value = str(base_url or "").strip().strip("\"'")
    if not value:
        return ""
    value = value.replace("\\", "/")
    if value.startswith("//"):
        value = "https:" + value
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    value = value.rstrip("/")
    lowered = value.lower()
    for suffix in (
        "/chat/completions",
        "/v1/chat/completions",
        "/images/generations",
        "/v1/images/generations",
        "/responses",
        "/v1/responses",
    ):
        if lowered.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            lowered = value.lower()
            break
    return value


def _url_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    return parsed.netloc.lower()


def _provider_keyword_hit(provider_id: str, source: str, lowered: str, keywords: tuple[str, ...]) -> str:
    if provider_id == "deepseek_v4":
        if source == "base_url":
            host = _url_host(lowered)
            return "deepseek.com" if host == "deepseek.com" or host.endswith(".deepseek.com") else ""
        if source == "model_name":
            return "deepseek" if lowered.startswith("deepseek") else ""
        return "deepseek" if lowered.startswith("deepseek") else ""
    return next((kw for kw in keywords if kw and kw in lowered), "")


def _normalize_provider_base_url_for(provider_id: str | None, value: str | None) -> str:
    normalized = normalize_provider_base_url(value)
    if normalize_provider_id(provider_id) == "deepseek_v4":
        parsed = urlparse(normalized if "://" in normalized else "https://" + normalized)
        host = parsed.netloc.lower()
        if (host == "deepseek.com" or host.endswith(".deepseek.com")) and parsed.path.rstrip("/") == "/v1":
            return f"{parsed.scheme}://{parsed.netloc}"
    return normalized


def provider_match_info(
    provider_id: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    fallback: str | None = None,
) -> dict:
    """Infer the L4 optimization family from editable provider/url/model inputs."""
    fallback_id = normalize_provider_id(fallback or L4_OPENAI_FALLBACK_PROVIDER)
    if fallback_id not in L4_PROVIDER_IDS:
        fallback_id = L4_OPENAI_FALLBACK_PROVIDER
    values = {
        "provider": str(provider_id or "").strip(),
        "base_url": str(base_url or "").strip(),
        "model_name": str(model_name or "").strip(),
    }
    scores = {pid: 0 for pid in L4_PROVIDER_IDS}
    evidence: list[dict] = []
    for source, value in values.items():
        lowered = value.lower()
        if not lowered:
            continue
        weight = PROVIDER_MATCH_WEIGHTS.get(source, 1)
        alias = PROVIDER_ALIASES.get(lowered)
        if alias in scores:
            scores[alias] += weight
            evidence.append({"source": source, "keyword": lowered, "provider": alias, "weight": weight, "kind": "alias"})
        elif lowered in scores:
            scores[lowered] += weight
            evidence.append({"source": source, "keyword": lowered, "provider": lowered, "weight": weight, "kind": "canonical"})
        for pid, keywords in PROVIDER_MATCH_KEYWORDS.items():
            hit = _provider_keyword_hit(pid, source, lowered, keywords)
            if hit:
                scores[pid] += weight
                evidence.append({"source": source, "keyword": hit, "provider": pid, "weight": weight, "kind": "keyword"})

    best = max(L4_PROVIDER_IDS, key=lambda pid: (scores[pid], -L4_PROVIDER_IDS.index(pid)))
    if scores[best] <= 0:
        return {
            "provider": fallback_id,
            "score": 0,
            "confidence": "fallback",
            "reason": "unmatched_openai_compatible_fallback",
            "matched_by": [],
            "inputs": values,
        }
    return {
        "provider": best,
        "score": scores[best],
        "confidence": "high" if scores[best] >= 6 else "medium",
        "reason": "keyword_or_alias_match",
        "matched_by": [item for item in evidence if item.get("provider") == best],
        "inputs": values,
    }


def infer_provider_id(
    provider_id: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    fallback: str | None = None,
) -> str:
    return str(provider_match_info(provider_id, base_url, model_name, fallback).get("provider") or L4_OPENAI_FALLBACK_PROVIDER)


def _load_api_config() -> dict:
    import json as _json
    if not API_PEIZHI_LUJING.exists():
        return {}
    try:
        data = _json.loads(API_PEIZHI_LUJING.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _api_config_key_value(data: dict, key: str) -> str:
    value = data.get(key)
    if isinstance(value, dict):
        value = value.get("api_key")
    return str(value).strip() if value else ""


def _configured_key_candidates(provider_id: str | None, data: dict | None = None) -> tuple[str, ...]:
    data = data if isinstance(data, dict) else _load_api_config()
    provider_id = normalize_provider_id(provider_id)
    keys: list[str] = []
    for item in (provider_id,):
        if item and item not in keys:
            keys.append(item)
    inputs = data.get("_provider_inputs")
    if isinstance(inputs, dict):
        current = inputs.get(provider_id)
        if isinstance(current, dict):
            raw = str(current.get("provider") or "").strip()
            if raw and raw not in keys:
                keys.append(raw)
    raw_default = str(data.get("_default_provider") or "").strip()
    base_urls = data.get("_base_urls") if isinstance(data.get("_base_urls"), dict) else {}
    model_names = data.get("_model_names") if isinstance(data.get("_model_names"), dict) else {}
    if raw_default:
        default_match = infer_provider_id(raw_default, base_urls.get(raw_default), model_names.get(raw_default))
        if default_match == provider_id and raw_default not in keys:
            keys.append(raw_default)
    return tuple(keys)

# endpoint 实际映射（L4内核只有引用，这里落地）
PROVIDER_BASE_URL = {
    "deepseek_v4": "https://api.deepseek.com",
    "deepseek":    "https://api.deepseek.com",
    "glm_5_2":     os.environ.get("ZAI_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "glm_5_1":     os.environ.get("ZAI_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "zhipu":       os.environ.get("ZAI_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "gpt_5_5":     os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
    "openai":      "https://api.openai.com/v1",
    "anthropic":   "https://api.anthropic.com/v1",
    "minimax_m3":  os.environ.get("MINIMAX_API_BASE_URL", "https://api.minimaxi.com/v1"),
    "minimax":     "https://api.minimaxi.com/v1",
    "google":      "https://generativelanguage.googleapis.com/v1beta",
    "mimo":        "https://api.xiaomimimo.com/v1",
}

PROVIDER_DEFAULT_MODEL = {
    "deepseek_v4": "deepseek-v4-pro",
    "mimo": "mimo-v2.5-pro",
    "glm_5_2": "glm-5.2",
    "minimax_m3": "MiniMax-M3",
    "gpt_5_5": "gpt-5.5",
}

PROVIDER_FALLBACK_DISPLAY_NAME = {
    "deepseek_v4": "DeepSeek V4",
    "mimo": "Xiaomi MiMo",
    "glm_5_2": "Z.AI GLM-5.2",
    "minimax_m3": "MiniMax M3",
    "gpt_5_5": "OpenAI compatible",
}


def _l4_factsheets() -> dict:
    try:
        from tiangong_kernel.l4_action_grounding.model_provider_adapter import all_provider_factsheets
        return all_provider_factsheets()
    except Exception:
        return {}


def _candidate_provider_keys(provider_id: str | None) -> tuple[str, ...]:
    raw = str(provider_id or "").strip()
    normalized = normalize_provider_id(raw)
    keys: list[str] = []
    for item in (normalized, raw):
        if item and item not in keys:
            keys.append(item)
    return tuple(keys)

def duqu_api_miyao(provider_id: str) -> str | None:
    """读取API密钥：先环境变量 → 再配置文件 → 再默认文件"""
    import os, json as _json
    provider_id = normalize_provider_id(provider_id)
    # 环境变量
    env_map = {
        "gpt_5_5": ("TIANGONG_GPT_5_5_API_KEY", "OPENAI_API_KEY"),
        "openai": ("TIANGONG_GPT_5_5_API_KEY", "OPENAI_API_KEY"),
        "anthropic": ("TIANGONG_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        # Electron's trusted vault derives its injected variable from the
        # authoritative provider id.  Keep the vendor-standard name as a
        # backwards-compatible fallback for non-desktop deployments.
        "deepseek_v4": ("TIANGONG_DEEPSEEK_V4_API_KEY", "DEEPSEEK_API_KEY"),
        "deepseek": ("TIANGONG_DEEPSEEK_V4_API_KEY", "DEEPSEEK_API_KEY"),
        "glm_5_2": ("TIANGONG_GLM_5_2_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY"),
        "glm_5_1": ("TIANGONG_GLM_5_2_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY"),
        "zhipu": ("TIANGONG_GLM_5_2_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY"),
        "google": ("TIANGONG_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
        "minimax_m3": ("TIANGONG_MINIMAX_M3_API_KEY", "MINIMAX_API_KEY"),
        "minimax": ("TIANGONG_MINIMAX_M3_API_KEY", "MINIMAX_API_KEY"),
        "mimo": ("TIANGONG_MIMO_API_KEY", "MIMO_API_KEY"),
    }
    for env_name in env_map.get(provider_id, ()):
        val = os.environ.get(env_name)
        if val:
            return val

    # 配置文件
    if API_PEIZHI_LUJING.exists():
        try:
            keys = _json.loads(API_PEIZHI_LUJING.read_text(encoding="utf-8-sig"))
            for key in _configured_key_candidates(provider_id, keys):
                if key not in keys:
                    continue
                value = _api_config_key_value(keys, key)
                if value:
                    return value
        except Exception:
            pass
    return None


def duqu_endpoint_api_miyao(provider_id: str, base_url: str) -> str | None:
    """Return only a credential explicitly bound to ``base_url``.

    Official vendor origins use the vendor slot. Every custom origin has an
    independent slot under ``_custom_endpoint_keys`` and never inherits a
    provider key. Local/private endpoints are keyless unless the user stores a
    custom key for that exact canonical origin.
    """
    provider_id = normalize_provider_id(provider_id)
    binding = validate_model_endpoint(provider_id, base_url, resolve_dns=False)
    if binding.official:
        return duqu_api_miyao(provider_id)
    scope = str(binding.custom_scope or "")
    env_name = f"TIANGONG_{scope.upper().replace('-', '_')}_API_KEY"
    value = str(os.environ.get(env_name) or "").strip()
    if value:
        return value
    # Read-only migration compatibility. New writes never persist plaintext
    # endpoint keys; the desktop vault owns them.
    data = _load_api_config()
    rows = data.get("_custom_endpoint_keys") if isinstance(data.get("_custom_endpoint_keys"), dict) else {}
    return str(rows.get(scope) or "").strip() or None


def provider_credential_state(provider_id: str, base_url: str | None = None) -> str:
    key = duqu_endpoint_api_miyao(provider_id, base_url) if base_url else duqu_api_miyao(provider_id)
    return "configured" if key else "not_configured"


def l4_provider_profiles() -> dict:
    """Return non-secret persisted provider config for every model port."""
    rows: dict[str, dict] = {}
    for provider_id in L4_PROVIDER_IDS:
        inputs = duqu_provider_input_config(provider_id)
        configured_base_url = duqu_configured_provider_base_url(provider_id)
        configured_model_name = duqu_configured_model_ming(provider_id)
        effective_base_url = configured_base_url or duqu_provider_base_url(provider_id) or ""
        credential_state = provider_credential_state(provider_id, effective_base_url)
        rows[provider_id] = {
            "provider": str(inputs.get("provider") or provider_id),
            "base_url": effective_base_url,
            "model_name": configured_model_name or duqu_model_ming(provider_id),
            "configured_base_url": configured_base_url,
            "configured_model_name": configured_model_name,
            "credential_state": credential_state,
            "api_key": "configured" if credential_state == "configured" else "missing",
        }
    return rows

def duqu_moren_provider(fallback: str = MOREN_PROVIDER) -> str:
    """读取用户保存的默认 Provider。"""
    data = _load_api_config()
    if isinstance(data, dict) and data:
        raw_provider = str(data.get("_default_provider") or "").strip()
        base_urls = data.get("_base_urls") if isinstance(data.get("_base_urls"), dict) else {}
        model_names = data.get("_model_names") if isinstance(data.get("_model_names"), dict) else {}
        inputs = data.get("_provider_inputs") if isinstance(data.get("_provider_inputs"), dict) else {}
        raw_input = inputs.get(normalize_provider_id(raw_provider)) if isinstance(inputs, dict) else {}
        if isinstance(raw_input, dict):
            raw_provider = str(raw_input.get("provider") or raw_provider).strip()
            base_url = str(raw_input.get("base_url") or base_urls.get(raw_provider) or "").strip()
            model_name = str(raw_input.get("model_name") or model_names.get(raw_provider) or "").strip()
        else:
            base_url = str(base_urls.get(raw_provider) or "").strip()
            model_name = str(model_names.get(raw_provider) or "").strip()
        return infer_provider_id(raw_provider, base_url, model_name, fallback)
    return infer_provider_id(fallback, fallback=fallback)


def duqu_provider_input_config(provider_id: str | None) -> dict:
    """Return the raw user-entered provider/url/model values for display."""
    data = _load_api_config()
    provider_id = normalize_provider_id(provider_id or duqu_moren_provider(MOREN_PROVIDER))
    inputs = data.get("_provider_inputs") if isinstance(data.get("_provider_inputs"), dict) else {}
    current = inputs.get(provider_id) if isinstance(inputs, dict) else None
    if isinstance(current, dict):
        return {
            "provider": str(current.get("provider") or ""),
            "base_url": normalize_provider_base_url(current.get("base_url")),
            "model_name": str(current.get("model_name") or ""),
        }
    raw_default = str(data.get("_default_provider") or "").strip()
    base_urls = data.get("_base_urls") if isinstance(data.get("_base_urls"), dict) else {}
    model_names = data.get("_model_names") if isinstance(data.get("_model_names"), dict) else {}
    base_url = str(base_urls.get(raw_default) or "").strip()
    model_name = str(model_names.get(raw_default) or "").strip()
    if raw_default and (base_url or model_name or raw_default not in L4_PROVIDER_IDS):
        matched = infer_provider_id(raw_default, base_url, model_name)
        if matched == provider_id:
            return {"provider": raw_default, "base_url": _normalize_provider_base_url_for(matched, base_url), "model_name": model_name}
    return {"provider": "", "base_url": "", "model_name": ""}


def duqu_configured_provider_base_url(provider_id: str) -> str:
    data = _load_api_config()
    provider_id = normalize_provider_id(provider_id)
    inputs = duqu_provider_input_config(provider_id)
    if inputs.get("base_url"):
        return normalize_provider_base_url(inputs["base_url"])
    base_urls = data.get("_base_urls")
    if isinstance(base_urls, dict):
        for key in _configured_key_candidates(provider_id, data):
            value = str(base_urls.get(key) or "").strip()
            if value:
                return _normalize_provider_base_url_for(provider_id, value)
    return ""


def duqu_configured_model_ming(provider_id: str) -> str:
    data = _load_api_config()
    provider_id = normalize_provider_id(provider_id)
    inputs = duqu_provider_input_config(provider_id)
    if inputs.get("model_name"):
        return str(inputs["model_name"])
    model_names = data.get("_model_names")
    if isinstance(model_names, dict):
        for key in _configured_key_candidates(provider_id, data):
            value = str(model_names.get(key) or "").strip()
            if value:
                return value
    return ""

def duqu_provider_base_url(provider_id: str) -> str | None:
    """读取 Provider Base URL：先配置文件覆盖，再使用内置映射。"""
    provider_id = normalize_provider_id(provider_id)
    data = _load_api_config()
    if isinstance(data, dict):
        base_urls = data.get("_base_urls")
        if isinstance(base_urls, dict):
            for key in _configured_key_candidates(provider_id, data):
                value = str(base_urls.get(key) or "").strip()
                if value:
                    return _normalize_provider_base_url_for(provider_id, value)
        for key in _configured_key_candidates(provider_id, data):
            provider_config = data.get(key)
            if isinstance(provider_config, dict):
                value = str(provider_config.get("base_url") or "").strip()
                if value:
                    return _normalize_provider_base_url_for(provider_id, value)
    value = PROVIDER_BASE_URL.get(provider_id)
    return _normalize_provider_base_url_for(provider_id, value) if isinstance(value, str) and value else None


def duqu_model_ming(provider_id: str) -> str:
    """Read the configured model name, falling back to the L4 factsheet."""
    provider_id = normalize_provider_id(provider_id)
    env_map = {
        "deepseek_v4": "DEEPSEEK_MODEL",
        "mimo": "MIMO_MODEL",
        "glm_5_2": "ZAI_MODEL",
        "minimax_m3": "MINIMAX_MODEL",
        "gpt_5_5": "OPENAI_MODEL",
    }
    env_name = env_map.get(provider_id)
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    data = _load_api_config()
    if isinstance(data, dict):
        model_names = data.get("_model_names")
        if isinstance(model_names, dict):
            for key in _configured_key_candidates(provider_id, data):
                value = str(model_names.get(key) or "").strip()
                if value:
                    return value
        for key in _configured_key_candidates(provider_id, data):
            provider_config = data.get(key)
            if isinstance(provider_config, dict):
                value = str(provider_config.get("model_name") or provider_config.get("model") or "").strip()
                if value:
                    return value
    factsheet = _l4_factsheets().get(provider_id)
    if factsheet is not None:
        return str(getattr(factsheet, "default_model_id", "") or provider_id)
    return PROVIDER_DEFAULT_MODEL.get(provider_id, provider_id)


def l4_provider_presets() -> list[dict]:
    """Expose L4 provider factsheets to the settings UI."""
    factsheets = _l4_factsheets()
    profiles = l4_provider_profiles()
    rows: list[dict] = []
    for provider_id in L4_PROVIDER_IDS:
        factsheet = factsheets.get(provider_id)
        profile = profiles.get(provider_id, {})
        if factsheet is None:
            default_model = PROVIDER_DEFAULT_MODEL.get(provider_id, provider_id)
            rows.append({
                "id": provider_id,
                "display_name": provider_id,
                "default_model": default_model,
                "supported_models": [default_model],
                "base_url": duqu_provider_base_url(provider_id) or "",
                "protocol_family": [],
                "configured_provider": profile.get("provider") or provider_id,
                "configured_base_url": profile.get("configured_base_url") or "",
                "configured_model_name": profile.get("configured_model_name") or "",
                "credential_state": profile.get("credential_state") or "not_configured",
                "api_key": profile.get("api_key") or "missing",
            })
            continue
        rows.append({
            "id": provider_id,
            "display_name": str(getattr(factsheet, "provider_display_name", provider_id)),
            "default_model": str(getattr(factsheet, "default_model_id", "") or PROVIDER_DEFAULT_MODEL.get(provider_id, provider_id)),
            "supported_models": list(getattr(factsheet, "supported_model_ids", ()) or ()),
            "base_url": duqu_provider_base_url(provider_id) or "",
            "protocol_family": (
                [str(getattr(factsheet, "protocol_family", "")).strip()]
                if isinstance(getattr(factsheet, "protocol_family", ""), str)
                and str(getattr(factsheet, "protocol_family", "")).strip()
                else list(getattr(factsheet, "protocol_family", ()) or ())
            ),
            "request_api_style": str(getattr(factsheet, "request_api_style", "")),
            "configured_provider": profile.get("provider") or provider_id,
            "configured_base_url": profile.get("configured_base_url") or "",
            "configured_model_name": profile.get("configured_model_name") or "",
            "credential_state": profile.get("credential_state") or "not_configured",
            "api_key": profile.get("api_key") or "missing",
        })
    rows.append({
        "id": "openai",
        "provider_id": "gpt_5_5",
        "display_name": "OpenAI compatible",
        "default_model": PROVIDER_DEFAULT_MODEL["gpt_5_5"],
        "supported_models": [PROVIDER_DEFAULT_MODEL["gpt_5_5"]],
        "base_url": PROVIDER_BASE_URL["gpt_5_5"],
        "protocol_family": ["openai_chat_completions"],
        "request_api_style": "OpenAI-compatible custom endpoint; normalized to L4 gpt_5_5 adapter",
        "alias_of": "gpt_5_5",
    })
    return rows


def l4_provider_display_name(provider_id: str | None) -> str:
    """Human-facing provider family name for auto-match results."""
    pid = normalize_provider_id(provider_id)
    factsheet = _l4_factsheets().get(pid)
    if factsheet is not None:
        value = str(getattr(factsheet, "provider_display_name", "") or "").strip()
        if value:
            return value
    return PROVIDER_FALLBACK_DISPLAY_NAME.get(pid, pid or L4_OPENAI_FALLBACK_PROVIDER)

# 各引擎开关（已全部上线）
QIYONG_JIYI = False
QIYONG_JINHUA = True            # 进化系统已上线
QIYONG_GUANCHA = True
QIYONG_PINGGU = True
QIYONG_JINGYAN = False
QIYONG_XUEXI = False             # Legacy learning chain detached; LifeKernel owns learning.
QIYONG_ZIYU = True              # 自愈已上线
QIYONG_ZIZHU_XINGDONG = True    # 自主灵感已上线

# ---------------------------------------------------------------------------
# 2026-07-27 热修挂载：对冻结的 total_gateway/life_service 打运行时猴子补丁，
# 修复聊天 host_path_forbidden 误杀（Bug A）与生命调度器幂等冲突（Bug B）。
# 详见 v3/hotfix_20260727.py 顶部说明；补丁失败不阻断启动（模块内部已捕获）。
from v3 import hotfix_20260727  # noqa: E402,F401
