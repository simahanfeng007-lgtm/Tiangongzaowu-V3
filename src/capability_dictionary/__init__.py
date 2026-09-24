"""Published Tool/Skill definitions. Execution and authorization stay in Gateway.

No legacy index fallback is allowed. A process pins one immutable release; an
updated dictionary takes effect on restart/new runs through release validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any


class DictionaryError(ValueError):
    pass


def dictionary_root() -> Path:
    configured = os.environ.get("TIANGONG_DICTIONARY_ROOT", "").strip()
    if configured:
        root = Path(configured).resolve(strict=True)
        if not (root / "tools/catalog.json").is_file():
            raise DictionaryError("configured_dictionary_missing")
        return root
    for parent in Path(__file__).resolve().parents:
        root = parent / "dictionaries"
        if (root / "tools/catalog.json").is_file():
            return root
    raise DictionaryError("dictionary_missing: install the published dictionaries package")


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise DictionaryError("dictionary_resource_unsafe")
    return path.read_bytes()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise DictionaryError("dictionary_duplicate_key")
        result[key] = value
    return result


def _json(raw: bytes):
    return json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(DictionaryError("nonfinite_dictionary_value")))


@lru_cache(maxsize=128)
def _dependency_available(dependency: str) -> bool:
    """Dependencies are stable for one installed process; restart after install."""
    kind, name = dependency.split(":", 1)
    if kind == "python":
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False
    if kind == "executable":
        if shutil.which(name):
            return True
        if name == "ffmpeg":
            try:
                import imageio_ffmpeg
                return Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
            except (ImportError, RuntimeError, OSError):
                return False
    return False


@dataclass(frozen=True)
class DictionaryRelease:
    root: Path
    version: str
    sha256: str
    tools: dict[str, dict[str, Any]]
    schemas: dict[str, Any]
    skills: dict[str, Any]
    skill_bodies: dict[str, str]
    execution_profiles: dict[str, dict[str, Any]]
    host_protocol: dict[str, Any]
    applications: dict[str, Any]

    def verify_published(self) -> None:
        """The release marker is written last; mixed/stale generated views fail."""
        marker = _json(_read(self.root, "registry/release.json"))
        if marker.get("dictionary_sha256") != self.sha256 or marker.get("version") != self.version:
            raise DictionaryError("dictionary_published_release_stale")
        for name, digest in marker.get("views", {}).items():
            if hashlib.sha256(_read(self.root, "registry/" + name)).hexdigest() != digest:
                raise DictionaryError("dictionary_published_view_stale:" + name)
        if set(marker.get("views", {})) != {"actions.json", "capability_manifest.generated.json"}:
            raise DictionaryError("dictionary_published_views_missing")

    def action_metadata(self) -> dict[str, dict[str, Any]]:
        return {name: {**row["runtime"], "execution_binding": dict(row["binding"])} for name, row in self.tools.items()}

    def readiness(self, action: str, *, runtime=None) -> dict[str, Any]:
        row = self.tools.get(action)
        if row is None:
            return {"ready": False, "status": "unknown", "reasons": ["not_in_dictionary"]}
        reasons = []
        if row["binding"]["kind"] == "alias":
            readiness = self.readiness(row["binding"]["target"], runtime=runtime)
            reasons.extend(readiness["reasons"])
        if not row["runtime"].get("implemented"):
            reasons.append("implementation_unavailable")
        if runtime is not None:
            compiled = getattr(runtime, "capability_manifest", None)
            capability = getattr(compiled, "capabilities", {}).get(action)
            if capability is None or not capability.executable:
                reasons.append("execution_binding_unavailable")
        for dependency in row.get("required_dependencies", []):
            kind, name = dependency.split(":", 1)
            available = bool(kind == "executable" and runtime and getattr(runtime, name, None)) or _dependency_available(dependency)
            if not available:
                reasons.append("missing:" + dependency)
        # Readiness is descriptive. It never grants workspace/side-effect rights.
        return {"ready": not reasons, "status": "ready" if not reasons else "unavailable",
                "reasons": sorted(set(reasons)), "dictionary_version": self.version, "dictionary_sha256": self.sha256,
                "optional_available": [item for item in row.get("optional_dependencies", []) if _dependency_available(item)],
                "optional_unavailable": [item for item in row.get("optional_dependencies", []) if not _dependency_available(item)]}


@lru_cache(maxsize=4)
def load_dictionary(root: Path | None = None) -> DictionaryRelease:
    root = (root or dictionary_root()).resolve(strict=True)
    raws = {path: _read(root, path) for path in ("tools/catalog.json", "tools/schemas.json", "skills/catalog.json", "execution-profiles.json", "host-protocol.json", "tools/apps.json")}
    tools_doc, schemas, skills, profiles_doc, host, applications = (_json(raws[p]) for p in raws)
    if host.get("schema") != "tiangong.dictionary.host-protocol.v1" or host.get("name") != "omni_body" or not isinstance(host.get("parameters"), dict):
        raise DictionaryError("dictionary_host_protocol_invalid")
    profiles = profiles_doc["profiles"]
    if profiles_doc.get("schema") != "tiangong.dictionary.execution-profiles.v1":
        raise DictionaryError("dictionary_execution_profiles_invalid")
    for profile in profiles.values():
        if any(type(profile.get(key)) is not int or profile[key] <= 0 for key in (
            "timeout_seconds", "max_log_bytes", "max_artifact_bytes")) or profile["timeout_seconds"] > 600:
            raise DictionaryError("dictionary_execution_budget_invalid")
    if tools_doc.get("schema") != "tiangong.tool-dictionary.v1" or skills.get("schema") != "tiangong.skill-dictionary.v1":
        raise DictionaryError("dictionary_schema_incompatible")
    tools = tools_doc["tools"]
    if applications.get("schema") != "tiangong.dictionary.applications.v1":
        raise DictionaryError("dictionary_applications_invalid")
    app_ids = set()
    for app in applications["apps"]:
        if app["app_id"] in app_ids or set(app["actions"]) - tools.keys():
            raise DictionaryError("dictionary_application_reference_invalid")
        app_ids.add(app["app_id"])
    for name, row in tools.items():
        binding = row.get("binding") or {}
        if row.get("budget", {}).get("profile") not in profiles:
            raise DictionaryError("dictionary_execution_profile_missing:" + name)
        if row.get("id") != name or binding.get("kind") not in {"method", "delivery", "alias"}:
            raise DictionaryError("dictionary_binding_invalid:" + name)
        if binding["kind"] == "alias" and binding.get("target") not in tools:
            raise DictionaryError("dictionary_alias_missing:" + name)
        if binding["kind"] == "method" and not re.fullmatch(r"_action_[A-Za-z0-9_]+", binding.get("target", "")):
            raise DictionaryError("dictionary_method_binding_invalid:" + name)
        for dependency in row.get("required_dependencies", []) + row.get("optional_dependencies", []):
            if not re.fullmatch(r"(?:python|executable):[A-Za-z0-9_.-]+", dependency):
                raise DictionaryError("dictionary_dependency_invalid:" + name)
        visited = {name}
        current = row
        while current["binding"]["kind"] == "alias":
            target = current["binding"]["target"]
            if target in visited:
                raise DictionaryError("dictionary_alias_cycle:" + name)
            visited.add(target)
            current = tools[target]
    seen = set()
    skill_bodies = {}
    for row in skills["skills"]:
        if row["id"] in seen:
            raise DictionaryError("dictionary_skill_duplicate")
        seen.add(row["id"])
        raw = _read(root, row["file"])
        raws[row["file"]] = raw
        skill_bodies[row["id"]] = raw.decode("utf-8")
        refs = set(re.findall(r'"action"\s*:\s*"([A-Za-z0-9_.]+)"', raw.decode("utf-8")))
        declared = {action for key, actions in row.items()
                    if key.endswith("_actions") and isinstance(actions, list)
                    for action in actions}
        missing = (refs | declared) - tools.keys()
        if missing:
            raise DictionaryError("dictionary_skill_unknown_actions:" + row["id"] + ":" + ",".join(sorted(missing)))
    for path in sorted((root / "skills/methods").glob("*.json")):
        relative = path.relative_to(root).as_posix()
        raws[relative] = _read(root, relative)
    digest = hashlib.sha256(json.dumps({p: hashlib.sha256(raw).hexdigest() for p, raw in sorted(raws.items())},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DictionaryRelease(root, str(tools_doc["version"]), digest, tools, schemas, skills, skill_bodies, profiles, host, applications)


__all__ = ["DictionaryError", "DictionaryRelease", "dictionary_root", "load_dictionary"]
