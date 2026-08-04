"""
safe_io — 天工造物 v3 统一安全 IO 层

所有文件读写必须通过此模块，确保 Windows GBK 环境下不丢字符。
逐步替换裸 write_text() / read_text() / json.dumps + write 调用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_text_utf8(path: Path, text: str) -> None:
    """写文本文件，强制 UTF-8。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text_utf8(path: Path) -> str:
    """读文本文件，UTF-8，非法字节用 replace 容错。"""
    return path.read_text(encoding="utf-8", errors="replace")


def write_json_utf8(path: Path, data: Any, indent: int = 2) -> None:
    """写 JSON 文件，UTF-8。"""
    write_text_utf8(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_json_utf8(path: Path) -> Any:
    """读 JSON 文件，UTF-8。"""
    return json.loads(read_text_utf8(path))
