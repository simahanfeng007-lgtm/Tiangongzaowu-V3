"""Tamper-evident personal life/action log.

This is not a compliance product. It is the organism's durable causal memory:
each gateway decision/action is chained to the previous record and authenticated
with a device-local key stored outside the log directory. A signed head detects
record edits, reordering and truncation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Iterator, Mapping


class LifeLogError(RuntimeError):
    pass


_ZERO_HASH = "0" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mac(key: bytes, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


@contextmanager
def _locked(handle: Any) -> Iterator[None]:
    if os.name == "nt":
        import msvcrt
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LifeLog:
    def __init__(self, state_root: Path) -> None:
        self.root = state_root.expanduser().resolve() / "life-log"
        self.keys_root = state_root.expanduser().resolve() / "keys"
        self.events_path = self.root / "events.jsonl"
        self.head_path = self.root / "head.json"
        self.lock_path = self.root / ".append.lock"
        self.key_path = self.keys_root / "life-log.key"
        self.root.mkdir(parents=True, exist_ok=True)
        self.keys_root.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        self._key = self._load_or_create_key()
        self.lock_path.touch(exist_ok=True)
        if not self.head_path.exists():
            self._write_head(0, _ZERO_HASH)
        self.verify()

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            raw = self.key_path.read_bytes()
            if len(raw) != 32:
                raise LifeLogError("life_log_key_invalid")
            return raw
        raw = secrets.token_bytes(32)
        if len(raw) != 32:
            raise LifeLogError("life_log_key_generation_invalid")
        # Windows CRT descriptors default to text mode. Without O_BINARY, a
        # random 0x0A byte is persisted as 0x0D 0x0A, turning a valid 32-byte
        # key into 33 bytes and making the next gateway epoch fail closed.
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(self.key_path, flags, 0o600)
        try:
            pending = memoryview(raw)
            while pending:
                written = os.write(fd, pending)
                if written <= 0:
                    raise LifeLogError("life_log_key_write_failed")
                pending = pending[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if self.key_path.read_bytes() != raw:
            raise LifeLogError("life_log_key_write_failed")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return raw

    def _head_payload(self, sequence: int, record_hash: str) -> dict[str, object]:
        return {
            "schema": "tiangong.life-log.head.v1",
            "sequence": int(sequence),
            "record_hash": record_hash,
            "head_mac": _mac(self._key, str(sequence), record_hash),
        }

    def _write_head(self, sequence: int, record_hash: str) -> None:
        payload = self._head_payload(sequence, record_hash)
        temp = self.head_path.with_suffix(".tmp")
        with temp.open("wb") as handle:
            handle.write(_canonical(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.head_path)

    def _read_head(self) -> tuple[int, str]:
        try:
            data = json.loads(self.head_path.read_text(encoding="utf-8"))
            sequence = int(data["sequence"])
            record_hash = str(data["record_hash"])
            expected = _mac(self._key, str(sequence), record_hash)
            if not hmac.compare_digest(str(data.get("head_mac") or ""), expected):
                raise LifeLogError("life_log_head_authentication_failed")
            return sequence, record_hash
        except LifeLogError:
            raise
        except Exception as exc:
            raise LifeLogError("life_log_head_invalid") from exc

    def append(self, event_type: str, fields: Mapping[str, object] | None = None, *, occurred_at_ms: int | None = None) -> dict[str, object]:
        event = {
            "event_type": str(event_type or "").strip(),
            "occurred_at_ms": int(occurred_at_ms if occurred_at_ms is not None else time.time() * 1000),
            "fields": dict(fields or {}),
        }
        if not event["event_type"] or len(event["event_type"]) > 160:
            raise LifeLogError("life_log_event_type_invalid")
        with self._thread_lock, self.lock_path.open("r+", encoding="utf-8") as lock_handle, _locked(lock_handle):
            sequence, previous_hash = self._read_head()
            record_core = {
                "schema": "tiangong.life-log.record.v1",
                "sequence": sequence + 1,
                "previous_hash": previous_hash,
                "event": event,
            }
            record_hash = _sha(previous_hash.encode("ascii") + b"\0" + _canonical(record_core))
            record = {
                **record_core,
                "record_hash": record_hash,
                "record_mac": _mac(self._key, str(sequence + 1), previous_hash, record_hash),
            }
            with self.events_path.open("ab") as handle:
                handle.write(_canonical(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_head(sequence + 1, record_hash)
            return record

    def verify(self) -> dict[str, object]:
        with self._thread_lock:
            sequence = 0
            previous_hash = _ZERO_HASH
            if self.events_path.exists():
                with self.events_path.open("rb") as handle:
                    for raw in handle:
                        if not raw.strip():
                            continue
                        try:
                            record = json.loads(raw)
                        except Exception as exc:
                            raise LifeLogError("life_log_record_json_invalid") from exc
                        sequence += 1
                        if int(record.get("sequence", -1)) != sequence or str(record.get("previous_hash") or "") != previous_hash:
                            raise LifeLogError("life_log_chain_sequence_invalid")
                        core = {key: record[key] for key in ("schema", "sequence", "previous_hash", "event")}
                        expected_hash = _sha(previous_hash.encode("ascii") + b"\0" + _canonical(core))
                        if not hmac.compare_digest(str(record.get("record_hash") or ""), expected_hash):
                            raise LifeLogError("life_log_record_hash_invalid")
                        expected_mac = _mac(self._key, str(sequence), previous_hash, expected_hash)
                        if not hmac.compare_digest(str(record.get("record_mac") or ""), expected_mac):
                            raise LifeLogError("life_log_record_authentication_failed")
                        previous_hash = expected_hash
            head_sequence, head_hash = self._read_head()
            if head_sequence != sequence or head_hash != previous_hash:
                raise LifeLogError("life_log_truncation_or_head_mismatch")
            return {
                "ok": True,
                "schema": "tiangong.life-log.verify.v1",
                "sequence": sequence,
                "head_hash": previous_hash,
            }


__all__ = ["LifeLog", "LifeLogError"]
