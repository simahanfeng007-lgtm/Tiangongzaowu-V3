"""Replace one exact string constant inside a CPython .pyc code tree.

This utility preserves the 16-byte pyc header and recursively rewrites only
matching string constants. It is intended for release sanitization of frozen
configuration defaults, not for general bytecode editing.
"""

from __future__ import annotations

import argparse
import marshal
from pathlib import Path
from types import CodeType


def replace_string(code: CodeType, old: str, new: str) -> tuple[CodeType, int]:
    constants: list[object] = []
    count = 0
    for item in code.co_consts:
        if isinstance(item, CodeType):
            item, nested_count = replace_string(item, old, new)
            count += nested_count
        elif isinstance(item, str) and item == old:
            item = new
            count += 1
        constants.append(item)
    return code.replace(co_consts=tuple(constants)), count


def patch_file(path: Path, old: str, new: str) -> int:
    payload = path.read_bytes()
    if len(payload) < 16:
        raise ValueError(f"Invalid pyc file: {path}")
    root = marshal.loads(payload[16:])
    if not isinstance(root, CodeType):
        raise TypeError(f"Pyc payload is not a code object: {path}")
    updated, count = replace_string(root, old, new)
    if count:
        path.write_bytes(payload[:16] + marshal.dumps(updated))
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", default="")
    args = parser.parse_args()
    count = patch_file(args.path, args.old, args.new)
    if count == 0:
        raise SystemExit("No matching string constant found")
    print(f"patched={count} file={args.path}")


if __name__ == "__main__":
    main()
