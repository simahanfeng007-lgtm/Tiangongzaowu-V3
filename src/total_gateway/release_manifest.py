"""Deterministic single-file release manifest generation and verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from contracts import (
    ComponentDescriptor,
    ComponentManifest,
    ReleaseInputDigest,
    ReleaseManifest,
    ReleaseSourceTree,
    canonical_sha256,
    contract_schema_bundle_sha256,
)
from contracts.artifacts import generate_contract_artifact_documents

from communication_service.embedded_runtime import EMBEDDED_COMMUNICATION_BUILD_ID
from life_service.embedded_runtime import EMBEDDED_LIFE_BUILD_ID
from source_authority.validator import validate_source_authority
from runtime_security.path_identity import PathIdentityError, resolve_existing_path, verify_relative_path

from . import SINGLE_PROCESS_GATEWAY_BUILD_ID
from .embedded_backend import EMBEDDED_BACKEND_BUILD_ID
from .skill_selection import load_filesystem_skill_catalog


RELEASE_MANIFEST_FILENAME = "release-manifest.json"
MAX_RELEASE_MANIFEST_BYTES = 4 * 1024 * 1024
_BACKEND_ROOT = Path("app/backend/tiangong-backend")
_REGISTRY_ROOT = _BACKEND_ROOT / "_internal/omni_body_skill/registry"
_SKILL_ROOT = _BACKEND_ROOT / "_internal/omni_body_skill"


class ReleaseManifestError(RuntimeError):
    pass


_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _release_version_key(value: str) -> tuple[object, ...]:
    match = _SEMVER.fullmatch(value)
    if match is not None and match.group(4) is not None:
        identifiers = match.group(4).split(".")
        if any(item.isdigit() and len(item) > 1 and item.startswith("0") for item in identifiers):
            match = None
    if match is None:
        # Legacy non-SemVer builds remain selectable, but every valid SemVer
        # release outranks them.  The normalized text gives deterministic
        # ordering without trusting filesystem timestamps.
        return (0, value.casefold(), value)
    prerelease = match.group(4)
    prerelease_key: tuple[tuple[int, object], ...] = ()
    if prerelease is not None:
        prerelease_key = tuple(
            (0, int(item)) if item.isdigit() else (1, item)
            for item in prerelease.split(".")
        )
    return (
        1,
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        prerelease_key,
    )


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseManifestError("release input contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ReleaseManifestError("release input contains a non-finite JSON number")


def _strict_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except ReleaseManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"release JSON input is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise ReleaseManifestError(f"release JSON input is not an object: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    # Release evidence must describe observed bytes. Neither matching stat
    # metadata nor an on-disk cache proves that content stayed unchanged.
    # Hash afresh, including when a verification follows a prior generation.
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_workspace(workspace_root: Path) -> Path:
    if not workspace_root.is_absolute() or not workspace_root.is_dir() or workspace_root.is_symlink():
        raise ReleaseManifestError("release workspace root is missing or unsafe")
    if os.name == "nt":
        _verify_windows_release_path(workspace_root, workspace_root)
        return workspace_root
    return workspace_root.resolve(strict=True)


def _verify_windows_release_path(workspace_root: Path, path: Path) -> None:
    # Reuse the same normalized native volume/root/relative identity check as
    # source startup. Do not replace strict evidence with Path.resolve(False)
    # when AppContainer denies the DOS-volume query used by pathlib.
    try:
        verify_relative_path(workspace_root, path)
    except (OSError, PathIdentityError) as exc:
        raise ReleaseManifestError("release input physical path is unsafe") from exc


def _safe_file(workspace_root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ReleaseManifestError("release input path is unsafe")
    path = workspace_root / relative
    if not path.is_file() or path.is_symlink():
        raise ReleaseManifestError(f"release input file is missing or unsafe: {relative_path}")
    if os.name == "nt":
        _verify_windows_release_path(workspace_root, path)
    else:
        resolved = path.resolve(strict=True)
        if workspace_root not in resolved.parents:
            raise ReleaseManifestError("release input escaped the workspace")
    return path


def _input_digest(workspace_root: Path, input_id: str, relative_path: str) -> ReleaseInputDigest:
    path = _safe_file(workspace_root, relative_path)
    return ReleaseInputDigest(
        input_id=input_id,
        relative_path=relative_path.replace("\\", "/"),
        size_bytes=path.stat().st_size,
        sha256=_sha256_file(path),
    )


def _tree_files(workspace_root: Path, roots: Iterable[str]) -> tuple[Path, ...]:
    files: dict[str, Path] = {}
    for relative_text in roots:
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ReleaseManifestError("release tree root is unsafe")
        path = workspace_root / relative
        if not path.exists() or path.is_symlink():
            raise ReleaseManifestError(f"release tree root is missing or unsafe: {relative_text}")
        candidates = (path,) if path.is_file() else tuple(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise ReleaseManifestError("release tree contains a symbolic link")
            if not candidate.is_file():
                continue
            if candidate.suffix == ".pyc" or "__pycache__" in candidate.parts:
                continue
            if os.name == "nt":
                _verify_windows_release_path(workspace_root, candidate)
            else:
                resolved = candidate.resolve(strict=True)
                if workspace_root not in resolved.parents:
                    raise ReleaseManifestError("release tree file escaped the workspace")
            relative_name = candidate.relative_to(workspace_root).as_posix()
            files[relative_name] = candidate
    return tuple(files[name] for name in sorted(files))


def _source_tree(
    workspace_root: Path,
    tree_id: str,
    roots: tuple[str, ...],
) -> ReleaseSourceTree:
    normalized_roots = tuple(sorted(set(item.replace("\\", "/") for item in roots)))
    files = _tree_files(workspace_root, normalized_roots)
    entries = tuple(
        {
            "path": path.relative_to(workspace_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in files
    )
    return ReleaseSourceTree(
        tree_id=tree_id,
        roots=normalized_roots,
        file_count=len(entries),
        size_bytes=sum(int(item["size_bytes"]) for item in entries),
        tree_sha256=canonical_sha256(
            {
                "domain": "tiangong.release-source-tree.v1",
                "tree_id": tree_id,
                "entries": entries,
            }
        ),
    )


def _gateway_source_roots(workspace_root: Path) -> tuple[str, ...]:
    """Bind the monolith's source closure using the existing ownership policy.

    This is release provenance, not a new Source/Action authority. Unlike the
    compiler input closure, release inputs also include generated files inside
    these roots: they are installed bytes, and this digest is not fed back into
    capability compilation. Existing source-tree hashing semantics stay intact.
    """
    policy = _strict_json(_safe_file(workspace_root, "source-ownership.json"))
    authority = policy.get("authority_policy")
    if not isinstance(authority, dict):
        raise ReleaseManifestError("release source ownership policy is incomplete")
    editable = authority.get("editable_roots")
    frozen = authority.get("frozen_roots")
    if (
        not isinstance(editable, list) or not editable
        or not isinstance(frozen, list)
        or any(not isinstance(item, str) for item in [*editable, *frozen])
        or validate_source_authority(policy, repo_root=workspace_root)
    ):
        raise ReleaseManifestError("release source ownership topology is invalid")
    roots = tuple(sorted({
        # Retain every previously bound Gateway authority even if a malformed
        # policy attempts to omit it. Tool/backend/frozen roots come from the
        # single Source Authority rather than a copied list of implementations.
        "pyproject.toml", "src/contracts", "src/runtime_security", "src/total_gateway",
        "source-ownership.json", "requirements-source.lock", *editable, *frozen,
    }))
    try:
        ReleaseSourceTree.validate_roots(roots)
    except ValueError as exc:
        raise ReleaseManifestError("release source ownership roots are unsafe") from exc
    if len(roots) > 64:
        raise ReleaseManifestError("release source ownership roots exceed their limit")
    return roots


def _desktop_source_roots(workspace_root: Path) -> tuple[str, ...]:
    app_root = workspace_root / "app"
    # Generated release authorities and local dependency trees are not source
    # inputs. Excluding them prevents a manifest from recursively hashing its
    # own output and keeps clean-checkout verification deterministic.
    excluded = {
        ".venv",
        "backend",
        "communication-service",
        "life-service",
        "node_modules",
        "release",
        "release-manifest.json",
        "resources",
        "SHA256SUMS.txt",
    }
    return tuple(
        sorted(
            item.relative_to(workspace_root).as_posix()
            for item in app_root.iterdir()
            if item.name not in excluded
        )
    )


def _component(
    workspace_root: Path,
    *,
    component_id: str,
    version: str,
    build_id: str,
    role: str,
    entry_path: str,
    ports: tuple[int, ...],
    api_contract_ids: tuple[str, ...],
    schema_sha256: str,
) -> ComponentDescriptor:
    entry = _safe_file(workspace_root, entry_path)
    return ComponentDescriptor(
        component_id=component_id,
        version=version,
        build_id=build_id,
        role=role,
        executable_relative_path=entry_path,
        sha256=_sha256_file(entry),
        size_bytes=entry.stat().st_size,
        ports=ports,
        api_contract_ids=tuple(sorted(api_contract_ids)),
        schema_bundle_hash=schema_sha256,
    )


def _component_from_file(
    *,
    component_id: str,
    version: str,
    build_id: str,
    role: str,
    executable_relative_path: str,
    executable_path: Path,
    ports: tuple[int, ...],
    api_contract_ids: tuple[str, ...],
    schema_sha256: str,
) -> ComponentDescriptor:
    if (
        not executable_path.is_absolute()
        or not executable_path.is_file()
        or executable_path.is_symlink()
    ):
        raise ReleaseManifestError(
            f"production component is missing or unsafe: {component_id}"
        )
    return ComponentDescriptor(
        component_id=component_id,
        version=version,
        build_id=build_id,
        role=role,
        executable_relative_path=executable_relative_path,
        sha256=_sha256_file(executable_path),
        size_bytes=executable_path.stat().st_size,
        ports=ports,
        api_contract_ids=tuple(sorted(api_contract_ids)),
        schema_bundle_hash=schema_sha256,
    )


def generate_release_manifest(workspace_root: Path) -> ReleaseManifest:
    root = _safe_workspace(workspace_root)
    return _generate_release_manifest(root)


def _generate_release_manifest(root: Path) -> ReleaseManifest:
    package_path = _safe_file(root, "app/package.json")
    build_info_path = _safe_file(root, "app/build-info.json")
    backend_release_path = _safe_file(root, (_BACKEND_ROOT / "_internal/release.json").as_posix())
    snapshot_path = _safe_file(root, "manifest.json")
    project_path = _safe_file(root, "pyproject.toml")
    action_path = _safe_file(root, (_REGISTRY_ROOT / "actions.json").as_posix())
    capability_path = _safe_file(
        root,
        (_REGISTRY_ROOT / "capability_manifest.generated.json").as_posix(),
    )
    skill_index_path = _safe_file(
        root,
        (_REGISTRY_ROOT / "skill_router_index.json").as_posix(),
    )

    package = _strict_json(package_path)
    build_info = _strict_json(build_info_path)
    backend_release = _strict_json(backend_release_path)
    snapshot = _strict_json(snapshot_path)
    _strict_json(action_path)
    _strict_json(capability_path)
    _strict_json(skill_index_path)
    try:
        project = tomllib.loads(project_path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseManifestError("Python project metadata is invalid") from exc

    product_version = str(package.get("version") or "")
    build_id = str(build_info.get("build_id") or "")
    backend_version = str(backend_release.get("version") or "")
    project_version = str(project.get("project", {}).get("version") or "")
    if (
        product_version != str(build_info.get("product_version") or "")
        or not product_version
        or not build_id
        or not backend_version
        or not project_version
        or backend_release.get("build_id") != build_info.get("backend_build_id")
        or backend_release.get("api_contract_id") != build_info.get("api_contract_version")
    ):
        raise ReleaseManifestError("release version or contract authorities disagree")
    try:
        generated_at_ms = int(
            datetime.fromisoformat(str(snapshot["generated_at"])).timestamp() * 1_000
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseManifestError("source snapshot timestamp is invalid") from exc

    schema_sha256 = contract_schema_bundle_sha256()
    action_sha256 = _sha256_file(action_path)
    capability_sha256 = _sha256_file(capability_path)
    skill_index_sha256 = _sha256_file(skill_index_path)
    loaded_skills = load_filesystem_skill_catalog(
        root / _SKILL_ROOT,
        expected_index_sha256=skill_index_sha256,
    )
    release_policy = package.get("tiangongRelease")
    if not isinstance(release_policy, dict) or not release_policy:
        raise ReleaseManifestError("desktop release policy is missing")
    release_policy_sha256 = canonical_sha256(release_policy)

    contract_documents = generate_contract_artifact_documents()
    contract_manifest_bytes = contract_documents["contract-artifacts.manifest.json"]
    contract_manifest = json.loads(contract_manifest_bytes)
    contract_manifest_sha256 = str(contract_manifest.get("manifest_sha256") or "")

    components = tuple(
        sorted(
            (
                _component(
                    root,
                    component_id="tiangong-backend",
                    version=backend_version,
                    build_id=EMBEDDED_BACKEND_BUILD_ID,
                    role="execution",
                    entry_path="src/total_gateway/embedded_backend.py",
                    ports=(),
                    api_contract_ids=(str(backend_release["api_contract_id"]),),
                    schema_sha256=schema_sha256,
                ),
                _component(
                    root,
                    component_id="tiangong-communication-service",
                    version=project_version,
                    build_id=EMBEDDED_COMMUNICATION_BUILD_ID,
                    role="communication",
                    entry_path="src/communication_service/embedded_runtime.py",
                    ports=(),
                    api_contract_ids=("tiangong.communication.api.v1",),
                    schema_sha256=schema_sha256,
                ),
                _component(
                    root,
                    component_id="tiangong-desktop",
                    version=product_version,
                    build_id=build_id,
                    role="desktop",
                    entry_path="app/main.js",
                    ports=(),
                    api_contract_ids=(
                        str(build_info.get("frontend_contract_id") or ""),
                        "tiangong.total-gateway.api.v1",
                    ),
                    schema_sha256=schema_sha256,
                ),
                _component(
                    root,
                    component_id="tiangong-life-service",
                    version=product_version,
                    build_id=EMBEDDED_LIFE_BUILD_ID,
                    role="life",
                    entry_path="src/life_service/embedded_runtime.py",
                    ports=(),
                    api_contract_ids=("tiangong.life.api.v2",),
                    schema_sha256=schema_sha256,
                ),
                _component(
                    root,
                    component_id="tiangong-total-gateway",
                    version=project_version,
                    build_id=SINGLE_PROCESS_GATEWAY_BUILD_ID,
                    role="orchestrator",
                    entry_path="src/total_gateway/__main__.py",
                    ports=(7184,),
                    api_contract_ids=("tiangong.total-gateway.api.v1",),
                    schema_sha256=schema_sha256,
                ),
            ),
            key=lambda item: item.component_id,
        )
    )
    component_manifest = ComponentManifest(
        manifest_id=f"component-manifest-{product_version}-single-process-source",
        product_version=product_version,
        generated_at_ms=generated_at_ms,
        contract_schema_bundle_hash=schema_sha256,
        capability_manifest_hash=capability_sha256,
        skill_index_hash=skill_index_sha256,
        release_policy_hash=release_policy_sha256,
        components=components,
        production_claim=False,
        manifest_sha256="0" * 64,
    ).with_computed_manifest_sha256()

    inputs = tuple(
        sorted(
            (
                _input_digest(root, "action-registry", action_path.relative_to(root).as_posix()),
                _input_digest(root, "backend-release", backend_release_path.relative_to(root).as_posix()),
                _input_digest(root, "capability-manifest", capability_path.relative_to(root).as_posix()),
                _input_digest(root, "desktop-build-info", build_info_path.relative_to(root).as_posix()),
                _input_digest(root, "desktop-package", package_path.relative_to(root).as_posix()),
                _input_digest(root, "python-project", project_path.relative_to(root).as_posix()),
                _input_digest(root, "skill-index", skill_index_path.relative_to(root).as_posix()),
                _input_digest(root, "source-snapshot", snapshot_path.relative_to(root).as_posix()),
            ),
            key=lambda item: item.input_id,
        )
    )
    source_trees = tuple(
        sorted(
            (
                _source_tree(
                    root,
                    "communication-source",
                    ("pyproject.toml", "src/communication_service", "src/contracts", "src/runtime_security"),
                ),
                _source_tree(root, "desktop-source", _desktop_source_roots(root)),
                _source_tree(
                    root,
                    "gateway-source",
                    _gateway_source_roots(root),
                ),
                _source_tree(
                    root,
                    "life-source",
                    (
                        "app/life-service/runtime314/contracts",
                        "app/life-service/runtime314/life_service",
                        "app/life-service/runtime314/tiangong_life_bootstrap.py",
                        "app/life-service/runtime314/tiangong_life_runtime_fixes.py",
                        "baselines/life-runtime-p0.json",
                        "pyproject.toml",
                        "readable-python-source/life-bootstrap",
                        "src/contracts",
                        "src/life_service",
                    ),
                ),
            ),
            key=lambda item: item.tree_id,
        )
    )
    return ReleaseManifest(
        release_id=f"tiangong-{product_version}-p11-source",
        product_version=product_version,
        build_id=build_id,
        release_channel="development",
        generated_at_ms=generated_at_ms,
        component_manifest=component_manifest,
        contract_artifact_manifest_file_sha256=hashlib.sha256(
            contract_manifest_bytes
        ).hexdigest(),
        contract_artifact_manifest_sha256=contract_manifest_sha256,
        contract_schema_bundle_sha256=schema_sha256,
        action_registry_sha256=action_sha256,
        capability_manifest_sha256=capability_sha256,
        skill_index_sha256=skill_index_sha256,
        skill_catalog_sha256=loaded_skills.catalog.sha256,
        release_policy_sha256=release_policy_sha256,
        inputs=inputs,
        source_trees=source_trees,
        production_claim=False,
        release_manifest_sha256="0" * 64,
    ).with_computed_release_manifest_sha256()


def generate_production_release_manifest(
    workspace_root: Path,
    runtime_root: Path,
    *,
    platform_name: str,
    architecture: str,
    desktop_archive_path: Path,
    generated_at_ms: int | None = None,
) -> ReleaseManifest:
    if platform_name not in {"win32", "darwin"}:
        raise ReleaseManifestError("production release platform is unsupported")
    if architecture not in {"x64", "arm64"}:
        raise ReleaseManifestError("production release architecture is unsupported")

    root = _safe_workspace(workspace_root)
    runtime = _safe_workspace(runtime_root)
    if not desktop_archive_path.is_absolute() or desktop_archive_path.is_symlink():
        raise ReleaseManifestError("production desktop archive is missing or unsafe")
    try:
        if os.name == "nt":
            # Observe both names through the same existing no-reparse primitive.
            # A proven 8.3 alias may expand; a redirected ancestor may not.
            runtime = resolve_existing_path(runtime)
            desktop_archive = resolve_existing_path(desktop_archive_path)
            _verify_windows_release_path(runtime, desktop_archive)
        else:
            desktop_archive = desktop_archive_path.resolve(strict=True)
    except (OSError, PathIdentityError) as exc:
        raise ReleaseManifestError("production desktop archive is missing or unsafe") from exc
    if (
        not desktop_archive.is_file()
        or desktop_archive.is_symlink()
        or (os.name != "nt" and os.path.normcase(str(desktop_archive))
            != os.path.normcase(str(desktop_archive_path.absolute())))
        or runtime not in desktop_archive.parents
        or desktop_archive.name != "app.asar"
    ):
        raise ReleaseManifestError("production desktop archive is missing or unsafe")
    source = generate_release_manifest(root)
    if generated_at_ms is None:
        raw_generated_at_ms = os.environ.get("TIANGONG_RELEASE_GENERATED_AT_MS", "").strip()
        try:
            generated_at_ms = (
                int(raw_generated_at_ms)
                if raw_generated_at_ms
                else time.time_ns() // 1_000_000
            )
        except ValueError as exc:
            raise ReleaseManifestError("production release timestamp is invalid") from exc
    if (
        isinstance(generated_at_ms, bool)
        or not isinstance(generated_at_ms, int)
        or generated_at_ms < source.generated_at_ms
        or generated_at_ms > 9_007_199_254_740_991
    ):
        raise ReleaseManifestError("production release timestamp is invalid")
    source_components = {
        item.component_id: item for item in source.component_manifest.components
    }
    suffix = ".exe" if platform_name == "win32" else ""

    # The packaged product is a modular monolith: Runtime, Life, Communication
    # and orchestration remain separately described logical components, but all
    # four are cryptographically bound to the same frozen 7184 executable.
    single_executable = _safe_file(
        runtime, f"total-gateway/tiangong-total-gateway{suffix}",
    )
    executable_paths = {
        component_id: single_executable
        for component_id in (
            "tiangong-backend",
            "tiangong-life-service",
            "tiangong-communication-service",
            "tiangong-total-gateway",
        )
    }
    single_logical_path = f"total-gateway/tiangong-total-gateway{suffix}"
    logical_paths = {
        component_id: single_logical_path
        for component_id in executable_paths
    }

    components: list[ComponentDescriptor] = []
    for component_id in (
        "tiangong-backend",
        "tiangong-communication-service",
        "tiangong-life-service",
        "tiangong-total-gateway",
    ):
        descriptor = source_components[component_id]
        component_version = (
            source.product_version
            if "dev" in descriptor.version.lower()
            else descriptor.version
        )
        components.append(
            _component_from_file(
                component_id=component_id,
                version=component_version,
                build_id=descriptor.build_id,
                role=descriptor.role,
                executable_relative_path=logical_paths[component_id],
                executable_path=executable_paths[component_id],
                ports=descriptor.ports,
                api_contract_ids=descriptor.api_contract_ids,
                schema_sha256=source.contract_schema_bundle_sha256,
            )
        )

    desktop_source = source_components["tiangong-desktop"]
    components.append(
        _component_from_file(
            component_id="tiangong-desktop",
            version=desktop_source.version,
            build_id=desktop_source.build_id,
            role=desktop_source.role,
            executable_relative_path="app.asar",
            executable_path=desktop_archive,
            ports=desktop_source.ports,
            api_contract_ids=desktop_source.api_contract_ids,
            schema_sha256=source.contract_schema_bundle_sha256,
        )
    )

    component_manifest = ComponentManifest(
        manifest_id=(
            f"component-manifest-{source.product_version}-stable-"
            f"{platform_name}-{architecture}"
        ),
        product_version=source.product_version,
        generated_at_ms=generated_at_ms,
        contract_schema_bundle_hash=source.contract_schema_bundle_sha256,
        capability_manifest_hash=source.capability_manifest_sha256,
        skill_index_hash=source.skill_index_sha256,
        release_policy_hash=source.release_policy_sha256,
        components=tuple(sorted(components, key=lambda item: item.component_id)),
        production_claim=True,
        manifest_sha256="0" * 64,
    ).with_computed_manifest_sha256()

    return ReleaseManifest(
        release_id=(
            f"tiangong-{source.product_version}-stable-"
            f"{platform_name}-{architecture}"
        ),
        product_version=source.product_version,
        build_id=source.build_id,
        release_channel="stable",
        generated_at_ms=generated_at_ms,
        component_manifest=component_manifest,
        contract_artifact_manifest_file_sha256=(
            source.contract_artifact_manifest_file_sha256
        ),
        contract_artifact_manifest_sha256=source.contract_artifact_manifest_sha256,
        contract_schema_bundle_sha256=source.contract_schema_bundle_sha256,
        action_registry_sha256=source.action_registry_sha256,
        capability_manifest_sha256=source.capability_manifest_sha256,
        skill_index_sha256=source.skill_index_sha256,
        skill_catalog_sha256=source.skill_catalog_sha256,
        release_policy_sha256=source.release_policy_sha256,
        inputs=source.inputs,
        source_trees=source.source_trees,
        production_claim=True,
        release_manifest_sha256="0" * 64,
    ).with_computed_release_manifest_sha256()


def release_manifest_bytes(manifest: ReleaseManifest) -> bytes:
    if not manifest.has_valid_release_manifest_sha256():
        raise ReleaseManifestError("release manifest self digest is invalid")
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _windows_appcontainer_sddl() -> str | None:
    """Describe a NEW private stage for the effective OS token, never an ACL repair.

    CPython's Windows mkdir(0700) excludes the AppContainer SID (gh-134587).
    Ordinary host creation stays with tempfile. Only an OS-observed container
    adds its own exact SID to its newly-created stage; no existing path ACL,
    token, privilege, parent or sandbox permission is changed.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    security.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenThreadToken.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.IsValidSid.argtypes = [wintypes.LPVOID]
    security.IsValidSid.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    # Respect effective impersonation. Only ERROR_NO_TOKEN permits process
    # fallback; access-denied or unobservable identity must reject creation.
    if not security.OpenThreadToken(kernel.GetCurrentThread(), 0x8, True, ctypes.byref(token)):
        error = ctypes.get_last_error()
        if error != 1008:
            raise ctypes.WinError(error)
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        flag, length = wintypes.DWORD(), wintypes.DWORD()
        if not security.GetTokenInformation(token, 29, ctypes.byref(flag), ctypes.sizeof(flag), ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if length.value != ctypes.sizeof(flag) or flag.value not in (0, 1):
            raise ReleaseManifestError("release staging token flag is invalid")
        if not flag.value:
            return None

        def sid_text(kind: int) -> str:
            required = wintypes.DWORD()
            ok = security.GetTokenInformation(token, kind, None, 0, ctypes.byref(required))
            if ok or ctypes.get_last_error() != 122 or not ctypes.sizeof(ctypes.c_void_p) <= required.value <= 65536:
                raise ReleaseManifestError("release staging SID evidence is unavailable")
            data = ctypes.create_string_buffer(required.value)
            if not security.GetTokenInformation(token, kind, data, len(data), ctypes.byref(required)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not ctypes.sizeof(ctypes.c_void_p) <= required.value <= len(data):
                raise ReleaseManifestError("release staging SID evidence size changed")
            # TOKEN_USER and TOKEN_APPCONTAINER_INFORMATION start with a SID*.
            sid = ctypes.c_void_p.from_buffer(data).value
            if not sid or not security.IsValidSid(sid):
                raise ReleaseManifestError("release staging SID is invalid")
            text = wintypes.LPWSTR()
            if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                value = text.value or ""
                if re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", value) is None:
                    raise ReleaseManifestError("release staging SID text is invalid")
                return value
            finally:
                if kernel.LocalFree(text):
                    raise ReleaseManifestError("release staging SID cleanup failed")

        user_sid, app_sid = sid_text(1), sid_text(31)
        if re.fullmatch(r"S-1-15-2-(?:[0-9]+-){6}[0-9]+", app_sid) is None:
            raise ReleaseManifestError("release staging requires an exact AppContainer SID")
        # Protected, non-inherited DACL: host owner/admin/system plus precisely
        # this container, NOT Everyone, Users or ALL APPLICATION PACKAGES.
        return "D:P" + "".join(f"(A;OICI;FA;;;{sid})" for sid in ("SY", "BA", user_sid, app_sid))
    finally:
        if not kernel.CloseHandle(token):
            raise ReleaseManifestError("release staging token cleanup failed")


def _windows_private_stage(target: Path, descriptor: str) -> Path:
    import ctypes
    from ctypes import wintypes
    import secrets

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                    ("bInheritHandle", wintypes.BOOL)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    native = wintypes.LPVOID()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(descriptor, 1, ctypes.byref(native), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not native.value:
            raise ReleaseManifestError("release staging descriptor is missing")
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), native, False)
        for _ in range(16):
            stage = target.parent / f".{target.name}-{secrets.token_hex(16)}"
            # Exclusive creation; an existing directory/link is never reused.
            if kernel.CreateDirectoryW(str(stage), ctypes.byref(attributes)):
                return stage
            error = ctypes.get_last_error()
            if error not in (80, 183):
                raise ctypes.WinError(error)
        raise FileExistsError("release staging name collision limit reached")
    finally:
        if kernel.LocalFree(native):
            raise ReleaseManifestError("release staging descriptor cleanup failed")


def _create_release_stage(target: Path) -> Path:
    if os.name == "nt":
        descriptor = _windows_appcontainer_sddl()
        if descriptor is not None:
            return _windows_private_stage(target, descriptor)
    return Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))


@contextmanager
def _release_staging(output_dir: Path):
    target = output_dir.absolute()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise FileExistsError("release manifest target must be absent or empty")
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = _create_release_stage(target)
    failure = None
    try:
        yield stage, target
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if stage.exists():
                shutil.rmtree(stage)
        except OSError as cleanup:
            if failure is None:
                raise
            failure.add_note(f"release_stage_cleanup_failed: {type(cleanup).__name__}: {cleanup}")


def write_release_manifest(output_dir: Path, workspace_root: Path) -> ReleaseManifest:
    with _release_staging(output_dir) as (stage, target):
        manifest = generate_release_manifest(workspace_root)
        (stage / RELEASE_MANIFEST_FILENAME).write_bytes(release_manifest_bytes(manifest))
        os.replace(stage, target)
    return verify_release_manifest_file(target / RELEASE_MANIFEST_FILENAME, workspace_root)


def write_production_release_manifest(
    output_dir: Path,
    workspace_root: Path,
    runtime_root: Path,
    *,
    platform_name: str,
    architecture: str,
    desktop_archive_path: Path,
) -> ReleaseManifest:
    with _release_staging(output_dir) as (stage, target):
        manifest = generate_production_release_manifest(
            workspace_root,
            runtime_root,
            platform_name=platform_name,
            architecture=architecture,
            desktop_archive_path=desktop_archive_path,
        )
        (stage / RELEASE_MANIFEST_FILENAME).write_bytes(release_manifest_bytes(manifest))
        os.replace(stage, target)
    verified = verify_release_manifest_file(target / RELEASE_MANIFEST_FILENAME)
    if release_manifest_bytes(verified) != release_manifest_bytes(manifest):
        raise ReleaseManifestError("production release manifest verification drifted")
    return verified


def verify_release_manifest_file(path: Path, workspace_root: Path | None = None) -> ReleaseManifest:
    if not path.is_file() or path.is_symlink():
        raise ReleaseManifestError("release manifest file is missing or unsafe")
    size_bytes = path.stat().st_size
    if size_bytes < 2 or size_bytes > MAX_RELEASE_MANIFEST_BYTES:
        raise ReleaseManifestError("release manifest size is invalid")
    try:
        raw = path.read_bytes()
        json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
        # Validate from JSON mode so strict tuple fields accept JSON arrays while
        # scalar coercion stays disabled.  The guard above remains authoritative
        # for duplicate keys and non-finite numbers.
        manifest = ReleaseManifest.model_validate_json(raw, strict=True)
    except ReleaseManifestError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseManifestError("release manifest is invalid") from exc
    if not manifest.has_valid_release_manifest_sha256():
        raise ReleaseManifestError("release manifest self digest is invalid")
    if raw != release_manifest_bytes(manifest):
        raise ReleaseManifestError("release manifest encoding is not canonical")
    if workspace_root is not None:
        expected = generate_release_manifest(workspace_root)
        if release_manifest_bytes(manifest) != release_manifest_bytes(expected):
            raise ReleaseManifestError("release manifest does not match current source authority")
    return manifest


def select_latest_release_manifest(
    candidates: Iterable[Path],
    *,
    require_production: bool = False,
) -> ReleaseManifest:
    """Select the newest fully verified release authority.

    Selection is based on semantic product version first and the signed-in
    manifest generation time second. Filesystem modification time is never an
    authority. Invalid, duplicate, and non-production candidates are ignored;
    if none remain the caller fails closed.
    """

    _, manifest = select_latest_release_manifest_with_path(
        candidates,
        require_production=require_production,
    )
    return manifest


def select_latest_release_manifest_with_path(
    candidates: Iterable[Path],
    *,
    require_production: bool = False,
) -> tuple[Path, ReleaseManifest]:
    """Return the authoritative manifest together with its verified origin.

    Keeping the path attached to the selected manifest matters in production:
    component binary paths are relative to the resources directory containing
    that manifest.  Re-discovering a path after selection can accidentally
    bind evidence from one release to another release's files.
    """

    verified: list[tuple[Path, ReleaseManifest]] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            manifest = verify_release_manifest_file(resolved)
        except ReleaseManifestError:
            continue
        if require_production and not manifest.production_claim:
            continue
        verified.append((resolved, manifest))
    if not verified:
        raise ReleaseManifestError("no verified release manifest candidate is available")
    return max(
        verified,
        key=lambda item: (
            _release_version_key(item[1].product_version),
            item[1].generated_at_ms,
            item[1].release_manifest_sha256,
            str(item[0]).casefold(),
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-root")
    parser.add_argument("--platform", choices=("win32", "darwin"))
    parser.add_argument("--arch", choices=("x64", "arm64"))
    parser.add_argument("--desktop-archive")
    parser.add_argument("--production", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.production:
        if (
            not arguments.runtime_root
            or not arguments.platform
            or not arguments.arch
            or not arguments.desktop_archive
        ):
            parser.error(
                "--production requires --runtime-root, --platform, --arch, and --desktop-archive"
            )
        manifest = write_production_release_manifest(
            Path(arguments.output),
            Path(arguments.workspace),
            Path(arguments.runtime_root),
            platform_name=arguments.platform,
            architecture=arguments.arch,
            desktop_archive_path=Path(arguments.desktop_archive),
        )
    else:
        if (
            arguments.runtime_root
            or arguments.platform
            or arguments.arch
            or arguments.desktop_archive
        ):
            parser.error("runtime arguments require --production")
        manifest = write_release_manifest(
            Path(arguments.output),
            Path(arguments.workspace),
        )
    print(
        json.dumps(
            {
                "release_id": manifest.release_id,
                "release_manifest_sha256": manifest.release_manifest_sha256,
                "component_manifest_sha256": manifest.component_manifest.manifest_sha256,
                "production_claim": manifest.production_claim,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_RELEASE_MANIFEST_BYTES",
    "RELEASE_MANIFEST_FILENAME",
    "ReleaseManifestError",
    "generate_production_release_manifest",
    "generate_release_manifest",
    "release_manifest_bytes",
    "select_latest_release_manifest",
    "select_latest_release_manifest_with_path",
    "verify_release_manifest_file",
    "write_production_release_manifest",
    "write_release_manifest",
]
