"""Content-bound Tool Source build packages; no publication or activation.

The trusted build parent invokes this only after contained compilation. The
package keeps that exact source revision, compiled Manifest and official
mirrors together after the private build snapshot is removed. It is review
material, not a Registry, release approval, import hook or running-task switch.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
from typing import Callable
import zipfile

from contracts import canonical_json_bytes, canonical_sha256

from .action_registry import compile_action_authority
from .tool_source_candidate import SourceCandidateError, _repository_path, _strict_pairs, _invalid_constant
from .tool_source_inputs import ToolSourceInputFileV1, ToolSourceInputsV1, compile_tool_source_inputs, _is_sha256


_SCHEMA = "tiangong.tool-source-build-bundle.v1"
_INDEX = "bundle-manifest.json"
_REPORT = "build-report.json"
_MANIFEST = "src/omni_body_skill/registry/capability_manifest.generated.json"
_RESERVED = {".tiangong-source-build-worker.py", ".tiangong-candidate-compiled-manifest.json"}
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_FILES = 20000


def _json_bytes(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _file_rows(snapshot: Path) -> list[tuple[str, Path]]:
    rows = []
    folded = set()
    for path in sorted(snapshot.rglob("*")):
        relative = path.relative_to(snapshot).as_posix()
        if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
            raise SourceCandidateError("source bundle contains a link or junction")
        if path.is_dir():
            continue
        _repository_path(relative)
        if relative in _RESERVED:
            continue
        if not path.is_file() or snapshot not in path.resolve(strict=True).parents:
            raise SourceCandidateError("source bundle contains an unsafe file")
        name = "source/" + relative
        if name.casefold() in folded:
            raise SourceCandidateError("source bundle has a path collision")
        folded.add(name.casefold())
        rows.append((name, path))
        if len(rows) >= _MAX_FILES:
            raise SourceCandidateError("source bundle file count exceeds its limit")
    return sorted(rows)


def _entry(name: str, raw: bytes, *, executable: bool = False) -> dict[str, object]:
    if len(raw) > _MAX_FILE_BYTES:
        raise SourceCandidateError("source bundle file exceeds its size limit")
    return {"path": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "executable": executable}


def _zip_write(archive: zipfile.ZipFile, name: str, raw: bytes, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, raw)


def write_tool_source_bundle(
    snapshot: Path,
    *,
    source_inputs: ToolSourceInputsV1,
    report: dict[str, object],
    output_path: Path,
    synchronize_mirrors: Callable[[Path], None],
    mirror_generator_sha256: str,
) -> dict[str, object]:
    """Prepare a new review package; never overwrite or activate a version.

    Only the caller-owned private snapshot is transformed. The generated
    Manifest comes from the existing contained compiler; every mirror is made
    by the official generator supplied by the installed trusted build parent.
    A partial file after an I/O failure is retained but cannot pass verification.
    """
    if (not output_path.is_absolute() or not output_path.parent.is_dir()
            or output_path.exists() or output_path.is_symlink()):
        raise SourceCandidateError("source bundle output must be a new absolute file")
    if snapshot == output_path.parent or snapshot in output_path.resolve(strict=False).parents:
        raise SourceCandidateError("source bundle output cannot be inside its input snapshot")
    if not _is_sha256(mirror_generator_sha256):
        raise SourceCandidateError("source bundle mirror generator identity is invalid")
    if not source_inputs.has_valid_sha256() or compile_tool_source_inputs(snapshot) != source_inputs:
        raise SourceCandidateError("source bundle inputs differ from the isolated build")
    artifact = report.get("build_artifact")
    if (report.get("status") != "ISOLATED_BUILD_OBSERVED"
            or any(report.get(key) is not False for key in ("may_publish", "may_authorize", "may_execute"))
            or not isinstance(artifact, dict)
            or canonical_json_bytes(artifact.get("source_inputs")) != canonical_json_bytes(asdict(source_inputs))):
        raise SourceCandidateError("source bundle requires an unapproved isolated-build report")
    manifest = artifact.get("gateway_manifest")
    if not isinstance(manifest, dict) or manifest.get("source_inputs_sha256") != source_inputs.source_inputs_sha256:
        raise SourceCandidateError("source bundle Manifest revision is invalid")
    compile_action_authority(manifest, generated_at_ms=0)
    _file_rows(snapshot)  # Validate destinations before transforming any file.
    manifest_path = snapshot / _MANIFEST
    if not manifest_path.is_file():
        raise SourceCandidateError("source bundle lacks the existing Manifest output")
    manifest_raw = _json_bytes(manifest)
    manifest_path.write_bytes(manifest_raw)
    synchronize_mirrors(snapshot)
    if compile_tool_source_inputs(snapshot) != source_inputs:
        raise SourceCandidateError("source bundle generation changed compiler inputs")
    rows = _file_rows(snapshot)
    report_raw = _json_bytes(report)
    entries = [_entry(_REPORT, report_raw)]
    total = len(report_raw)
    with output_path.open("xb") as stream, zipfile.ZipFile(stream, "w") as archive:
        _zip_write(archive, _REPORT, report_raw)
        for name, path in rows:
            if not 0 <= path.stat().st_size <= _MAX_FILE_BYTES:
                raise SourceCandidateError("source bundle file exceeds its size limit")
            raw = path.read_bytes()
            executable = bool(path.stat().st_mode & 0o111)
            entry = _entry(name, raw, executable=executable)
            total += len(raw)
            if total > _MAX_TOTAL_BYTES:
                raise SourceCandidateError("source bundle exceeds its total size limit")
            entries.append(entry)
            _zip_write(archive, name, raw, executable)
        index = {
            "schema": _SCHEMA, "source_inputs_sha256": source_inputs.source_inputs_sha256,
            "capability_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "mirror_generator_sha256": mirror_generator_sha256,
            "file_count": len(entries), "size_bytes": total,
            "entries": sorted(entries, key=lambda item: item["path"]),
            "may_publish": False, "may_authorize": False, "may_execute": False,
        }
        index["bundle_manifest_sha256"] = canonical_sha256(index)
        _zip_write(archive, _INDEX, _json_bytes(index))
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    verified = verify_tool_source_bundle(output_path, expected_sha256=digest)
    return {"path": str(output_path), "sha256": digest,
            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            "source_inputs_sha256": source_inputs.source_inputs_sha256,
            "capability_manifest_sha256": verified["capability_manifest_sha256"],
            "file_count": verified["file_count"], "size_bytes": verified["size_bytes"],
            "may_publish": False, "may_authorize": False, "may_execute": False}


def verify_tool_source_bundle(path: Path, *, expected_sha256: str) -> dict[str, object]:
    """Verify every packaged byte without extraction, imports or approval."""
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or not 0 < path.stat().st_size <= _MAX_TOTAL_BYTES):
        raise SourceCandidateError("source bundle file is missing or unsafe")
    raw = path.read_bytes()
    if len(raw) > _MAX_TOTAL_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SourceCandidateError("source bundle digest does not match")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (not 2 <= len(infos) <= _MAX_FILES + 1 or len(set(names)) != len(names)
                    or len({name.casefold() for name in names}) != len(names) or _INDEX not in names):
                raise ValueError("invalid bundle inventory")
            total = 0
            for info in infos:
                _repository_path(info.filename)
                if (info.is_dir() or info.flag_bits & 1 or not 0 <= info.file_size <= _MAX_FILE_BYTES
                        or (info.external_attr >> 16) not in {0o100644, 0o100755}):
                    raise ValueError("invalid bundle entry")
                total += info.file_size
                if total > _MAX_TOTAL_BYTES:
                    raise ValueError("bundle exceeds total size limit")
            index_raw = archive.read(_INDEX)
            index = json.loads(index_raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
            if (not isinstance(index, dict) or index.get("schema") != _SCHEMA or set(index) != {
                    "schema", "source_inputs_sha256", "capability_manifest_sha256", "mirror_generator_sha256",
                    "file_count", "size_bytes", "entries", "may_publish", "may_authorize", "may_execute",
                    "bundle_manifest_sha256",
            } or any(not _is_sha256(index.get(key)) for key in (
                "source_inputs_sha256", "capability_manifest_sha256", "mirror_generator_sha256", "bundle_manifest_sha256",
            ))):
                raise ValueError("invalid bundle index")
            digest = index.get("bundle_manifest_sha256")
            payload = {key: value for key, value in index.items() if key != "bundle_manifest_sha256"}
            if (digest != canonical_sha256(payload) or index_raw != _json_bytes(index)
                    or any(index.get(key) is not False for key in ("may_publish", "may_authorize", "may_execute"))):
                raise ValueError("invalid bundle index binding")
            observed = []
            for info in infos:
                if info.filename != _INDEX:
                    observed.append(_entry(info.filename, archive.read(info),
                                           executable=(info.external_attr >> 16) == 0o100755))
            observed.sort(key=lambda item: item["path"])
            if (canonical_json_bytes(index.get("entries")) != canonical_json_bytes(observed)
                    or type(index.get("file_count")) is not int or index["file_count"] != len(observed)
                    or type(index.get("size_bytes")) is not int
                    or index["size_bytes"] != sum(item["size_bytes"] for item in observed)):
                raise ValueError("bundle entries differ from index")
            by_name = {item["path"]: item for item in observed}
            if (_REPORT not in by_name or "source/" + _MANIFEST not in by_name
                    or by_name["source/" + _MANIFEST]["sha256"] != index.get("capability_manifest_sha256")
                    or any(name != _REPORT and not name.startswith("source/") for name in by_name)):
                raise ValueError("bundle source layout is invalid")
            report_raw = archive.read(_REPORT)
            report = json.loads(report_raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
            if (not isinstance(report, dict) or report.get("status") != "ISOLATED_BUILD_OBSERVED"
                    or report_raw != _json_bytes(report)
                    or any(report.get(key) is not False for key in ("may_publish", "may_authorize", "may_execute"))):
                raise ValueError("invalid packaged build report")
            artifact = report["build_artifact"]
            inputs = artifact["source_inputs"]
            if (not isinstance(inputs, dict) or set(inputs) != {
                    "schema", "ownership_sha256", "files", "source_inputs_sha256", "may_authorize", "may_execute",
            } or not isinstance(inputs["files"], list)):
                raise ValueError("invalid packaged source inputs")
            observed_inputs = ToolSourceInputsV1(**{
                **inputs, "files": tuple(ToolSourceInputFileV1(**item) for item in inputs["files"]),
            })
            if (not observed_inputs.has_valid_sha256()
                    or observed_inputs.source_inputs_sha256 != index["source_inputs_sha256"]):
                raise ValueError("packaged source revision is invalid")
            for item in observed_inputs.files:
                packaged = by_name.get("source/" + item.path)
                if (packaged is None or packaged["sha256"] != item.content_sha256
                        or packaged["size_bytes"] != item.size_bytes):
                    raise ValueError("packaged source differs from compiler inputs")
            manifest = artifact["gateway_manifest"]
            if (not isinstance(manifest, dict)
                    or manifest.get("source_inputs_sha256") != index["source_inputs_sha256"]
                    or archive.read("source/" + _MANIFEST) != _json_bytes(manifest)):
                raise ValueError("packaged Manifest differs from the compiled artifact")
            compile_action_authority(manifest, generated_at_ms=0)
            return index
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SourceCandidateError("source bundle structure or content is invalid") from exc
