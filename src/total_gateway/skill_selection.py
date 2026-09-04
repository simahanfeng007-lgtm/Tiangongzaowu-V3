"""Deterministic system-recommendation and model-initiated Skill selection."""

from __future__ import annotations

import hashlib
import unicodedata
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts import (
    CapabilityAction,
    CapabilityManifest,
    SkillCandidate,
    SkillSelectionRecord,
    canonical_sha256,
)
from .action_registry import LoadedActionAuthority, compile_action_authority


SkillOperation = Literal["skill.route", "skill.list", "skill.get", "skill.read"]


class SkillSelectionError(RuntimeError):
    pass


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold().strip()
    if not normalized or "\x00" in normalized or len(normalized) > 16_384:
        raise ValueError("Skill query is empty or malformed")
    return " ".join(normalized.split())


class SkillDefinition(BaseModel):
    """One immutable Skill source item presented to either selection channel."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    skill_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(default="", max_length=2048)
    category: str = Field(min_length=1, max_length=160)
    keywords: tuple[str, ...] = Field(default=(), max_length=256)
    task_intents: tuple[str, ...] = Field(default=(), max_length=128)
    required_actions: tuple[str, ...] = Field(default=(), max_length=256)
    content: str = Field(min_length=1, max_length=1_048_576)

    @field_validator("title", "summary", "category", "content")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if unicodedata.normalize("NFC", value) != value or "\x00" in value:
            raise ValueError("Skill text must be normalized and contain no NUL")
        return value

    @field_validator("keywords", "task_intents")
    @classmethod
    def validate_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_text(item) for item in value)
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("Skill matching terms must be normalized, sorted, and unique")
        return normalized

    @field_validator("required_actions")
    @classmethod
    def validate_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("Skill required actions must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_content_digest(self) -> Self:
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("Skill content digest does not match source")
        return self


class SkillCatalog:
    """Immutable catalog snapshot; routing never silently falls back to a default Skill."""

    def __init__(self, definitions: tuple[SkillDefinition, ...]) -> None:
        if not definitions:
            raise ValueError("Skill catalog cannot be empty")
        if definitions != tuple(sorted(definitions, key=lambda item: item.skill_id)):
            raise ValueError("Skill catalog must be sorted by skill_id")
        if len({item.skill_id for item in definitions}) != len(definitions):
            raise ValueError("Skill catalog IDs must be unique")
        self._definitions = definitions
        self._by_id = {item.skill_id: item for item in definitions}
        self.sha256 = canonical_sha256(
            {
                "domain": "tiangong.gateway.skill-catalog.v1",
                "skills": [
                    {
                        "skill_id": item.skill_id,
                        "version": item.version,
                        "sha256": item.sha256,
                        "source_ref": item.source_ref,
                        "required_actions": list(item.required_actions),
                    }
                    for item in definitions
                ],
            }
        )

    @property
    def definitions(self) -> tuple[SkillDefinition, ...]:
        return self._definitions

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._by_id.get(skill_id)


@dataclass(frozen=True)
class LoadedSkillCatalog:
    catalog: SkillCatalog
    index_sha256: str
    source_file_count: int


@dataclass(frozen=True)
class LoadedModelCapabilityManifest:
    manifest: CapabilityManifest
    source_sha256: str
    executable_count: int
    action_authority: LoadedActionAuthority


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SkillSelectionError("Skill index contains a duplicate JSON key")
        result[key] = value
    return result


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SkillSelectionError(f"Skill index {field} must be a string list")
    return tuple(value)


def _skill_version(skill_id: str) -> str:
    match = re.search(r"_v([1-9][0-9]*)$", skill_id)
    if match is None:
        raise SkillSelectionError("Skill ID does not bind an explicit version")
    return "v" + match.group(1)


def load_filesystem_skill_catalog(
    root: Path,
    *,
    expected_index_sha256: str,
    expected_catalog_sha256: str | None = None,
) -> LoadedSkillCatalog:
    """Load the compatibility Skill source without trusting its stale legacy manifest."""

    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise SkillSelectionError("Skill source root is missing or unsafe")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_index_sha256):
        raise ValueError("expected Skill index digest is invalid")
    if expected_catalog_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_catalog_sha256):
        raise ValueError("expected Skill catalog digest is invalid")
    root_resolved = root.resolve(strict=True)
    index_path = root / "registry" / "skill_router_index.json"
    if (
        not index_path.is_file()
        or index_path.is_symlink()
        or index_path.parent.is_symlink()
        or root_resolved not in index_path.resolve(strict=True).parents
    ):
        raise SkillSelectionError("Skill index path is missing or unsafe")
    index_bytes = index_path.read_bytes()
    if not index_bytes or len(index_bytes) > 4 * 1024 * 1024:
        raise SkillSelectionError("Skill index size is invalid")
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    if index_sha256 != expected_index_sha256:
        raise SkillSelectionError("Skill index digest does not match the pinned release")
    try:
        payload = json.loads(
            index_bytes.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                SkillSelectionError("Skill index contains a non-finite number")
            ),
        )
    except SkillSelectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillSelectionError("Skill index is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise SkillSelectionError("Skill index root must be an object")
    expected_root_keys = {"schema", "version", "principle", "skill_count", "skills", "actions", "tool_boundary"}
    if set(payload) != expected_root_keys or payload.get("schema") != "tiangong.v3.omni_body.skill_router_index.v1":
        raise SkillSelectionError("Skill index schema or root fields are incompatible")
    actions = _string_list(payload.get("actions"), "actions")
    if not {"skill.route", "skill.list", "skill.get", "skill.read"}.issubset(actions):
        raise SkillSelectionError("Skill index does not expose the complete model query surface")
    skills = payload.get("skills")
    if (
        not isinstance(skills, list)
        or isinstance(payload.get("skill_count"), bool)
        or payload.get("skill_count") != len(skills)
        or not 1 <= len(skills) <= 10_000
    ):
        raise SkillSelectionError("Skill index count is invalid")

    definitions: list[SkillDefinition] = []
    seen_paths: set[str] = set()
    for raw in skills:
        if not isinstance(raw, dict):
            raise SkillSelectionError("Skill index item must be an object")
        skill_id = raw.get("id")
        title = raw.get("mingcheng")
        category = raw.get("category")
        relative = raw.get("file")
        if not all(isinstance(item, str) and item for item in (skill_id, title, category, relative)):
            raise SkillSelectionError("Skill index identity fields are malformed")
        posix = PurePosixPath(relative)
        if (
            posix.is_absolute()
            or ".." in posix.parts
            or len(posix.parts) != 2
            or posix.parts[0] != "deliverable_skills"
            or posix.suffix.lower() != ".md"
            or relative in seen_paths
        ):
            raise SkillSelectionError("Skill source reference is unsafe or duplicated")
        seen_paths.add(relative)
        source_path = root.joinpath(*posix.parts)
        if (
            not source_path.is_file()
            or source_path.is_symlink()
            or source_path.parent.is_symlink()
            or root_resolved not in source_path.resolve(strict=True).parents
        ):
            raise SkillSelectionError("Skill source file is missing or unsafe")
        source_bytes = source_path.read_bytes()
        if not source_bytes or len(source_bytes) > 1024 * 1024:
            raise SkillSelectionError("Skill source file size is invalid")
        try:
            content = source_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SkillSelectionError("Skill source is not UTF-8") from exc
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        required_actions = tuple(
            sorted(
                set().union(
                    *(
                        set(_string_list(raw.get(field), field))
                        for field in (
                            "starter_actions",
                            "production_actions",
                            "inspection_actions",
                            "quality_gates",
                            "repair_actions",
                            "final_actions",
                        )
                    )
                )
            )
        )
        definitions.append(
            SkillDefinition(
                skill_id=skill_id,
                version=_skill_version(skill_id),
                sha256=source_sha256,
                source_ref="skill_source_" + source_sha256,
                title=title,
                summary=str(raw.get("category")),
                category=category,
                keywords=tuple(sorted(set(_normalize_text(item) for item in _string_list(raw.get("keywords"), "keywords")))),
                task_intents=tuple(
                    sorted(set(_normalize_text(item) for item in _string_list(raw.get("taskIntents"), "taskIntents")))
                ),
                required_actions=required_actions,
                content=content,
            )
        )
    catalog = SkillCatalog(tuple(sorted(definitions, key=lambda item: item.skill_id)))
    if expected_catalog_sha256 is not None and catalog.sha256 != expected_catalog_sha256:
        raise SkillSelectionError("Skill catalog digest does not match the pinned release")
    return LoadedSkillCatalog(catalog=catalog, index_sha256=index_sha256, source_file_count=len(definitions))


def _routing_side_effects(effect: str) -> tuple[str, ...]:
    if effect in {"read", "verify", "inspect", "list"}:
        return ("read",)
    if effect in {"write", "create", "update"}:
        return ("local_write", "read")
    return ("external_send", "external_write", "local_write", "read")


def load_model_capability_manifest(
    path: Path,
    *,
    expected_sha256: str,
    component_manifest_hash: str,
    generated_at_ms: int,
) -> LoadedModelCapabilityManifest:
    """Load the pinned executable action surface used for Skill compatibility.

    The gateway execution ticket intentionally exposes only one compatibility
    action.  Reusing that narrow ticket manifest for Skill routing made every
    real Skill look incompatible.  This loader creates a separate, read-only
    routing view from the backend's release-pinned capability manifest.
    """

    if not path.is_absolute() or not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise SkillSelectionError("model capability manifest path is missing or unsafe")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("expected model capability digest is invalid")
    data = path.read_bytes()
    if not data or len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise SkillSelectionError("model capability manifest digest does not match the pinned release")
    try:
        payload = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                SkillSelectionError("model capability manifest contains a non-finite number")
            ),
        )
    except SkillSelectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillSelectionError("model capability manifest is not strict UTF-8 JSON") from exc
    expected_root = {
        "capabilities",
        "executable",
        "schema",
        "source_hash",
        "total",
        "unavailable",
        "validation",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_root
        or payload.get("schema") != "tiangong.v3.capability_manifest.v1"
        or not isinstance(payload.get("capabilities"), dict)
        or not isinstance(payload.get("validation"), dict)
        or payload["validation"].get("ok") is not True
        or payload["validation"].get("source_hash") != payload.get("source_hash")
    ):
        raise SkillSelectionError("model capability manifest schema or validation is invalid")
    capabilities = payload["capabilities"]
    executable_count = sum(
        1 for item in capabilities.values() if isinstance(item, dict) and item.get("executable") is True
    )
    if (
        isinstance(payload.get("total"), bool)
        or payload.get("total") != len(capabilities)
        or isinstance(payload.get("executable"), bool)
        or payload.get("executable") != executable_count
        or payload.get("unavailable") != len(capabilities) - executable_count
        or executable_count < 1
    ):
        raise SkillSelectionError("model capability manifest counts are invalid")

    try:
        action_authority = compile_action_authority(
            payload,
            generated_at_ms=generated_at_ms,
        )
    except (TypeError, ValueError) as exc:
        raise SkillSelectionError(
            "model capability schema authority is invalid"
        ) from exc

    actions: list[CapabilityAction] = []
    for action_id, raw in capabilities.items():
        if not isinstance(action_id, str) or not isinstance(raw, dict) or raw.get("id") != action_id:
            raise SkillSelectionError("model capability identity is invalid")
        if raw.get("executable") is not True:
            continue
        risk = str(raw.get("risk") or "")
        if risk not in {"A0", "A1", "A2", "A3", "A4", "A5"}:
            raise SkillSelectionError("model capability risk class is invalid")
        effect = str(raw.get("effect") or "execute")
        try:
            argument_schema_sha256 = action_authority.schema_catalog.resolve(
                action_id,
                "omni-registry-v1",
            ).argument_schema_sha256
        except (TypeError, ValueError) as exc:
            raise SkillSelectionError(
                "model capability schema binding is invalid"
            ) from exc
        actions.append(
            CapabilityAction(
                action_id=action_id,
                version="runtime-capability-v1",
                provider_component_id="tiangong-backend",
                argument_schema_sha256=argument_schema_sha256,
                result_schema_sha256=canonical_sha256(
                    {
                        "domain": "tiangong.model-capability.result.v1",
                        "action_id": action_id,
                        "source_hash": payload["source_hash"],
                    }
                ),
                risk_class=risk,
                allowed_side_effects=_routing_side_effects(effect),
                idempotency_mode="effect_id_required",
                max_runtime_ms=3_600_000,
                max_output_bytes=536_870_912,
                max_tool_calls=10_000,
                available=True,
                model_visible=True,
            )
        )
    manifest = CapabilityManifest(
        manifest_id="omni-body-model-capabilities-v1",
        revision=1,
        generated_at_ms=generated_at_ms,
        component_manifest_hash=component_manifest_hash,
        actions=tuple(sorted(actions, key=lambda item: (item.action_id, item.version))),
        sha256="0" * 64,
    ).with_computed_sha256()
    return LoadedModelCapabilityManifest(
        manifest=manifest,
        source_sha256=expected_sha256,
        executable_count=executable_count,
        action_authority=action_authority,
    )


class SkillResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record: SkillSelectionRecord
    content: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if self.content is not None:
            if self.record.decision != "activate" or self.record.activation_state != "active":
                raise ValueError("Skill content is released only for an activated resolution")
            if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.record.selected_skill_sha256:
                raise ValueError("released Skill content does not match selected digest")
        return self


def _query_sha256(query: str | None) -> str | None:
    if query is None:
        return None
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9_.+ -]*", term):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            text,
        ) is not None
    return term in text


def _score(definition: SkillDefinition, query: str) -> int:
    normalized = _normalize_text(query)
    if not normalized:
        return 0
    score = 0
    for keyword in definition.keywords:
        if _contains_term(normalized, keyword):
            score += min(240, 70 + len(keyword) * 12)
    for intent in definition.task_intents:
        if _contains_term(normalized, intent):
            score += min(300, 110 + len(intent) * 14)
    title = _normalize_text(definition.title)
    category = _normalize_text(definition.category)
    if _contains_term(normalized, title):
        score += 260
    if _contains_term(normalized, category):
        score += 120
    return min(1000, score)


def _available_actions(manifest: CapabilityManifest) -> set[str]:
    if not manifest.has_valid_sha256():
        raise SkillSelectionError("capability manifest digest is invalid")
    return {
        action.action_id
        for action in manifest.actions
        if action.available and action.model_visible
    }


def _candidate(
    definition: SkillDefinition,
    *,
    score_millis: int,
    available_actions: set[str],
) -> SkillCandidate:
    missing = tuple(sorted(set(definition.required_actions) - available_actions))
    reasons = (
        ("skill.no_executable_actions",)
        if not definition.required_actions
        else (() if not missing else ("skill.required_action_unavailable",))
    )
    return SkillCandidate(
        skill_id=definition.skill_id,
        version=definition.version,
        sha256=definition.sha256,
        source_ref=definition.source_ref,
        score_millis=score_millis,
        required_actions=definition.required_actions,
        missing_actions=missing,
        incompatible_reasons=reasons,
        compatible=not missing and bool(definition.required_actions),
    )


def _selection(values: dict[str, object]) -> SkillSelectionRecord:
    candidate = dict(values)
    candidate["selection_id"] = "sel_" + canonical_sha256(
        {"domain": "tiangong.gateway.skill-selection.v1", **values}
    )
    return SkillSelectionRecord(**candidate)


class SkillSelectionService:
    """One policy surface for automatic suggestions and model-owned Skill lookup."""

    def __init__(self, catalog: SkillCatalog, *, minimum_route_score: int = 80) -> None:
        if not 1 <= minimum_route_score <= 1000:
            raise ValueError("minimum Skill route score is out of bounds")
        self._catalog = catalog
        self._minimum_route_score = minimum_route_score

    @property
    def catalog(self) -> SkillCatalog:
        return self._catalog

    def _ranked(
        self,
        query: str,
        manifest: CapabilityManifest,
        *,
        limit: int,
    ) -> tuple[SkillCandidate, ...]:
        if not 1 <= limit <= 32:
            raise ValueError("Skill candidate limit is out of bounds")
        available = _available_actions(manifest)
        scored = [
            (score, definition)
            for definition in self._catalog.definitions
            # Core reference cards document the action surface.  They remain
            # available through skill.list/get/read, but are not executable
            # workflows and must never outrank a task-specific Skill during
            # automatic or model-requested routing.
            if definition.category != "core"
            if (score := _score(definition, query)) >= self._minimum_route_score
        ]
        scored.sort(key=lambda item: (-item[0], item[1].skill_id))
        return tuple(
            _candidate(definition, score_millis=score, available_actions=available)
            for score, definition in scored[:limit]
        )

    def _base(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        capability_manifest: CapabilityManifest,
        decided_at_ms: int,
    ) -> dict[str, object]:
        if generation < 0 or decided_at_ms < 0:
            raise ValueError("Skill selection generation or time is invalid")
        if not capability_manifest.has_valid_sha256():
            raise SkillSelectionError("capability manifest digest is invalid")
        return {
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "skill_catalog_hash": self._catalog.sha256,
            "capability_manifest_hash": capability_manifest.sha256,
            "decided_at_ms": decided_at_ms,
        }

    def system_recommend(
        self,
        query: str,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        capability_manifest: CapabilityManifest,
        decided_at_ms: int,
        limit: int = 3,
    ) -> SkillSelectionRecord:
        normalized = _normalize_text(query)
        candidates = self._ranked(normalized, capability_manifest, limit=limit)
        selected = next((item for item in candidates if item.compatible), None)
        return _selection(
            {
                **self._base(
                    request_id=request_id,
                    run_id=run_id,
                    generation=generation,
                    capability_manifest=capability_manifest,
                    decided_at_ms=decided_at_ms,
                ),
                "origin": "system_recommendation",
                "operation": "system.recommend",
                "query_hash": _query_sha256(normalized),
                "candidates": candidates,
                "decision": "defer" if selected is not None else "no_skill",
                "selected_skill_id": selected.skill_id if selected else None,
                "selected_skill_version": selected.version if selected else None,
                "selected_skill_sha256": selected.sha256 if selected else None,
                "activation_state": "candidate" if selected is not None else "none",
                "resolved_via": None,
                "reason_code": "skill.system_candidate" if selected else "skill.no_compatible_match",
            }
        )

    def model_request(
        self,
        operation: SkillOperation,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        capability_manifest: CapabilityManifest,
        decided_at_ms: int,
        query: str | None = None,
        skill_id: str | None = None,
        decline: bool = False,
        limit: int = 32,
    ) -> SkillResolution:
        base = self._base(
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            capability_manifest=capability_manifest,
            decided_at_ms=decided_at_ms,
        )
        if decline:
            if operation not in {"skill.route", "skill.list"} or skill_id is not None:
                raise ValueError("explicit Skill decline is valid only for route/list")
            normalized = _normalize_text(query) if query is not None else None
            record = _selection(
                {
                    **base,
                    "origin": "model_request",
                    "operation": operation,
                    "query_hash": _query_sha256(normalized),
                    "candidates": (),
                    "decision": "no_skill",
                    "selected_skill_id": None,
                    "selected_skill_version": None,
                    "selected_skill_sha256": None,
                    "activation_state": "none",
                    "resolved_via": None,
                    "reason_code": "skill.model_declined",
                }
            )
            return SkillResolution(record=record)

        if operation == "skill.route":
            normalized = _normalize_text(query or "")
            candidates = self._ranked(normalized, capability_manifest, limit=limit)
            record = _selection(
                {
                    **base,
                    "origin": "model_request",
                    "operation": operation,
                    "query_hash": _query_sha256(normalized),
                    "candidates": candidates,
                    "decision": "defer" if candidates else "no_skill",
                    "selected_skill_id": None,
                    "selected_skill_version": None,
                    "selected_skill_sha256": None,
                    "activation_state": "candidate" if candidates else "none",
                    "resolved_via": None,
                    "reason_code": "skill.route_candidates" if candidates else "skill.no_match",
                }
            )
            return SkillResolution(record=record)

        if operation == "skill.list":
            available = _available_actions(capability_manifest)
            if query is None:
                definitions = self._catalog.definitions[:limit]
                query_hash = None
                candidates = tuple(
                    _candidate(item, score_millis=0, available_actions=available) for item in definitions
                )
            else:
                normalized = _normalize_text(query)
                query_hash = _query_sha256(normalized)
                candidates = self._ranked(normalized, capability_manifest, limit=limit)
            record = _selection(
                {
                    **base,
                    "origin": "model_request",
                    "operation": operation,
                    "query_hash": query_hash,
                    "candidates": candidates,
                    "decision": "defer" if candidates else "no_skill",
                    "selected_skill_id": None,
                    "selected_skill_version": None,
                    "selected_skill_sha256": None,
                    "activation_state": "candidate" if candidates else "none",
                    "resolved_via": None,
                    "reason_code": "skill.list_candidates" if candidates else "skill.no_match",
                }
            )
            return SkillResolution(record=record)

        if operation not in {"skill.get", "skill.read"}:
            raise ValueError("unsupported Skill operation")
        if query is not None or not skill_id:
            raise ValueError("skill.get/read requires exactly one skill_id")
        definition = self._catalog.get(skill_id)
        if definition is None:
            record = _selection(
                {
                    **base,
                    "origin": "model_request",
                    "operation": operation,
                    "query_hash": _query_sha256(skill_id),
                    "candidates": (),
                    "decision": "reject",
                    "selected_skill_id": None,
                    "selected_skill_version": None,
                    "selected_skill_sha256": None,
                    "activation_state": "rejected",
                    "resolved_via": None,
                    "reason_code": "skill.not_found",
                }
            )
            return SkillResolution(record=record)
        candidate = _candidate(
            definition,
            score_millis=1000,
            available_actions=_available_actions(capability_manifest),
        )
        compatible = candidate.compatible
        record = _selection(
            {
                **base,
                "origin": "model_request",
                "operation": operation,
                "query_hash": _query_sha256(skill_id),
                "candidates": (candidate,),
                "decision": "activate" if compatible else "reject",
                "selected_skill_id": candidate.skill_id,
                "selected_skill_version": candidate.version,
                "selected_skill_sha256": candidate.sha256,
                "activation_state": "active" if compatible else "rejected",
                "resolved_via": operation if compatible else None,
                "reason_code": "skill.activated" if compatible else "skill.required_action_unavailable",
            }
        )
        return SkillResolution(record=record, content=definition.content if compatible else None)


__all__ = [
    "LoadedSkillCatalog",
    "LoadedModelCapabilityManifest",
    "SkillCatalog",
    "SkillDefinition",
    "SkillOperation",
    "SkillResolution",
    "SkillSelectionError",
    "SkillSelectionService",
    "load_filesystem_skill_catalog",
    "load_model_capability_manifest",
]
