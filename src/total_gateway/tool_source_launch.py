"""Pre-service source consistency checks, never Source publication approval.

P8 source-pinned releases must match their measured authority inputs and every
generated mirror before Gateway starts services. Legacy releases do not gain a
fabricated source identity. The existing release and Action compilers remain
the authorities; this module only rejects inconsistent installation evidence.
"""

from __future__ import annotations

import hashlib
from importlib.machinery import PathFinder
import json
from pathlib import Path
import sys

from .tool_source_candidate import SourceCandidateError, _strict_pairs, _invalid_constant
from .tool_source_inputs import compile_tool_source_inputs, _read_input, _is_link


_MANIFEST = "src/omni_body_skill/registry/capability_manifest.generated.json"
_MARKER = ".tiangong-generated-source.json"


class SourceLaunchError(RuntimeError):
    pass


def _safe_path(root: Path, path: Path) -> str:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise SourceLaunchError("source_launch.path_not_canonical")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SourceLaunchError("source_launch.path_outside_installation") from exc
    for current in (path, *path.parents):
        if _is_link(current):
            raise SourceLaunchError("source_launch.link_or_junction")
        if current == root:
            break
    return relative.as_posix()


def _mirror_files(root: Path, policy: dict, inputs: dict[str, str]) -> dict[str, str]:
    """Compare mirrors to measured source bytes, not their editable markers."""
    verified = dict(inputs)
    for mapping in policy["mappings"]:
        source = mapping["source"]
        source_path = root / source
        if not mapping["targets"]:
            continue
        source_is_file = source_path.is_file()
        rows = {name: digest for name, digest in inputs.items()
                if name == source or name.startswith(source + "/")}
        if not rows:
            raise SourceLaunchError("source_launch.mirror_source_not_measured")
        for target in mapping["targets"]:
            target_path = root / target
            _safe_path(root, target_path)
            expected = {
                target if source_is_file else target + name[len(source):]: digest
                for name, digest in rows.items()
            }
            actual = set()
            if source_is_file:
                paths = (target_path,)
            else:
                paths = target_path.rglob("*")
            for path in paths:
                name = _safe_path(root, path)
                if path.is_dir():
                    continue
                # Generator markers are descriptive metadata, not execution
                # inputs. Cache/extra executable files are NOT ignored here.
                if not source_is_file and path == target_path / _MARKER:
                    continue
                actual.add(name)
                if name not in expected or hashlib.sha256(_read_input(path)).hexdigest() != expected[name]:
                    raise SourceLaunchError("source_launch.generated_source_drift")
            if actual != set(expected):
                raise SourceLaunchError("source_launch.generated_source_inventory")
            verified.update(expected)
    return verified


def _package_roots(root: Path, policy: dict, files: dict[str, str]) -> dict[str, tuple[Path, ...]]:
    packages = {}
    for name in files:
        parts = Path(name).parts
        if len(parts) > 2 and parts[0] == "src" and name.endswith(".py"):
            package = parts[1]
            if package.isidentifier():
                packages[package] = (root / "src" / package,)
    for package in ("v3", "tiangong_kernel"):
        directory = root / "app/backend/tiangong-backend" / package
        if directory.is_dir():
            packages[package] = (directory,)
    for mapping in policy["mappings"]:
        for package, bases in tuple(packages.items()):
            if root / mapping["source"] == bases[0]:
                packages[package] = (*bases, *(root / name for name in mapping["targets"]))
        source = root / mapping["source"]
        if source.is_file() and source.suffix == ".py":
            for path in (source, *(root / name for name in mapping["targets"])):
                if path.stem.isidentifier() and path.stem != "__init__":
                    packages[path.stem] = (*packages.get(path.stem, ()), path)
            parts = source.parts
            if "frozen_modules" in parts:
                index = parts.index("frozen_modules") + 1
                package = parts[index]
                if package.isidentifier() and index < len(parts) - 1:
                    base = Path(*parts[:index + 1])
                    suffix_length = len(parts) - index - 1
                    targets = tuple((root / name).parents[suffix_length - 1] for name in mapping["targets"])
                    packages[package] = tuple(dict.fromkeys((*packages.get(package, ()), base, *targets)))
    return packages


def _verify_import_origins(root: Path, policy: dict, files: dict[str, str], modules, search_path) -> None:
    packages = _package_roots(root, policy, files)

    def check(bases, origin, locations):
        if origin is None and locations is None:
            raise SourceLaunchError("source_launch.module_origin_missing")
        if isinstance(locations, (str, bytes)):
            raise SourceLaunchError("source_launch.module_search_path_invalid")
        entries = [(origin, True)]
        if locations is not None:
            entries.extend((value, False) for value in locations)
        if origin is None and not entries[1:]:
            raise SourceLaunchError("source_launch.module_origin_missing")
        for value, is_file in entries:
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                raise SourceLaunchError("source_launch.module_origin_invalid")
            path = Path(value)
            relative = _safe_path(root, path)
            if not any(path == base or base in path.parents for base in bases):
                raise SourceLaunchError("source_launch.module_package_mismatch")
            if is_file and relative not in files:
                raise SourceLaunchError("source_launch.module_not_measured")

    for name, module in modules.items():
        package = name.split(".", 1)[0]
        if package.startswith(("_tiangong_omni_", "_tiangong_omni_capability_")):
            package = "omni_body_skill"
        if package not in packages:
            continue
        check(packages[package], getattr(module, "__file__", None), getattr(module, "__path__", None))
        spec = getattr(module, "__spec__", None)
        if spec is not None:
            check(packages[package], spec.origin, spec.submodule_search_locations)
    planned_path = [str(root / "src"), str(root / "app/backend/tiangong-backend"), *search_path]
    for package, bases in packages.items():
        if package in modules:
            continue
        spec = PathFinder.find_spec(package, planned_path)
        if spec is None:
            continue  # No current import route; this does not attest future path changes.
        check(bases, spec.origin, spec.submodule_search_locations)


def verify_source_revision(root: Path, *, source_inputs_sha256: str, capability_sha256: str,
                           skill_root: Path | None = None) -> dict[str, object]:
    """Observe source/mirrors/import origins without importing installation code."""
    _safe_path(root, root)
    observed = compile_tool_source_inputs(root)
    if observed.source_inputs_sha256 != source_inputs_sha256:
        raise SourceLaunchError("source_launch.source_input_drift")
    inputs = {item.path: item.content_sha256 for item in observed.files}
    if any(Path(name).suffix.lower() in {".pyc", ".pyo"} or "__pycache__" in Path(name).parts for name in inputs):
        raise SourceLaunchError("source_launch.bytecode_cache_present")
    manifest_raw = _read_input(root / _MANIFEST)
    if hashlib.sha256(manifest_raw).hexdigest() != capability_sha256:
        raise SourceLaunchError("source_launch.capability_manifest_drift")
    inputs[_MANIFEST] = capability_sha256
    policy_raw = _read_input(root / "source-ownership.json")
    if hashlib.sha256(policy_raw).hexdigest() != observed.ownership_sha256:
        raise SourceLaunchError("source_launch.ownership_drift")
    policy = json.loads(policy_raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
    if skill_root is not None:
        allowed = {root / "src/omni_body_skill"}
        for mapping in policy["mappings"]:
            if mapping["source"] == "src/omni_body_skill":
                allowed.update(root / name for name in mapping["targets"])
        if skill_root not in allowed:
            raise SourceLaunchError("source_launch.skill_root_not_owned")
    files = _mirror_files(root, policy, inputs)
    for name in files:
        observed_stat = (root / name).stat()
        if observed_stat.st_mode & 0o222 or observed_stat.st_nlink != 1:
            raise SourceLaunchError("source_launch.source_writable_or_hardlinked")
    _verify_import_origins(root, policy, files, dict(sys.modules), list(sys.path))
    return {"status": "SOURCE_CONSISTENCY_OBSERVED", "source_root": str(root), "source_inputs_sha256": source_inputs_sha256,
            "capability_manifest_sha256": capability_sha256,
            "observed_file_count": len(files), "may_publish": False, "may_authorize": False, "may_execute": False}


def preflight_source_revision(config) -> dict[str, object] | None:
    """Reject inconsistent P8 sources before leases, stores or services start.

    Source-pinned startup requires an explicit existing release manifest and
    bytecode writes disabled by the launcher (-B). It never generates a release,
    changes import paths, selects a fallback, or treats build data as approval.
    """
    root = config.release_source_root
    if root is None:
        if config.skill_root is None:
            return None
        manifest_path = config.skill_root / "registry/capability_manifest.generated.json"
    else:
        manifest_path = root / _MANIFEST
    try:
        raw = _read_input(manifest_path)
        payload = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
        if not isinstance(payload, dict):
            raise SourceLaunchError("source_launch.manifest_invalid")
        if "source_inputs_sha256" not in payload:
            return None  # Legacy is unbound, never reported as source-verified.
        if root is None:
            raise SourceLaunchError("source_launch.explicit_source_root_required")
        if config.deployment_mode != "embedded":
            raise SourceLaunchError("source_launch.embedded_mode_required")
        if not sys.dont_write_bytecode:
            raise SourceLaunchError("source_launch.bytecode_writes_must_be_disabled")
        from .release_manifest import select_latest_release_manifest_with_path
        from .skill_selection import load_model_capability_manifest

        candidates = tuple(path for path in (config.release_manifest_path, *config.release_manifest_candidates) if path is not None)
        if not candidates:
            raise SourceLaunchError("source_launch.explicit_release_required")
        _, release = select_latest_release_manifest_with_path(candidates, require_production=config.environment == "production")
        if hashlib.sha256(raw).hexdigest() != release.capability_manifest_sha256:
            raise SourceLaunchError("source_launch.release_capability_mismatch")
        # Existing release-pinned loader validates the complete document and
        # compiles its sole Action Registry; a self-hash is not that authority.
        load_model_capability_manifest(manifest_path, expected_sha256=release.capability_manifest_sha256,
                                       component_manifest_hash=release.component_manifest.manifest_sha256,
                                       generated_at_ms=release.generated_at_ms)
        if config.skill_root is not None:
            selected = config.skill_root / "registry/capability_manifest.generated.json"
            _safe_path(root, selected)
            if hashlib.sha256(_read_input(selected)).hexdigest() != release.capability_manifest_sha256:
                raise SourceLaunchError("source_launch.skill_manifest_mismatch")
        result = verify_source_revision(root, source_inputs_sha256=payload["source_inputs_sha256"],
                                        capability_sha256=release.capability_manifest_sha256,
                                        skill_root=(config.skill_root or root / "app/backend/tiangong-backend/_internal/omni_body_skill"))
        result["release_manifest_sha256"] = release.release_manifest_sha256
        return result
    except SourceLaunchError:
        raise
    except (OSError, TypeError, ValueError, SourceCandidateError) as exc:
        raise SourceLaunchError("source_launch.invalid_source_evidence") from exc
