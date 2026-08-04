"""Resolve user-facing path words to runtime filesystem paths."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .runtime_environment import collect_runtime_environment


KNOWN_FOLDER_ALIASES = {
    "home": ("home", "user home", "user directory", "profile", "\u4e3b\u76ee\u5f55", "\u7528\u6237\u76ee\u5f55", "\u7528\u6237\u6587\u4ef6\u5939"),
    "desktop": ("desktop", "\u684c\u9762"),
    "downloads": ("downloads", "download", "\u4e0b\u8f7d", "\u4e0b\u8f7d\u6587\u4ef6\u5939"),
    "documents": ("documents", "document", "docs", "\u6587\u6863", "\u6211\u7684\u6587\u6863"),
    "pictures": ("pictures", "picture", "images", "\u56fe\u7247", "\u7167\u7247"),
    "videos": ("videos", "video", "\u89c6\u9891"),
    "music": ("music", "audio", "\u97f3\u4e50"),
    "workspace": ("workspace", "workdir", "\u5de5\u4f5c\u533a", "\u9879\u76ee\u76ee\u5f55", "\u5f53\u524d\u5de5\u4f5c\u533a"),
}

DRIVE_WORDS = ("\u76d8", "drive")
ROOT_WORDS = ("\u6839\u76ee\u5f55", "root")


def _norm_text(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'").strip()


def _is_url(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered.startswith(("http://", "https://", "data:", "file://"))


def _clean_path(value: Any) -> str:
    raw = _norm_text(value)
    if not raw:
        return ""
    try:
        return str(Path(os.path.expandvars(raw)).expanduser().resolve(strict=False))
    except Exception:
        return raw


def _path_exists(path: str) -> bool:
    try:
        return Path(path).exists()
    except Exception:
        return False


def _is_abs_path(value: str) -> bool:
    text = _norm_text(value)
    if not text:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", text):
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    try:
        return Path(text).expanduser().is_absolute()
    except Exception:
        return False


def _same_or_under(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        left = os.path.normcase(str(Path(path).resolve(strict=False)))
        right = os.path.normcase(str(Path(root).resolve(strict=False)))
        common = os.path.commonpath([left, right])
        return common == right
    except Exception:
        return False


def _join_root(root: str, tail: str) -> str:
    if not tail:
        return _clean_path(root)
    return _clean_path(Path(root) / tail.replace("/", os.sep).replace("\\", os.sep))


def _known_folder_path(env: dict[str, Any], key: str) -> str:
    if key == "workspace":
        workspace = env.get("workspace") if isinstance(env.get("workspace"), dict) else {}
        return _clean_path(workspace.get("workspace") or "")
    folders = env.get("known_folders") if isinstance(env.get("known_folders"), dict) else {}
    item = folders.get(key) if isinstance(folders.get(key), dict) else {}
    return _clean_path(item.get("path") or "")


def _match_known_folder(text: str, env: dict[str, Any]) -> tuple[str, str, str]:
    clean = _norm_text(text)
    lowered = clean.lower().replace("/", "\\")
    for key, aliases in KNOWN_FOLDER_ALIASES.items():
        root = _known_folder_path(env, key)
        if not root:
            continue
        for alias in aliases:
            alias_lower = alias.lower().replace("/", "\\")
            if lowered == alias_lower:
                return key, root, ""
            if lowered.startswith(alias_lower + "\\"):
                tail = clean[len(alias):].lstrip("\\/")
                return key, root, tail
    return "", "", ""


def _drive_by_letter(env: dict[str, Any], letter: str) -> dict[str, Any] | None:
    letter = str(letter or "").strip().upper().rstrip(":")
    for item in env.get("drives", []) if isinstance(env.get("drives"), list) else []:
        if str(item.get("letter") or "").upper() == letter:
            return item
    return None


def _match_drive_word(text: str, env: dict[str, Any]) -> tuple[str, str]:
    clean = _norm_text(text).replace("/", "\\")
    if not clean:
        return "", ""
    # D, D:, D:\, D drive, D drive\foo, D\u76d8, D\u76d8\foo
    match = re.match(r"^([a-zA-Z])(?::)?(?:\s*(?:drive|\u76d8))?(?:\s*(?:root|\u6839\u76ee\u5f55))?(?:[\\](.*))?$", clean, flags=re.IGNORECASE)
    if not match:
        return "", ""
    letter = match.group(1).upper()
    tail = match.group(2) or ""
    item = _drive_by_letter(env, letter)
    if not item:
        return f"{letter}:\\", tail
    return str(item.get("root") or f"{letter}:\\"), tail


def drive_info_for_path(path: str, env: dict[str, Any] | None = None) -> dict[str, Any] | None:
    env = env or collect_runtime_environment()
    clean = _clean_path(path)
    if not clean:
        return None
    lowered = os.path.normcase(clean)
    best = None
    best_len = -1
    for item in env.get("drives", []) if isinstance(env.get("drives"), list) else []:
        root = str(item.get("root") or "")
        if not root:
            continue
        root_norm = os.path.normcase(_clean_path(root))
        if lowered.startswith(root_norm) and len(root_norm) > best_len:
            best = item
            best_len = len(root_norm)
    return best


def classify_path(path: str, env: dict[str, Any] | None = None) -> dict[str, Any]:
    env = env or collect_runtime_environment()
    clean = _clean_path(path)
    result = {
        "scope": "unknown",
        "scope_key": "",
        "path": clean,
        "exists": _path_exists(clean),
        "readable": os.access(clean, os.R_OK) if clean else False,
        "writable": os.access(clean, os.W_OK) if clean else False,
        "drive": None,
        "is_root": False,
        "is_system": False,
    }
    if not clean:
        return result
    p = Path(clean)
    try:
        result["is_root"] = bool(p.anchor and str(p.resolve(strict=False)) == str(Path(p.anchor).resolve(strict=False)))
    except Exception:
        result["is_root"] = False

    workspace = _known_folder_path(env, "workspace")
    if workspace and _same_or_under(clean, workspace):
        result.update(scope="workspace", scope_key="workspace")
        return result

    folders = env.get("known_folders") if isinstance(env.get("known_folders"), dict) else {}
    ordered_folder_keys = [key for key in folders.keys() if key != "home"] + (["home"] if "home" in folders else [])
    for key in ordered_folder_keys:
        item = folders.get(key)
        root = item.get("path") if isinstance(item, dict) else ""
        if root and _same_or_under(clean, root):
            result.update(scope="known_folder" if key != "home" else "home", scope_key=key)
            return result

    drive = drive_info_for_path(clean, env)
    result["drive"] = drive
    lowered = os.path.normcase(clean)
    system_markers = [
        os.environ.get("WINDIR") or os.environ.get("SystemRoot"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramData"),
    ]
    if any(marker and _same_or_under(lowered, marker) for marker in system_markers):
        result.update(scope="system", is_system=True)
        return result
    if result["is_root"]:
        result["scope"] = "root"
        return result
    if drive:
        dtype = str(drive.get("type") or "unknown")
        if dtype == "removable":
            result["scope"] = "removable"
        elif dtype == "network":
            result["scope"] = "network"
        elif dtype == "fixed":
            result["scope"] = "external_drive"
        else:
            result["scope"] = dtype or "unknown"
    elif clean.startswith("\\\\"):
        result["scope"] = "network"
    return result


def resolve_path_text(raw: Any, *, intent: str = "", base: str = "", env: dict[str, Any] | None = None) -> dict[str, Any]:
    env = env or collect_runtime_environment()
    text = _norm_text(raw)
    if not text:
        return {"ok": False, "error": "empty_path", "input": raw}
    if _is_url(text):
        return {"ok": True, "input": raw, "resolved_path": text, "source": "url", "scope": "url"}

    expanded = os.path.expandvars(text)
    known_key, known_root, known_tail = _match_known_folder(expanded, env)
    if known_key:
        resolved = _join_root(known_root, known_tail)
        info = classify_path(resolved, env)
        return {
            "ok": True,
            "input": raw,
            "resolved_path": resolved,
            "source": "known_folder",
            "known_folder": known_key,
            **info,
        }

    drive_root, drive_tail = _match_drive_word(expanded, env)
    if drive_root:
        resolved = _join_root(drive_root, drive_tail)
        info = classify_path(resolved, env)
        drive = _drive_by_letter(env, drive_root[0])
        if drive is None:
            return {
                "ok": False,
                "input": raw,
                "resolved_path": resolved,
                "source": "drive",
                "error": "drive_not_available",
                "available_drives": [item.get("root") for item in env.get("drives", [])],
                **info,
            }
        return {"ok": True, "input": raw, "resolved_path": resolved, "source": "drive", **info}

    if _is_abs_path(expanded) or expanded.startswith("~"):
        resolved = _clean_path(expanded)
        info = classify_path(resolved, env)
        if re.match(r"^[a-zA-Z]:[\\/]", resolved) and drive_info_for_path(resolved, env) is None:
            return {
                "ok": False,
                "input": raw,
                "resolved_path": resolved,
                "source": "absolute",
                "error": "drive_not_available",
                "available_drives": [item.get("root") for item in env.get("drives", [])],
                **info,
            }
        return {"ok": True, "input": raw, "resolved_path": resolved, "source": "absolute", **info}

    workspace = base or _known_folder_path(env, "workspace")
    resolved = _join_root(workspace, expanded) if workspace else _clean_path(expanded)
    info = classify_path(resolved, env)
    return {"ok": True, "input": raw, "resolved_path": resolved, "source": "relative_workspace", **info}


_TRAILING_PUNCT = ".,;:!?)]}，。；、！？）】》」』\"'“”‘’\\/ \t"
_CANDIDATE_CHAR_EXCLUSIONS = "\\s\"'<>|*?，。；、！？（）()【】\\[\\]{}《》“”‘’"
_WINDOWS_ABS_RE = re.compile(r"[a-zA-Z]:[\\/][^" + _CANDIDATE_CHAR_EXCLUSIONS + "]*")
_DRIVE_WORD_REL_RE = re.compile(
    r"[a-zA-Z]\\s*(?:drive|盘)(?:\\s*(?:root|根目录))?(?:[\\/][^" + _CANDIDATE_CHAR_EXCLUSIONS + "]+)?",
    flags=re.IGNORECASE,
)
_BASENAME_RE = re.compile(
    r"(?<![\\w./\\:\\u4e00-\\u9fff-])([\\w\\u4e00-\\u9fff][\\w\\u4e00-\\u9fff.()\\[\\] -]*\\.[A-Za-z0-9]{1,12})(?![\\w/\\\\])"
)


def _alias_relative_re() -> re.Pattern[str]:
    aliases = sorted(
        {alias for group in KNOWN_FOLDER_ALIASES.values() for alias in group},
        key=len,
        reverse=True,
    )
    body = "|".join(re.escape(alias) for alias in aliases)
    return re.compile(
        r"(?:^|[^\\w/\\])(" + body + r")([\\/][^" + _CANDIDATE_CHAR_EXCLUSIONS + "]+)?",
        flags=re.IGNORECASE,
    )


def extract_user_path_candidates(message: Any, env: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Mechanically pull path candidates out of the current user message.

    This is a string-level extractor only: it never rewrites tool arguments and
    never guesses intent.  A candidate marks a path the user explicitly named
    this turn (absolute path, drive word, known-folder alias, or a file name);
    permission_settings matches tool path arguments against it.
    """
    text = str(message or "")
    if not text.strip():
        return []
    env = env or collect_runtime_environment()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, raw: str, resolved: str = "") -> None:
        clean_raw = str(raw or "").strip().rstrip(_TRAILING_PUNCT)
        if not clean_raw:
            return
        key = (kind, os.path.normcase(clean_raw))
        if key in seen:
            return
        seen.add(key)
        candidates.append({"kind": kind, "raw": clean_raw, "resolved_path": resolved})

    for match in _WINDOWS_ABS_RE.finditer(text):
        raw = match.group(0).rstrip(_TRAILING_PUNCT)
        if not raw or len(raw) < 3:
            continue
        resolved = resolve_path_text(raw, env=env)
        add("absolute", raw, str(resolved.get("resolved_path") or "") if resolved.get("ok") else "")

    for match in _DRIVE_WORD_REL_RE.finditer(text):
        raw = match.group(0).rstrip(_TRAILING_PUNCT)
        if not raw:
            continue
        root, tail = _match_drive_word(raw, env)
        if not root:
            continue
        add("drive", raw, _join_root(root, tail))

    alias_re = _alias_relative_re()
    for match in alias_re.finditer(text):
        raw = (match.group(1) + (match.group(2) or "")).rstrip(_TRAILING_PUNCT)
        if not raw:
            continue
        key, root, tail = _match_known_folder(raw, env)
        if not key or not root:
            continue
        add("known_folder", raw, _join_root(root, tail))

    for match in _BASENAME_RE.finditer(text):
        raw = match.group(1).strip().rstrip(_TRAILING_PUNCT)
        if not raw or _is_abs_path(raw):
            continue
        add("basename", raw, "")

    return candidates


def path_matches_user_candidates(resolved_path: str, candidates: list[dict[str, Any]]) -> bool:
    """True when a resolved tool path is equal to, under, or names a candidate."""
    clean = _clean_path(resolved_path)
    if not clean:
        return False
    base = os.path.normcase(Path(clean).name)
    for item in candidates or []:
        kind = str(item.get("kind") or "")
        if kind == "basename":
            candidate_base = os.path.normcase(str(item.get("raw") or "").strip())
            if candidate_base and base == candidate_base:
                return True
            continue
        candidate_path = _clean_path(item.get("resolved_path") or "")
        if not candidate_path:
            continue
        if os.path.normcase(candidate_path) == os.path.normcase(clean):
            return True
        if _same_or_under(clean, candidate_path) or _same_or_under(candidate_path, clean):
            return True
    return False


def resolve_paths_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    text = payload.get("text") or payload.get("path") or payload.get("value") or ""
    intent = str(payload.get("intent") or "")
    refresh = bool(payload.get("refresh"))
    env = collect_runtime_environment(refresh=refresh)
    return resolve_path_text(text, intent=intent, env=env)
