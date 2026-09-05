"""Immutable content-addressed storage for raw channel payloads before parsing."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from runtime_security.path_identity import resolve_existing_path


@dataclass(frozen=True)
class RawInboundObject:
    object_id: str
    sha256: str
    size_bytes: int
    path: Path


class RawInboundStore:
    def __init__(self, root: Path, *, max_object_bytes: int = 16_777_216) -> None:
        if not root.is_absolute() or root == Path(root.anchor) or root.is_symlink():
            raise ValueError("raw inbound root is unsafe")
        if not 1_024 <= max_object_bytes <= 134_217_728:
            raise ValueError("raw inbound object limit is invalid")
        root.mkdir(parents=True, exist_ok=True)
        self.root = resolve_existing_path(root)
        self.max_object_bytes = max_object_bytes

    def put(self, payload: bytes) -> RawInboundObject:
        if not payload or len(payload) > self.max_object_bytes:
            raise ValueError("raw inbound payload size is invalid")
        digest = hashlib.sha256(payload).hexdigest()
        parent = self.root / digest[:2]
        parent.mkdir(exist_ok=True)
        path = parent / digest
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise OSError("raw inbound content address is corrupt")
        else:
            temporary = parent / f".{digest}.{os.getpid()}.tmp"
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                os.chmod(path, 0o600)
            finally:
                temporary.unlink(missing_ok=True)
        return RawInboundObject("raw_" + digest, digest, len(payload), path)


__all__ = ["RawInboundObject", "RawInboundStore"]
