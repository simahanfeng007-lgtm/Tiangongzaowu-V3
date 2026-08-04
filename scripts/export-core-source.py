#!/usr/bin/env python3
"""Export the authoritative Tiangong core source without third-party dependencies.

The export is intentionally allowlisted.  Runtime interpreters, node_modules,
compiled bytecode, release artifacts, user data, caches, and black-box evidence
never enter the archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "tiangong.core-source-archive.v1"

ROOT_FILES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "electron-builder.config.cjs",
    "GATEWAY_REFACTOR_PLAN.md",
    "LIFE_CAUSAL_ARCHITECTURE_REFACTOR_PLAN.md",
    "LIFE_REFACTOR_CHECKPOINT.md",
    "manifest.json",
    "MISSING_ORIGINAL_SOURCE.md",
    "PRIVACY_SANITIZATION.md",
    "pyproject.toml",
    "pytest.ini",
    "README.md",
    "RELEASE.md",
    "requirements-release.lock",
    "requirements-source.lock",
    "SINGLE_PROCESS_MERGE_STATUS_20260721.md",
    "source-ownership.json",
    "start-tiangong.bat",
}

DIRECTORY_ROOTS = (
    ".github",
    "baselines",
    "config-templates",
    "docs",
    "installer",
    "maintenance-tools",
    "readable-python-source",
    "scripts",
    "src",
    "tests",
    "app/assets",
    "app/frontend-v2",
    "app/lib",
    "app/scripts",
    "app/communication-service",
    "app/backend/tiangong-backend/v3",
    "app/backend/tiangong-backend/tiangong_kernel",
)

REDISTRIBUTABLE_ANIMATION_FILES = frozenset(
    {
        "app/assets/animations/vrma/Angry.vrma",
        "app/assets/animations/vrma/Clapping.vrma",
        "app/assets/animations/vrma/LookAround.vrma",
        "app/assets/animations/vrma/Relax.vrma",
        "app/assets/animations/vrma/Sad.vrma",
        "app/assets/animations/vrma/Surprised.vrma",
        "app/assets/animations/vrma/Thinking.vrma",
    }
)

REDISTRIBUTABLE_ANIMATION_NOTICES = frozenset(
    {
        "app/assets/animations/vrma/ATTRIBUTION.txt",
        "app/assets/animations/vrma/LICENSE-tk256ailab-vrm-viewer.txt",
    }
)

APP_ROOT_FILES = {
    "avatar-asset-host.cjs",
    "avatar-storage-host.cjs",
    "build-info.json",
    "LICENSE.txt",
    "main.js",
    "package-lock.json",
    "package.json",
    "preload.js",
    "qa-web-preload.js",
    "README_提取说明.md",
    "release-manifest.json",
    "runtime-root.js",
    "secure-updater.js",
    "service-supervisor.js",
    "update-trust.json",
    "vrc-import.js",
    "zhuomian.html",
    "对话窗口.html",
    "捏脸.html",
    "桌面宠物.html",
}

EXTRA_FILES = {
    "app/assets/tiangong-logo-icon.png",
    "app/assets/tiangong-logo.ico",
    "app/assets/tiangong-logo.icns",
    "app/assets/avatar/builtin-models.json",
    *REDISTRIBUTABLE_ANIMATION_FILES,
    *REDISTRIBUTABLE_ANIMATION_NOTICES,
    "app/life-service/life_server.py",
    "app/backend/tiangong-backend/_internal/backend_life_context_authority.py",
    "app/backend/tiangong-backend/_internal/confirmation_bridge.py",
    "app/backend/tiangong-backend/_internal/release.json",
    "build/entitlements.mac.plist",
    "build/installer.nsh",
    "build/version-backend.txt",
    "build/version-communication-service.txt",
    "build/version-life-service.txt",
    "build/version-total-gateway.txt",
}

EXTRA_GLOBS = (
    "app/backend/tiangong-backend/_internal/frozen_modules/**/*.py",
    "app/backend/tiangong-backend/_internal/legacy_pyz_modules/**/*.py",
)

FORBIDDEN_PARTS = {
    ".e2e-logs",
    ".e2e-state",
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".tiangong_emergency_audit",
    "__pycache__",
    "node_modules",
    "output",
    "release-artifacts",
    "release-repair",
    "release-stage",
    "site-packages",
}

FORBIDDEN_PREFIXES = {
    ("app", "runtime"),
}

FORBIDDEN_SUFFIXES = {
    ".7z",
    ".a",
    ".class",
    ".dll",
    ".dmg",
    ".dylib",
    ".exe",
    ".gz",
    ".glb",
    ".gltf",
    ".jar",
    ".node",
    ".o",
    ".pyd",
    ".pyc",
    ".pyo",
    ".rar",
    ".so",
    ".tar",
    ".vrm",
    ".vrma",
    ".whl",
    ".zip",
}

SENSITIVE_FILENAMES = {
    ".env",
    "api_key",
    "api_keys.json",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> Path:
    return path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))


def forbidden_source_path(path: Path | PurePosixPath) -> bool:
    parts = path.parts
    return any(part in FORBIDDEN_PARTS for part in parts) or any(
        parts[: len(prefix)] == prefix for prefix in FORBIDDEN_PREFIXES
    )


def safe_source_file(path: Path) -> bool:
    rel = relative(path)
    if path.is_symlink():
        raise RuntimeError(f"symbolic link is not allowed in source export: {rel.as_posix()}")
    if forbidden_source_path(rel):
        return False
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES and rel.as_posix() not in REDISTRIBUTABLE_ANIMATION_FILES:
        return False
    lowered_name = path.name.lower()
    if lowered_name in SENSITIVE_FILENAMES or lowered_name.endswith((".pem", ".key")):
        raise RuntimeError(f"sensitive file name is not allowed in source export: {rel.as_posix()}")
    return True


def collect_files() -> dict[str, Path]:
    selected: dict[str, Path] = {}

    def add(path: Path) -> None:
        if not path.is_file() or not safe_source_file(path):
            return
        rel = relative(path).as_posix()
        selected[rel] = path

    for name in sorted(ROOT_FILES):
        add(ROOT / name)
    for name in sorted(APP_ROOT_FILES):
        add(ROOT / "app" / name)
    for name in sorted(EXTRA_FILES):
        add(ROOT / name)
    for root_name in DIRECTORY_ROOTS:
        directory = ROOT / root_name
        if not directory.is_dir():
            raise RuntimeError(f"required source directory is missing: {root_name}")
        for path in sorted(directory.rglob("*")):
            add(path)
    for pattern in EXTRA_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            add(path)

    missing = [
        name
        for name in sorted(ROOT_FILES | EXTRA_FILES)
        if not (ROOT / name).is_file()
    ]
    missing.extend(
        f"app/{name}"
        for name in sorted(APP_ROOT_FILES)
        if not (ROOT / "app" / name).is_file()
    )
    if missing:
        raise RuntimeError("required source files are missing: " + ", ".join(missing))
    return dict(sorted(selected.items()))


def verify_source_boundary(files: dict[str, Path]) -> dict[str, object]:
    ownership = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
    missing_authorities: list[str] = []
    for mapping in ownership["mappings"]:
        source = str(mapping["source"])
        if not (ROOT / source).exists():
            missing_authorities.append(source)
    if missing_authorities:
        raise RuntimeError(
            "source ownership authorities are missing: " + ", ".join(missing_authorities)
        )

    compiled = [
        name
        for name in files
        if (
            PurePosixPath(name).suffix.lower() in FORBIDDEN_SUFFIXES
            and name not in REDISTRIBUTABLE_ANIMATION_FILES
        )
    ]
    if compiled:
        raise RuntimeError("compiled/archive payload leaked into export: " + ", ".join(compiled))

    forbidden_paths = [
        name
        for name in files
        if forbidden_source_path(PurePosixPath(name))
    ]
    if forbidden_paths:
        raise RuntimeError("dependency/runtime path leaked into export: " + ", ".join(forbidden_paths))

    frozen_root = (
        ROOT
        / "app"
        / "backend"
        / "tiangong-backend"
        / "_internal"
        / "frozen_modules"
    )
    bytecode = sorted(frozen_root.rglob("*.pyc"))
    source_backed: list[dict[str, str]] = []
    for path in bytecode:
        rel = path.relative_to(frozen_root).as_posix()
        if "/__pycache__/" in f"/{rel}":
            module_name = path.name.split(".cpython-", 1)[0] + ".py"
            candidate = path.parent.parent / module_name
        elif rel.startswith("omni_body_skill/"):
            module_rel = rel.removeprefix("omni_body_skill/")[:-1]
            candidate = ROOT / "readable-python-source" / "omni_body_skill" / module_rel
        else:
            candidate = path.with_suffix(".py")
        if not candidate.is_file():
            raise RuntimeError(f"frozen bytecode has no readable source: {rel}")
        source_backed.append(
            {
                "bytecode": rel,
                "source": candidate.relative_to(ROOT).as_posix(),
            }
        )

    return {
        "source_ownership_mappings": len(ownership["mappings"]),
        "compiled_only_core_files": 0,
        "frozen_bytecode_files_checked": len(bytecode),
        "frozen_bytecode_source_map": source_backed,
        "dependencies_included": False,
    }


def write_zip(
    files: dict[str, Path],
    output: Path,
    archive_root: str,
    qc: dict[str, object],
) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix="tiangong-core-source-") as temporary_name:
        staging = Path(temporary_name) / archive_root
        staging.mkdir(parents=True)
        for rel, source in files.items():
            destination = staging / Path(rel)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        qc_text = (
            "# 天工造物核心源码质检说明\n\n"
            "- 结论：当前核心功能均有可读源码权威，不存在只能依靠冻结二进制恢复的核心模块。\n"
            "- 冻结边界：发布目录中的字节码均已核对到对应的可读 `.py`；本存档不包含字节码。\n"
            "- 依赖边界：不包含 `node_modules`、Python 运行时、`site-packages`、解释器、缓存、"
            "构建产物、运行数据、VRM 模型或历史黑盒样本；仅包含清单中明确列名且附带许可与"
            "归属文件的 7 个可再分发 VRMA 动画。\n"
            "- 重建边界：依赖版本声明和发布脚本保留；正式原生程序须在目标平台重新安装依赖并构建。\n"
            "- 溯源边界：这是完整的重建源码基线，不宣称包含未提供的历史 Git 提交或原作者签名二进制。\n"
        )
        qc_path = staging / "CORE_SOURCE_QC.md"
        qc_path.write_text(qc_text, encoding="utf-8", newline="\n")

        entries: list[dict[str, object]] = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                rel = path.relative_to(staging).as_posix()
                entries.append(
                    {
                        "path": rel,
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        manifest = {
            "schema": SCHEMA,
            "product": "天工造物 v3.0.3",
            "archive_kind": "authoritative-core-source-without-dependencies",
            "production_claim": False,
            "source_file_count": len(entries),
            "source_bytes": sum(int(item["bytes"]) for item in entries),
            "quality_control": qc,
            "files": entries,
        }
        manifest_path = staging / "CORE_SOURCE_MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise RuntimeError(f"refusing to overwrite existing archive: {output}")
        with zipfile.ZipFile(
            output,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for path in sorted(staging.rglob("*")):
                if not path.is_file():
                    continue
                member = f"{archive_root}/{path.relative_to(staging).as_posix()}"
                info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)

    expected = {
        f"{archive_root}/{item['path']}": (int(item["bytes"]), str(item["sha256"]))
        for item in entries
    }
    manifest_member = f"{archive_root}/CORE_SOURCE_MANIFEST.json"
    with zipfile.ZipFile(output, "r") as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in members}
        if names != set(expected) | {manifest_member}:
            raise RuntimeError("ZIP member set differs from the staged source manifest")
        for item in members:
            if item.filename == manifest_member:
                continue
            payload = archive.read(item)
            size, digest = expected[item.filename]
            if len(payload) != size or sha256_bytes(payload) != digest:
                raise RuntimeError(f"ZIP member verification failed: {item.filename}")
        loaded_manifest = json.loads(archive.read(manifest_member).decode("utf-8"))
        if loaded_manifest["schema"] != SCHEMA:
            raise RuntimeError("ZIP source manifest schema mismatch")
    return len(expected) + 1, output.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve(strict=True)
    if not output_dir.is_dir():
        raise RuntimeError(f"output directory is not a directory: {output_dir}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_root = args.name or f"天工造物v3.0.3-核心源码-无依赖-{timestamp}"
    if any(char in archive_root for char in '<>:"/\\|?*'):
        raise RuntimeError("archive name contains characters invalid on Windows")
    output = output_dir / f"{archive_root}.zip"

    files = collect_files()
    qc = verify_source_boundary(files)
    member_count, archive_bytes = write_zip(files, output, archive_root, qc)
    print(
        json.dumps(
            {
                "ok": True,
                "archive": str(output),
                "archive_bytes": archive_bytes,
                "archive_sha256": sha256_file(output),
                "zip_file_count": member_count,
                "source_file_count": member_count - 1,
                "compiled_only_core_files": qc["compiled_only_core_files"],
                "frozen_bytecode_files_checked": qc["frozen_bytecode_files_checked"],
                "dependencies_included": qc["dependencies_included"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
