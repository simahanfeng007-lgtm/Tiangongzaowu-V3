"""Gateway-owned input evidence for an already materialized Tool Source build.

The caller owns Git provenance and isolation. This module only hashes bytes;
it neither imports candidate code nor approves, publishes or executes it.
Use a conservative closure of the existing editable/frozen source roots, not
a guessed per-handler dependency graph. Generated mirrors and the generated
capability manifest are outputs, so they cannot feed their own revision hash.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from contracts import canonical_sha256
from source_authority.validator import validate_source_authority

from .tool_source_candidate import (
    SourceCandidateError,
    _invalid_constant,
    _repository_path,
    _strict_pairs,
    _under,
)


_SCHEMA = "tiangong.tool-source-inputs.v1"
_MANIFEST = "src/omni_body_skill/registry/capability_manifest.generated.json"
_BUILD_INPUTS = frozenset({"source-ownership.json", "pyproject.toml", "requirements-source.lock"})
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_FILES = 20000


@dataclass(frozen=True, slots=True)
class ToolSourceInputFileV1:
    path: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ToolSourceInputsV1:
    schema: str
    ownership_sha256: str
    files: tuple[ToolSourceInputFileV1, ...]
    source_inputs_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("source_inputs_sha256")
        return value

    def has_valid_sha256(self) -> bool:
        if (
            self.schema != _SCHEMA
            or self.may_authorize is not False
            or self.may_execute is not False
            or type(self.files) is not tuple
            or not 1 <= len(self.files) <= _MAX_FILES
        ):
            return False
        paths = []
        total = 0
        ownership = None
        try:
            for item in self.files:
                if (
                    type(item) is not ToolSourceInputFileV1
                    or type(item.size_bytes) is not int
                    or not 0 <= item.size_bytes <= _MAX_FILE_BYTES
                    or not _is_sha256(item.content_sha256)
                ):
                    return False
                paths.append(_repository_path(item.path))
                total += item.size_bytes
                if item.path == "source-ownership.json":
                    ownership = item.content_sha256
            return (
                total <= _MAX_TOTAL_BYTES
                and paths == sorted(set(paths))
                and len({path.casefold() for path in paths}) == len(paths)
                and _MANIFEST not in paths
                and any(path not in _BUILD_INPUTS for path in paths)
                and _is_sha256(self.ownership_sha256)
                and ownership == self.ownership_sha256
                and _is_sha256(self.source_inputs_sha256)
                and self.source_inputs_sha256 == canonical_sha256(self.payload())
            )
        except (TypeError, ValueError):
            return False


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _read_input(path: Path) -> bytes:
    if _is_link(path) or not path.is_file():
        raise SourceCandidateError("source input is not a regular file")
    size = path.stat().st_size
    if not 0 <= size <= _MAX_FILE_BYTES:
        raise SourceCandidateError("source input exceeds its file size limit")
    raw = path.read_bytes()
    if len(raw) != size:
        raise SourceCandidateError("source input changed while being observed")
    return raw


def compile_tool_source_inputs(snapshot: Path) -> ToolSourceInputsV1:
    """Hash a verified private snapshot before candidate execution.

    Every byte under the policy's editable/frozen roots participates, including
    templates and transitive helper modules. No mtime, checkout path, Git commit
    message, generated mirror or generated Manifest participates. Dependency
    locks and the source-ownership policy are also inputs. This is source
    identity, not a claim about installed dependencies or runtime availability.
    """
    if not snapshot.is_absolute() or _is_link(snapshot) or not snapshot.is_dir():
        raise SourceCandidateError("source input snapshot is missing or unsafe")
    policy_raw = _read_input(snapshot / "source-ownership.json")
    try:
        policy = json.loads(policy_raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise SourceCandidateError("source input ownership policy is invalid") from exc
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != "tiangong.source-ownership.v2"
        or not isinstance(policy.get("authority_policy"), dict)
        or not isinstance(policy.get("mappings"), list)
        or validate_source_authority(policy, repo_root=snapshot)
    ):
        raise SourceCandidateError("source input ownership topology is invalid")
    authority = policy["authority_policy"]
    editable, frozen = authority.get("editable_roots"), authority.get("frozen_roots")
    if not isinstance(editable, list) or not editable or not isinstance(frozen, list):
        raise SourceCandidateError("source input ownership roots are incomplete")
    roots = tuple(sorted({_repository_path(path) for path in [*editable, *frozen]}))
    generated = set()
    for mapping in policy["mappings"]:
        generated.update(_repository_path(path) for path in mapping["targets"])
        generated.update(
            _repository_path(mapping["source"] + "/" + path)
            for path in mapping.get("generated_exclusions", ())
        )

    def excluded(relative: str) -> bool:
        return relative == _MANIFEST or any(_under(relative, path) for path in generated)

    def possible(relative: str) -> bool:
        return not excluded(relative) and any(
            _under(relative, root) or _under(root, relative) for root in roots
        )

    rows: list[ToolSourceInputFileV1] = []
    total_bytes = 0
    folded_paths: set[str] = set()

    def walk_error(error: OSError) -> None:
        raise SourceCandidateError("source input inventory is unreadable") from error

    for directory, dirs, files in os.walk(snapshot, followlinks=False, onerror=walk_error):
        current = Path(directory)
        selected_dirs = []
        for name in sorted(dirs):
            path = current / name
            relative = path.relative_to(snapshot).as_posix()
            if not possible(relative):
                continue
            _repository_path(relative)
            if _is_link(path):
                raise SourceCandidateError("source input directory is a link or junction")
            selected_dirs.append(name)
        dirs[:] = selected_dirs
        for name in sorted(files):
            path = current / name
            relative = path.relative_to(snapshot).as_posix()
            if excluded(relative) or not (
                relative in _BUILD_INPUTS or any(_under(relative, root) for root in roots)
            ):
                continue
            _repository_path(relative)
            if relative.casefold() in folded_paths:
                raise SourceCandidateError("source input inventory has a path collision")
            folded_paths.add(relative.casefold())
            raw = _read_input(path)
            total_bytes += len(raw)
            if total_bytes > _MAX_TOTAL_BYTES or len(rows) >= _MAX_FILES:
                raise SourceCandidateError("source input inventory exceeds its size limit")
            rows.append(ToolSourceInputFileV1(relative, len(raw), hashlib.sha256(raw).hexdigest()))
    rows.sort(key=lambda item: item.path)
    if not any(item.path != "source-ownership.json" and item.path not in _BUILD_INPUTS for item in rows):
        raise SourceCandidateError("source input inventory contains no source")
    draft = ToolSourceInputsV1(
        schema=_SCHEMA,
        ownership_sha256=hashlib.sha256(policy_raw).hexdigest(),
        files=tuple(rows),
        source_inputs_sha256="0" * 64,
    )
    return replace(draft, source_inputs_sha256=canonical_sha256(draft.payload()))
