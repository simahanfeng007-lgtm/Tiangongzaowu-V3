"""Canonical Omni Body action names and fail-closed argument validation.

The execution model is allowed to propose calls, but it is not authoritative
about action aliases, path scope, or argument shape.  Validation in this
module is intentionally side-effect free so callers can reject malformed
requests before creating operation records, taking mutation locks, or making
rollback snapshots.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


CANONICAL_ACTION_ALIASES: dict[str, str] = {
    "file.patch_replace": "code.patch_replace",
    "file.patch": "code.patch_replace",
    "code.patch": "code.patch_replace",
}

NOVEL_ACTIONS = frozenset(
    {
        "novel.project.create",
        "novel.project.status",
        "novel.project.recover",
        "novel.blueprint.update",
        "novel.blueprint.patch",
        "novel.blueprint.upsert_many",
        "novel.blueprint.assist",
        "novel.reference.resolve",
        "novel.timeline.calculate",
        "novel.timeline.shift_suffix",
        "novel.timeline.normalize",
        "novel.mobility.align_initial_many",
        "novel.blueprint.compile",
        "novel.plan.rebase",
        "novel.chapter.checkout",
        "novel.chapter.submit",
        "novel.scene.design",
        "novel.context.query",
        "novel.project.audit",
    }
)

NOVEL_BLUEPRINT_LIST_SECTIONS = frozenset(
    {
        "characters",
        "locations",
        "routes",
        "schedules",
        "progression_rules",
        "plot_events",
        "chapters",
        "relationships",
        "foreshadows",
        "emotional_accounts",
    }
)
NOVEL_BLUEPRINT_OBJECT_SECTIONS = frozenset({"story", "world", "calendar", "settings"})
NOVEL_BLUEPRINT_REQUIRED_LIST_SECTIONS = frozenset(
    {"characters", "locations", "plot_events", "chapters"}
)

PATH_TARGET_ACTIONS = frozenset(
    {
        "code.patch_replace",
        "code.read",
        "code.write",
        "file.append",
        "file.copy",
        "file.delete_to_trash",
        "file.hash",
        "file.list",
        "file.mkdir",
        "file.move",
        "file.read",
        "file.rename",
        "file.search",
        "file.write",
        "docx.create",
        "pptx.create",
        "pptx.read",
        "mindmap.create",
        "qc.ppt.delivery_check",
        "quality.javascript_syntax",
        "quality.python_syntax",
        *NOVEL_ACTIONS,
    }
)

ACTION_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "life.body.state.query": {
        "target": "empty; reads this Life's current state",
        "args": {
            "sections": "optional array: identity|health|emotion|drives|lifecycle|autonomy|environment|evolution|memory|recent_actions|body|context|summary",
            "recent_limit": "optional integer from 0 to 50",
        },
    },
    "template.apply": {
        "target": "optional workspace-relative story/outline output path",
        "args": {
            "template_id": "template id such as executive_ppt",
            "variables": "optional content variables object",
            "design_output": "optional workspace-relative machine-readable design output .json path",
        },
    },
    "qc.ppt.delivery_check": {
        "target": "existing workspace .pptx path (required)",
        "args": {"min_slides": "optional positive integer", "min_visual_coverage": "optional ratio from 0 to 1"},
    },
    "skill.route": {
        "target": "optional explicit skill_id; normally empty",
        "args": {"job": "task description", "context": "optional routing context object", "skill_id": "optional exact skill id"},
        "any_of": ["target", "args.job", "args.context", "args.skill_id"],
    },
    "skill.list": {
        "target": "optional intent filter",
        "args": {"intent": "optional intent filter", "category": "optional exact category"},
    },
    "skill.get": {
        "target": "exact skill_id (required unless args.skill_id is provided)",
        "args": {"skill_id": "exact skill id"},
        "any_of": ["target", "args.skill_id"],
    },
    "skill.read": {
        "target": "exact skill_id (required unless args.skill_id is provided)",
        "args": {"skill_id": "exact skill id"},
        "any_of": ["target", "args.skill_id"],
    },
    "file.write": {
        "target": "workspace-relative file path (required)",
        "args": {"content": "string, or base64 when binary=true", "binary": "optional boolean", "encoding": "optional string"},
        "any_of": ["args.content", "args.base64"],
    },
    "file.append": {
        "target": "workspace-relative file path (required)",
        "args": {"content": "string (required)", "encoding": "optional string"},
        "required": ["args.content"],
    },
    "file.mkdir": {
        "target": "workspace-relative directory path (required)",
        "args": {"exist_ok": "optional boolean"},
    },
    "file.copy": {
        "target": "workspace-relative source path (required)",
        "args": {"destination": "workspace-relative destination path (required)", "overwrite": "optional boolean"},
        "required": ["args.destination"],
    },
    "file.move": {
        "target": "workspace-relative source path (required)",
        "args": {"destination": "workspace-relative destination path (required)", "overwrite": "optional boolean"},
        "required": ["args.destination"],
    },
    "file.rename": {
        "target": "workspace-relative source path (required)",
        "args": {"new_name": "new basename only (required)", "overwrite": "optional boolean"},
        "required": ["args.new_name"],
    },
    "code.patch_replace": {
        "aliases": ["file.patch_replace", "file.patch", "code.patch"],
        "target": "workspace-relative existing file path (required, never a directory)",
        "args": {
            "find": "non-empty literal or regex string (required)",
            "replace": "replacement string (optional; empty deletes the match)",
            "regex": "optional boolean",
            "count": "optional non-negative integer",
            "allow_noop": "optional boolean",
            "encoding": "optional string",
        },
        "required": ["args.find"],
    },
    "quality.javascript_syntax": {
        "target": "workspace-relative .js/.mjs/.cjs file or directory (required)",
        "args": {"recursive": "optional boolean"},
    },
    "shell.run": {
        "target": "empty; execution cwd is the backend-owned workspace",
        "args": {"command": "string or argv array (required)", "timeout": "optional positive integer"},
        "required": ["args.command"],
        "windows_note": "cmd.exe semantics; prefer typed file/code actions",
    },
    "python.run": {
        "target": "optional workspace-relative existing .py script",
        "args": {"code": "Python source string required when target is empty", "argv": "optional array", "timeout": "optional positive integer"},
        "any_of": ["target", "args.code"],
        "note": "Use file.write/code.write for file creation; python.run is not a file-writing substitute.",
    },
    "docx.create": {
        "target": "workspace-relative output .docx path (required)",
        "args": {
            "source": "optional existing workspace .md or .txt source; preferred for long documents",
            "content": "optional inline Markdown/plain text",
            "title": "optional document title for structured mode",
            "subtitle": "optional subtitle",
            "sections": "optional array of {heading, level, paragraphs, bullets, table}",
        },
        "any_of": ["args.source", "args.content", "args.title", "args.sections"],
    },
    "pptx.create": {
        "target": "workspace-relative output .pptx path (required)",
        "args": {
            "source": "optional existing workspace .md or .txt slide script; split slides with --- or ##",
            "content": "optional inline slide Markdown",
            "title": "optional title slide title",
            "subtitle": "optional title slide subtitle",
            "slides": "optional array of {title, bullets|body, notes, chart, table, image}",
            "template_id": "optional shipped design template id; defaults to executive_ppt",
            "design_spec": "optional workspace .json path or compact design object returned by template.apply",
            "style": "optional named preset or compact design override object",
        },
        "any_of": ["args.source", "args.content", "args.title", "args.slides"],
    },
    "pptx.read": {
        "target": "existing workspace .pptx path (required)",
        "args": {"max_chars_per_slide": "optional integer from 200 to 20000"},
    },
    "mindmap.create": {
        "target": "workspace-relative output .md path (required)",
        "args": {
            "source": "optional existing workspace .md or .txt indented outline",
            "content": "optional inline indented outline",
            "title": "map root title",
            "tree": "optional nested object/array tree",
            "opml": "optional boolean; also emit .opml",
        },
        "any_of": ["args.source", "args.content", "args.tree"],
    },
    "novel.project.create": {
        "target": "workspace-relative new or empty novel project directory (required)",
        "args": {"title": "non-empty string", "genre": "non-empty string", "planned_chapters": "full-book count, integer >= ceil(target_words/5000), never a writing checkpoint", "target_words": "integer >= 1000"},
        "required": ["args.title", "args.genre", "args.planned_chapters", "args.target_words"],
    },
    "novel.project.status": {"target": "managed novel project directory (required)", "args": {}},
    "novel.project.recover": {"target": "managed novel project directory (required)", "args": {}},
    "novel.blueprint.update": {
        "target": "managed novel project directory (required)",
        "args": {
            "section": "supported blueprint section",
            "data": "direct JSON array for list sections; JSON object for story/calendar/settings; never wrap arrays in item/items",
            "expected_revision": "optional non-negative integer",
            "replace_all": "must be omitted for list sections; destructive whole-list replacement is forbidden",
        },
        "required": ["args.section", "args.data"],
        "section_contracts": {
            "story": {"soul": "non-empty story soul", "core_conflict": "non-empty central conflict", "ending": "non-empty ending direction", "themes": "non-empty string array", "protected_anchors": "non-empty immutable anchor id array"},
            "calendar": {"tick_unit": "minute|hour|day|month|year", "ticks_per_year": "positive integer", "start_tick": "integer"},
            "characters[]": {"id": "unique string", "name": "string", "birth_tick": "integer", "age_at_start": "non-negative integer consistent with calendar", "initial": "object with location and realm"},
            "plot_events[]": {"id": "unique string", "chapter": "planned chapter number", "phase": "setup|develop|turn|close", "start_tick": "integer", "duration_ticks": "positive integer", "participants": "non-empty character id array", "location": "declared location id", "evidence_terms": "1-3 concrete prose terms"},
            "chapters[]": {"number": "every integer from 1 through project.planned_chapters", "title": "string", "event_ids": "non-empty ids bound to this chapter", "participants": "non-empty ids", "locations": "non-empty ids", "start_tick": "integer", "duration_ticks": "positive integer", "required_outcomes": "non-empty tags", "theme_tags": "non-empty tags"},
        },
    },
    "novel.blueprint.compile": {"target": "managed novel project directory (required)", "args": {}},
    "novel.blueprint.patch": {
        "target": "managed novel project directory (required)",
        "args": {
            "section": "supported section",
            "selector": "{} for object sections; exactly {id: ...} or chapters {number: ...} for list sections",
            "changes": "non-empty merge object",
            "expected_revision": "optional non-negative integer",
            "create_if_missing": "optional boolean",
        },
        "required": ["args.section", "args.selector", "args.changes"],
    },
    "novel.blueprint.upsert_many": {
        "target": "managed novel project directory (required)",
        "args": {
            "section": "list section only",
            "items": "non-empty array; at most 15 chapters or 30 objects for other sections; chapters require number, other sections may omit id for backend allocation",
            "expected_revision": "optional non-negative integer",
        },
        "required": ["args.section", "args.items"],
    },
    "novel.blueprint.assist": {
        "target": "managed novel project directory (required)",
        "args": {"previous_energy": "optional non-negative integer", "batch_size": "optional integer 1-20"},
    },
    "novel.reference.resolve": {
        "target": "managed novel project directory (required)",
        "args": {"entity_type": "character|location|event|chapter", "queries": "non-empty label array"},
        "required": ["args.entity_type", "args.queries"],
    },
    "novel.timeline.calculate": {
        "target": "managed novel project directory (required)",
        "args": {"operation": "age|arrival|overlap", "operation_fields": "see action schema and repair errors"},
        "required": ["args.operation"],
    },
    "novel.timeline.shift_suffix": {
        "target": "managed novel project directory (required)",
        "args": {
            "event_id": "canonical pivot event id",
            "delta_ticks": "positive integer gap to insert before the pivot and its chronological suffix",
            "reason": "non-empty explanation",
            "expected_revision": "optional non-negative integer",
        },
        "required": ["args.event_id", "args.delta_ticks", "args.reason"],
    },
    "novel.timeline.normalize": {
        "target": "managed novel project directory (required)",
        "args": {
            "reason": "non-empty explanation",
            "max_shifts": "optional integer 1-256; default 128",
            "expected_revision": "optional non-negative integer",
        },
        "required": ["args.reason"],
    },
    "novel.mobility.align_initial_many": {
        "target": "managed novel project directory (required)",
        "args": {
            "items": "1-30 objects with character_id and declared first physical location",
            "expected_revision": "optional non-negative integer",
        },
        "required": ["args.items"],
    },
    "novel.plan.rebase": {
        "target": "managed novel project directory (required)",
        "args": {"expected_state_hash": "current canonical state hash", "reason": "non-empty explanation", "event_updates": "future-only update array", "chapter_updates": "future-only update array", "maintained_anchor_ids": "all protected anchor ids"},
        "required": ["args.expected_state_hash", "args.reason", "args.event_updates", "args.chapter_updates", "args.maintained_anchor_ids"],
    },
    "novel.chapter.checkout": {
        "target": "managed novel project directory (required)",
        "args": {"chapter_number": "positive integer"},
        "required": ["args.chapter_number"],
    },
    "novel.chapter.submit": {
        "target": "managed novel project directory (required)",
        "args": {
            "lease_id": "checkout lease id",
            "chapter_number": "positive integer",
            "title": "non-empty string",
            "content": "final prose string",
            "actual": {
                "summary": "required factual summary string",
                "theme_tags": ["string"],
                "events": [{"id": "planned event id", "status": "progressed|turned|closed", "start_tick": "integer", "duration_ticks": "positive integer", "participants": ["character id"], "location": "location id", "evidence_terms": ["exact prose term"]}],
                "state_changes": [{"character_id": "id", "field": "alive|location|realm|injuries|inventory|knowledge", "from": "optional current value", "to": "new value", "op": "optional set|add|remove"}],
                "relationship_changes": [{"relationship_id": "declared id", "character_ids": ["id"], "delta": "number", "state": "string"}],
                "foreshadow_ops": [{"id": "id", "op": "planted|reinforced|revealed|resolved", "note": "string"}],
                "emotional_transactions": [{"account_id": "declared id", "kind": "deposit|withdraw", "evidence_terms": ["exact prose term"], "factors": "0..1 factor object", "related_event_ids": ["event id"]}],
                "convergence_proof": {"reason": "required only for high deviation", "maintained_anchor_ids": ["protected anchor id"]},
            },
        },
        "required": ["args.lease_id", "args.chapter_number", "args.title", "args.content", "args.actual"],
    },
    "novel.scene.design": {
        "target": "managed novel project directory (required)",
        "args": {"trigger_id": "pending emotion trigger id", "candidates": "array of 2-3 structured candidates"},
        "required": ["args.trigger_id", "args.candidates"],
    },
    "novel.context.query": {
        "target": "managed novel project directory (required)",
        "args": {"entity_type": "character|event|foreshadow|relationship|chapter|emotion", "entity_ids": "array of stable ids"},
        "required": ["args.entity_type", "args.entity_ids"],
    },
    "novel.project.audit": {"target": "managed novel project directory (required)", "args": {"scope": "optional all|timeline|events|emotion|files"}},
}


def canonical_action(action: Any) -> str:
    normalized = str(action or "").strip().lower()
    return CANONICAL_ACTION_ALIASES.get(normalized, normalized)


def schema_for_action(action: Any) -> dict[str, Any]:
    normalized = canonical_action(action)
    return {
        "action": normalized,
        **dict(ACTION_ARGUMENT_SCHEMAS.get(normalized) or {
            "target": "action-specific target; use system.action_schema when uncertain",
            "args": "action-specific object",
        }),
    }


def _inside_workspace(raw: str, workspace: str | Path) -> tuple[bool, str]:
    root = Path(workspace).expanduser().resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return False, str(resolved)
    return True, str(resolved)


# D-21 用户指定即授权（与网关 omni_grant_authority 同一口径）：
# 用户本轮明确指定的路径根之外的 outside_workspace 判定豁免；硬禁区不豁免。
_CONTRACT_HARD_DENY_DIR_PARTS = frozenset({".ssh", ".aws", ".gnupg", ".azure", ".config"})
_CONTRACT_HARD_DENY_PREFIXES = (
    os.path.normcase(os.environ.get("SystemRoot") or r"C:\Windows"),
    os.path.normcase(os.environ.get("ProgramFiles") or r"C:\Program Files"),
    os.path.normcase(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
    os.path.normcase(os.environ.get("ProgramData") or r"C:\ProgramData"),
)
_COMMAND_ABSOLUTE_PATH_RE = re.compile(
    r"""(?<![A-Za-z0-9])([A-Za-z]:\\(?:[^"\s\\]|\\.)*|\\\\[^"\s\\]+(?:\\[^"\s\\]+)+)"""
)


def _contract_hard_deny(resolved: Path) -> bool:
    text = os.path.normcase(str(resolved))
    for prefix in _CONTRACT_HARD_DENY_PREFIXES:
        if text == prefix or text.startswith(prefix + os.sep):
            return True
    if len(text) <= 3:
        return True
    if any(part.casefold() in _CONTRACT_HARD_DENY_DIR_PARTS for part in resolved.parts):
        return True
    return resolved.name.casefold().endswith(".env")


def _user_specified_allowed(resolved_text: str, user_roots: Iterable[str | Path]) -> bool:
    if not user_roots:
        return False
    resolved = Path(resolved_text)
    if _contract_hard_deny(resolved):
        return False
    for root in user_roots:
        try:
            resolved.relative_to(Path(str(root)).resolve(strict=False))
            return True
        except (ValueError, OSError):
            continue
    return False


def _contract_full_disk_mode() -> bool:
    """全盘写入模式：TIANGONG_WORKSPACE_MODE=full（设置面板切换，重启后生效）。"""
    return str(os.environ.get("TIANGONG_WORKSPACE_MODE") or "").strip().lower() == "full"


def _contract_path_allowed(
    inside: bool,
    resolved_text: str,
    user_roots: Iterable[str | Path],
) -> bool:
    """工作区/全盘统一的路径放行判定。

    硬禁区（Windows 核心目录、凭据目录、磁盘根、.env）永不放行；
    全盘模式下工作区外路径放行；工作区模式下仅工作区内或用户显式指定根放行。
    """
    resolved = Path(str(resolved_text))
    if _contract_hard_deny(resolved):
        return False
    if inside:
        return True
    if _contract_full_disk_mode():
        return True
    return _user_specified_allowed(str(resolved), user_roots)


def _command_hard_deny_issues(command: Any) -> list[dict]:
    """扫描 shell/python 命令文本中的绝对路径，命中硬禁区即拒绝。

    全盘模式也只放行结构化路径字段，命令文本里的硬禁区（Windows 核心目录、
    凭据目录等）同样不放行，防止通过 shell 绕过硬禁区。
    """
    text = str(command or "")
    if not text.strip():
        return []
    issues: list[dict] = []
    seen: set[str] = set()
    for match in _COMMAND_ABSOLUTE_PATH_RE.finditer(text):
        token = str(match.group(1) or "").strip().strip('"').strip("'")
        if not token or token in seen:
            continue
        seen.add(token)
        try:
            resolved = Path(token).expanduser().resolve(strict=False)
        except Exception:
            continue
        if _contract_hard_deny(resolved):
            issues.append(
                _issue(
                    "args.command",
                    "hard_deny_path",
                    f"command references hard-deny path: {token}",
                )
            )
            break
    return issues


def _managed_novel_prose_path(raw: str, workspace: str | Path) -> bool:
    root = Path(workspace).expanduser().resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    for parent in (resolved, *resolved.parents):
        if parent == root.parent:
            break
        if parent.name == "正文" and (parent.parent / ".novel-system" / "manifest.json").exists():
            return True
        if parent == root:
            break
    return False


def _workspace_has_managed_novel(workspace: str | Path) -> bool:
    root = Path(workspace).expanduser().resolve()
    try:
        return any(root.rglob(".novel-system/manifest.json"))
    except OSError:
        return False


def _issue(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _complete_novel_blueprint_item_issues(section: str, item: Mapping[str, Any], path: str) -> list[dict[str, str]]:
    """Side-effect-free structural checks for canonical blueprint list items."""
    issues: list[dict[str, str]] = []

    def non_empty_string(field: str) -> None:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(_issue(f"{path}.{field}", "required_non_empty_string", f"{field} must be a non-empty string"))

    def integer(field: str, *, positive: bool = False, non_negative: bool = False) -> None:
        value = item.get(field)
        valid = isinstance(value, int) and not isinstance(value, bool)
        if positive:
            valid = valid and value > 0
        if non_negative:
            valid = valid and value >= 0
        if not valid:
            issues.append(_issue(f"{path}.{field}", "integer", f"{field} must be a valid JSON integer"))

    def string_array(field: str, *, maximum: int | None = None) -> None:
        value = item.get(field)
        valid = isinstance(value, list) and bool(value) and all(isinstance(row, str) and row.strip() for row in value)
        if maximum is not None:
            valid = valid and len(value) <= maximum
        if not valid:
            issues.append(_issue(f"{path}.{field}", "non_empty_string_array", f"{field} must be a non-empty string array"))

    if section == "characters":
        non_empty_string("id")
        non_empty_string("name")
        integer("birth_tick")
        integer("age_at_start", non_negative=True)
        initial = item.get("initial")
        if not isinstance(initial, Mapping):
            issues.append(_issue(f"{path}.initial", "object_required", "initial must contain location and realm"))
        else:
            for field in ("location", "realm"):
                value = initial.get(field)
                if not isinstance(value, str) or not value.strip():
                    issues.append(_issue(f"{path}.initial.{field}", "required_non_empty_string", f"initial.{field} must be a non-empty string"))
    elif section == "locations":
        non_empty_string("id")
        non_empty_string("name")
    elif section == "plot_events":
        non_empty_string("id")
        integer("chapter", positive=True)
        if item.get("phase") not in {"setup", "develop", "turn", "close"}:
            issues.append(_issue(f"{path}.phase", "enum", "phase must be setup, develop, turn, or close"))
        integer("start_tick")
        integer("duration_ticks", positive=True)
        string_array("participants")
        non_empty_string("location")
        string_array("evidence_terms", maximum=3)
    elif section == "chapters":
        integer("number", positive=True)
        non_empty_string("title")
        string_array("event_ids")
        string_array("participants")
        string_array("locations")
        integer("start_tick")
        integer("duration_ticks", positive=True)
        string_array("required_outcomes")
        string_array("theme_tags")
    return issues


def _decode_novel_item_wrappers(value: Any) -> Any:
    """Decode MiniMax XML-style item wrappers without guessing domain ids."""
    if isinstance(value, list):
        flattened: list[Any] = []
        for item in value:
            decoded = _decode_novel_item_wrappers(item)
            if isinstance(decoded, list):
                flattened.extend(decoded)
            else:
                flattened.append(decoded)
        return flattened
    if isinstance(value, Mapping):
        keys = set(value)
        if keys in ({"item"}, {"items"}):
            key = "item" if "item" in value else "items"
            nested = value.get(key)
            rows = nested if isinstance(nested, list) else [nested]
            flattened: list[Any] = []
            for item in rows:
                decoded = _decode_novel_item_wrappers(item)
                if isinstance(decoded, list):
                    flattened.extend(decoded)
                else:
                    flattened.append(decoded)
            return flattened
        return {str(key): _decode_novel_item_wrappers(item) for key, item in value.items()}
    return value


def _normalize_chapter_actual_sequences(value: Any) -> Any:
    """Normalize only schema-declared chapter arrays after lossless wrapper decoding."""
    if not isinstance(value, Mapping):
        return value
    actual = copy.deepcopy(dict(value))
    object_arrays = ("events", "state_changes", "relationship_changes", "foreshadow_ops", "emotional_transactions")
    for field in object_arrays:
        rows = actual.get(field)
        if isinstance(rows, list):
            actual[field] = [
                row for row in rows
                if row is not None and not (isinstance(row, str) and not row.strip())
            ]
    string_arrays = ("theme_tags",)
    for field in string_arrays:
        rows = actual.get(field)
        if isinstance(rows, list):
            actual[field] = [row for row in rows if isinstance(row, str) and row.strip()]
    for event in actual.get("events") or []:
        if not isinstance(event, dict):
            continue
        for field in ("participants", "evidence_terms", "requires_events", "caused_by_event_ids", "outcome_tags"):
            rows = event.get(field)
            if isinstance(rows, list):
                event[field] = [row for row in rows if isinstance(row, str) and row.strip()]
    for change in actual.get("relationship_changes") or []:
        if isinstance(change, dict) and isinstance(change.get("character_ids"), list):
            change["character_ids"] = [row for row in change["character_ids"] if isinstance(row, str) and row.strip()]
    for transaction in actual.get("emotional_transactions") or []:
        if not isinstance(transaction, dict):
            continue
        for field in ("evidence_terms", "related_event_ids"):
            rows = transaction.get(field)
            if isinstance(rows, list):
                transaction[field] = [row for row in rows if isinstance(row, str) and row.strip()]
    proof = actual.get("convergence_proof")
    if isinstance(proof, dict) and isinstance(proof.get("maintained_anchor_ids"), list):
        proof["maintained_anchor_ids"] = [row for row in proof["maintained_anchor_ids"] if isinstance(row, str) and row.strip()]
    return actual


NOVEL_INTEGER_FIELDS = frozenset(
    {
        "planned_chapters", "target_words", "number", "chapter_number", "chapter",
        "start_tick", "duration_ticks", "birth_tick", "age_at_start", "realm_since_tick",
        "ticks_per_year", "min_duration_ticks", "phase_tick", "period_ticks",
        "deadline_chapter", "initial_balance", "threshold", "previous_energy", "batch_size", "delta_ticks", "max_shifts",
    }
)


def _coerce_novel_integer_fields(value: Any) -> Any:
    """Losslessly coerce decimal strings only for schema-declared integer fields."""
    if isinstance(value, list):
        return [_coerce_novel_integer_fields(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            item = _coerce_novel_integer_fields(raw_value)
            if key in NOVEL_INTEGER_FIELDS and isinstance(item, str) and re.fullmatch(r"-?\d+", item.strip()):
                item = int(item.strip())
            normalized[key] = item
        return normalized
    return value


def _windows_shell_issues(command: Any) -> list[dict[str, str]]:
    if os.name != "nt" or not isinstance(command, str):
        return []
    value = command.strip()
    issues: list[dict[str, str]] = []
    typed_starts = {
        "mkdir": "file.mkdir",
        "md": "file.mkdir",
        "copy": "file.copy",
        "move": "file.move",
        "ren": "file.rename",
        "rename": "file.rename",
        "del": "file.delete_to_trash",
        "erase": "file.delete_to_trash",
        "type": "file.read",
    }
    first = re.split(r"\s+", value, maxsplit=1)[0].lower() if value else ""
    if first in typed_starts:
        issues.append(
            _issue(
                "args.command",
                "typed_tool_required",
                f"Do not use {first} through shell.run; use {typed_starts[first]} with structured target/args.",
            )
        )
    unix_tokens = re.findall(r"(?:^|[|&;]\s*)(head|tail|grep|sed|awk|cat|ls|bash|sh)(?=\s|$)", value, re.IGNORECASE)
    if unix_tokens:
        issues.append(
            _issue(
                "args.command",
                "wrong_platform_shell",
                "Windows shell.run uses cmd.exe, not bash; use file.read/file.list/file.search or a Windows-compatible argv command.",
            )
        )
    if re.search(r"\bmkdir\s+-p\b|\$\([^)]*\)|(?:^|[;&]\s*)export\s+\w+=|/bin/(?:ba)?sh", value, re.IGNORECASE):
        issues.append(
            _issue(
                "args.command",
                "bash_syntax_forbidden",
                "Bash-only syntax is forbidden on this Windows cmd.exe runtime; use typed tools or Windows-compatible commands.",
            )
        )
    return issues


def validate_tool_request(
    action: Any,
    target: Any,
    args: Any,
    *,
    workspace: str | Path,
    available_actions: Iterable[str] = (),
    user_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Return a normalized request or a machine-repairable rejection.

    D-21: ``user_roots`` carries the gateway-attested user-specified path roots
    (extracted from the user's own message by the grant authority).  Paths under
    those roots skip the outside_workspace issue; hard-deny zones never skip.
    """
    received_action = str(action or "").strip()
    normalized = canonical_action(received_action)
    normalized_target = str(target or "").strip()
    issues: list[dict[str, str]] = []
    payload = dict(args) if isinstance(args, Mapping) else {}
    argument_aliases: list[str] = []
    available = sorted({canonical_action(item) for item in available_actions if str(item).strip()})

    if normalized in {"docx.create", "pptx.create", "mindmap.create"} and "source" not in payload:
        for alias in ("source_path", "markdown_path", "markdown_file", "input"):
            if isinstance(payload.get(alias), str) and payload[alias].strip():
                payload["source"] = payload.pop(alias)
                argument_aliases.append(f"args.{alias}->args.source")
                break

    if normalized == "novel.blueprint.upsert_many":
        raw_items = payload.get("items")
        if isinstance(raw_items, Mapping):
            if set(raw_items) == {"items"} and isinstance(raw_items.get("items"), list):
                payload["items"] = list(raw_items["items"])
                argument_aliases.append("args.items.items->args.items")
            elif set(raw_items) == {"item"} and isinstance(raw_items.get("item"), list):
                payload["items"] = list(raw_items["item"])
                argument_aliases.append("args.items.item->args.items")
            elif set(raw_items) == {"item"} and isinstance(raw_items.get("item"), Mapping):
                payload["items"] = [dict(raw_items["item"])]
                argument_aliases.append("args.items.item->args.items[]")
            else:
                payload["items"] = [dict(raw_items)]
                argument_aliases.append("args.items.object->args.items[]")
        elif (not isinstance(raw_items, list) or not raw_items) and isinstance(payload.get("item"), Mapping):
            payload["items"] = [dict(payload["item"])]
            argument_aliases.append("args.item->args.items[]")
        normalized_items = _decode_novel_item_wrappers(payload.get("items"))
        if normalized_items != payload.get("items"):
            payload["items"] = normalized_items
            argument_aliases.append("args.items.nested-item-wrapper->array")

    if normalized in {"novel.context.query", "novel.reference.resolve"}:
        entity_aliases = {
            "characters": "character",
            "locations": "location",
            "events": "event",
            "chapters": "chapter",
            "foreshadows": "foreshadow",
            "relationships": "relationship",
            "emotions": "emotion",
        }
        entity_type = str(payload.get("entity_type") or "").strip().lower()
        if entity_type in entity_aliases:
            payload["entity_type"] = entity_aliases[entity_type]
            argument_aliases.append("args.entity_type.plural->singular")

    if normalized == "novel.context.query":
        raw_ids = payload.get("entity_ids")
        if isinstance(raw_ids, Mapping) and set(raw_ids) in ({"item"}, {"items"}):
            nested = raw_ids.get("item") if "item" in raw_ids else raw_ids.get("items")
            payload["entity_ids"] = list(nested) if isinstance(nested, list) else [nested]
            argument_aliases.append("args.entity_ids.item(s)->args.entity_ids")
        elif isinstance(raw_ids, (str, int)):
            payload["entity_ids"] = [raw_ids]
            argument_aliases.append("args.entity_ids.scalar->args.entity_ids[]")
        elif raw_ids is None and isinstance(payload.get("entity_id"), (str, int)):
            payload["entity_ids"] = [payload["entity_id"]]
            argument_aliases.append("args.entity_id->args.entity_ids[]")

    if normalized == "novel.reference.resolve":
        raw_queries = payload.get("queries")
        if isinstance(raw_queries, (str, int)):
            payload["queries"] = [raw_queries]
            argument_aliases.append("args.queries.scalar->args.queries[]")

    if normalized == "novel.blueprint.assist":
        for field in ("previous_energy", "batch_size"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip().isdigit():
                payload[field] = int(value.strip())
                argument_aliases.append(f"args.{field}.numeric-string->integer")

    if normalized in NOVEL_ACTIONS:
        for field in (
            "actual", "candidates", "event_updates", "chapter_updates", "maintained_anchor_ids",
        ):
            if field not in payload:
                continue
            decoded_value = _decode_novel_item_wrappers(payload.get(field))
            if decoded_value != payload.get(field):
                payload[field] = decoded_value
                argument_aliases.append(f"args.{field}.item-wrapper->array")
        if normalized == "novel.chapter.submit" and "actual" in payload:
            normalized_actual = _normalize_chapter_actual_sequences(payload.get("actual"))
            if normalized_actual != payload.get("actual"):
                payload["actual"] = normalized_actual
                argument_aliases.append("args.actual.schema-sequences->direct-arrays")
        coerced_payload = _coerce_novel_integer_fields(payload)
        if coerced_payload != payload:
            payload = coerced_payload
            argument_aliases.append("args.schema-integer.numeric-string->integer")

    if not normalized:
        issues.append(_issue("action", "required", "action is required"))
    elif available and normalized not in available:
        issues.append(_issue("action", "unknown_action", f"Unknown action: {received_action}"))
    if not isinstance(args, Mapping):
        issues.append(_issue("args", "type", "args must be a JSON object"))

    # Execution time is a resource boundary, not a best-effort hint.  Reject
    # negative, boolean, string, and effectively unbounded timeouts before a
    # subprocess or adapter can occupy a worker indefinitely.
    for timeout_field in ("timeout", "probe_timeout"):
        if timeout_field not in payload:
            continue
        timeout_value = payload.get(timeout_field)
        if (
            not isinstance(timeout_value, int)
            or isinstance(timeout_value, bool)
            or not 1 <= timeout_value <= 3600
        ):
            issues.append(
                _issue(
                    f"args.{timeout_field}",
                    "bounded_positive_integer",
                    f"{timeout_field} must be a JSON integer from 1 to 3600 seconds",
                )
            )

    if normalized in PATH_TARGET_ACTIONS:
        if not normalized_target:
            issues.append(
                _issue(
                    "target",
                    "required_non_empty",
                    f"{normalized} requires a non-empty workspace-relative target; the workspace root is never inferred.",
                )
            )
        else:
            inside, resolved = _inside_workspace(normalized_target, workspace)
            if not _contract_path_allowed(inside, resolved, user_roots):
                issues.append(_issue("target", "outside_workspace", f"target resolves outside workspace: {resolved}"))

    for field in ("destination", "output"):
        raw = payload.get(field)
        if normalized in {"file.copy", "file.move"} and field == "destination" and raw:
            inside, resolved = _inside_workspace(str(raw), workspace)
            if not _contract_path_allowed(inside, resolved, user_roots):
                issues.append(_issue(f"args.{field}", "outside_workspace", f"path resolves outside workspace: {resolved}"))

    if normalized in {"file.write", "code.write"} and "content" not in payload and "base64" not in payload:
        issues.append(_issue("args", "missing_content", f"{normalized} requires args.content or args.base64"))
    if normalized == "file.append" and "content" not in payload:
        issues.append(_issue("args.content", "required", "file.append requires args.content"))
    if normalized in {"file.copy", "file.move"} and not str(payload.get("destination") or "").strip():
        issues.append(_issue("args.destination", "required_non_empty", f"{normalized} requires args.destination"))
    if normalized == "file.rename":
        new_name = str(payload.get("new_name") or "").strip()
        if not new_name:
            issues.append(_issue("args.new_name", "required_non_empty", "file.rename requires args.new_name"))
        elif Path(new_name).name != new_name or "/" in new_name or "\\" in new_name:
            issues.append(_issue("args.new_name", "basename_only", "new_name must be a basename, not a path"))
    if normalized == "code.patch_replace":
        find = payload.get("find")
        if not isinstance(find, str) or not find:
            issues.append(_issue("args.find", "required_non_empty_string", "code.patch_replace requires a non-empty args.find string"))
        if "replace" in payload and not isinstance(payload.get("replace"), str):
            issues.append(_issue("args.replace", "type", "args.replace must be a string"))
        try:
            count = int(payload.get("count", 0))
            if count < 0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(_issue("args.count", "non_negative_integer", "args.count must be a non-negative integer"))
        if payload.get("regex") and isinstance(find, str):
            try:
                re.compile(find)
            except re.error as exc:
                issues.append(_issue("args.find", "invalid_regex", f"invalid regex: {exc}"))
    if normalized == "shell.run":
        command = payload.get("command")
        if not isinstance(command, (str, list, tuple)) or not command:
            issues.append(_issue("args.command", "required", "shell.run requires a non-empty string or argv array"))
        issues.extend(_windows_shell_issues(command))
        if isinstance(command, (str, list, tuple)):
            command_text = command if isinstance(command, str) else " ".join(str(item) for item in command)
            issues.extend(_command_hard_deny_issues(command_text))
    if normalized == "python.run":
        code = payload.get("code")
        if normalized_target:
            inside, resolved = _inside_workspace(normalized_target, workspace)
            if not _contract_path_allowed(inside, resolved, user_roots):
                issues.append(_issue("target", "outside_workspace", f"target resolves outside workspace: {resolved}"))
            elif Path(normalized_target).suffix.lower() != ".py":
                issues.append(_issue("target", "python_script_required", "python.run target must be a .py script"))
        elif not isinstance(code, str) or not code.strip():
            issues.append(_issue("args.code", "required_non_empty_string", "python.run requires a target .py script or non-empty args.code"))
        if isinstance(code, str) and code.strip():
            issues.extend(_command_hard_deny_issues(code))
            try:
                compile(code, "<python.run>", "exec")
            except SyntaxError as exc:
                issues.append(
                    _issue(
                        "args.code",
                        "python_syntax_error",
                        f"Python syntax error at line {exc.lineno}, column {exc.offset}: {exc.msg}",
                    )
                )

    if normalized == "life.body.state.query":
        if normalized_target:
            issues.append(_issue("target", "must_be_empty", "life.body.state.query does not accept a path target"))
        sections = payload.get("sections")
        allowed_sections = {
            "identity", "health", "emotion", "drives", "lifecycle",
            "autonomy", "environment", "evolution", "memory", "recent_actions",
            "body", "context", "summary",
        }
        if sections is not None and (
            not isinstance(sections, list)
            or not all(isinstance(item, str) and item in allowed_sections for item in sections)
        ):
            issues.append(_issue("args.sections", "enum_array", "sections contains an unsupported body state section"))
        recent_limit = payload.get("recent_limit")
        if recent_limit is not None and (
            not isinstance(recent_limit, int)
            or isinstance(recent_limit, bool)
            or not 0 <= recent_limit <= 50
        ):
            issues.append(_issue("args.recent_limit", "bounded_integer", "recent_limit must be an integer from 0 to 50"))

    if normalized in {"docx.create", "pptx.create", "mindmap.create"}:
        expected_suffix = {"docx.create": ".docx", "pptx.create": ".pptx", "mindmap.create": ".md"}[normalized]
        if normalized_target and Path(normalized_target).suffix.lower() != expected_suffix:
            issues.append(_issue("target", "output_extension", f"{normalized} target must end with {expected_suffix}"))
        source = payload.get("source")
        if source is not None:
            if not isinstance(source, str) or not source.strip():
                issues.append(_issue("args.source", "required_non_empty_string", "source must be a workspace .md or .txt path"))
            else:
                inside, resolved = _inside_workspace(source, workspace)
                if not _contract_path_allowed(inside, resolved, user_roots):
                    issues.append(_issue("args.source", "outside_workspace", f"source resolves outside workspace: {resolved}"))
                elif Path(source).suffix.lower() not in {".md", ".txt"}:
                    issues.append(_issue("args.source", "text_source_required", "source must be a .md or .txt file"))
        content = payload.get("content")
        if content is not None and (not isinstance(content, str) or not content.strip()):
            issues.append(_issue("args.content", "non_empty_string", "content must be a non-empty string"))
        has_structured = (
            bool(str(payload.get("title") or "").strip())
            or (normalized == "docx.create" and isinstance(payload.get("sections"), list) and bool(payload["sections"]))
            or (normalized == "pptx.create" and isinstance(payload.get("slides"), list) and bool(payload["slides"]))
            or (normalized == "mindmap.create" and isinstance(payload.get("tree"), (list, dict)) and bool(payload["tree"]))
        )
        if not (isinstance(source, str) and source.strip()) and not (isinstance(content, str) and content.strip()) and not has_structured:
            issues.append(_issue("args", "content_source_required", f"{normalized} requires args.source, args.content, or structured content"))

        if normalized == "pptx.create":
            template_id = payload.get("template_id")
            if template_id is not None and (
                not isinstance(template_id, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", template_id.strip())
            ):
                issues.append(_issue("args.template_id", "safe_template_id", "template_id must contain only letters, digits, underscore, or hyphen"))
            design_spec = payload.get("design_spec")
            if isinstance(design_spec, str) and design_spec.strip():
                inside, resolved = _inside_workspace(design_spec, workspace)
                if not _contract_path_allowed(inside, resolved, user_roots):
                    issues.append(_issue("args.design_spec", "outside_workspace", f"design_spec resolves outside workspace: {resolved}"))
                elif Path(design_spec).suffix.lower() != ".json":
                    issues.append(_issue("args.design_spec", "json_design_required", "design_spec path must be a .json file"))
            elif design_spec is not None and not isinstance(design_spec, Mapping):
                issues.append(_issue("args.design_spec", "object_or_path", "design_spec must be a JSON object or workspace .json path"))

    if normalized == "pptx.read":
        if normalized_target and Path(normalized_target).suffix.lower() != ".pptx":
            issues.append(_issue("target", "input_extension", "pptx.read target must end with .pptx"))
        max_chars = payload.get("max_chars_per_slide")
        if max_chars is not None and (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or not 200 <= max_chars <= 20_000
        ):
            issues.append(_issue("args.max_chars_per_slide", "bounded_integer", "max_chars_per_slide must be an integer from 200 to 20000"))

    if normalized == "template.apply":
        template_id = payload.get("template_id", payload.get("id"))
        if template_id is not None and (
            not isinstance(template_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", template_id.strip())
        ):
            issues.append(_issue("args.template_id", "safe_template_id", "template_id must contain only letters, digits, underscore, or hyphen"))
        design_output = payload.get("design_output")
        if design_output is not None:
            if not isinstance(design_output, str) or not design_output.strip():
                issues.append(_issue("args.design_output", "non_empty_path", "design_output must be a non-empty workspace .json path"))
            else:
                inside, resolved = _inside_workspace(design_output, workspace)
                if not _contract_path_allowed(inside, resolved, user_roots):
                    issues.append(_issue("args.design_output", "outside_workspace", f"design_output resolves outside workspace: {resolved}"))
                elif Path(design_output).suffix.lower() != ".json":
                    issues.append(_issue("args.design_output", "json_design_required", "design_output must end with .json"))

    if normalized == "qc.ppt.delivery_check":
        if normalized_target and Path(normalized_target).suffix.lower() != ".pptx":
            issues.append(_issue("target", "input_extension", "qc.ppt.delivery_check target must end with .pptx"))
        min_slides = payload.get("min_slides")
        if min_slides is not None and (
            not isinstance(min_slides, int)
            or isinstance(min_slides, bool)
            or not 1 <= min_slides <= 500
        ):
            issues.append(_issue("args.min_slides", "bounded_integer", "min_slides must be an integer from 1 to 500"))
        coverage = payload.get("min_visual_coverage")
        if coverage is not None and (
            not isinstance(coverage, (int, float))
            or isinstance(coverage, bool)
            or not 0 <= float(coverage) <= 1
        ):
            issues.append(_issue("args.min_visual_coverage", "ratio", "min_visual_coverage must be a number from 0 to 1"))

    if normalized in NOVEL_ACTIONS:
        if normalized == "novel.project.create":
            if not isinstance(payload.get("title"), str) or not payload.get("title", "").strip():
                issues.append(_issue("args.title", "required_non_empty_string", "novel.project.create requires title"))
            if not isinstance(payload.get("genre"), str) or not payload.get("genre", "").strip():
                issues.append(_issue("args.genre", "required_non_empty_string", "novel.project.create requires genre"))
            if not isinstance(payload.get("planned_chapters"), int) or isinstance(payload.get("planned_chapters"), bool) or payload.get("planned_chapters", 0) < 1:
                issues.append(_issue("args.planned_chapters", "positive_integer", "planned_chapters must be a positive integer"))
            if not isinstance(payload.get("target_words"), int) or isinstance(payload.get("target_words"), bool) or payload.get("target_words", 0) < 1000:
                issues.append(_issue("args.target_words", "minimum_1000", "target_words must be an integer >= 1000"))
            planned = payload.get("planned_chapters")
            words = payload.get("target_words")
            if (
                isinstance(planned, int) and not isinstance(planned, bool) and planned > 0
                and isinstance(words, int) and not isinstance(words, bool) and words >= 1000
            ):
                minimum_chapters = (words + 4999) // 5000
                if planned < minimum_chapters:
                    issues.append(
                        _issue(
                            "args.planned_chapters",
                            "full_book_chapter_count",
                            f"planned_chapters is the full-book count, not the writing checkpoint; target_words={words} requires at least {minimum_chapters} chapters",
                        )
                    )
        elif normalized == "novel.blueprint.update":
            section = str(payload.get("section") or "")
            if section not in {
                "story", "characters", "world", "calendar", "locations", "routes", "schedules",
                "progression_rules", "plot_events", "chapters", "relationships", "foreshadows",
                "emotional_accounts", "settings",
            }:
                issues.append(_issue("args.section", "enum", "Unsupported blueprint section"))
            data = payload.get("data")
            if isinstance(data, str):
                raw_data = data.strip()
                if len(raw_data) <= 1_000_000 and raw_data[:1] in {"[", "{"}:
                    try:
                        decoded_data = json.loads(raw_data)
                    except json.JSONDecodeError:
                        pass
                    else:
                        if isinstance(decoded_data, (dict, list)):
                            data = decoded_data
                            payload["data"] = data
                            argument_aliases.append("args.data.json-string->args.data")
            if section in NOVEL_BLUEPRINT_LIST_SECTIONS and isinstance(data, dict):
                wrapper = next(
                    (
                        key
                        for key in ("items", "item")
                        if set(data) == {key} and isinstance(data.get(key), list)
                    ),
                    None,
                )
                if wrapper:
                    data = data[wrapper]
                    payload["data"] = data
                    argument_aliases.append("args.data.item(s)->args.data")
            decoded_data = _decode_novel_item_wrappers(data)
            if decoded_data != data:
                data = decoded_data
                payload["data"] = data
                argument_aliases.append("args.data.nested-item-wrapper->array")
            if section in NOVEL_BLUEPRINT_LIST_SECTIONS:
                if not isinstance(data, list):
                    issues.append(
                        _issue(
                            "args.data",
                            "direct_array_required",
                            f"{section} data must be a direct JSON array, not an object wrapper",
                        )
                    )
                else:
                    if section in NOVEL_BLUEPRINT_REQUIRED_LIST_SECTIONS and not data:
                        issues.append(_issue("args.data", "required_array", f"{section} cannot be empty"))
                    if not all(isinstance(item, dict) for item in data):
                        issues.append(_issue("args.data", "object_items_required", f"Every {section} item must be an object"))
                    else:
                        max_items = 15 if section == "chapters" else 30
                        if len(data) > max_items:
                            issues.append(_issue("args.data", "bounded_object_array", f"{section} must contain at most {max_items} objects; use novel.blueprint.upsert_many for the next batch"))
                        for index, item in enumerate(data):
                            issues.extend(_complete_novel_blueprint_item_issues(section, item, f"args.data[{index}]"))
            elif section in NOVEL_BLUEPRINT_OBJECT_SECTIONS and not isinstance(data, dict):
                issues.append(_issue("args.data", "object_required", f"{section} data must be a JSON object"))
            elif section and not isinstance(data, (dict, list)):
                issues.append(_issue("args.data", "object_or_array", "blueprint data must be a JSON object or array"))
            if section == "calendar" and isinstance(data, dict):
                if str(data.get("tick_unit") or "").lower() not in {"minute", "hour", "day", "month", "year"}:
                    issues.append(_issue("args.data.tick_unit", "enum", "tick_unit must be minute, hour, day, month, or year"))
                for field in ("ticks_per_year", "start_tick"):
                    value = data.get(field)
                    if not isinstance(value, int) or isinstance(value, bool):
                        issues.append(_issue(f"args.data.{field}", "integer", f"{field} must be a JSON integer, not a numeric string"))
                if isinstance(data.get("ticks_per_year"), int) and data["ticks_per_year"] < 1:
                    issues.append(_issue("args.data.ticks_per_year", "positive_integer", "ticks_per_year must be positive"))
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
            if "replace_all" in payload and not isinstance(payload.get("replace_all"), bool):
                issues.append(_issue("args.replace_all", "boolean", "replace_all must be boolean"))
            if section in NOVEL_BLUEPRINT_LIST_SECTIONS and payload.get("replace_all") is True:
                issues.append(_issue("args.replace_all", "destructive_list_replacement_forbidden", "replace_all is forbidden for list sections; use upsert_many or patch"))
        elif normalized == "novel.blueprint.patch":
            section = str(payload.get("section") or "")
            if section not in NOVEL_BLUEPRINT_LIST_SECTIONS | NOVEL_BLUEPRINT_OBJECT_SECTIONS:
                issues.append(_issue("args.section", "enum", "Unsupported blueprint section"))
            selector = payload.get("selector")
            changes = payload.get("changes")
            if not isinstance(selector, dict):
                issues.append(_issue("args.selector", "object", "selector must be an object"))
            if not isinstance(changes, dict) or not changes:
                issues.append(_issue("args.changes", "non_empty_object", "changes must be a non-empty object"))
            if section in NOVEL_BLUEPRINT_OBJECT_SECTIONS and isinstance(selector, dict) and selector:
                issues.append(_issue("args.selector", "must_be_empty", f"{section} is patched directly and requires an empty selector"))
            if section in NOVEL_BLUEPRINT_LIST_SECTIONS and isinstance(selector, dict):
                required_key = "number" if section == "chapters" else "id"
                if set(selector) != {required_key}:
                    issues.append(_issue("args.selector", "exact_selector", f"{section} selector must contain exactly {required_key}"))
                elif required_key == "number" and (
                    not isinstance(selector.get("number"), int)
                    or isinstance(selector.get("number"), bool)
                    or selector.get("number", 0) < 1
                ):
                    issues.append(_issue("args.selector.number", "positive_integer", "chapter selector number must be positive"))
                elif required_key == "id" and (not isinstance(selector.get("id"), str) or not selector.get("id", "").strip()):
                    issues.append(_issue("args.selector.id", "required_non_empty_string", "selector id must be a non-empty string or auto"))
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
            if "create_if_missing" in payload and not isinstance(payload.get("create_if_missing"), bool):
                issues.append(_issue("args.create_if_missing", "boolean", "create_if_missing must be boolean"))
        elif normalized == "novel.blueprint.upsert_many":
            section = str(payload.get("section") or "")
            if section not in NOVEL_BLUEPRINT_LIST_SECTIONS:
                issues.append(_issue("args.section", "list_section_required", "Bulk upsert only supports blueprint list sections"))
            items = payload.get("items")
            max_items = 15 if section == "chapters" else 30
            if not isinstance(items, list) or not items or len(items) > max_items or not all(isinstance(item, dict) for item in items):
                issues.append(_issue("args.items", "bounded_object_array", f"items must contain 1-{max_items} objects for {section or 'this section'}; submit the next contiguous batch only after this batch succeeds"))
            else:
                key = "number" if section == "chapters" else "id"
                seen = set()
                for index, item in enumerate(items):
                    value = item.get(key)
                    valid = (
                        isinstance(value, int) and not isinstance(value, bool) and value > 0
                        if key == "number"
                        else value is None or value == "auto" or (isinstance(value, str) and bool(value.strip()))
                    )
                    if not valid:
                        issues.append(_issue(f"args.items[{index}].{key}", "canonical_key_required", f"{key} is required and must be canonical"))
                    elif value not in {None, "auto"} and value in seen:
                        issues.append(_issue(f"args.items[{index}].{key}", "duplicate", f"duplicate {key}: {value}"))
                    elif value not in {None, "auto"}:
                        seen.add(value)
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
        elif normalized == "novel.blueprint.assist":
            previous = payload.get("previous_energy")
            if previous is not None and (not isinstance(previous, int) or isinstance(previous, bool) or previous < 0):
                issues.append(_issue("args.previous_energy", "non_negative_integer", "previous_energy must be non-negative"))
            batch_size = payload.get("batch_size", 6)
            if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 20:
                issues.append(_issue("args.batch_size", "range_1_20", "batch_size must be an integer from 1 to 20"))
        elif normalized == "novel.reference.resolve":
            if str(payload.get("entity_type") or "") not in {"character", "location", "event", "chapter"}:
                issues.append(_issue("args.entity_type", "enum", "entity_type must be character, location, event, or chapter"))
            queries = payload.get("queries")
            if not isinstance(queries, list) or not queries or not all(isinstance(item, (str, int)) for item in queries):
                issues.append(_issue("args.queries", "non_empty_array", "queries must be a non-empty string or integer array"))
        elif normalized == "novel.timeline.calculate":
            operation = str(payload.get("operation") or "")
            if operation not in {"age", "arrival", "overlap"}:
                issues.append(_issue("args.operation", "enum", "operation must be age, arrival, or overlap"))
            required_fields = {
                "age": ("birth_tick",),
                "arrival": ("from", "to", "depart_tick"),
                "overlap": ("a_start", "a_duration", "b_start", "b_duration"),
            }.get(operation, ())
            for field in required_fields:
                value = payload.get(field)
                if operation == "arrival" and field in {"from", "to"}:
                    if not isinstance(value, str) or not value.strip():
                        issues.append(_issue(f"args.{field}", "required_non_empty_string", f"{field} is required"))
                elif not isinstance(value, int) or isinstance(value, bool):
                    issues.append(_issue(f"args.{field}", "integer", f"{field} must be an integer"))
        elif normalized == "novel.timeline.shift_suffix":
            if not isinstance(payload.get("event_id"), str) or not payload.get("event_id", "").strip():
                issues.append(_issue("args.event_id", "required_non_empty_string", "event_id must be a canonical non-empty string"))
            delta = payload.get("delta_ticks")
            if not isinstance(delta, int) or isinstance(delta, bool) or delta < 1:
                issues.append(_issue("args.delta_ticks", "positive_integer", "delta_ticks must be a positive integer"))
            if not isinstance(payload.get("reason"), str) or not payload.get("reason", "").strip():
                issues.append(_issue("args.reason", "required_non_empty_string", "reason must explain the timeline insertion"))
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
        elif normalized == "novel.timeline.normalize":
            if not isinstance(payload.get("reason"), str) or not payload.get("reason", "").strip():
                issues.append(_issue("args.reason", "required_non_empty_string", "reason must explain the deterministic timeline normalization"))
            max_shifts = payload.get("max_shifts", 128)
            if not isinstance(max_shifts, int) or isinstance(max_shifts, bool) or not 1 <= max_shifts <= 256:
                issues.append(_issue("args.max_shifts", "range_1_256", "max_shifts must be an integer from 1 to 256"))
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
        elif normalized == "novel.mobility.align_initial_many":
            items = payload.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 30 or not all(isinstance(item, Mapping) for item in items):
                issues.append(_issue("args.items", "bounded_object_array", "items must contain 1-30 character/location objects"))
            else:
                seen = set()
                for index, item in enumerate(items):
                    character_id = item.get("character_id")
                    location = item.get("location")
                    if not isinstance(character_id, str) or not character_id.strip():
                        issues.append(_issue(f"args.items[{index}].character_id", "required_non_empty_string", "character_id is required"))
                    elif character_id in seen:
                        issues.append(_issue(f"args.items[{index}].character_id", "duplicate", f"duplicate character_id: {character_id}"))
                    else:
                        seen.add(character_id)
                    if not isinstance(location, str) or not location.strip():
                        issues.append(_issue(f"args.items[{index}].location", "required_non_empty_string", "location is required"))
            revision = payload.get("expected_revision")
            if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
                issues.append(_issue("args.expected_revision", "non_negative_integer", "expected_revision must be a non-negative integer"))
        elif normalized == "novel.chapter.checkout":
            chapter = payload.get("chapter_number")
            if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
                issues.append(_issue("args.chapter_number", "positive_integer", "chapter_number must be a positive integer"))
        elif normalized == "novel.plan.rebase":
            for field in ("expected_state_hash", "reason"):
                if not isinstance(payload.get(field), str) or not payload.get(field, "").strip():
                    issues.append(_issue(f"args.{field}", "required_non_empty_string", f"{field} must be a non-empty string"))
            for field in ("event_updates", "chapter_updates", "maintained_anchor_ids"):
                if not isinstance(payload.get(field), list):
                    issues.append(_issue(f"args.{field}", "array", f"{field} must be an array"))
        elif normalized == "novel.chapter.submit":
            for field in ("lease_id", "title", "content"):
                if not isinstance(payload.get(field), str) or not payload.get(field, "").strip():
                    issues.append(_issue(f"args.{field}", "required_non_empty_string", f"{field} must be a non-empty string"))
            chapter = payload.get("chapter_number")
            if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
                issues.append(_issue("args.chapter_number", "positive_integer", "chapter_number must be a positive integer"))
            actual = payload.get("actual")
            if not isinstance(actual, dict):
                issues.append(_issue("args.actual", "object", "actual must be a structured chapter delta object"))
            else:
                if not isinstance(actual.get("summary"), str) or not actual.get("summary", "").strip():
                    issues.append(_issue("args.actual.summary", "required_non_empty_string", "actual.summary must be a factual non-empty string"))
                object_arrays = (
                    "events", "state_changes", "relationship_changes", "foreshadow_ops", "emotional_transactions",
                )
                for field in object_arrays:
                    value = actual.get(field, [])
                    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
                        issues.append(_issue(f"args.actual.{field}", "object_array", f"actual.{field} must be a direct JSON array of objects; do not group it under character_updates/sowed/item wrappers"))
                events = actual.get("events") if isinstance(actual.get("events"), list) else []
                if not events:
                    issues.append(_issue("args.actual.events", "non_empty_object_array", "actual.events must contain the chapter's planned event records"))
                for index, event in enumerate(item for item in events if isinstance(item, Mapping)):
                    for field in ("id", "status", "location"):
                        if not isinstance(event.get(field), str) or not event.get(field, "").strip():
                            issues.append(_issue(f"args.actual.events[{index}].{field}", "required_non_empty_string", f"event {field} is required"))
                    if str(event.get("status") or "") not in {"progressed", "turned", "closed"}:
                        issues.append(_issue(f"args.actual.events[{index}].status", "enum", "event status must be progressed, turned, or closed"))
                    for field in ("start_tick", "duration_ticks"):
                        value = event.get(field)
                        if not isinstance(value, int) or isinstance(value, bool) or (field == "duration_ticks" and value < 1):
                            issues.append(_issue(f"args.actual.events[{index}].{field}", "integer", f"event {field} must be an integer"))
                    for field in ("participants", "evidence_terms"):
                        value = event.get(field)
                        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                            issues.append(_issue(f"args.actual.events[{index}].{field}", "string_array", f"event {field} must be a direct string array"))
        elif normalized == "novel.scene.design":
            if not isinstance(payload.get("trigger_id"), str) or not payload.get("trigger_id", "").strip():
                issues.append(_issue("args.trigger_id", "required_non_empty_string", "trigger_id is required"))
            candidates = payload.get("candidates")
            if not isinstance(candidates, list) or not 2 <= len(candidates) <= 3 or not all(isinstance(item, dict) for item in candidates):
                issues.append(_issue("args.candidates", "array_2_to_3", "candidates must contain 2-3 objects"))
        elif normalized == "novel.context.query":
            if str(payload.get("entity_type") or "") not in {"character", "event", "foreshadow", "relationship", "chapter", "emotion"}:
                issues.append(_issue("args.entity_type", "enum", "Unsupported novel context entity type"))
            if not isinstance(payload.get("entity_ids"), list) or not all(isinstance(item, (str, int)) for item in payload.get("entity_ids", [])):
                issues.append(_issue("args.entity_ids", "array", "entity_ids must be an array of string or integer ids"))

    if normalized in {"file.write", "file.append", "code.write", "code.patch_replace", "file.delete_to_trash", "file.move", "file.rename"}:
        if normalized_target and _managed_novel_prose_path(normalized_target, workspace):
            issues.append(
                _issue(
                    "target",
                    "dedicated_novel_tool_required",
                    "Managed novel prose is canonical and cannot be changed through generic file tools; use novel.chapter.submit.",
                )
            )
    if normalized in {"file.copy", "file.move"}:
        destination = str(payload.get("destination") or "").strip()
        if destination and _managed_novel_prose_path(destination, workspace):
            issues.append(
                _issue(
                    "args.destination",
                    "dedicated_novel_tool_required",
                    "Generic copy/move cannot place content into managed novel prose; use novel.chapter.submit.",
                )
            )
    if normalized in {"shell.run", "python.run"}:
        free_text = str(payload.get("command") if normalized == "shell.run" else payload.get("code") or "")
        if (".novel-system" in free_text or "正文" in free_text) and _workspace_has_managed_novel(workspace):
            issues.append(
                _issue(
                    "args.command" if normalized == "shell.run" else "args.code",
                    "dedicated_novel_tool_required",
                    "Free-form execution cannot mutate or bypass managed novel authority; use novel.* actions.",
                )
            )

    closest = difflib.get_close_matches(normalized or received_action.lower(), available, n=5, cutoff=0.35) if available else []
    expected = schema_for_action(normalized or received_action)
    ok = not issues
    result: dict[str, Any] = {
        "ok": ok,
        "action": normalized,
        "received_action": received_action,
        "target": normalized_target,
        "args": payload,
        "alias_applied": bool(received_action and received_action.lower() != normalized),
        "issues": issues,
        "expected": expected,
    }
    if argument_aliases:
        result["argument_aliases"] = argument_aliases
    if not ok:
        target_invalid = any(str(issue.get("path") or "") == "target" for issue in issues)
        repair_target = normalized_target
        if normalized in PATH_TARGET_ACTIONS and (not normalized_target or target_invalid):
            repair_target = "relative/path/inside/workspace"
        repair_args = copy.deepcopy(payload) if payload else copy.deepcopy(expected.get("args", {}))
        result.update(
            {
                "status": "INVALID_TOOL_ARGUMENTS",
                "failure_class": "TOOL_DETERMINISTIC",
                "executed": False,
                "retryable": False,
                "must_change_arguments": True,
                "valid_action_candidates": closest,
                "repair": {
                    "action": normalized if normalized in available or not available else (closest[0] if closest else "system.action_schema"),
                    "target": repair_target,
                    "args": repair_args,
                    "expected_args_schema": expected.get("args", {}),
                },
                "llm_brief": "Tool call rejected before side effects. Correct action/target/args; do not retry the identical payload.",
                "validation_json": json.dumps(issues, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return result
