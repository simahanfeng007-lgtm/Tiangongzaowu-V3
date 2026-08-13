import json
import os
import re
from collections.abc import Mapping


_P11_REQUIRED_ENV = (
    "TIANGONG_LIFE_P11_SNAPSHOT",
    "TIANGONG_LIFE_P11_FINAL_MANIFEST",
    "TIANGONG_LIFE_P11_OVERLAY",
    "TIANGONG_LIFE_P11_HANDOFF",
    "TIANGONG_LIFE_P11_PUBLIC_KEY",
    "TIANGONG_LIFE_P11_TRUSTED_PUBLIC_KEY_SHA256",
    "TIANGONG_DESKTOP_TOKEN",
)


def _p11_cutover_configured():
    """Never enter source-writer mode from a partial or implicit configuration."""
    return all(str(os.environ.get(name) or "").strip() for name in _P11_REQUIRED_ENV)


if _p11_cutover_configured():
    # This branch is deliberately before every frozen-module import.  A valid
    # handoff therefore has one implementation in the process, while an absent
    # handoff leaves the frozen runtime as the compatibility fallback.
    from life_service.production_api import serve_production_from_environment

    serve_production_from_environment()
    raise SystemExit(0)

if str(os.environ.get("TIANGONG_LIFE_P11_CUTOVER_REQUIRED") or "").strip() == "1":
    # Once a handoff has existed, the frozen authority can never silently
    # regain write ownership.  A partial/tampered cutover gets only immutable
    # legacy reads until the signed source artifacts are recovered.
    from life_service.production_api import (
        serve_cutover_read_only_fallback_from_environment,
    )

    serve_cutover_read_only_fallback_from_environment()
    raise SystemExit(0)

import life_core
import life_scheduler
import life_server
from life_service.identity_migration import migrate_legacy_identities
from tiangong_life_runtime_fixes import (
    install_runtime_fixes,
    install_scoped_execution_credentials,
)


_IDENTITY_MIGRATION = migrate_legacy_identities(life_core)
if _IDENTITY_MIGRATION.get("status") == "failed":
    details = "; ".join(
        f"{item.get('code', 'identity_migration_failed')}: {item.get('message', '')}"
        for item in _IDENTITY_MIGRATION.get("failures", [])
    )
    raise RuntimeError(f"LIFE_IDENTITY_MIGRATION_FAILED: {details}")


_ORIGINAL_JSON_OBJECT = life_scheduler._json_object
_ORIGINAL_RESPONSE_TEXT = life_scheduler._response_text
_ORIGINAL_INVOKE_LIFECYCLE = life_scheduler.LifeAutonomyScheduler._invoke_lifecycle


def _structured_json_object(text):
    """Prefer the final structured answer over JSON examples inside M3 thinking."""
    raw = str(text or "").strip()
    if not raw:
        return None

    # MiniMax-M3 can place schema examples in a <think> block.  The bundled
    # parser returned the first object it saw, so it accidentally accepted the
    # example and discarded the real final decision/plan that followed it.
    without_thinking = re.sub(
        r"<think\b[^>]*>.*?</think>",
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()

    decoder = json.JSONDecoder()
    candidates = []
    for index, char in enumerate(without_thinking):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(without_thinking[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)

    # The final JSON object is authoritative.  Prefer a known scheduler
    # contract when prose happens to contain some unrelated object afterwards.
    for value in reversed(candidates):
        if "tasks" in value or "should_act" in value:
            return value
    if candidates:
        return candidates[-1]
    return _ORIGINAL_JSON_OBJECT(without_thinking)


def _provider_response_text(data):
    """Support providers that return message.content as typed content parts."""
    if isinstance(data, Mapping):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message")
            if isinstance(message, Mapping) and isinstance(message.get("content"), list):
                parts = []
                for item in message["content"]:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, Mapping):
                        value = item.get("text") or item.get("content")
                        if isinstance(value, str):
                            parts.append(value)
                joined = "\n".join(part for part in parts if part.strip()).strip()
                if joined:
                    return joined
    return _ORIGINAL_RESPONSE_TEXT(data)


def _invoke_lifecycle_with_scheduler_contract(
    self,
    active,
    *,
    purpose,
    prompt,
    timeout,
    decision_action,
    allow_tools,
    require_action_fact=False,
):
    local_now = self._now().isoformat(timespec="minutes")
    if purpose == "daily_plan":
        prompt = (
            f"{prompt}\n当前本地时间：{local_now}。"
            "任务时间窗不得早于当前本地时间；若当天已较晚，就只规划剩余时段。"
            "最终一行必须是唯一 JSON 对象，必须包含非空 tasks 数组；"
            "不要在最终 JSON 前后输出解释、Markdown 或 JSON 示例。"
        )
    elif purpose == "autonomous_judgment":
        prompt = (
            f"{prompt}\n当前本地时间：{local_now}。"
            "最终一行必须是唯一 JSON 对象。若 should_act=true，task_id 必须逐字复制"
            " pending_tasks 中一个仍在时间窗内的 id；否则 should_act=false 且 task_id 为空。"
        )
    return _ORIGINAL_INVOKE_LIFECYCLE(
        self,
        active,
        purpose=purpose,
        prompt=prompt,
        timeout=timeout,
        decision_action=decision_action,
        allow_tools=allow_tools,
        require_action_fact=require_action_fact,
    )


life_scheduler._json_object = _structured_json_object
life_scheduler._response_text = _provider_response_text
life_scheduler.LifeAutonomyScheduler._invoke_lifecycle = _invoke_lifecycle_with_scheduler_contract
install_runtime_fixes(life_core, life_scheduler)
install_scoped_execution_credentials(life_server, life_scheduler)

life_server.main()
