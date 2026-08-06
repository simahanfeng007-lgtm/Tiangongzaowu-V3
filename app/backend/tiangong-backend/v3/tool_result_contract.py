"""Shared omni_body result normalization for the v3 runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


TOOL_RESULT_SCHEMA = "tiangong.v3.tool_result.v1"

SUCCESS_STATES = {
    "completed",
    "done",
    "ok",
    "success",
    "wancheng",
    "yicunzai",
    "yifuzhi",
    "yishanchu",
    "yixiazai",
    "yixieru",
    "yiyidong",
}
FAILURE_STATES = {
    "blocked",
    "cancelled",
    "canceled",
    "cuowu",
    "error",
    "failed",
    "failure",
    "not_completed",
    "permission_denied",
    "plan_only",
    "plan_ready",
    "planning_only",
    "target_exists",
    "timeout",
}
WRITE_ACTIONS = {
    "append",
    "batch_copy",
    "batch_move",
    "copy",
    "delete",
    "mkdir",
    "move",
    "replace",
    "write",
    "zip",
    "archive",
    "package",
    "audio.concat",
    "audio.tone",
    "audio.trim",
    "code.patch_replace",
    "code.write",
    "docx.create",
    "file.append",
    "file.copy",
    "file.delete_to_trash",
    "file.mkdir",
    "file.move",
    "file.rename",
    "file.write",
    "image.add_text",
    "image.compose",
    "image.convert",
    "image.create_canvas",
    "image.crop",
    "image.resize",
    "image.rotate",
    "mindmap.create",
    "pdf.create_from_text",
    "pptx.create",
    "rollback.apply",
    "sheet.create",
    "video.add_audio",
    "video.cut",
    "video.extract_audio",
    "video.slideshow",
    "zip.create",
    "zip.extract",
}
WRITE_TOOLS: set[str] = set()
EXECUTION_ACTIONS = {"run", "command.run", "python.run", "quality.run_tests", "shell.run"}
PATH_KEYS = (
    "absolute_path",
    "archive_path",
    "created_path",
    "destination",
    "download_path",
    "file_path",
    "file",
    "lujing",
    "output_path",
    "output_file",
    "path",
    "result_path",
    "saved_path",
    "shuchu",
    "source",
    "target",
    "zhen_mulu",
    "zip_path",
    "zimu_wenjian",
)
ARTIFACT_KEYS = ("artifact", "artifacts", "generated_files", "output_files", "updated_paths")
ERROR_KEYS = ("cuowu", "error", "stderr", "traceback")
MESSAGE_KEYS = ("zhaiyao", "message", "jielun", "summary", "note")
MEDIA_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif", ".tif", ".tiff"}
MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".ogv", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp", ".ts", ".m2ts"}
DELIVERABLE_ATTACHMENT_EXTS = (
    MEDIA_IMAGE_EXTS
    | MEDIA_VIDEO_EXTS
    | {
        ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac", ".silk",
        ".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".epub",
        ".xlsx", ".xls", ".ods", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
        ".pptx", ".ppt", ".odp", ".key",
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".apk", ".ipa",
        ".html", ".htm",
    }
)


def _safe_text(value: Any, limit: int = 800) -> str:
    text = str(value or "")
    return text[: max(1, int(limit))]


def _json_preview(value: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(value)
    return _safe_text(text, limit)


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _candidate_dicts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return bounded nested result dictionaries without following cycles."""
    output: list[dict[str, Any]] = []
    pending: list[tuple[dict[str, Any], int]] = [(data, 0)]
    seen: set[int] = set()
    nested_keys = (
        "data",
        "destination",
        "evidence",
        "execution",
        "output",
        "result",
        "source",
        "write_evidence",
    )
    while pending and len(output) < 96:
        current, depth = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(current)
        if depth >= 6:
            continue
        for key in nested_keys:
            nested = current.get(key)
            if isinstance(nested, dict):
                pending.append((nested, depth + 1))
        for key in ("operations", "snapshots"):
            value = current.get(key)
            if isinstance(value, list):
                pending.extend(
                    (item, depth + 1)
                    for item in value
                    if isinstance(item, dict)
                )
    return output


def _extend_paths(out: list[str], value: Any) -> None:
    if value in (None, ""):
        return
    if isinstance(value, dict):
        for key in PATH_KEYS:
            _extend_paths(out, value.get(key))
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _extend_paths(out, item)
        return
    out.append(str(value))


def _contract_source(result: Any) -> dict[str, Any]:
    return result if isinstance(result, dict) else {"value": result}


def _status_from_result(data: dict[str, Any]) -> str:
    status = str(data.get("zhuangtai") or data.get("status") or "").strip().lower()
    if status:
        return status
    if data.get("ok") is False or data.get("success") is False:
        return "failed"
    if any(data.get(key) not in (None, "") for key in ERROR_KEYS):
        return "cuowu"
    if data.get("ok") is True or data.get("success") is True:
        return "success"
    if "neirong" in data or "content" in data:
        return "success"
    if any(data.get(key) not in (None, "") for key in PATH_KEYS + ARTIFACT_KEYS):
        return "success"
    if data:
        return "unknown"
    return "empty"


def _error_from_result(data: dict[str, Any]) -> str:
    for key in ERROR_KEYS + ("error_code",):
        value = data.get(key)
        if value not in (None, ""):
            return _safe_text(value, 1200)
    if str(data.get("status") or data.get("zhuangtai") or "").strip().lower() in FAILURE_STATES:
        for key in MESSAGE_KEYS:
            value = data.get(key)
            if value not in (None, ""):
                return _safe_text(value, 1200)
    return ""


def _readback_failed(data: dict[str, Any]) -> bool:
    readback = data.get("readback")
    if isinstance(readback, dict):
        return readback.get("ok") is False
    if isinstance(readback, list):
        return any(isinstance(item, dict) and item.get("ok") is False for item in readback)
    return False


def _summary_from_result(data: dict[str, Any], error: str, status: str) -> str:
    if error:
        return _safe_text(error, 500)
    for key in MESSAGE_KEYS:
        value = data.get(key)
        if value not in (None, ""):
            return _safe_text(value, 500)
    if data.get("path") or data.get("lujing") or data.get("target") or data.get("output_path"):
        return _safe_text(data.get("path") or data.get("lujing") or data.get("target") or data.get("output_path"), 500)
    if "neirong" in data:
        value = data.get("neirong")
        if isinstance(value, str):
            return _safe_text(value, 500)
        if isinstance(value, list):
            return f"items={len(value)}"
    return status or _json_preview(data, 500)


def _collect_paths(
    tool_name: str,
    data: dict[str, Any],
    *,
    ok: bool,
    write_evidence: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    artifacts: list[str] = []
    for key in PATH_KEYS:
        _extend_paths(paths, data.get(key))
    _extend_paths(paths, data.get("evidence"))
    _extend_paths(paths, data.get("result"))
    for candidate in _candidate_dicts(data):
        for key in PATH_KEYS:
            _extend_paths(paths, candidate.get(key))
        if ok:
            for key in ARTIFACT_KEYS:
                _extend_paths(artifacts, candidate.get(key))
    if ok and isinstance(write_evidence, dict):
        # Only observed post-state paths are deliverables. Planned targets and
        # deleted paths remain diagnostic paths but never become attachments.
        _extend_paths(artifacts, write_evidence.get("changed_files"))
        for item in write_evidence.get("post") or []:
            if isinstance(item, dict) and item.get("exists") is not False:
                _extend_paths(artifacts, item.get("path"))
    return _unique(paths), _unique(artifacts)


def _path_key(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    return text.replace("\\", "/").rstrip("/").casefold()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _observed_write_evidence(
    tool_name: str,
    data: dict[str, Any],
    ok: bool,
) -> dict[str, Any] | None:
    """Build write evidence only from broker deltas or proven pre/post state."""
    if not ok:
        return None
    name = str(tool_name or data.get("tool_name") or "").strip()
    action = str(data.get("action") or data.get("caozuo") or "").strip().lower()
    candidates = _candidate_dicts(data)

    changed_files: list[str] = []
    deleted_files: list[str] = []
    verified_unchanged_files: list[str] = []
    post_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        _extend_paths(changed_files, candidate.get("changed_files"))
        _extend_paths(deleted_files, candidate.get("deleted_files"))
        _extend_paths(verified_unchanged_files, candidate.get("verified_unchanged_files"))
    changed_files = _unique(changed_files)
    deleted_files = _unique(deleted_files)
    if changed_files or deleted_files:
        return {
            "schema": "tiangong.v3.write_evidence.v1",
            "authoritative": True,
            "source": "sandbox_broker",
            "action": action or name,
            "changed_files": changed_files,
            "deleted_files": deleted_files,
            "post": [],
        }

    if action in EXECUTION_ACTIONS:
        # Successful execution is only potential mutation. The sandbox broker
        # must report concrete changed/deleted files before completion may use
        # it as an observed write.
        return None
    if name not in WRITE_TOOLS and action not in WRITE_ACTIONS:
        return None

    snapshots: list[dict[str, Any]] = []
    for candidate in candidates:
        value = candidate.get("snapshots")
        if isinstance(value, list):
            snapshots.extend(item for item in value if isinstance(item, dict))

    post_by_path: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        path = str(candidate.get("path") or candidate.get("absolute_path") or "").strip()
        if not path:
            continue
        has_post_fact = any(
            key in candidate
            for key in ("exists", "is_file", "is_dir", "sha256", "size_bytes")
        )
        if not has_post_fact:
            continue
        row = {
            "path": path,
            "exists": candidate.get("exists"),
            "is_file": candidate.get("is_file"),
            "is_dir": candidate.get("is_dir"),
            "size_bytes": candidate.get("size_bytes"),
            "sha256": str(candidate.get("sha256") or ""),
        }
        post_by_path[_path_key(path)] = row

    for snapshot in snapshots:
        path = str(snapshot.get("path") or "").strip()
        if not path:
            continue
        key = _path_key(path)
        pre_existed = snapshot.get("existed") is True
        post = post_by_path.get(key)

        if post and post.get("exists") is True and not pre_existed:
            changed_files.append(path)
            post_rows.append(post)
            continue

        if post and post.get("exists") is True and pre_existed:
            backup_text = str(snapshot.get("backup_path") or "").strip()
            backup = Path(backup_text) if backup_text else None
            pre_size: int | None = None
            pre_sha256 = ""
            try:
                if backup is not None and backup.is_file():
                    pre_size = backup.stat().st_size
                    if pre_size <= 200 * 1024 * 1024:
                        pre_sha256 = _file_sha256(backup)
            except OSError:
                pre_size = None
                pre_sha256 = ""
            post_size = post.get("size_bytes")
            post_sha256 = str(post.get("sha256") or "")
            changed = (
                bool(pre_sha256 and post_sha256 and pre_sha256 != post_sha256)
                or (
                    isinstance(pre_size, int)
                    and isinstance(post_size, int)
                    and pre_size != post_size
                )
            )
            if changed:
                changed_files.append(path)
                row = dict(post)
                if pre_size is not None:
                    row["pre_size_bytes"] = pre_size
                if pre_sha256:
                    row["pre_sha256"] = pre_sha256
                post_rows.append(row)
            elif (
                pre_sha256
                and post_sha256
                and pre_sha256 == post_sha256
                and isinstance(pre_size, int)
                and isinstance(post_size, int)
                and pre_size == post_size
            ):
                # 幂等重写（内容逐字节一致）：不冒充"已变更"（changed_files 保持为空），
                # 但请求的目标状态已被验证在位——单独记 verified_unchanged_files，
                # 供完成门判"写意图已达成"，不得误判"读回未验证"。
                verified_unchanged_files.append(path)
                row = dict(post)
                row["pre_size_bytes"] = pre_size
                row["pre_sha256"] = pre_sha256
                row["idempotent_unchanged"] = True
                post_rows.append(row)

    source_exists_after = next(
        (
            candidate.get("source_exists_after")
            for candidate in candidates
            if isinstance(candidate.get("source_exists_after"), bool)
        ),
        None,
    )
    if source_exists_after is False:
        for snapshot in snapshots:
            if snapshot.get("existed") is True and snapshot.get("path"):
                deleted_files.append(str(snapshot.get("path")))
                break

    changed_files = _unique(changed_files)
    deleted_files = _unique(deleted_files)
    verified_unchanged_files = _unique(verified_unchanged_files)
    if not changed_files and not deleted_files and not verified_unchanged_files:
        # B4：无 broker 快照的简单写入结果，只要带回读/哈希事实（目标路径 +
        # 存在性 + sha256/大小），就应认定为真实落盘，不能因为契约缺
        # observed_write_effect 就把已完成产物判成“无写效应”。
        readback = data.get("readback")
        readback_items: list[dict[str, Any]] = []
        if isinstance(readback, dict):
            readback_items.append(readback)
        elif isinstance(readback, list):
            readback_items.extend(item for item in readback if isinstance(item, dict))
        readback_ok = (
            (isinstance(readback, dict) and readback.get("ok") is True)
            or (
                isinstance(readback, list)
                and bool(readback)
                and all(isinstance(item, dict) and item.get("ok") is True for item in readback)
            )
        )
        fact_sources = list(candidates) + readback_items
        direct_rows: dict[str, dict[str, Any]] = {}
        for candidate in fact_sources:
            path = str(
                candidate.get("path")
                or candidate.get("absolute_path")
                or candidate.get("target")
                or candidate.get("output_path")
                or ""
            ).strip()
            if not path:
                continue
            has_post_fact = any(
                key in candidate
                for key in ("exists", "is_file", "is_dir", "sha256", "size_bytes")
            )
            if not has_post_fact:
                continue
            direct_rows[_path_key(path)] = {
                "path": path,
                "exists": candidate.get("exists"),
                "is_file": candidate.get("is_file"),
                "is_dir": candidate.get("is_dir"),
                "size_bytes": candidate.get("size_bytes"),
                "sha256": str(candidate.get("sha256") or ""),
            }
        if not direct_rows:
            # 路径在 A 对象、事实在 B 对象（evidence 只带 exists/sha256）时按路径配对。
            evidence_items = [
                item
                for item in list(candidates) + readback_items
                if isinstance(item, dict)
                and not str(
                    item.get("path")
                    or item.get("absolute_path")
                    or item.get("target")
                    or item.get("output_path")
                    or ""
                ).strip()
                and any(key in item for key in ("exists", "is_file", "is_dir", "sha256", "size_bytes"))
            ]
            if evidence_items:
                for candidate in fact_sources:
                    path = str(
                        candidate.get("path")
                        or candidate.get("absolute_path")
                        or candidate.get("target")
                        or candidate.get("output_path")
                        or ""
                    ).strip()
                    if not path or _path_key(path) in direct_rows:
                        continue
                    for item in evidence_items:
                        if any(key in item for key in ("exists", "is_file", "is_dir", "sha256", "size_bytes")):
                            direct_rows[_path_key(path)] = {
                                "path": path,
                                "exists": item.get("exists"),
                                "is_file": item.get("is_file"),
                                "is_dir": item.get("is_dir"),
                                "size_bytes": item.get("size_bytes"),
                                "sha256": str(item.get("sha256") or ""),
                            }
                            break
        if direct_rows and readback_ok:
            post_rows: list[dict[str, Any]] = []
            for row in direct_rows.values():
                if (
                    isinstance(row.get("exists"), bool)
                    or row.get("is_file") is not None
                    or row.get("sha256")
                    or row.get("size_bytes") is not None
                ):
                    post_rows.append(row)
            if post_rows:
                return {
                    "schema": "tiangong.v3.write_evidence.v1",
                    "authoritative": True,
                    "source": "tool_post_readback",
                    "action": action or name,
                    "changed_files": _unique([row["path"] for row in post_rows]),
                    "deleted_files": [],
                    "post": post_rows,
                }
        return None
    unique_post: list[dict[str, Any]] = []
    seen_post: set[str] = set()
    for row in post_rows:
        key = _path_key(row.get("path"))
        if key and key not in seen_post:
            seen_post.add(key)
            unique_post.append(row)
    return {
        "schema": "tiangong.v3.write_evidence.v1",
        "authoritative": True,
        "source": "tool_pre_post",
        "action": action or name,
        "changed_files": changed_files,
        "deleted_files": deleted_files,
        "verified_unchanged_files": verified_unchanged_files,
        "post": unique_post,
    }


def _may_mutate(tool_name: str, data: dict[str, Any]) -> bool:
    name = str(tool_name or data.get("tool_name") or "").strip()
    action = str(data.get("action") or data.get("caozuo") or "").strip().lower()
    return name in WRITE_TOOLS or action in WRITE_ACTIONS or action in EXECUTION_ACTIONS


def _generated_media(tool_name: str, data: dict[str, Any], ok: bool) -> dict[str, str] | None:
    if not ok:
        return None
    path = str(data.get("lujing") or data.get("path") or data.get("url") or data.get("output_path") or "").strip()
    if not path:
        return None
    suffix = Path(path.split("?", 1)[0]).suffix.lower()
    if suffix in MEDIA_IMAGE_EXTS:
        return {"kind": "image", "path": path}
    if suffix in MEDIA_VIDEO_EXTS:
        return {"kind": "video", "path": path}
    return None


def _attachment_kind(path: str) -> str:
    suffix = Path(str(path or "").split("?", 1)[0]).suffix.lower()
    if suffix in MEDIA_IMAGE_EXTS:
        return "image"
    if suffix in MEDIA_VIDEO_EXTS:
        return "video"
    return "document"


def _generated_attachments(paths: list[str], artifacts: list[str], ok: bool) -> list[dict[str, str]]:
    if not ok:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in list(artifacts or []) + list(paths or []):
        path = str(raw or "").strip()
        if not path or path.startswith(("http://", "https://")):
            continue
        suffix = Path(path.split("?", 1)[0]).suffix.lower()
        if suffix not in DELIVERABLE_ATTACHMENT_EXTS:
            continue
        key = path.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": _attachment_kind(path), "path": path})
    return out


def normalize_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    data = _contract_source(result)
    status = _status_from_result(data)
    error = _error_from_result(data)
    readback_failed = _readback_failed(data)
    ok = isinstance(result, dict)
    if data.get("plan_only") is True or data.get("satisfies_intent") is False:
        ok = False
    if data.get("ok") is False or data.get("success") is False:
        ok = False
    if status in FAILURE_STATES:
        ok = False
    if error:
        ok = False
    if readback_failed:
        ok = False
        if not error:
            error = "WRITE_READBACK_FAILED"
    if data.get("ok") is True or data.get("success") is True or status in SUCCESS_STATES:
        ok = ok and True
    elif isinstance(result, dict) and ("neirong" in data or "content" in data) and status in {"", "success", "unknown"}:
        ok = True
    elif isinstance(result, dict) and any(data.get(key) not in (None, "") for key in PATH_KEYS + ARTIFACT_KEYS) and status in {"success", "unknown"}:
        ok = True
    elif isinstance(result, dict) and status not in SUCCESS_STATES:
        ok = False
    write_evidence = _observed_write_evidence(tool_name, data, bool(ok))
    may_mutate = _may_mutate(tool_name, data)
    action = str(data.get("action") or data.get("caozuo") or "").strip().lower()
    name = str(tool_name or data.get("tool_name") or "").strip()
    legacy_write_classification = bool(
        ok and (name in WRITE_TOOLS or action in WRITE_ACTIONS)
    )
    paths, artifacts = _collect_paths(
        tool_name,
        data,
        ok=bool(ok),
        write_evidence=write_evidence,
    )
    media = _generated_media(tool_name, data, ok)
    attachments = _generated_attachments([], artifacts, ok)
    return {
        "schema": TOOL_RESULT_SCHEMA,
        "tool_name": str(tool_name or data.get("tool_name") or "").strip(),
        "ok": bool(ok),
        "status": status or ("success" if ok else "failed"),
        "error": error,
        "summary": _summary_from_result(data, error, status),
        "paths": paths,
        "artifacts": artifacts,
        "may_mutate": may_mutate,
        # Kept as a compatibility classification for older checkpoints.
        # Completion logic must use observed_write_effect/write_evidence.
        "write_effect": write_evidence is not None or legacy_write_classification,
        "observed_write_effect": write_evidence is not None,
        "write_evidence": write_evidence,
        "generated_media": media,
        "generated_attachments": attachments,
        "raw_preview": _json_preview(result, 1200),
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def tool_result_ok(tool_name: str, result: Any) -> bool:
    return bool(normalize_tool_result(tool_name, result).get("ok"))


def tool_result_status(tool_name: str, result: Any) -> str:
    return str(normalize_tool_result(tool_name, result).get("status") or "")


def tool_result_error(tool_name: str, result: Any) -> str:
    contract = normalize_tool_result(tool_name, result)
    return str(contract.get("error") or contract.get("summary") or "")


def tool_result_paths(tool_name: str, result: Any) -> list[str]:
    return list(normalize_tool_result(tool_name, result).get("paths") or [])


def tool_result_artifacts(tool_name: str, result: Any) -> list[str]:
    return list(normalize_tool_result(tool_name, result).get("artifacts") or [])


def tool_result_write_effect(tool_name: str, result: Any) -> bool:
    contract = normalize_tool_result(tool_name, result)
    return bool(contract.get("observed_write_effect"))


def tool_result_write_evidence(tool_name: str, result: Any) -> dict[str, Any] | None:
    evidence = normalize_tool_result(tool_name, result).get("write_evidence")
    return evidence if isinstance(evidence, dict) else None


def tool_result_media(tool_name: str, result: Any) -> dict[str, str] | None:
    media = normalize_tool_result(tool_name, result).get("generated_media")
    return media if isinstance(media, dict) else None


def tool_result_attachments(tool_name: str, result: Any) -> list[dict[str, str]]:
    attachments = normalize_tool_result(tool_name, result).get("generated_attachments")
    return [item for item in attachments if isinstance(item, dict)] if isinstance(attachments, list) else []
