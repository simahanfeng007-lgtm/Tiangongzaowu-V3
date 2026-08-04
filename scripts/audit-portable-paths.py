from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".bat", ".cjs", ".cmd", ".css", ".html", ".js", ".json", ".md",
    ".mjs", ".nsi", ".nsh", ".ps1", ".py", ".toml", ".txt", ".xml", ".yml", ".yaml",
}
SKIP_PARTS = {
    ".git", "__pycache__", "node_modules", "release-artifacts", "release-stage",
}
PLACEHOLDER_PROFILE_NAMES = {"...", "xxx", "someone", "<user>", "{user}", "${user}"}
PRODUCT_SITE_PACKAGES = {"communication_service", "contracts", "life_service", "total_gateway"}
NAMED_WINDOWS_PROFILE = re.compile(
    r"(?i)(?:[a-z]:[\\/]+|file:///[a-z]:/|/[a-z]/)users[\\/]+([^\\/\s\"'`<>|]+)"
)
EXTERNAL_LOCATION_LITERALS = (
    re.compile(r"(?i)[a-z]:[\\/]+program files(?: \(x86\))?[\\/]+"),
    re.compile(r"(?i)[a-z]:[\\/]+windows[\\/]+"),
    re.compile(r"(?i)/(?:usr/share/fonts|system/library/fonts)/"),
)


def release_authored_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for relative in (
        "app",
        "src",
        "readable-python-source",
        "config-templates",
    ):
        base = root / relative
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(root)
            if any(part in SKIP_PARTS for part in rel.parts):
                continue
            # The release packager excludes pip's generated Scripts launchers:
            # their shebangs and launcher payloads bind the provisioning host.
            if rel.parts[:4] == ("app", "runtime", "python312", "Scripts"):
                continue
            # Third-party packages carry upstream documentation and build
            # provenance. Product-owned distribution metadata is checked
            # separately below.
            if rel.parts[:5] == ("app", "runtime", "python312", "Lib", "site-packages"):
                if len(rel.parts) < 6 or rel.parts[5] not in PRODUCT_SITE_PACKAGES:
                    continue
            candidates.append(path)
    for relative in (
        "electron-builder.config.cjs",
        "start-tiangong.bat",
    ):
        path = root / relative
        if path.is_file():
            candidates.append(path)
    return sorted(set(candidates))


def inspect_text(path: Path, root: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in NAMED_WINDOWS_PROFILE.finditer(line):
            profile_name = match.group(1).lower()
            if profile_name not in PLACEHOLDER_PROFILE_NAMES:
                findings.append({
                    "path": path.relative_to(root).as_posix(),
                    "line": line_number,
                    "reason": "named_windows_profile",
                    "value": match.group(0),
                })
        for pattern in EXTERNAL_LOCATION_LITERALS:
            match = pattern.search(line)
            if match:
                findings.append({
                    "path": path.relative_to(root).as_posix(),
                    "line": line_number,
                    "reason": "external_install_literal",
                    "value": match.group(0),
                })
    return findings


def audit(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in release_authored_files(root):
        findings.extend(inspect_text(path, root))
    embedded_site_packages = root / "app" / "runtime" / "python312" / "Lib" / "site-packages"
    if embedded_site_packages.is_dir():
        for path in embedded_site_packages.rglob("direct_url.json"):
            findings.append({
                "path": path.relative_to(root).as_posix(),
                "line": 1,
                "reason": "local_build_provenance",
                "value": "direct_url.json",
            })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject host-specific paths from Tiangong release inputs.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.expanduser().resolve(strict=True)
    findings = audit(root)
    payload = {
        "ok": not findings,
        "schema": "tiangong.release.portable-path-audit.v1",
        "root": str(root),
        "checked_files": len(release_authored_files(root)),
        "findings": findings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
