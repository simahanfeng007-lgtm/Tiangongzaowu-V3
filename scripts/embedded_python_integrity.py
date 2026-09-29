"""Check installed embedded-Python files, beyond pip's metadata-only check.

The source runtime deliberately removes pip's direct_url.json provenance after
installation. Bytecode is also regenerable. All other files listed in an
installed distribution's RECORD must still exist and match its recorded size
and digest before the runtime is ready. RECORD itself has no digest.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import hmac
import importlib.metadata as metadata
from pathlib import Path, PureWindowsPath
import re
import tomllib
from typing import NamedTuple


MAX_RECORDED_FILES = 100_000
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_TOTAL_RECORD_BYTES = 16 * 1024 * 1024
MAX_DISTRIBUTIONS = 512
MAX_HASHED_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_HASHED_BYTES = 1024 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
SUPPORTED_RECORD_HASHES = frozenset({"sha256", "sha384", "sha512"})
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.+_-]*)\Z")


class AuditResult(NamedTuple):
    problems: tuple[str, ...]
    repair_specs: tuple[str, ...]
    repair_project: bool


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(requirements: Path) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    visited: set[Path] = set()

    def read_lock(path: Path) -> None:
        path = path.resolve(strict=True)
        if path in visited:
            return
        if len(visited) >= 8:
            raise ValueError("Embedded dependency lock chain exceeds the audit limit")
        if path.parent != requirements.parent.resolve():
            raise ValueError(f"Embedded dependency lock escaped its directory: {path.name}")
        visited.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-r "):
                read_lock(path.parent / line[3:].strip())
                continue
            match = _PIN.fullmatch(line)
            if not match:
                raise ValueError(f"Unsupported embedded dependency lock entry: {line}")
            name, version = match.groups()
            key = _normalized_name(name)
            if key in pins and pins[key][1] != version:
                raise ValueError(f"Conflicting embedded dependency pin: {name}")
            pins[key] = (name, version)

    read_lock(requirements)
    return pins


def _record_is_optional(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    if relative.lower().endswith(".pyc"):
        return True
    return len(parts) >= 2 and parts[-1].lower() == "direct_url.json" and parts[-2].lower().endswith(".dist-info")


def _record_digest(specification: str) -> tuple[str, bytes] | None:
    algorithm, separator, encoded = specification.partition("=")
    if separator != "=" or algorithm not in SUPPORTED_RECORD_HASHES or not encoded:
        return None
    try:
        expected = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(expected) != hashlib.new(algorithm).digest_size:
        return None
    return algorithm, expected


def audit_embedded_install(site_packages: Path, requirements: Path, project: Path | None = None) -> AuditResult:
    """Return missing files and precise packages that need reinstallation.

    RECORD inspection is bounded; an unexpectedly huge or malformed install
    fails closed instead of making startup scan arbitrary paths indefinitely.
    """
    pins = _pins(requirements)
    project_key = ""
    project_version = ""
    if project is not None:
        project_data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        project_key = _normalized_name(project_data["name"])
        project_version = project_data["version"]
    runtime_root = site_packages.resolve().parent.parent
    problems: list[str] = []
    repair: dict[str, str] = {}
    repair_project = False
    installed: dict[str, str] = {}
    file_count = 0
    total_record_bytes = 0
    total_hashed_bytes = 0

    for distribution_count, dist in enumerate(metadata.distributions(path=[str(site_packages)])):
        if distribution_count >= MAX_DISTRIBUTIONS:
            problems.append("Embedded distribution count exceeds the audit limit")
            break
        name = dist.metadata.get("Name")
        version = dist.metadata.get("Version")
        if not name or not _PIN.fullmatch(f"{name}=={version}"):
            problems.append("Installed distribution has missing or invalid identity")
            continue
        key = _normalized_name(name)
        installed[key] = version
        damaged = False
        dist_path = Path(getattr(dist, "_path", ""))
        record_path = dist_path / "RECORD"
        if not dist_path.resolve().is_relative_to(site_packages.resolve()) or not record_path.is_file():
            problems.append(f"{name}: installation RECORD is missing")
            damaged = True
        elif record_path.stat().st_size > MAX_RECORD_BYTES:
            problems.append(f"{name}: installation RECORD exceeds the audit limit")
            damaged = True
        else:
            total_record_bytes += record_path.stat().st_size
            if total_record_bytes > MAX_TOTAL_RECORD_BYTES:
                problems.append("Embedded installation RECORD bytes exceed the audit limit")
                break
            with record_path.open("r", encoding="utf-8", newline="") as stream:
                for row in csv.reader(stream):
                    file_count += 1
                    if file_count > MAX_RECORDED_FILES:
                        problems.append("Embedded package file count exceeds the audit limit")
                        damaged = True
                        break
                    relative = row[0] if row else ""
                    if not relative or PureWindowsPath(relative).anchor:
                        problems.append(f"{name}: invalid installation RECORD path")
                        damaged = True
                        break
                    if _record_is_optional(relative):
                        continue
                    if len(row) != 3:
                        problems.append(f"{name}: malformed installation RECORD entry: {relative}")
                        damaged = True
                        break
                    installed_file = dist.locate_file(relative).resolve()
                    if not installed_file.is_relative_to(runtime_root) or not installed_file.is_file():
                        problems.append(f"{name}: installed file missing or outside runtime: {relative}")
                        damaged = True
                        break
                    # pip cannot hash RECORD itself (the digest would be
                    # recursive); generated bytecode and removed provenance
                    # were already excluded above. Every other wheel file
                    # needs both a recorded size and a digest.
                    if relative.replace("\\", "/").lower() == f"{dist_path.name}/record".lower():
                        continue
                    digest = _record_digest(row[1])
                    if digest is None or len(row[2]) > 20 or not row[2].isascii() or not row[2].isdigit():
                        problems.append(f"{name}: missing or invalid file size/hash: {relative}")
                        damaged = True
                        break
                    expected_size = int(row[2])
                    actual_size = installed_file.stat().st_size
                    if actual_size != expected_size:
                        problems.append(f"{name}: installed file size mismatch: {relative}")
                        damaged = True
                        break
                    if actual_size > MAX_HASHED_FILE_BYTES or total_hashed_bytes + actual_size > MAX_TOTAL_HASHED_BYTES:
                        problems.append(f"{name}: installed file hashing exceeds the audit limit")
                        damaged = True
                        break
                    algorithm, expected_hash = digest
                    hasher = hashlib.new(algorithm)
                    read_size = 0
                    with installed_file.open("rb") as installed_stream:
                        while chunk := installed_stream.read(HASH_CHUNK_BYTES):
                            read_size += len(chunk)
                            if read_size > actual_size:
                                break
                            hasher.update(chunk)
                    total_hashed_bytes += read_size
                    if read_size != actual_size or not hmac.compare_digest(hasher.digest(), expected_hash):
                        problems.append(f"{name}: installed file content hash mismatch: {relative}")
                        damaged = True
                        break
        if damaged:
            if key == project_key:
                repair_project = True
            else:
                pinned = pins.get(key)
                repair[key] = f"{pinned[0]}=={pinned[1]}" if pinned else f"{name}=={version}"
        if file_count > MAX_RECORDED_FILES:
            break

    for key, (name, version) in pins.items():
        if installed.get(key) != version:
            problems.append(f"{name}: required version {version} is not installed")
            repair[key] = f"{name}=={version}"
    if project_key and installed.get(project_key) != project_version:
        problems.append(f"{project_key}: project version {project_version} is not installed")
        repair_project = True
    return AuditResult(tuple(problems), tuple(repair.values()), repair_project)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    result = audit_embedded_install(args.site_packages, args.requirements, args.project)
    if result.problems:
        for problem in result.problems[:5]:
            print(f"Embedded Python incomplete: {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
