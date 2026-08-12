"""Evidence-gated learning materialization for the new Life chain.

This ports the useful *process* from the detached legacy ``xuexi_lian``:
source collection, targeted research, evidence screening, and synthesis.  It
does not import the old module, write its registry, or create legacy tools.
The caller remains responsible for compiling and publishing the resulting
draft through the new artifact executor.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Mapping

from contracts import canonical_sha256


LEARNING_EXECUTOR_SCHEMA = "tiangong.life.learning-executor.v1"
_NETWORK_TERMS = ("联网", "搜索", "检索", "查资料", "查最新", "官方文档", "论文", "标准", "research", "official doc")
_TEMPORAL_TERMS = ("最新", "current", "today", "版本", "api", "sdk", "价格", "政策", "法规", "漏洞", "cve", "release")
_INJECTION_TERMS = ("ignore previous", "system prompt", "忽略此前", "忽略之前", "系统提示", "你是chatgpt")
_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")


class LearningExecutionError(ValueError):
    pass


def _text(value: Any, *, limit: int, fallback: str = "") -> str:
    result = str(value or "").strip()
    return (result or fallback)[:limit]


def _terms(value: Any) -> list[str]:
    return list(dict.fromkeys(item.casefold() for item in _TERM.findall(str(value or ""))))[:32]


def _source(learning: Mapping[str, Any], activity_scope: Mapping[str, Any]) -> dict[str, Any]:
    draft = learning.get("draft_artifact") if isinstance(learning.get("draft_artifact"), Mapping) else {}
    direct = _text(learning.get("request") or learning.get("title"), limit=4_000)
    material = _text(draft.get("content") or draft.get("markdown") or draft.get("text"), limit=16_000)
    active_l3 = (
        activity_scope.get("active_l3_refs")
        if isinstance(activity_scope.get("active_l3_refs"), list)
        else None
    )
    if active_l3 is None:
        # Legacy callers without the layered scope still work, but production
        # paths always supply active_l3_refs so learning never ingests every
        # memory layer.
        active_l3 = [
            {
                "memory_id": row.get("memory_id"),
                "content": row.get("content"),
            }
            for row in activity_scope.get("recent_memories") or []
            if isinstance(row, Mapping)
        ]
    memories = [
        {
            "derivation_id": _text(row.get("derivation_id"), limit=160),
            "memory_id": _text(row.get("memory_id"), limit=160),
            "content": _text(
                row.get("content")
                or row.get("summary")
                or row.get("plaintext"),
                limit=4_000,
            ),
        }
        for row in active_l3
        if isinstance(row, Mapping) and row.get("memory_id")
    ][:12]
    combined = "\n\n".join(part for part in (direct, material, *(row["content"] for row in memories)) if part)
    repository_rows = activity_scope.get("repository_evidence") if isinstance(activity_scope.get("repository_evidence"), list) else []
    repository_evidence = [
        {
            "frame_id": _text(row.get("frame_id"), limit=160),
            "frame_revision_hash": _text(row.get("frame_revision_hash"), limit=64),
            "commit": _text(row.get("commit"), limit=64),
            "entity_refs": [
                {
                    "record_id": _text(item.get("record_id"), limit=160),
                    "revision": item.get("revision"),
                    "sha256": _text(item.get("sha256"), limit=64),
                }
                for item in (row.get("entity_refs") or [])
                if isinstance(item, Mapping)
            ][:32],
        }
        for row in repository_rows
        if isinstance(row, Mapping)
    ][:8]
    return {
        "kind": "user_memory_and_repository" if repository_evidence else "user_and_memory",
        "topic": _text(learning.get("title") or direct, limit=240, fallback="life learning"),
        "content": combined[:32_000],
        "memory_refs": [
            row["memory_id"] for row in memories if row["memory_id"]
        ],
        "derivation_refs": [
            row["derivation_id"] for row in memories if row["derivation_id"]
        ],
        "repository_evidence": repository_evidence,
        "source_sha256": canonical_sha256({
            "request": direct,
            "material": material,
            "memories": memories,
            "repository_evidence": repository_evidence,
        }),
    }


def _needs_network(topic: str, source: Mapping[str, Any]) -> bool:
    text = f"{topic}\n{source.get('content') or ''}".casefold()
    return any(term.casefold() in text for term in (*_NETWORK_TERMS, *_TEMPORAL_TERMS))


def _queries(topic: str) -> list[str]:
    clean = re.sub(r"\s+", " ", topic).strip()[:120] or "life learning"
    return [f"{clean} official documentation primary source", f"{clean} standard paper best practices"]


def _normalise_items(value: Any) -> list[dict[str, str]]:
    if isinstance(value, Mapping):
        for key in ("items", "results", "data", "result"):
            if key in value:
                return _normalise_items(value[key])
        value = [value]
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for raw in value:
        if isinstance(raw, Mapping):
            rows.append({
                "title": _text(raw.get("title") or raw.get("name"), limit=240),
                "url": _text(raw.get("url") or raw.get("link"), limit=800),
                "content": _text(raw.get("content") or raw.get("summary") or raw.get("snippet") or raw.get("text"), limit=4_000),
            })
        elif isinstance(raw, str):
            rows.append({"title": "", "url": "", "content": _text(raw, limit=4_000)})
    return rows


def _screen(items: list[dict[str, str]], topic: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    topic_terms = set(_terms(topic))
    for row in items:
        text = f"{row.get('title', '')}\n{row.get('content', '')}"
        lowered = text.casefold()
        reason = ""
        if not row.get("content"):
            reason = "empty_content"
        elif any(marker in lowered for marker in _INJECTION_TERMS):
            reason = "prompt_injection_marker"
        elif topic_terms and not topic_terms.intersection(_terms(text)):
            reason = "topic_irrelevant"
        elif row.get("url") and not row["url"].startswith(("https://", "http://")):
            reason = "unsupported_url"
        if reason:
            rejected.append({**row, "rejection_reason": reason})
        elif len(accepted) < 8:
            accepted.append(row)
    return accepted, rejected[:16]


def _fallback_artifact(learning: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    draft = deepcopy(dict(learning.get("draft_artifact") or {}))
    summary = _text(learning.get("summary"), limit=4_000, fallback="Learning result")
    citations = [item.get("url") or item.get("title") for item in evidence.get("accepted") or []]
    source_note = "\n".join(f"- {item}" for item in citations if item)
    body = _text(draft.get("content") or draft.get("markdown"), limit=200_000, fallback=f"# {learning.get('title') or 'Learning'}\n\n{summary}")
    if source_note:
        body = f"{body}\n\n## Evidence\n{source_note}"
    draft["content"] = body
    return draft


def execute_learning_preview(
    learning: Mapping[str, Any],
    *,
    activity_scope: Mapping[str, Any],
    researcher: Callable[[str], Any] | None = None,
    synthesizer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Materialize a preview from bounded sources, evidence, and optional LLM.

    Research can only be read-only and is optional.  If unavailable, local
    user/memory material still produces an auditable preview rather than a
    fabricated claim that network research occurred.
    """
    source = _source(learning, activity_scope)
    topic = source["topic"]
    network_requested = _needs_network(topic, source)
    raw: list[dict[str, str]] = []
    errors: list[str] = []
    queries = _queries(topic) if network_requested else []
    if network_requested and callable(researcher):
        for query in queries:
            try:
                raw.extend(_normalise_items(researcher(query)))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}:{query[:120]}")
    accepted, rejected = _screen(raw, topic)
    evidence = {
        "schema": LEARNING_EXECUTOR_SCHEMA,
        "source": source,
        "queries": queries,
        "network_requested": network_requested,
        "network_used": bool(raw),
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors[-4:],
    }
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    synthesis_input = {
        "schema": LEARNING_EXECUTOR_SCHEMA,
        "learning": {
            key: deepcopy(learning.get(key))
            for key in ("learning_id", "target", "title", "summary", "risk_level", "draft_artifact", "learning_plan")
        },
        "evidence": evidence,
    }
    patch: dict[str, Any] = {}
    if callable(synthesizer):
        try:
            response = synthesizer(synthesis_input)
            if isinstance(response, Mapping):
                patch = {key: deepcopy(response[key]) for key in ("title", "summary", "draft_artifact") if key in response}
        except Exception as exc:
            errors.append(f"synthesizer:{type(exc).__name__}")
    patch.setdefault("draft_artifact", _fallback_artifact(learning, evidence))
    evidence["errors"] = errors[-4:]
    evidence["evidence_sha256"] = canonical_sha256({key: evidence[key] for key in evidence if key != "evidence_sha256"})
    status = "completed" if not errors else "completed_with_warnings"
    return {
        "schema": LEARNING_EXECUTOR_SCHEMA,
        "status": status,
        "source": source,
        "evidence": evidence,
        "patch": patch,
        "execution_sha256": canonical_sha256({"evidence": evidence, "patch": patch, "status": status}),
    }


__all__ = ["LEARNING_EXECUTOR_SCHEMA", "LearningExecutionError", "execute_learning_preview"]
