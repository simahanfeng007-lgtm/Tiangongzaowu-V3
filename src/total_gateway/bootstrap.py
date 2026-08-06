"""Strict configuration, single-instance epoch, and disk health primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts import canonical_json_bytes, canonical_sha256

from . import DEFAULT_PORT
from .gateway_url import DEFAULT_GATEWAY_URL, gateway_url_from_environment, normalize_gateway_url


_ENV_PREFIX = "TIANGONG_GATEWAY_"
_ALLOWED_ENV = {
    "TIANGONG_GATEWAY_DISK_PROBE_BYTES",
    "TIANGONG_GATEWAY_DISK_PROBE_INTERVAL_MS",
    "TIANGONG_GATEWAY_ENVIRONMENT",
    "TIANGONG_GATEWAY_DEPLOYMENT_MODE",
    "TIANGONG_GATEWAY_MAX_EVIDENCE_AGE_MS",
    "TIANGONG_GATEWAY_LIFE_INTENT_TOKEN",
    "TIANGONG_GATEWAY_MIN_FREE_BYTES",
    "TIANGONG_GATEWAY_PORT",
    "TIANGONG_GATEWAY_COMMUNICATION_TOKEN",
    "TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH",
    "TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES",
    "TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT",
    "TIANGONG_GATEWAY_SKILL_ROOT",
    "TIANGONG_GATEWAY_SHADOW_TOKEN",
    "TIANGONG_GATEWAY_STATE_ROOT",
    "TIANGONG_GATEWAY_URL",
    "TIANGONG_GATEWAY_WORKSPACE_ROOT",
}
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")


class GatewayConfigurationError(ValueError):
    pass


class SingleInstanceError(RuntimeError):
    pass


class EpochStateError(RuntimeError):
    pass


def _strict_uint(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    if re.fullmatch(r"0|[1-9][0-9]*", raw) is None:
        raise GatewayConfigurationError(f"{name} must be an unsigned decimal integer")
    return int(raw)


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    environment: Literal["development", "production", "test"] = "production"
    deployment_mode: Literal["embedded", "standalone_services"] = "standalone_services"
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=DEFAULT_PORT, ge=0, le=65_535)
    gateway_url: str = DEFAULT_GATEWAY_URL
    state_root: Path
    min_free_bytes: int = Field(default=67_108_864, ge=1_048_576, le=1_099_511_627_776)
    disk_probe_bytes: int = Field(default=4_096, ge=32, le=1_048_576)
    disk_probe_interval_ms: int = Field(default=20_000, ge=100, le=60_000)
    max_evidence_age_ms: int = Field(default=5_000, ge=1, le=60_000)
    shadow_api_token: str = Field(default="", max_length=512, repr=False)
    communication_api_token: str = Field(default="", max_length=512, repr=False)
    life_action_intent_token: str = Field(default="", max_length=512, repr=False)
    backend_internal_token: str = Field(default="", max_length=512, repr=False)
    life_internal_token: str = Field(default="", max_length=512, repr=False)
    release_manifest_path: Path | None = None
    release_manifest_candidates: tuple[Path, ...] = ()
    release_source_root: Path | None = None
    skill_root: Path | None = None
    workspace_root: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_callback_url_from_listener(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "gateway_url" not in data:
            port = data.get("port", DEFAULT_PORT)
            if type(port) is int and 1 <= port <= 65_535:
                data["gateway_url"] = f"http://127.0.0.1:{port}"
        return data

    @field_validator("state_root")
    @classmethod
    def validate_state_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("gateway state root must be absolute")
        if value.exists() and value.is_symlink():
            raise ValueError("gateway state root cannot be a symbolic link")
        resolved = value.resolve(strict=False)
        if resolved == Path(resolved.anchor) or len(str(resolved)) > 240:
            raise ValueError("gateway state root is unsafe")
        return resolved

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway_url(cls, value: str) -> str:
        return normalize_gateway_url(value)

    @field_validator("release_manifest_path", "release_source_root", "skill_root", "workspace_root")
    @classmethod
    def validate_optional_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("gateway runtime paths must be absolute")
        if value.exists() and value.is_symlink():
            raise ValueError("gateway runtime paths cannot be symbolic links")
        resolved = value.resolve(strict=False)
        if resolved == Path(resolved.anchor) or len(str(resolved)) > 240:
            raise ValueError("gateway runtime path is unsafe")
        return resolved

    @field_validator("release_manifest_candidates")
    @classmethod
    def validate_release_manifest_candidates(
        cls,
        values: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        if len(values) > 16:
            raise ValueError("too many gateway release manifest candidates")
        resolved: list[Path] = []
        for value in values:
            if not value.is_absolute() or (value.exists() and value.is_symlink()):
                raise ValueError("gateway release manifest candidate is unsafe")
            item = value.resolve(strict=False)
            if item == Path(item.anchor) or len(str(item)) > 240:
                raise ValueError("gateway release manifest candidate is unsafe")
            if item not in resolved:
                resolved.append(item)
        return tuple(resolved)

    @model_validator(mode="after")
    def validate_environment_port(self) -> Self:
        if self.port != 0:
            try:
                normalize_gateway_url(self.gateway_url, expected_port=self.port)
            except ValueError as exc:
                raise ValueError(
                    "gateway callback URL must match the configured listener port"
                ) from exc
        if self.environment == "production" and self.port != DEFAULT_PORT:
            raise ValueError("production total gateway must listen on port 7184")
        if self.environment == "production" and self.deployment_mode != "embedded":
            raise ValueError("production total gateway must use embedded single-process mode")
        if self.environment != "test" and self.port == 0:
            raise ValueError("ephemeral ports are allowed only in test mode")
        if self.shadow_api_token and len(self.shadow_api_token) < 32:
            raise ValueError("shadow API token length is invalid")
        if self.communication_api_token and len(self.communication_api_token) < 32:
            raise ValueError("communication API token length is invalid")
        if self.life_action_intent_token and len(self.life_action_intent_token) < 32:
            raise ValueError("life action-intent token length is invalid")
        if self.backend_internal_token and len(self.backend_internal_token) < 32:
            raise ValueError("backend internal token length is invalid")
        if self.life_internal_token and len(self.life_internal_token) < 32:
            raise ValueError("life internal token length is invalid")
        return self

    @property
    def execution_assembly_configured(self) -> bool:
        manifest_available = bool(self.release_manifest_candidates) or self.release_manifest_path is not None or (
            self.environment != "production" and self.release_source_root is not None
        )
        common = bool(
            manifest_available
            and self.workspace_root is not None
            and self.backend_internal_token
        )
        if self.deployment_mode == "embedded":
            return common
        return bool(common and self.communication_api_token and self.life_internal_token)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self:
        source = dict(os.environ if environ is None else environ)
        unknown = sorted(name for name in source if name.startswith(_ENV_PREFIX) and name not in _ALLOWED_ENV)
        if unknown:
            raise GatewayConfigurationError(
                "unknown total-gateway environment variables: " + ",".join(unknown)
            )
        appdata = source.get("APPDATA") or str(Path.home() / ".tiangong-v3")
        root_text = source.get(
            "TIANGONG_GATEWAY_STATE_ROOT",
            str(Path(appdata) / "tiangong-v3-qiyuan" / "runtime" / "gateway"),
        )
        port = _strict_uint(source, "TIANGONG_GATEWAY_PORT", DEFAULT_PORT)
        try:
            return cls(
                environment=source.get("TIANGONG_GATEWAY_ENVIRONMENT", "production"),
                deployment_mode=source.get("TIANGONG_GATEWAY_DEPLOYMENT_MODE", "embedded"),
                port=port,
                gateway_url=gateway_url_from_environment(source),
                state_root=Path(root_text),
                min_free_bytes=_strict_uint(
                    source,
                    "TIANGONG_GATEWAY_MIN_FREE_BYTES",
                    67_108_864,
                ),
                disk_probe_bytes=_strict_uint(
                    source,
                    "TIANGONG_GATEWAY_DISK_PROBE_BYTES",
                    4_096,
                ),
                disk_probe_interval_ms=_strict_uint(
                    source,
                    "TIANGONG_GATEWAY_DISK_PROBE_INTERVAL_MS",
                    20_000,
                ),
                max_evidence_age_ms=_strict_uint(
                    source,
                    "TIANGONG_GATEWAY_MAX_EVIDENCE_AGE_MS",
                    5_000,
                ),
                shadow_api_token=source.get("TIANGONG_GATEWAY_SHADOW_TOKEN", ""),
                communication_api_token=source.get(
                    "TIANGONG_GATEWAY_COMMUNICATION_TOKEN",
                    "",
                ),
                life_action_intent_token=source.get(
                    "TIANGONG_GATEWAY_LIFE_INTENT_TOKEN", ""
                ),
                backend_internal_token=source.get("TIANGONG_BACKEND_INTERNAL_TOKEN", ""),
                life_internal_token=source.get("TIANGONG_LIFE_INTERNAL_TOKEN", ""),
                release_manifest_path=(
                    None
                    if not source.get("TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH", "").strip()
                    else Path(source["TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH"])
                ),
                release_manifest_candidates=tuple(
                    Path(item.strip())
                    for item in source.get(
                        "TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES", ""
                    ).split(os.pathsep)
                    if item.strip()
                ),
                release_source_root=(
                    None
                    if not source.get("TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT", "").strip()
                    else Path(source["TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT"])
                ),
                skill_root=(
                    None
                    if not source.get("TIANGONG_GATEWAY_SKILL_ROOT", "").strip()
                    else Path(source["TIANGONG_GATEWAY_SKILL_ROOT"])
                ),
                workspace_root=(
                    None
                    if not source.get("TIANGONG_GATEWAY_WORKSPACE_ROOT", "").strip()
                    else Path(source["TIANGONG_GATEWAY_WORKSPACE_ROOT"])
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, GatewayConfigurationError):
                raise
            raise GatewayConfigurationError("invalid total-gateway configuration") from exc


class _EpochRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_type: Literal["tiangong.gateway.epoch.v1"] = "tiangong.gateway.epoch.v1"
    gateway_epoch: int = Field(ge=1)
    instance_id: str = Field(min_length=1, max_length=160)
    previous_instance_id: str | None = Field(default=None, min_length=1, max_length=160)
    updated_at_ms: int = Field(ge=0)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("instance_id", "previous_instance_id")
    @classmethod
    def validate_instance_id(cls, value: str | None) -> str | None:
        if value is not None and _INSTANCE_ID.fullmatch(value) is None:
            raise ValueError("epoch instance ID is invalid")
        return value

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"record_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.record_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"record_sha256": self.computed_sha256()})


def _read_epoch(path: Path) -> _EpochRecord:
    if path.is_symlink() or not path.is_file():
        raise EpochStateError("gateway epoch file is not a regular file")
    raw = path.read_bytes()
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=lambda pairs: _reject_duplicate_pairs(pairs),
        )
        record = _EpochRecord.model_validate(decoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EpochStateError("gateway epoch state is corrupt") from exc
    if raw != canonical_json_bytes(record.model_dump(mode="json")) + b"\n":
        raise EpochStateError("gateway epoch state is not canonical JSON")
    if not record.has_valid_sha256():
        raise EpochStateError("gateway epoch state digest is invalid")
    return record


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _atomic_write_epoch(path: Path, record: _EpochRecord) -> None:
    stage = path.parent / f".gateway-epoch-{os.getpid()}-{secrets.token_hex(8)}.tmp"
    payload = canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
    try:
        with stage.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        if stage.exists():
            stage.unlink()


def _lock_file(stream: object) -> None:
    descriptor = stream.fileno()  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt

        stream.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(stream: object) -> None:
    descriptor = stream.fileno()  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt

        stream.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


class InstanceEpochLease:
    def __init__(self, state_root: Path, instance_id: str, stream: object, epoch: int) -> None:
        self.state_root = state_root
        self.instance_id = instance_id
        self.gateway_epoch = epoch
        self._stream = stream
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    @classmethod
    def acquire(cls, state_root: Path, instance_id: str, *, now_ms: int) -> Self:
        if _INSTANCE_ID.fullmatch(instance_id) is None or now_ms < 0:
            raise ValueError("single-instance lease identity or time is invalid")
        existed = state_root.exists()
        existing_entries = tuple(state_root.iterdir()) if existed and state_root.is_dir() else ()
        if existed and (state_root.is_symlink() or not state_root.is_dir()):
            raise EpochStateError("gateway state root is not a real directory")
        state_root.mkdir(parents=True, exist_ok=True)
        lock_path = state_root / "gateway.instance.lock"
        if lock_path.exists() and lock_path.is_symlink():
            raise EpochStateError("gateway instance lock is a symbolic link")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.fstat(descriptor).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            try:
                _lock_file(stream)
            except OSError as exc:
                raise SingleInstanceError("another total gateway instance owns the state root") from exc
            epoch_path = state_root / "gateway.epoch.json"
            if epoch_path.exists():
                previous = _read_epoch(epoch_path)
                epoch = previous.gateway_epoch + 1
                previous_instance_id = previous.instance_id
            else:
                if existed and existing_entries:
                    raise EpochStateError("gateway epoch is missing from an initialized state root")
                epoch = 1
                previous_instance_id = None
            record = _EpochRecord(
                gateway_epoch=epoch,
                instance_id=instance_id,
                previous_instance_id=previous_instance_id,
                updated_at_ms=now_ms,
                record_sha256="0" * 64,
            ).with_computed_sha256()
            _atomic_write_epoch(epoch_path, record)
            return cls(state_root, instance_id, stream, epoch)
        except Exception:
            try:
                _unlock_file(stream)
            except OSError:
                pass
            stream.close()
            raise

    def release(self) -> None:
        if not self._active:
            return
        try:
            _unlock_file(self._stream)
        finally:
            self._stream.close()
            self._active = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(frozen=True)
class DiskHealthEvidence:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    free_bytes: int
    probe_sha256: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "reason_code": self.reason_code,
            "checked_at_ms": self.checked_at_ms,
            "free_bytes": self.free_bytes,
            "probe_sha256": self.probe_sha256,
        }


def probe_disk_health(
    state_root: Path,
    *,
    min_free_bytes: int,
    probe_bytes: int,
    now_ms: int,
) -> DiskHealthEvidence:
    if now_ms < 0 or min_free_bytes < 1 or not 32 <= probe_bytes <= 1_048_576:
        raise ValueError("disk-health policy is invalid")
    if state_root.is_symlink() or not state_root.is_dir():
        return DiskHealthEvidence(False, "disk.state_root.invalid", now_ms, 0, None)
    try:
        free_bytes = shutil.disk_usage(state_root).free
    except OSError:
        return DiskHealthEvidence(False, "disk.usage.unavailable", now_ms, 0, None)
    if free_bytes < min_free_bytes:
        return DiskHealthEvidence(False, "disk.free_space.insufficient", now_ms, free_bytes, None)

    probe = state_root / f".disk-probe-{os.getpid()}-{secrets.token_hex(8)}"
    payload = b"tiangong.gateway.disk-probe.v1\0" + secrets.token_bytes(probe_bytes)
    probe_sha256 = hashlib.sha256(payload).hexdigest()
    healthy = False
    reason = "disk.probe.failed"
    descriptor: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(probe, flags, 0o600)
        with os.fdopen(descriptor, "w+b", buffering=0) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != payload:
                reason = "disk.probe.readback_mismatch"
            else:
                healthy = True
                reason = "disk.ok"
    except OSError:
        reason = "disk.probe.io_error"
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            healthy = False
            reason = "disk.probe.cleanup_failed"
    return DiskHealthEvidence(healthy, reason, now_ms, free_bytes, probe_sha256)


class DiskHealthMonitor:
    def __init__(self, config: GatewayConfig) -> None:
        import threading

        self._config = config
        self._lock = threading.Lock()
        self._last_checked_ns = 0
        self._last: DiskHealthEvidence | None = None

    def check(self, *, now_ms: int, force: bool = False) -> DiskHealthEvidence:
        with self._lock:
            current_ns = time.monotonic_ns()
            elapsed_ms = (current_ns - self._last_checked_ns) // 1_000_000
            if (
                not force
                and self._last is not None
                and elapsed_ms < self._config.disk_probe_interval_ms
            ):
                return self._last
            self._last = probe_disk_health(
                self._config.state_root,
                min_free_bytes=self._config.min_free_bytes,
                probe_bytes=self._config.disk_probe_bytes,
                now_ms=now_ms,
            )
            self._last_checked_ns = current_ns
            return self._last


__all__ = [
    "DiskHealthEvidence",
    "DiskHealthMonitor",
    "EpochStateError",
    "GatewayConfig",
    "GatewayConfigurationError",
    "InstanceEpochLease",
    "SingleInstanceError",
    "probe_disk_health",
]
