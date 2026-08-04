#!/usr/bin/env python3
"""Normalize repository text to the Linux/Windows release policy."""
from __future__ import annotations

import argparse
from pathlib import Path

from verify_cross_platform_source import ROOT, expected_newline, is_text_source, source_files


def normalize(root: Path) -> dict[str, int]:
    changed = 0
    checked = 0
    for path in source_files(root.resolve()):
        if not is_text_source(path):
            continue
        checked += 1
        raw = path.read_bytes()
        # Existing source may contain UTF-8 BOM; other encodings fail closed.
        text = raw.decode("utf-8-sig", errors="strict")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        newline = expected_newline(path).decode("ascii")
        if newline == "\r\n":
            text = text.replace("\n", "\r\n")
        encoded = text.encode("utf-8", errors="strict")
        if encoded != raw:
            path.write_bytes(encoded)
            changed += 1
    return {"checked": checked, "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = normalize(args.root)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
