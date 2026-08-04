"""Source-owned fixes for life scheduling, memory recall, and learning production."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import math
import os
import re
import sqlite3
import sys


CHINA_STANDARD_TIME = timezone(timedelta(hours=8), "Asia/Shanghai")
_PATCH_MARKER = "__tiangong_life_runtime_fixes_v3__"
_CREDENTIAL_PATCH_MARKER = "__tiangong_gateway_action_intent_bridge_v2__"


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


_AFFECT_DEFAULTS = {
    "novelty": 0.0,
    "goal_congruence": 0.0,
    "threat": 0.0,
    "loss": 0.0,
    "obstruction": 0.0,
    "certainty": 0.5,
    "controllability": 0.5,
    "social_warmth": 0.5,
    "social_trust": 0.5,
    "intensity": 0.5,
}
_AFFECT_FIELDS = frozenset(_AFFECT_DEFAULTS)
_INTEGER_SETTING_FIELDS = frozenset(
    {
        "share_hourly_limit",
        "share_daily_limit",
        "llm_daily_budget",
        "llm_daily_attempt_budget",
        "llm_timeout_seconds",
        "heavy_interval_minutes",
        "max_jobs_per_tick",
        "user_idle_seconds",
        "daily_plan_hour",
        "dream_hour",
        "dream_catchup_until_hour",
    }
)


def _assert_finite_tree(value):
    """Reject IEEE non-finite values before they enter a signed fact chain."""

    pending = [value]
    visited = 0
    while pending:
        visited += 1
        if visited > 200000:
            raise ValueError("life state exceeds the bounded validation graph")
        item = pending.pop()
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite numbers are not valid life state")
            continue
        if isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
            continue
        if isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)


def _repair_nonfinite_projection(value, depth=0):
    """Make an old derived projection finite without rewriting signed history."""

    if depth > 32:
        return None
    if isinstance(value, Mapping):
        return {
            key: _repair_nonfinite_projection(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_repair_nonfinite_projection(item, depth + 1) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return 0.0
    return value


def _strict_finite_number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number between {low} and {high}")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < low or parsed > high:
        raise ValueError(f"{field} must be a finite number between {low} and {high}")
    return parsed


def _normalize_optional_timestamp(value, field):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _normalized_appraisal(appraisal):
    if not isinstance(appraisal, Mapping):
        raise ValueError("Affective appraisal must be an object")
    unknown = set(appraisal) - _AFFECT_FIELDS - {"explanation"}
    if unknown:
        raise ValueError(f"Unknown affect dimensions: {', '.join(sorted(map(str, unknown)))}")
    if not (set(appraisal) & _AFFECT_FIELDS):
        raise ValueError("Affective appraisal requires at least one dimension")
    normalized = dict(_AFFECT_DEFAULTS)
    for field in _AFFECT_FIELDS:
        if field not in appraisal:
            continue
        low = -1.0 if field == "goal_congruence" else 0.0
        normalized[field] = _strict_finite_number(appraisal[field], field, low, 1.0)
    explanation = appraisal.get("explanation", [])
    if explanation is None:
        explanation = []
    if not isinstance(explanation, (list, tuple)) or len(explanation) > 8:
        raise ValueError("Affective explanation must be an array with at most 8 items")
    if any(not isinstance(item, str) or len(item) > 240 for item in explanation):
        raise ValueError("Affective explanation items must be strings up to 240 characters")
    normalized["explanation"] = [item.strip() for item in explanation if item.strip()]
    return normalized


def _monotonic_affect_time(state, at, parse_time):
    previous_text = str(
        _mapping(state).get("last_decay_at")
        or _mapping(state).get("updated_at")
        or ""
    ).strip()
    if not previous_text:
        return at
    try:
        return previous_text if parse_time(at) < parse_time(previous_text) else at
    except (TypeError, ValueError, OverflowError):
        return at


def _china_business_now(value):
    if not isinstance(value, datetime):
        return datetime.now(CHINA_STANDARD_TIME)
    if value.tzinfo is None:
        # A naive value comes from the host clock.  Resolve it through the host
        # timezone first, then convert to the one product-wide business zone.
        value = value.astimezone()
    return value.astimezone(CHINA_STANDARD_TIME)


def _budget_date(system):
    try:
        panel = _mapping(system.get_panel())
        return str(_mapping(panel.get("budget")).get("date") or "").strip()
    except Exception:
        return ""


def _verified_action(result):
    result = _mapping(result)
    return bool(
        result.get("ok")
        and result.get("action_fact_verified")
        and str(result.get("terminal_status") or "").lower() == "success"
        and str(result.get("execution_event_id") or "").strip()
    )


def _plain_summary(value, limit=1200):
    text = re.sub(
        r"<think\b[^>]*>.*?</think>",
        "",
        str(value or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    return " ".join(text.split())[:limit]


def _redact_secret_text(value):
    """Remove credential material while leaving ordinary memory prose intact."""

    text = str(value or "")
    text = re.sub(
        r'''(?i)(["']?\b(?:api[_-]?key|authorization|password|secret|token|access[_-]?token|refresh[_-]?token|client[_-]?secret|cookie|session)\b["']?\s*[:=]\s*)(?:bearer\s+(?:\[REDACTED\]|[a-z0-9._~+/=-]+)|\[REDACTED\]|"[^"]*"|'[^']*'|[^\s,;}\]]+)''',
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(bearer)\s+(?!\[REDACTED\])([a-z0-9._~+/=-]+)",
        r"\1 [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(https?://[^\s/:@]+:)[^\s/@]+(@)",
        r"\1[REDACTED]\2",
        text,
    )
    text = re.sub(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", text)


_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "cookie",
        "session",
    }
)


def _redact_memory_value(value, depth=0):
    """Project decrypted memory without ever returning stored credentials."""

    if depth > 12:
        return None
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            normalized = str(key or "").strip().lower().replace("-", "_")
            result[key] = (
                "[REDACTED]"
                if normalized in _SECRET_FIELD_NAMES
                else _redact_memory_value(item, depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_memory_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bool, int)) or value is None:
        return value
    return _redact_secret_text(value)


def _model_safe_memory_row(item):
    projected = dict(item)
    if "content" in projected:
        projected["content"] = _redact_memory_value(projected.get("content"))
    return projected


def _scrub_memory_projection(system):
    """Remove legacy credential plaintext from the local searchable projection."""

    try:
        binding = _mapping(system.identities.active())
        life_root = str(binding.get("root") or "").strip()
        if not life_root:
            return
        database = os.path.join(life_root, "memory", "memory_index.sqlite3")
        scrubbed = getattr(system, "_credential_projection_scrubbed_paths", set())
        if database in scrubbed or not os.path.isfile(database):
            return
        changed = False
        connection = sqlite3.connect(database, timeout=5.0)
        try:
            rows = connection.execute(
                "SELECT memory_id, content_json, search_text, memory_type FROM memories"
            ).fetchall()
            for memory_id, content_json, search_text, memory_type in rows:
                try:
                    decoded = json.loads(content_json)
                    safe_content = json.dumps(
                        _redact_memory_value(decoded),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    safe_content = _redact_secret_text(content_json)
                safe_search = _redact_secret_text(search_text)
                if safe_content == content_json and safe_search == search_text:
                    continue
                connection.execute(
                    "UPDATE memories SET content_json = ?, search_text = ? WHERE memory_id = ?",
                    (safe_content, safe_search, memory_id),
                )
                connection.execute(
                    "DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,)
                )
                connection.execute(
                    "INSERT INTO memory_fts(memory_id, search_text, memory_type) VALUES(?, ?, ?)",
                    (memory_id, safe_search, memory_type),
                )
                changed = True
            connection.commit()
            if changed:
                connection.execute("INSERT INTO memory_fts(memory_fts) VALUES('optimize')")
                connection.commit()
                # A legacy process may have used WAL mode.  Truncate the WAL
                # before rebuilding the database so neither the main file nor
                # its sidecars retain the replaced credential-bearing pages.
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
        finally:
            connection.close()
        scrubbed = set(scrubbed)
        scrubbed.add(database)
        system._credential_projection_scrubbed_paths = scrubbed
    except (OSError, sqlite3.Error):
        # Model recall is independently redacted.  A busy or temporarily
        # unavailable projection is retried on the next search instead of
        # making the life service unavailable.
        return


def _score_component(value):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _superseded_memory_ids(system, rows):
    """Return assertion ids hidden by a later authoritative correction."""

    targets = set()

    def collect(items):
        for item in items:
            memory = _mapping(item)
            for relation in memory.get("relations") or ():
                relation = _mapping(relation)
                if str(relation.get("kind") or "").strip().lower() != "supersedes":
                    continue
                target = str(relation.get("target_memory_id") or "").strip()
                if target:
                    targets.add(target)

    collect(rows)
    try:
        binding = _mapping(system.identities.active())
        life_id = str(binding.get("life_id") or "")
        state = system._memory_state(life_id) if life_id else {}
        collect(_mapping(state).values())
    except Exception:
        # The public search result still carries relations, so failure to read
        # the projection must not break recall.  It only narrows this guard to
        # corrections already present in the bounded result set.
        pass
    return targets


def _trusted_recall_rows(system, rows):
    """Keep model-visible recall relevant and free of superseded assertions."""

    rows = [item for item in rows if isinstance(item, Mapping)]
    superseded = _superseded_memory_ids(system, rows)
    retained = []
    seen = set()
    for item in rows:
        memory_id = str(item.get("memory_id") or "").strip()
        status = str(item.get("status") or "active").strip().lower()
        if not memory_id or memory_id in seen or memory_id in superseded:
            continue
        if status in {"deleted", "recall_suppressed"}:
            continue
        components = _mapping(item.get("score_components"))
        if components and not (
            _score_component(components.get("lexical")) > 0
            or _score_component(components.get("fts")) > 0
        ):
            # Confidence and recency are ranking signals, not evidence that an
            # unrelated memory belongs in the current model context.
            continue
        seen.add(memory_id)
        retained.append(_model_safe_memory_row(item))
    return retained


_CHECKPOINT_KEYS = frozenset(
    {
        "artifact_ids",
        "artifacts",
        "bytes",
        "checkpoint",
        "code",
        "completed",
        "completed_steps",
        "completed_tool_call_ids",
        "content_sha256",
        "cursor",
        "error_code",
        "filename",
        "gateway_request_id",
        "id",
        "kind",
        "last_error",
        "media_type",
        "message",
        "model_turns",
        "next_action",
        "object_id",
        "path",
        "pending",
        "pending_steps",
        "pending_tool_call_ids",
        "phase",
        "request_id",
        "resume_hint",
        "run_id",
        "sequence",
        "sha256",
        "size_bytes",
        "stage",
        "status",
        "summary",
        "title",
        "updated_at",
    }
)


def _redact_checkpoint_text(value, limit=1000):
    return _redact_secret_text(_plain_summary(value, limit))


def _checkpoint_value(value, depth=0):
    if depth > 4:
        return None
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            normalized = str(key or "").strip()
            if normalized not in _CHECKPOINT_KEYS:
                continue
            projected = _checkpoint_value(item, depth + 1)
            if projected not in (None, "", [], {}):
                result[normalized] = projected
        return result
    if isinstance(value, (list, tuple)):
        return [
            projected
            for item in value[:32]
            if (projected := _checkpoint_value(item, depth + 1)) not in (None, "", [], {})
        ]
    if isinstance(value, str):
        return _redact_checkpoint_text(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bool, int)) or value is None:
        return value
    return _redact_checkpoint_text(value)


def _active_run_checkpoint(active_run):
    if not isinstance(active_run, Mapping):
        return None
    projected = _checkpoint_value(active_run)
    return projected if isinstance(projected, Mapping) and projected else None


def _pending_tool_call(value):
    """Keep only the identity of a call that must be resumed from run state."""

    call = _mapping(value)
    function = _mapping(call.get("function"))
    name = _redact_checkpoint_text(function.get("name"), 200)
    call_id = _redact_checkpoint_text(call.get("id"), 200)
    if not name and not call_id:
        return None
    projected = {
        "type": "function",
        "function": {
            "name": name or "unknown",
            # Authoritative arguments live in the runtime checkpoint.  Copying
            # them into the prompt makes secrets and large payloads immortal.
            "arguments": "{}",
        },
    }
    if call_id:
        projected["id"] = call_id
    return projected


def _messages_for_context(messages):
    """Fold completed tool exchanges and reduce pending calls to a breakpoint."""

    if not isinstance(messages, (list, tuple)):
        return messages

    result_call_ids = {
        str(_mapping(message).get("tool_call_id") or "").strip()
        for message in messages
        if str(_mapping(message).get("role") or "").strip().lower() == "tool"
        and str(_mapping(message).get("tool_call_id") or "").strip()
    }
    announced_call_ids = {
        str(_mapping(call).get("id") or "").strip()
        for message in messages
        if str(_mapping(message).get("role") or "").strip().lower() == "assistant"
        for call in (_mapping(message).get("tool_calls") or ())
        if str(_mapping(call).get("id") or "").strip()
    }
    completed_call_ids = result_call_ids & announced_call_ids
    projected_messages = []
    for raw_message in messages:
        if not isinstance(raw_message, Mapping):
            continue
        source_message = dict(raw_message)
        role = str(source_message.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "tool"}:
            # Conversation history is an untrusted projection.  System and
            # developer authority comes exclusively from the signed life
            # envelope, never from a frontend-supplied historical message.
            continue
        message = {
            "role": role,
            "content": _redact_memory_value(source_message.get("content")),
        }
        raw_tool_call_id = ""
        if role == "tool":
            raw_tool_call_id = str(source_message.get("tool_call_id") or "").strip()
            message["tool_call_id"] = _redact_checkpoint_text(raw_tool_call_id, 200)
        if role == "assistant" and isinstance(
            source_message.get("tool_calls"), (list, tuple)
        ):
            pending_calls = []
            for call in source_message.get("tool_calls") or ():
                call_id = str(_mapping(call).get("id") or "").strip()
                if call_id and call_id in completed_call_ids:
                    continue
                pending = _pending_tool_call(call)
                if pending:
                    pending_calls.append(pending)
            if not pending_calls:
                # A completed call/result pair is process telemetry.  The
                # durable final assistant answer carries its useful outcome.
                continue
            message["content"] = "存在未完成工具调用；请从权威运行断点恢复。"
            message["tool_calls"] = pending_calls
            projected_messages.append(message)
            continue
        if (
            role == "tool"
            and raw_tool_call_id in completed_call_ids
        ):
            # Paired results are already represented by the final response or
            # active-run checkpoint.  Orphan results deliberately remain so
            # the core compiler can quarantine them as malformed context.
            continue
        projected_messages.append(message)
    return projected_messages


def _bounded_context_budget(value):
    """Enforce the product's 120k compression boundary before core compile."""

    if isinstance(value, bool):
        raise ValueError("token_budget must be a finite integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError("token_budget must be a finite integer")
        parsed = int(text)
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    else:
        raise ValueError("token_budget must be a finite integer")
    if parsed <= 0:
        raise ValueError("token_budget must be positive")
    return min(parsed, 120000)


def _tool_followup_checkpoint(messages):
    """Snapshot a tool-result tail that still needs the model's final answer."""

    if not isinstance(messages, (list, tuple)):
        return None
    announced_call_ids = {
        str(_mapping(call).get("id") or "").strip()
        for message in messages
        if str(_mapping(message).get("role") or "").strip().lower() == "assistant"
        for call in (_mapping(message).get("tool_calls") or ())
        if str(_mapping(call).get("id") or "").strip()
    }
    completed_tail = []
    for raw_message in reversed(messages):
        message = _mapping(raw_message)
        role = str(message.get("role") or "").strip().lower()
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if call_id in announced_call_ids:
                completed_tail.append(call_id)
                continue
            # An orphan result is quarantined by the core compiler and cannot
            # prove that a known call is waiting for a final model turn.
            continue
        if role in {"user", "assistant"}:
            break
    if not completed_tail:
        return None
    safe_call_ids = list(
        dict.fromkeys(
            _redact_checkpoint_text(call_id, 200)
            for call_id in reversed(completed_tail)
        )
    )
    return {
        "status": "model_followup_pending",
        "completed_tool_call_ids": safe_call_ids,
        "next_action": "resume final response from authoritative tool result ledger",
    }


def _merged_active_run_checkpoint(active_run, messages):
    checkpoint = _active_run_checkpoint(active_run)
    followup = _tool_followup_checkpoint(messages)
    if not followup:
        return checkpoint
    merged = dict(checkpoint or {})
    merged.setdefault("status", followup["status"])
    merged["completed_tool_call_ids"] = followup["completed_tool_call_ids"]
    merged.setdefault("next_action", followup["next_action"])
    return merged


def _action_learning_candidate(system, decision, result, recorded):
    """Create one review-only candidate from a verified autonomous action."""

    if not _verified_action(result):
        return None
    decision = _mapping(decision)
    result = _mapping(result)
    recorded = _mapping(recorded)
    event = _mapping(recorded.get("event"))
    source_event_id = str(event.get("event_id") or "").strip()
    if not source_event_id:
        return None

    action_title = _plain_summary(decision.get("title") or "自主行动", 120)
    candidate_name = f"自主行动复用：{action_title}"
    panel = _mapping(system.get_panel())
    capabilities = _mapping(panel.get("capabilities"))
    artifacts = [
        _mapping(item)
        for item in _mapping(capabilities.get("by_id")).values()
        if isinstance(item, Mapping)
    ]
    same_name = [item for item in artifacts if str(item.get("name") or "") == candidate_name]
    published = next(
        (
            item
            for item in reversed(same_name)
            if str(item.get("status") or "") in {"released", "published", "active"}
        ),
        None,
    )
    if not published and any(
        str(item.get("status") or "")
        not in {"discarded", "rolled_back", "rejected", "failed"}
        for item in same_name
    ):
        return {"created": False, "reason": "candidate_already_exists"}

    digest = hashlib.sha256(source_event_id.encode("utf-8")).hexdigest()[:20]
    summary = _plain_summary(result.get("summary") or result.get("human_summary"), 600)
    instruction = _plain_summary(
        decision.get("instruction") or decision.get("reason") or summary,
        1600,
    )
    if len(instruction) < 20:
        instruction = (
            "复盘已通过生命执行链验证的行动，仅在同类低风险任务中复用，"
            "执行前重新确认当前权限、输入与用户边界。"
        )
    card = {
        "card_id": f"autolearn_{digest}",
        "title": candidate_name,
        "summary": summary or "由已验证自主行动自动生成的待审学习候选。",
        "description": (
            "此候选只记录可复用流程，不自动激活、不自动发布；"
            f"权威来源为已验证事件 {source_event_id}。"
        ),
        "instructions": instruction,
        "procedure": instruction,
        "kind": "skill",
        "risk_level": str(decision.get("risk") or "A1"),
        "source": "verified_autonomous_action",
        "source_task_id": str(decision.get("task_id") or ""),
        "upgrade_of": str(_mapping(published).get("artifact_id") or ""),
    }
    proposed = system.propose_capability(
        card,
        actor="life_learning_producer",
        source_event_ids=[source_event_id],
    )
    artifact = _mapping(_mapping(proposed).get("artifact"))
    return {
        "created": True,
        "card_id": card["card_id"],
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "upgrade_of": card["upgrade_of"],
    }


def _autonomy_fact_keys(value):
    value = _mapping(value)
    keys = []
    execution_event_id = str(value.get("execution_event_id") or "").strip()
    request_id = str(value.get("request_id") or "").strip()
    run_id = str(value.get("run_id") or "").strip()
    if execution_event_id:
        keys.append(f"event:{execution_event_id}")
    if request_id:
        keys.append(f"request:{request_id}:{run_id}")
        if not run_id:
            keys.append(f"request:{request_id}:")
    return keys


def _autonomy_fact_cache(system, life_id):
    caches = getattr(system, "_autonomy_fact_caches", None)
    if not isinstance(caches, dict):
        caches = {}
        system._autonomy_fact_caches = caches
    if life_id in caches:
        return caches[life_id]
    cache = {}
    try:
        events = system.journal.events(life_id)
    except Exception:
        events = []
    for event in events:
        event = _mapping(event)
        if str(event.get("event_type") or "") not in {
            "autonomy.action_completed",
            "autonomy.action_failed",
        }:
            continue
        for key in _autonomy_fact_keys(event.get("payload")):
            cache[key] = event
    caches[life_id] = cache
    return cache


def _recorded_autonomy_event(system, life_id, result):
    """Find the fact already committed for a retried execution completion."""

    cache = _autonomy_fact_cache(system, life_id)
    for key in _autonomy_fact_keys(result):
        if key in cache:
            return cache[key]
        # A retry may recover a run ID that the first terminal report omitted.
        if key.startswith("request:"):
            request_prefix = key.rsplit(":", 1)[0] + ":"
            for cached_key, event in cache.items():
                if cached_key.startswith(request_prefix):
                    return event
    return None


def _remember_autonomy_event(system, life_id, event):
    event = _mapping(event)
    for key in _autonomy_fact_keys(event.get("payload")):
        _autonomy_fact_cache(system, life_id)[key] = event


def install_runtime_fixes(life_core, life_scheduler):
    """Install the life service's budget, clock, and producer fixes."""

    system_class = life_core.CompleteLifeSystem
    scheduler_class = life_scheduler.LifeAutonomyScheduler
    if getattr(system_class, _PATCH_MARKER, False):
        return

    original_budget_day = system_class.ensure_scheduler_budget_day
    original_assert_memory = system_class.assert_memory
    original_appraise_affect = system_class.appraise_affect
    original_compile_context = system_class.compile_context
    original_correct_memory = system_class.correct_memory
    original_record_action = system_class.record_autonomous_action
    original_search_memory = system_class.search_memory
    original_initialize_affect = system_class.initialize_affect
    original_prepare_execution = system_class.prepare_execution
    original_update_settings = system_class.update_settings
    original_update_soul = system_class.update_soul
    original_verify_context = system_class.verify_context
    original_scheduler_init = scheduler_class.__init__
    original_journal_append = life_core.SemanticJournal.append

    life_contracts = sys.modules.get("life_contracts")
    life_affect = sys.modules.get("life_affect")
    life_memory = sys.modules.get("life_memory")

    if life_memory is not None:
        original_memory_replay = life_memory.LifeMemoryStore.replay

        @wraps(original_memory_replay)
        def finite_memory_replay(events, life_id):
            safe_events = []
            for raw_event in events:
                event = dict(raw_event)
                payload = dict(_mapping(event.get("payload")))
                assertion = dict(_mapping(payload.get("assertion")))
                provenance = dict(_mapping(assertion.get("provenance")))
                confidence = provenance.get("confidence", 0.5)
                try:
                    invalid_confidence = isinstance(confidence, bool) or not math.isfinite(
                        float(confidence)
                    )
                except (TypeError, ValueError, OverflowError):
                    invalid_confidence = True
                if assertion and invalid_confidence:
                    provenance["confidence"] = 0.0
                    assertion["provenance"] = provenance
                    assertion["status"] = "recall_suppressed"
                    payload["assertion"] = assertion
                    event["payload"] = payload
                safe_events.append(event)
            assertions, issues = original_memory_replay(safe_events, life_id)
            repaired = {}
            for memory_id, assertion in assertions.items():
                assertion = dict(assertion)
                provenance = dict(_mapping(assertion.get("provenance")))
                confidence = provenance.get("confidence", 0.5)
                if isinstance(confidence, bool):
                    invalid_confidence = True
                else:
                    try:
                        parsed_confidence = float(confidence)
                        invalid_confidence = not math.isfinite(parsed_confidence)
                    except (TypeError, ValueError, OverflowError):
                        invalid_confidence = True
                if invalid_confidence:
                    provenance["confidence"] = 0.0
                    assertion["provenance"] = provenance
                    assertion["status"] = "recall_suppressed"
                    assertion["legacy_numeric_repair"] = True
                repaired[memory_id] = assertion
            return repaired, issues

        life_memory.LifeMemoryStore.replay = staticmethod(finite_memory_replay)

    if life_contracts is not None:
        original_require_number_01 = life_contracts._require_number_01

        @wraps(original_require_number_01)
        def require_number_01_fix(value, field):
            try:
                _strict_finite_number(value, field, 0.0, 1.0)
            except ValueError as exc:
                raise life_contracts.ContractViolation(
                    "invalid_unit_value",
                    str(exc),
                    field=field,
                ) from exc
            return original_require_number_01(value, field)

        life_contracts._require_number_01 = require_number_01_fix

        for validator_name in (
            "validate_affective_state",
            "validate_capability_artifact",
            "validate_context_envelope",
            "validate_execution_terminal_evidence",
            "validate_life_cycle",
            "validate_memory_assertion",
            "validate_model_inference_evidence",
        ):
            original_validator = getattr(life_contracts, validator_name)

            @wraps(original_validator)
            def finite_contract_validator(
                document,
                *args,
                __original=original_validator,
                **kwargs,
            ):
                try:
                    _assert_finite_tree(document)
                except ValueError as exc:
                    raise life_contracts.ContractViolation(
                        "non_finite_contract_number",
                        str(exc),
                        field="document",
                    ) from exc
                return __original(document, *args, **kwargs)

            setattr(life_contracts, validator_name, finite_contract_validator)
            if getattr(life_core, validator_name, None) is original_validator:
                setattr(life_core, validator_name, finite_contract_validator)

    if life_affect is not None:
        original_clamp = life_affect.clamp
        original_decay_affective_state = life_affect.decay_affective_state
        original_apply_appraisal = life_affect.apply_appraisal

        @wraps(original_clamp)
        def finite_clamp(value, low=0.0, high=1.0):
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                return float(low)
            if math.isnan(parsed):
                return float(low)
            return max(float(low), min(float(high), parsed))

        @wraps(original_decay_affective_state)
        def monotonic_decay_affective_state(state, at):
            state = _repair_nonfinite_projection(state)
            effective_at = _monotonic_affect_time(state, at, life_affect.parse_time)
            return original_decay_affective_state(state, effective_at)

        @wraps(original_apply_appraisal)
        def monotonic_apply_appraisal(
            state,
            appraisal,
            at,
            *,
            source_event_ids,
            relationship_id="",
        ):
            effective_at = _monotonic_affect_time(state, at, life_affect.parse_time)
            return original_apply_appraisal(
                state,
                appraisal,
                effective_at,
                source_event_ids=source_event_ids,
                relationship_id=relationship_id,
            )

        life_affect.clamp = finite_clamp
        life_affect.decay_affective_state = monotonic_decay_affective_state
        life_affect.apply_appraisal = monotonic_apply_appraisal
        if getattr(life_core, "decay_affective_state", None) is original_decay_affective_state:
            life_core.decay_affective_state = monotonic_decay_affective_state
        if getattr(life_core, "apply_appraisal", None) is original_apply_appraisal:
            life_core.apply_appraisal = monotonic_apply_appraisal

    @wraps(original_journal_append)
    def finite_journal_append(
        self,
        life_id,
        event_type,
        payload=None,
        *,
        actor="life_system",
        idempotency_key="",
        epistemic_class="verified",
        cycle_id="",
    ):
        try:
            _assert_finite_tree(payload)
        except ValueError as exc:
            raise life_core.LifeCoreError(
                "non_finite_event_payload",
                str(exc),
                status=400,
            ) from exc
        return original_journal_append(
            self,
            life_id,
            event_type,
            payload,
            actor=actor,
            idempotency_key=idempotency_key,
            epistemic_class=epistemic_class,
            cycle_id=cycle_id,
        )

    @wraps(original_assert_memory)
    def assert_memory_fix(
        self,
        memory_type,
        content,
        provenance,
        *,
        actor,
        memory_id="",
        relations=None,
        valid_from="",
        valid_to="",
        idempotency_key="",
    ):
        try:
            valid_from = _normalize_optional_timestamp(valid_from, "valid_from")
            valid_to = _normalize_optional_timestamp(valid_to, "valid_to")
        except ValueError as exc:
            raise life_core.LifeCoreError(
                "invalid_memory_interval", str(exc), status=400
            ) from exc
        if valid_from and valid_to and valid_to < valid_from:
            raise life_core.LifeCoreError(
                "invalid_memory_interval",
                "valid_to cannot be earlier than valid_from",
                status=400,
            )
        return original_assert_memory(
            self,
            memory_type,
            _redact_memory_value(content),
            provenance,
            actor=actor,
            memory_id=memory_id,
            relations=relations,
            valid_from=valid_from,
            valid_to=valid_to,
            idempotency_key=idempotency_key,
        )

    @wraps(original_appraise_affect)
    def appraise_affect_fix(
        self,
        appraisal,
        source_event_ids,
        *,
        actor,
        relationship_id="",
    ):
        try:
            normalized = _normalized_appraisal(appraisal)
        except ValueError as exc:
            raise life_core.LifeCoreError(
                "invalid_affect_appraisal", str(exc), status=400
            ) from exc
        if not isinstance(source_event_ids, list) or len(source_event_ids) > 100:
            raise life_core.LifeCoreError(
                "invalid_affect_sources",
                "source_event_ids must be an array with at most 100 items",
                status=400,
            )
        relationship_id = str(relationship_id or "").strip()
        if len(relationship_id) > 128:
            raise life_core.LifeCoreError(
                "invalid_relationship_id",
                "relationship_id must be at most 128 characters",
                status=400,
            )
        return original_appraise_affect(
            self,
            normalized,
            source_event_ids,
            actor=actor,
            relationship_id=relationship_id,
        )

    @wraps(original_initialize_affect)
    def initialize_affect_fix(self):
        result = original_initialize_affect(self)
        try:
            _assert_finite_tree(_mapping(result).get("state"))
            return result
        except ValueError:
            pass
        if getattr(self, "_repairing_nonfinite_affect", False):
            return result
        self._repairing_nonfinite_affect = True
        try:
            neutral_repair = dict(_AFFECT_DEFAULTS)
            neutral_repair["intensity"] = 0.0
            neutral_repair["explanation"] = ["legacy non-finite affect projection repaired"]
            original_appraise_affect(
                self,
                neutral_repair,
                [],
                actor="life_affect_repair",
                relationship_id="",
            )
        finally:
            self._repairing_nonfinite_affect = False
        repaired = original_initialize_affect(self)
        try:
            _assert_finite_tree(_mapping(repaired).get("state"))
        except ValueError as exc:
            raise life_core.LifeCoreError(
                "affect_projection_repair_failed",
                str(exc),
                status=409,
            ) from exc
        return repaired

    @wraps(original_correct_memory)
    def correct_memory_fix(
        self,
        target_memory_id,
        content,
        provenance,
        *,
        actor,
        relation_kind="supersedes",
        memory_type="",
        idempotency_key="",
    ):
        return original_correct_memory(
            self,
            target_memory_id,
            _redact_memory_value(content),
            provenance,
            actor=actor,
            relation_kind=relation_kind,
            memory_type=memory_type,
            idempotency_key=idempotency_key,
        )

    @wraps(original_search_memory)
    def search_memory_fix(
        self,
        query,
        *,
        limit=10,
        memory_types=None,
        include_content=True,
    ):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise life_core.LifeCoreError(
                "invalid_memory_search_limit",
                "Memory search limit must be an integer between 1 and 100",
                status=400,
            )
        _scrub_memory_projection(self)
        result = original_search_memory(
            self,
            query,
            limit=limit,
            memory_types=memory_types,
            include_content=include_content,
        )
        if not isinstance(result, Mapping):
            return result
        filtered = _trusted_recall_rows(self, result.get("results") or ())
        response = dict(result)
        response["results"] = filtered
        response["count"] = len(filtered)
        return response

    @wraps(original_compile_context)
    def compile_context_fix(
        self,
        current_request,
        *,
        trigger=None,
        goal=None,
        token_budget=8000,
        cycle_id="",
        messages=None,
        active_run=None,
        relationship_id="user:primary",
        memory_types=None,
    ):
        return original_compile_context(
            self,
            current_request,
            trigger=trigger,
            goal=goal,
            token_budget=_bounded_context_budget(token_budget),
            cycle_id=cycle_id,
            messages=_messages_for_context(messages),
            active_run=_merged_active_run_checkpoint(active_run, messages),
            relationship_id=relationship_id,
            memory_types=memory_types,
        )

    def assert_context_fresh(self, envelope):
        envelope = _mapping(envelope)
        binding = _mapping(self.identities.active())
        life_id = str(binding.get("life_id") or "")
        if str(envelope.get("life_id") or "") != life_id:
            raise life_core.LifeCoreError(
                "stale_context_identity",
                "Compiled context no longer belongs to the active life IP",
                status=409,
            )
        if int(envelope.get("writer_epoch") or 0) != int(binding.get("writer_epoch") or 0):
            raise life_core.LifeCoreError(
                "stale_context_writer_epoch",
                "Compiled context belongs to an earlier writer epoch",
                status=409,
            )
        soul = _mapping(_mapping(self.get_soul()).get("soul"))
        if str(envelope.get("soul_revision") or "") != str(
            soul.get("revision_id") or ""
        ):
            raise life_core.LifeCoreError(
                "stale_context_soul_revision",
                "Soul changed after this context was compiled",
                status=409,
            )

        settings = _mapping(_mapping(self.get_panel()).get("settings"))
        permissions = _mapping(envelope.get("permissions"))
        current_privacy = _mapping(settings.get("privacy"))
        permission_snapshot = {
            "permission_mode": str(settings.get("permission_mode") or ""),
            "autonomous_risk_max": str(settings.get("autonomous_risk_max") or ""),
            "privacy": {
                "redact_llm": bool(current_privacy.get("redact_llm", True)),
                "redact_share": bool(current_privacy.get("redact_share", True)),
            },
        }
        compiled_permission_snapshot = {
            "permission_mode": str(permissions.get("permission_mode") or ""),
            "autonomous_risk_max": str(permissions.get("autonomous_risk_max") or ""),
            "privacy": dict(_mapping(permissions.get("privacy"))),
        }
        if compiled_permission_snapshot != permission_snapshot:
            raise life_core.LifeCoreError(
                "stale_context_permissions",
                "Permission or privacy policy changed after context compilation",
                status=409,
            )

        memory_cards = [
            _mapping(item)
            for item in envelope.get("memory_cards") or ()
            if isinstance(item, Mapping)
        ]
        if memory_cards:
            memory_state = _mapping(self._memory_state(life_id))
            superseded = _superseded_memory_ids(self, memory_state.values())
            for card in memory_cards:
                memory_id = str(card.get("memory_id") or "").strip()
                current = _mapping(memory_state.get(memory_id))
                status = str(current.get("status") or "").strip().lower()
                if (
                    not current
                    or memory_id in superseded
                    or status in {"deleted", "recall_suppressed"}
                ):
                    raise life_core.LifeCoreError(
                        "stale_context_memory",
                        "A memory used by this context was corrected, suppressed, or erased",
                        status=409,
                    )

        capabilities = _mapping(_mapping(self.get_panel()).get("capabilities"))
        by_id = _mapping(capabilities.get("by_id"))
        for collection, required_status in (
            (envelope.get("active_skills") or (), "active"),
            (envelope.get("released_tools") or (), "released"),
        ):
            for card in collection:
                card = _mapping(card)
                artifact_id = str(card.get("artifact_id") or "").strip()
                current = _mapping(by_id.get(artifact_id))
                if str(current.get("status") or "") != required_status:
                    raise life_core.LifeCoreError(
                        "stale_context_capability",
                        "A capability used by this context is no longer published",
                        status=409,
                    )
                for field in ("version", "artifact_hash"):
                    if card.get(field) and current.get(field) != card.get(field):
                        raise life_core.LifeCoreError(
                            "stale_context_capability",
                            "A capability used by this context changed version",
                            status=409,
                        )

    @wraps(original_prepare_execution)
    def prepare_execution_fix(
        self,
        context_hash,
        request_id,
        *,
        channel="desktop_frontend",
        decision_action="execute",
        purpose="",
    ):
        context_hash_text = str(context_hash or "").strip()
        if re.fullmatch(r"[0-9a-f]{64}", context_hash_text):
            binding = _mapping(self.identities.active())
            life_id = str(binding.get("life_id") or "")
            stored = life_core.EncryptedContextStore(
                self.identities.root_for(life_id)
            ).load(context_hash_text)
            assert_context_fresh(self, _mapping(stored).get("envelope"))
        return original_prepare_execution(
            self,
            context_hash,
            request_id,
            channel=channel,
            decision_action=decision_action,
            purpose=purpose,
        )

    @wraps(original_verify_context)
    def verify_context_fix(self, envelope):
        result = original_verify_context(self, envelope)
        assert_context_fresh(self, envelope)
        if isinstance(result, Mapping):
            result = dict(result)
            result["fresh"] = True
        return result

    @wraps(original_budget_day)
    def ensure_scheduler_budget_day_fix(self, life_id, day):
        requested_day = str(day or "").strip()
        try:
            requested_date = datetime.strptime(requested_day, "%Y-%m-%d").date()
        except ValueError as exc:
            raise life_core.LifeCoreError(
                "invalid_scheduler_budget_day",
                "Scheduler budget day must be a real YYYY-MM-DD calendar date",
                status=400,
            ) from exc
        business_today = _china_business_now(datetime.now(timezone.utc)).date()
        if requested_date > business_today:
            raise life_core.LifeCoreError(
                "future_scheduler_budget_day",
                "Scheduler budget day cannot be later than the current business date",
                status=409,
            )
        current_day = _budget_date(self)
        try:
            current_date = datetime.strptime(current_day, "%Y-%m-%d").date()
        except ValueError:
            current_date = None
        if current_date is not None and current_date > business_today:
            repaired = original_budget_day(self, life_id, business_today.isoformat())
            if isinstance(repaired, Mapping):
                repaired = dict(repaired)
                repaired["repaired_future_date"] = True
                repaired["invalid_previous_date"] = current_day
            return repaired
        if current_date is not None and requested_date < current_date:
            panel = _mapping(self.get_panel())
            return {
                "life_id": life_id,
                "budget": dict(_mapping(panel.get("budget"))),
                "reset": False,
                "ignored_date_regression": True,
                "requested_date": requested_day,
            }
        return original_budget_day(self, life_id, requested_day)

    @wraps(original_update_settings)
    def update_settings_fix(self, settings, *, actor):
        if not isinstance(settings, Mapping):
            return original_update_settings(self, settings, actor=actor)
        if "autonomy" in settings:
            raise life_core.LifeCoreError(
                "immutable_life_setting",
                "autonomy is derived from governed settings and cannot be written directly",
                status=400,
            )
        for field in _INTEGER_SETTING_FIELDS & set(settings):
            value = settings[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise life_core.LifeCoreError(
                    "invalid_life_setting_number",
                    f"{field} must be an integer without coercion",
                    status=400,
                )
        if "share_probability" in settings:
            try:
                _strict_finite_number(
                    settings["share_probability"],
                    "share_probability",
                    0.0,
                    1.0,
                )
            except ValueError as exc:
                raise life_core.LifeCoreError(
                    "invalid_share_probability",
                    str(exc),
                    status=400,
                ) from exc
        if "privacy" in settings:
            privacy = settings["privacy"]
            if not isinstance(privacy, Mapping):
                raise life_core.LifeCoreError(
                    "invalid_privacy_setting",
                    "privacy must be an object",
                    status=400,
                )
            unknown_privacy = set(privacy) - {"redact_llm", "redact_share"}
            if unknown_privacy or any(not isinstance(value, bool) for value in privacy.values()):
                raise life_core.LifeCoreError(
                    "invalid_privacy_setting",
                    "privacy accepts only boolean redact_llm and redact_share fields",
                    status=400,
                )
        return original_update_settings(self, settings, actor=actor)

    @wraps(original_update_soul)
    def update_soul_fix(self, soul, *, actor):
        if not isinstance(soul, Mapping):
            return original_update_soul(self, soul, actor=actor)
        allowed = {"name", "prompt", "values", "boundaries"}
        unknown = set(soul) - allowed
        if unknown:
            raise life_core.LifeCoreError(
                "unknown_soul_field",
                f"Unknown Soul fields: {', '.join(sorted(map(str, unknown)))}",
                status=400,
            )
        normalized = dict(soul)
        for field, limit in (("name", 120), ("prompt", 20000)):
            if field not in normalized:
                continue
            if not isinstance(normalized[field], str):
                raise life_core.LifeCoreError(
                    "invalid_soul_text",
                    f"Soul {field} must be text",
                    status=400,
                )
            if len(normalized[field]) > limit:
                raise life_core.LifeCoreError(
                    "soul_text_too_large",
                    f"Soul {field} exceeds {limit} characters",
                    status=413,
                )
        for field in ("values", "boundaries"):
            if field not in normalized:
                continue
            items = normalized[field]
            if not isinstance(items, list) or len(items) > 64:
                raise life_core.LifeCoreError(
                    "invalid_soul_structure",
                    f"Soul {field} must be an array with at most 64 items",
                    status=400,
                )
            if any(not isinstance(item, str) or len(item) > 240 for item in items):
                raise life_core.LifeCoreError(
                    "invalid_soul_structure",
                    f"Soul {field} items must be text up to 240 characters",
                    status=400,
                )
        return original_update_soul(self, normalized, actor=actor)

    @wraps(original_scheduler_init)
    def scheduler_init_fix(self, *args, **kwargs):
        original_scheduler_init(self, *args, **kwargs)
        original_now = self._now

        def business_now():
            return _china_business_now(original_now())

        self._now = business_now

    @wraps(original_record_action)
    def record_autonomous_action_fix(self, life_id, decision, result):
        prior = _recorded_autonomy_event(self, life_id, result)
        if prior is not None:
            return {
                "life_id": life_id,
                "event": prior,
                "source_sequence": int(prior.get("sequence") or 0),
                "deduplicated": True,
                "learning_producer": {
                    "created": False,
                    "reason": "execution_already_recorded",
                },
            }
        recorded = original_record_action(self, life_id, decision, result)
        _remember_autonomy_event(self, life_id, _mapping(recorded).get("event"))
        try:
            produced = _action_learning_candidate(self, decision, result, recorded)
        except Exception as exc:
            produced = {"created": False, "reason": type(exc).__name__}
        if produced is None or not isinstance(recorded, Mapping):
            return recorded
        response = dict(recorded)
        response["learning_producer"] = produced
        return response

    life_core.SemanticJournal.append = finite_journal_append
    system_class.ensure_scheduler_budget_day = ensure_scheduler_budget_day_fix
    system_class.assert_memory = assert_memory_fix
    system_class.appraise_affect = appraise_affect_fix
    system_class.initialize_affect = initialize_affect_fix
    system_class.prepare_execution = prepare_execution_fix
    system_class.compile_context = compile_context_fix
    system_class.correct_memory = correct_memory_fix
    system_class.record_autonomous_action = record_autonomous_action_fix
    system_class.search_memory = search_memory_fix
    system_class.update_settings = update_settings_fix
    system_class.update_soul = update_soul_fix
    system_class.verify_context = verify_context_fix
    scheduler_class.__init__ = scheduler_init_fix
    setattr(system_class, _PATCH_MARKER, True)


def install_scoped_execution_credentials(life_server, life_scheduler):
    """Remove autonomous execution authority; submit exact candidates to 7184."""

    service_class = life_server.LifeService
    if getattr(service_class, _CREDENTIAL_PATCH_MARKER, False):
        return
    original_init = service_class.__init__
    original_execute = life_scheduler.LifeAutonomyScheduler._execute_decision

    class GatewayActionIntentClient:
        def __init__(self, gateway_url, token):
            normalized = str(gateway_url or "").rstrip("/")
            if normalized != "http://127.0.0.1:7184":
                raise ValueError("life action intents require the fixed 7184 Gateway origin")
            if not 32 <= len(str(token or "")) <= 512:
                raise ValueError("life action-intent credential is invalid")
            self.gateway_url = normalized
            self.token = str(token)
            self._client = life_scheduler.ExecutionChainClient(normalized, self.token)

        def submit(self, payload, *, timeout):
            return self._client._request(
                "POST",
                "/api/v1/gateway/life/action-intents",
                payload,
                timeout=timeout,
            )

    @wraps(original_execute)
    def execute_decision_as_intent(self, active, decision, *, settings, timeout):
        del settings
        life_id = str(_mapping(active).get("life_id") or "")
        candidate = _mapping(decision)
        exact = {
            "action_id": str(candidate.get("action_id") or ""),
            "action_version": str(candidate.get("action_version") or ""),
            "arguments_sha256": str(candidate.get("arguments_sha256") or ""),
            "workspace_id": str(candidate.get("workspace_id") or ""),
            "workspace_scope_hash": str(candidate.get("workspace_scope_hash") or ""),
            "principal_scope_hash": str(candidate.get("principal_scope_hash") or ""),
            "request_id": str(candidate.get("request_id") or ""),
            "run_id": str(candidate.get("run_id") or ""),
        }
        missing = tuple(sorted(key for key, value in exact.items() if not value))
        client = getattr(self, "gateway_action_intent_client", None)
        if missing or client is None:
            result = {
                "ok": False,
                "kind": "autonomous_action",
                "blocked": True,
                "error": "gateway_action_intent_required",
                "missing_exact_fields": list(missing),
                "action_fact_verified": False,
                "terminal_status": "policy_rejected",
            }
            self.system.record_autonomous_action(life_id, decision, result)
            return result
        payload = {
            "schema": "tiangong.life.action-intent-candidate.v1",
            "life_id": life_id,
            "candidate": {**exact, "source": "life_scheduler"},
            "model_risk_is_untrusted": True,
            "model_reported_risk": str(candidate.get("risk") or ""),
        }
        status, response = client.submit(payload, timeout=max(1.0, min(float(timeout), 30.0)))
        accepted = status in {200, 202} and str(response.get("status") or "") in {
            "REJECTED", "CONFIRMATION_REQUIRED", "AUTHORIZED"
        }
        result = {
            "ok": False,
            "kind": "autonomous_action",
            "submitted": accepted,
            "blocked": str(response.get("status") or "") != "AUTHORIZED",
            "error": "" if accepted else "gateway_action_intent_rejected",
            "policy_status": str(response.get("status") or "REJECTED"),
            "policy_decision_id": str(response.get("policy_decision_id") or ""),
            "action_fact_verified": False,
            "terminal_status": "policy_pending" if accepted else "policy_rejected",
        }
        self.system.record_autonomous_action(life_id, decision, result)
        return result

    life_scheduler.LifeAutonomyScheduler._execute_decision = execute_decision_as_intent

    @wraps(original_init)
    def service_init_with_scoped_execution_token(self, *args, **kwargs):
        # Remove every 7174 authority credential before any frozen constructor
        # can observe the inherited process environment.  Restore the parent
        # mapping afterwards only for compatibility with unrelated code; no
        # object created by LifeService receives either credential.
        hidden = {}
        for name in ("TIANGONG_BACKEND_EXECUTION_TOKEN", "TIANGONG_BACKEND_INTERNAL_TOKEN"):
            if name in os.environ:
                hidden[name] = os.environ.pop(name)
        try:
            original_init(self, *args, **kwargs)
        finally:
            os.environ.update(hidden)
        gateway_token = str(os.environ.get("TIANGONG_GATEWAY_LIFE_INTENT_TOKEN") or "")
        gateway_url = str(os.environ.get("TIANGONG_GATEWAY_URL") or "http://127.0.0.1:7184")
        if gateway_token:
            self.scheduler.gateway_action_intent_client = GatewayActionIntentClient(
                gateway_url, gateway_token
            )

    service_class.__init__ = service_init_with_scoped_execution_token
    setattr(service_class, _CREDENTIAL_PATCH_MARKER, True)
