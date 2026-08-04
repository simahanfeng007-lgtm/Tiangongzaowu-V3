#!/usr/bin/env python3
"""Release gate for one source tree that must work on Linux and Windows.

The gate is intentionally stricter than either host filesystem: source/config
text is UTF-8 without BOM, line endings are deterministic, paths are NFC and
case-fold unique, and Windows-invalid names are rejected before packaging.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import unicodedata
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git", ".venv", "runtime", "node_modules", ".pytest_cache", "__pycache__",
    "release-artifacts", "release-stage", "repair_validation",
}
EXCLUDED_TOP_LEVEL = {
    ".e2e-logs",
    ".e2e-state",
    ".playwright-cli",
    ".tiangong_emergency_audit",
    "artifacts",
    "output",
}
EXCLUDED_TOP_LEVEL_FILES = {
    ".omni_workspace.lock",
    ".tiangong-release-hash-cache.json",
    "_blackbox_current_path.txt",
}
EXCLUDED_TOP_LEVEL_PREFIXES = (
    "_blackbox_",
    "_e2e_",
    "_identity_probe_",
    "_vrc_e2e_",
)
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".json", ".jsonl", ".md", ".txt", ".toml", ".ini", ".cfg",
    ".conf", ".yaml", ".yml", ".html", ".css", ".scss", ".xml",
    ".ps1", ".bat", ".cmd", ".sh", ".csv", ".tsv", ".svg", ".sql",
    ".properties", ".editorconfig", ".gitattributes", ".gitignore",
}
TEXT_NAMES = {".gitattributes", ".gitignore", ".editorconfig"}
WINDOWS_CRLF_EXTENSIONS = {".ps1", ".bat", ".cmd"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
}
WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*]')
BOMS = (
    b"\xef\xbb\xbf", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff",
    b"\xff\xfe", b"\xfe\xff",
)


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    file_count: int
    text_file_count: int
    json_file_count: int
    max_relative_utf16_units: int
    max_component_utf16_units: int
    failures: tuple[str, ...]

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def is_excluded(path: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return True
    if not path.parts:
        return False
    first = path.parts[0]
    return (
        first in EXCLUDED_TOP_LEVEL
        or (len(path.parts) == 1 and first in EXCLUDED_TOP_LEVEL_FILES)
        or any(first.startswith(prefix) for prefix in EXCLUDED_TOP_LEVEL_PREFIXES)
    )


def source_files(root: Path = ROOT) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and not is_excluded(path.relative_to(root)):
            yield path


def is_text_source(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_EXTENSIONS


def expected_newline(path: Path) -> bytes:
    return b"\r\n" if path.suffix.lower() in WINDOWS_CRLF_EXTENSIONS else b"\n"


def strict_json_loads(text: str, *, source: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"{source}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{source}: non-finite JSON constant {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le", errors="strict")) // 2


def validate_path_component(component: str) -> list[str]:
    failures: list[str] = []
    if unicodedata.normalize("NFC", component) != component:
        failures.append("not NFC")
    try:
        units = _utf16_units(component)
    except UnicodeEncodeError:
        return ["invalid Unicode scalar"]
    if units > 240:
        failures.append(f"component exceeds 240 UTF-16 units ({units})")
    if component.rstrip(" .") != component:
        failures.append("trailing dot or space")
    if WINDOWS_INVALID.search(component):
        failures.append("contains a Windows-invalid character")
    stem = component.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED:
        failures.append("Windows reserved device name")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in component):
        failures.append("control or invalid Unicode category")
    return failures


def validate_relative_path(relative: str, *, max_utf16_units: int = 220) -> list[str]:
    failures: list[str] = []
    if "\\" in relative:
        failures.append("repository paths must use forward slashes")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        failures.append("path is not a canonical relative path")
    if unicodedata.normalize("NFC", relative) != relative:
        failures.append("path is not NFC")
    try:
        units = _utf16_units(relative)
    except UnicodeEncodeError:
        failures.append("path has invalid Unicode scalar")
        units = 0
    if units > max_utf16_units:
        failures.append(f"path exceeds {max_utf16_units} UTF-16 units ({units})")
    for component in pure.parts:
        if component.startswith(".") and component in {".gitignore", ".gitattributes", ".editorconfig", ".github"}:
            continue
        for item in validate_path_component(component):
            failures.append(f"component {component!r}: {item}")
    return failures


def validate_zip_member_name(name: str) -> list[str]:
    failures = validate_relative_path(name, max_utf16_units=220)
    pure = PurePosixPath(name)
    if any(part == ".." for part in pure.parts):
        failures.append("zip traversal")
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        failures.append("zip absolute/rooted member")
    return failures


def _validate_line_endings(path: Path, raw: bytes) -> list[str]:
    expected = expected_newline(path)
    failures: list[str] = []
    lone_cr = raw.replace(b"\r\n", b"").find(b"\r") >= 0
    lone_lf = raw.replace(b"\r\n", b"").find(b"\n") >= 0
    if lone_cr:
        failures.append("contains lone CR")
    if expected == b"\n" and b"\r\n" in raw:
        failures.append("must use LF")
    if expected == b"\r\n" and lone_lf:
        failures.append("must use CRLF")
    return failures


def verify_tree(root: Path = ROOT) -> VerificationResult:
    root = root.resolve()
    failures: list[str] = []
    paths = list(source_files(root))
    relative_names = [path.relative_to(root).as_posix() for path in paths]

    casefold: dict[str, str] = {}
    normalized: dict[str, str] = {}
    max_path = 0
    max_component = 0
    text_count = 0
    json_count = 0

    for relative, path in zip(relative_names, paths):
        path_failures = validate_relative_path(relative)
        failures.extend(f"{relative}: {item}" for item in path_failures)
        try:
            max_path = max(max_path, _utf16_units(relative))
            for component in PurePosixPath(relative).parts:
                max_component = max(max_component, _utf16_units(component))
        except UnicodeEncodeError:
            pass
        folded = relative.casefold()
        prior = casefold.setdefault(folded, relative)
        if prior != relative:
            failures.append(f"case-insensitive collision: {prior} <> {relative}")
        nfc = unicodedata.normalize("NFC", relative)
        prior_nfc = normalized.setdefault(nfc, relative)
        if prior_nfc != relative:
            failures.append(f"Unicode normalization collision: {prior_nfc} <> {relative}")

        if not is_text_source(path):
            continue
        text_count += 1
        raw = path.read_bytes()
        if raw.startswith(BOMS):
            failures.append(f"{relative}: BOM is forbidden")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            failures.append(f"{relative}: not strict UTF-8: {exc}")
            continue
        failures.extend(f"{relative}: {item}" for item in _validate_line_endings(path, raw))
        if path.suffix.lower() in {".json", ".jsonl"}:
            if path.suffix.lower() == ".json":
                json_count += 1
                try:
                    strict_json_loads(text, source=relative)
                except Exception as exc:
                    failures.append(str(exc))
            else:
                for number, line in enumerate(text.splitlines(), 1):
                    if not line.strip():
                        continue
                    json_count += 1
                    try:
                        strict_json_loads(line, source=f"{relative}:{number}")
                    except Exception as exc:
                        failures.append(str(exc))

    attributes = (root / ".gitattributes").read_text(encoding="utf-8", errors="strict") if (root / ".gitattributes").is_file() else ""
    required_attributes = ("* text=auto eol=lf", "*.ps1 text eol=crlf", "*.bat text eol=crlf", "*.cmd text eol=crlf")
    for line in required_attributes:
        if line not in attributes:
            failures.append(f".gitattributes: missing {line!r}")
    if "* -text" in attributes:
        failures.append(".gitattributes: '* -text' disables cross-platform normalization")

    return VerificationResult(
        ok=not failures,
        file_count=len(paths),
        text_file_count=text_count,
        json_file_count=json_count,
        max_relative_utf16_units=max_path,
        max_component_utf16_units=max_component,
        failures=tuple(failures),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = verify_tree(args.root)
    print(result.as_json() if args.json else (
        f"cross-platform source: {'PASS' if result.ok else 'FAIL'}; "
        f"files={result.file_count}; text={result.text_file_count}; json={result.json_file_count}; "
        f"max_path_utf16={result.max_relative_utf16_units}"
    ))
    if result.failures and not args.json:
        for failure in result.failures:
            print(f"- {failure}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
