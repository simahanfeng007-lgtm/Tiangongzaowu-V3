"""Compile and verify learning artifacts without mutating release-pinned catalogs.

The life service owns dynamic learning state.  The gateway's Skill Catalog and
Capability Manifest are release snapshots, so a learned artifact can only bind
to their existing actions; it must never edit them in place.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid

from contracts import canonical_sha256


ARTIFACT_EXECUTOR_SCHEMA = "tiangong.life.artifact-executor.v1"
ARTIFACT_SCHEMA = "tiangong.life.learning-artifact.v2"
SKILL_SPEC_SCHEMA = "tiangong.life.skill-spec.v1"
_KINDS = frozenset({"knowledge", "skill", "tool"})
_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")


class ArtifactExecutorError(ValueError):
    """A draft cannot become a reproducible learning artifact."""


def _text(value: Any, field: str, *, limit: int = 32_000, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ArtifactExecutorError(f"artifact.{field}.invalid")
    result = value.strip()
    if len(result.encode("utf-8")) > limit:
        raise ArtifactExecutorError(f"artifact.{field}.too_large")
    if required and not result:
        raise ArtifactExecutorError(f"artifact.{field}.required")
    return result


def _risk(value: Any) -> str:
    result = str(value or "A3").strip().upper()
    if result not in _RISK_ORDER:
        raise ArtifactExecutorError("artifact.risk.invalid")
    return result


def _kind(value: Any) -> str:
    result = str(value or "knowledge").strip().casefold().replace("_", "-")
    aliases = {"knowledge-base": "knowledge", "knowledgebase": "knowledge", "kb": "knowledge", "capability": "skill"}
    result = aliases.get(result, result)
    if result not in _KINDS:
        raise ArtifactExecutorError("artifact.kind.invalid")
    return result


def _opaque(value: Any, field: str) -> str:
    result = _text(value, field, limit=160, required=True)
    if not result.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise ArtifactExecutorError(f"artifact.{field}.invalid")
    return result


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Artifact bundles live below life and content-addressed version folders.
    # Keep staging below the legacy Windows path limit while retaining an
    # independently generated sibling name for each atomic write.
    temporary = path.with_name(f"~{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _bundle_dir(root: Path, artifact: Mapping[str, Any]) -> Path:
    life_id = _opaque(artifact.get("life_id"), "life_id")
    artifact_id = _opaque(artifact.get("artifact_id"), "artifact_id")
    root = root.resolve(strict=False)
    if root == Path(root.anchor) or root.is_symlink():
        raise ArtifactExecutorError("artifact.store.root_unsafe")
    # ``artifact_id`` is content-addressed over the life ID and learning ID,
    # making it globally unique within this store.  Do not repeat the life ID
    # in the physical path: that extra 36-character segment pushes ordinary
    # Windows Documents roots past MAX_PATH during bundle publication.
    del life_id
    directory = root / artifact_id
    try:
        resolved = directory.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ArtifactExecutorError("artifact.store.path_unsafe") from exc
    return directory


def delete_artifact_bundle(root: Path, artifact: Mapping[str, Any]) -> bool:
    """Delete exactly one Life-generated bundle, never a release tool tree."""

    directory = _bundle_dir(root, artifact)
    if not directory.exists():
        return False
    if directory.is_symlink() or not directory.is_dir():
        raise ArtifactExecutorError("artifact.store.bundle_unsafe")
    shutil.rmtree(directory)
    return True


def persist_artifact_bundle(
    root: Path,
    artifact: Mapping[str, Any],
    *,
    publication: Mapping[str, Any] | None = None,
) -> Path:
    """Persist immutable build material and append publication evidence.

    `artifact.json`, the human-readable markdown, and `skill-spec.json` are
    written once for a content-addressed version.  Publication is represented
    separately so status changes never rewrite the validated build payload.
    """
    value = deepcopy(dict(artifact))
    if value.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactExecutorError("artifact.store.schema_invalid")
    digest = str(value.get("artifact_sha256") or "")
    build_value = deepcopy(value)
    if build_value.get("status") == "published":
        # Publication is a state transition over the immutable build.  The
        # artifact digest deliberately continues to cover the built form.
        build_value["status"] = "built"
        build_value.pop("publish_sha256", None)
    expected = canonical_sha256({"schema": ARTIFACT_SCHEMA, "artifact": {key: build_value[key] for key in build_value if key != "artifact_sha256"}})
    if not digest or digest != expected:
        raise ArtifactExecutorError("artifact.store.digest_invalid")
    directory = _bundle_dir(Path(root), value)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ArtifactExecutorError("artifact.store.directory_unsafe")
    artifact_path = directory / "artifact.json"
    if artifact_path.exists():
        try:
            existing = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactExecutorError("artifact.store.existing_invalid") from exc
        if not isinstance(existing, Mapping) or existing.get("artifact_sha256") != digest:
            raise ArtifactExecutorError("artifact.store.version_conflict")
    else:
        _atomic_json(artifact_path, build_value)
        document = build_value.get("document") if isinstance(build_value.get("document"), Mapping) else {}
        document_content = document.get("content") if isinstance(document.get("content"), str) else ""
        name = "SKILL.md" if build_value.get("kind") in {"skill", "tool"} else "knowledge.md"
        _atomic_text(directory / name, document_content)
        if isinstance(build_value.get("skill_spec"), Mapping):
            _atomic_json(directory / "skill-spec.json", build_value["skill_spec"])
        _atomic_json(directory / "evidence.json", {
            "schema": ARTIFACT_EXECUTOR_SCHEMA,
            "artifact_id": build_value.get("artifact_id"),
            "artifact_sha256": digest,
            "build_evidence": build_value.get("evidence") or [],
        })
    if publication is not None:
        _atomic_json(directory / "publication.json", {
            "schema": ARTIFACT_EXECUTOR_SCHEMA,
            "artifact_id": value.get("artifact_id"),
            "artifact_sha256": digest,
            "publication": deepcopy(dict(publication)),
        })
    return directory


def persist_current_pointer(root: Path, *, life_id: str, lineage_id: str, pointer: Mapping[str, Any]) -> Path:
    root = Path(root).resolve(strict=False)
    clean_life_id = _opaque(life_id, "life_id")
    clean_lineage_id = _opaque(lineage_id, "lineage_id")
    if root == Path(root.anchor) or root.is_symlink():
        raise ArtifactExecutorError("artifact.store.root_unsafe")
    # Pointers are addressed by their canonical identity in the document, so
    # use a compact deterministic directory key rather than duplicating two
    # long opaque IDs in the filesystem path.
    pointer_key = "ptr_" + canonical_sha256({
        "life_id": clean_life_id,
        "lineage_id": clean_lineage_id,
    })[:24]
    directory = root / pointer_key
    try:
        directory.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ArtifactExecutorError("artifact.store.path_unsafe") from exc
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(directory / "current.json", deepcopy(dict(pointer)))
    return directory / "current.json"


def _action_catalog(rows: Any) -> tuple[dict[str, dict[str, Any]], str]:
    if rows is None:
        return {}, canonical_sha256({"schema": ARTIFACT_EXECUTOR_SCHEMA, "actions": []})
    if not isinstance(rows, (list, tuple)):
        raise ArtifactExecutorError("artifact.action_catalog.invalid")
    parsed: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ArtifactExecutorError("artifact.action_catalog.row_invalid")
        action_id = _opaque(raw.get("action_id") or raw.get("id") or raw.get("name"), "action_id")
        if action_id in parsed:
            raise ArtifactExecutorError("artifact.action_catalog.duplicate")
        action_risk = _risk(raw.get("risk") or raw.get("risk_class") or "A3")
        parsed[action_id] = {
            "action_id": action_id,
            "risk": action_risk,
            "available": raw.get("available") is not False and raw.get("executable") is not False,
            "effect": _text(raw.get("effect") or "", "action_effect", limit=120),
            "argument_schema_sha256": _text(raw.get("argument_schema_sha256") or "", "argument_schema_sha256", limit=128),
            "result_schema_sha256": _text(raw.get("result_schema_sha256") or "", "result_schema_sha256", limit=128),
        }
    snapshot = [parsed[key] for key in sorted(parsed)]
    return parsed, canonical_sha256({"schema": ARTIFACT_EXECUTOR_SCHEMA, "actions": snapshot})


def _string_list(value: Any, field: str, *, limit: int = 128) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ArtifactExecutorError(f"artifact.{field}.invalid")
    result = [_text(item, field, limit=160, required=True) for item in value]
    if len(result) > limit or len(set(result)) != len(result):
        raise ArtifactExecutorError(f"artifact.{field}.duplicate_or_too_many")
    return result


def _draft_text(draft_artifact: Mapping[str, Any], *, title: str, summary: str) -> str:
    for key in ("markdown", "content", "text", "body", "instructions"):
        value = draft_artifact.get(key)
        if isinstance(value, str) and value.strip():
            return _text(value, key, limit=256_000, required=True)
    return f"# {title}\n\n{summary}".strip()


def compile_artifact(
    learning: Mapping[str, Any],
    *,
    action_catalog: Any = None,
    previous_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one normalized learning draft into an immutable build artifact.

    No publication takes place here.  A caller can safely show this result to a
    user, collect confirmation, and only then move its version pointer.
    """
    life_id = _opaque(learning.get("life_id"), "life_id")
    learning_id = _opaque(learning.get("learning_id"), "learning_id")
    kind = _kind(learning.get("target") or learning.get("kind"))
    title = _text(learning.get("title"), "title", limit=256, required=True)
    summary = _text(learning.get("summary"), "summary", limit=4_000, required=True)
    risk = _risk(learning.get("risk_level") or learning.get("risk"))
    draft_artifact = learning.get("draft_artifact") or learning.get("artifact") or {}
    if not isinstance(draft_artifact, Mapping):
        raise ArtifactExecutorError("artifact.draft.invalid")
    prior = dict(previous_artifact or {})
    version = int(prior.get("version") or 0) + 1
    artifact_id = "art_" + canonical_sha256({
        "schema": ARTIFACT_SCHEMA,
        "life_id": life_id,
        "learning_id": learning_id,
        "kind": kind,
        "version": version,
    })[:40]
    actions, action_catalog_sha256 = _action_catalog(action_catalog)
    base = {
        "schema": ARTIFACT_SCHEMA,
        "artifact_id": artifact_id,
        "life_id": life_id,
        "learning_id": learning_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "risk_level": risk,
        "version": version,
        "previous_artifact_id": _text(prior.get("artifact_id") or "", "previous_artifact_id", limit=160),
        "lineage_id": _opaque(
            prior.get("lineage_id") or draft_artifact.get("lineage_id") or artifact_id,
            "lineage_id",
        ),
        "action_catalog_sha256": action_catalog_sha256,
        "status": "built",
    }
    evidence: list[dict[str, Any]] = [{"check": "draft_schema", "ok": True}]
    learning_evidence = learning.get("learning_evidence")
    if learning_evidence is not None:
        if not isinstance(learning_evidence, Mapping):
            raise ArtifactExecutorError("artifact.learning_evidence.invalid")
        evidence.append({
            "check": "learning_materialization",
            "ok": True,
            "evidence_sha256": str(learning_evidence.get("evidence_sha256") or canonical_sha256(learning_evidence)),
        })
    if kind == "knowledge":
        document = _draft_text(draft_artifact, title=title, summary=summary)
        payload = {
            **base,
            "document": {"format": "markdown", "name": f"{artifact_id}.md", "content": document},
            "skill_spec": None,
            "required_actions": [],
        }
        evidence.append({"check": "knowledge_document", "ok": True, "content_sha256": canonical_sha256(document)})
    else:
        raw_spec = draft_artifact.get("skill_spec") or draft_artifact.get("spec") or draft_artifact
        if not isinstance(raw_spec, Mapping):
            raise ArtifactExecutorError("artifact.skill_spec.invalid")
        required = _string_list(raw_spec.get("required_actions") or draft_artifact.get("required_actions"), "required_actions")
        raw_steps = raw_spec.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ArtifactExecutorError("artifact.skill_spec.steps_required")
        steps: list[dict[str, Any]] = []
        step_ids: set[str] = set()
        for position, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, Mapping):
                raise ArtifactExecutorError("artifact.skill_spec.step_invalid")
            step_id = _opaque(raw_step.get("step_id") or f"step_{position + 1}", "step_id")
            if step_id in step_ids:
                raise ArtifactExecutorError("artifact.skill_spec.step_duplicate")
            step_ids.add(step_id)
            action_id = _opaque(raw_step.get("action_id"), "action_id")
            if action_id not in required:
                required.append(action_id)
            arguments = raw_step.get("arguments_template") or raw_step.get("arguments") or {}
            if not isinstance(arguments, Mapping):
                raise ArtifactExecutorError("artifact.skill_spec.arguments_invalid")
            steps.append({
                "step_id": step_id,
                "action_id": action_id,
                "arguments_template": deepcopy(dict(arguments)),
                "on_failure": _text(raw_step.get("on_failure") or "stop", "on_failure", limit=40),
            })
        if not required:
            raise ArtifactExecutorError("artifact.skill_spec.actions_required")
        bindings: list[dict[str, Any]] = []
        effective_risk = risk
        for action_id in sorted(required):
            action = actions.get(action_id)
            if action is None:
                raise ArtifactExecutorError(f"artifact.action.unknown:{action_id}")
            if action["available"] is not True:
                raise ArtifactExecutorError(f"artifact.action.unavailable:{action_id}")
            if _RISK_ORDER.index(action["risk"]) > _RISK_ORDER.index(effective_risk):
                effective_risk = action["risk"]
                evidence.append({"check": "risk_promoted", "ok": True, "action_id": action_id, "risk": effective_risk})
            bindings.append(deepcopy(action))
            evidence.append({"check": "action_binding", "ok": True, "action_id": action_id, "risk": action["risk"]})
        skill_id = _opaque(raw_spec.get("skill_id") or f"life.{artifact_id}_v{version}", "skill_id")
        if not skill_id.endswith(f"_v{version}"):
            skill_id = f"{skill_id}_v{version}"
        spec = {
            "schema": SKILL_SPEC_SCHEMA,
            "kind": kind,
            "skill_id": skill_id,
            "version": version,
            "task_intents": _string_list(raw_spec.get("task_intents") or raw_spec.get("intents"), "task_intents"),
            "input_schema": deepcopy(raw_spec.get("input_schema") or {"type": "object"}),
            "output_schema": deepcopy(raw_spec.get("output_schema") or {"type": "object"}),
            "required_actions": sorted(required),
            "steps": steps,
            "acceptance": deepcopy(raw_spec.get("acceptance") or [{"kind": "all_steps_succeeded"}]),
        }
        payload = {
            **base,
            "risk_level": effective_risk,
            "document": {"format": "markdown", "name": "SKILL.md", "content": _draft_text(draft_artifact, title=title, summary=summary)},
            "skill_spec": spec,
            "required_actions": spec["required_actions"],
            "action_bindings": bindings,
        }
    payload["evidence"] = evidence
    if isinstance(learning_evidence, Mapping):
        payload["learning_evidence"] = deepcopy(dict(learning_evidence))
    payload["artifact_sha256"] = canonical_sha256({"schema": ARTIFACT_SCHEMA, "artifact": payload})
    return payload


def publish_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(artifact))
    if value.get("schema") != ARTIFACT_SCHEMA or value.get("status") != "built":
        raise ArtifactExecutorError("artifact.publish.invalid_state")
    digest = str(value.get("artifact_sha256") or "")
    expected = canonical_sha256({"schema": ARTIFACT_SCHEMA, "artifact": {key: value[key] for key in value if key != "artifact_sha256"}})
    if digest != expected:
        raise ArtifactExecutorError("artifact.publish.digest_mismatch")
    value["status"] = "published"
    value["publish_sha256"] = canonical_sha256({"artifact_sha256": digest, "state": "published"})
    return value


def rollback_pointer(current: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    if current.get("schema") != ARTIFACT_SCHEMA or previous.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactExecutorError("artifact.rollback.schema_invalid")
    if current.get("life_id") != previous.get("life_id") or current.get("kind") != previous.get("kind"):
        raise ArtifactExecutorError("artifact.rollback.crosses_identity")
    return {
        "schema": ARTIFACT_EXECUTOR_SCHEMA,
        "kind": str(previous.get("kind")),
        "from_artifact_id": str(current.get("artifact_id")),
        "to_artifact_id": str(previous.get("artifact_id")),
        "to_artifact_sha256": str(previous.get("artifact_sha256")),
        "pointer_sha256": canonical_sha256({"current": current.get("artifact_sha256"), "previous": previous.get("artifact_sha256")}),
    }


__all__ = [
    "ARTIFACT_EXECUTOR_SCHEMA", "ARTIFACT_SCHEMA", "SKILL_SPEC_SCHEMA",
    "ArtifactExecutorError", "compile_artifact", "delete_artifact_bundle", "persist_artifact_bundle", "persist_current_pointer",
    "publish_artifact", "rollback_pointer",
]
