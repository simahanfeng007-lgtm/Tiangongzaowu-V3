"""Strict 7176 configuration and single-process ownership boundary."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath
from typing import Literal, Mapping, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import DEFAULT_PORT


_ENV_PREFIX = "TIANGONG_COMMUNICATION_"
_ALLOWED_ENV = {
    "TIANGONG_COMMUNICATION_ENVIRONMENT",
    "TIANGONG_COMMUNICATION_GATEWAY_TOKEN",
    "TIANGONG_COMMUNICATION_HOST",
    "TIANGONG_COMMUNICATION_MAX_BODY_BYTES",
    "TIANGONG_COMMUNICATION_PORT",
    "TIANGONG_COMMUNICATION_SHADOW_TOKEN",
    "TIANGONG_COMMUNICATION_STATE_ROOT",
    "TIANGONG_COMMUNICATION_TOTAL_GATEWAY_URL",
}
_LEGACY_BUSINESS_ENV = {
    "TIANGONG_BACKEND_URL",
    "TIANGONG_LIFE_URL",
    "TIANGONG_DESKTOP_WORKSPACE_ROOT",
    "TIANGONG_EXECUTION_LIFE_ROOT",
    "TIANGONG_EXECUTION_RUNTIME_ROOT",
    "TIANGONG_LIFE_KERNEL_ROOT",
    "TIANGONG_LIFE_ROOT",
}


class CommunicationConfigurationError(ValueError):
    pass


class CommunicationInstanceError(RuntimeError):
    pass


def _strict_uint(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    if re.fullmatch(r"0|[1-9][0-9]*", raw) is None:
        raise CommunicationConfigurationError(f"{name} must be an unsigned integer")
    return int(raw)


def _normalized_gateway_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("total gateway origin must be an exact loopback HTTP origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("total gateway port is invalid") from exc
    if port is None or not 1 <= port <= 65_535:
        raise ValueError("total gateway origin must include an explicit port")
    return f"http://127.0.0.1:{port}"


class CommunicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    environment: Literal["development", "production", "test"] = "production"
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=DEFAULT_PORT, ge=0, le=65_535)
    state_root: Path
    total_gateway_origin: str = "http://127.0.0.1:7184"
    max_body_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    shadow_api_token: str = Field(default="", max_length=512, repr=False)
    gateway_api_token: str = Field(default="", max_length=512, repr=False)

    @field_validator("state_root")
    @classmethod
    def validate_state_root(cls, value: Path) -> Path:
        # Pydantic materializes Path using the host flavour.  Source-release
        # tests and cross-compilation can therefore see a valid Windows path
        # on a POSIX host as a relative PosixPath.  Validate the original text
        # against both path grammars, while only resolving with the host
        # filesystem when its grammar matches.
        text = str(value)
        windows_absolute = PureWindowsPath(text).is_absolute()
        host_absolute = value.is_absolute()
        if not host_absolute and not windows_absolute:
            raise ValueError("communication state root must be absolute")
        if windows_absolute and os.name != "nt":
            if len(text) > 240 or PureWindowsPath(text).parent == PureWindowsPath(text):
                raise ValueError("communication state root is unsafe")
            return value
        if value.exists() and value.is_symlink():
            raise ValueError("communication state root cannot be a symbolic link")
        resolved = value.resolve(strict=False)
        if resolved == Path(resolved.anchor) or len(str(resolved)) > 240:
            raise ValueError("communication state root is unsafe")
        return resolved

    @field_validator("total_gateway_origin")
    @classmethod
    def validate_gateway_origin(cls, value: str) -> str:
        return _normalized_gateway_origin(value)

    @model_validator(mode="after")
    def validate_production_ports(self) -> Self:
        if self.environment == "production":
            if self.port != DEFAULT_PORT:
                raise ValueError("production communication service must listen on port 7176")
            if self.total_gateway_origin != "http://127.0.0.1:7184":
                raise ValueError("production communication service must use total gateway port 7184")
        elif self.environment != "test" and self.port == 0:
            raise ValueError("ephemeral communication port is test-only")
        if self.shadow_api_token and len(self.shadow_api_token) < 32:
            raise ValueError("communication shadow token length is invalid")
        if self.gateway_api_token and len(self.gateway_api_token) < 32:
            raise ValueError("communication gateway token length is invalid")
        return self

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self:
        source = dict(os.environ if environ is None else environ)
        legacy = sorted(name for name in _LEGACY_BUSINESS_ENV if source.get(name, "").strip())
        if legacy:
            raise CommunicationConfigurationError(
                "legacy backend/life business dependencies are forbidden: " + ",".join(legacy)
            )
        unknown = sorted(
            name for name in source if name.startswith(_ENV_PREFIX) and name not in _ALLOWED_ENV
        )
        if unknown:
            raise CommunicationConfigurationError(
                "unknown communication-service environment variables: " + ",".join(unknown)
            )
        appdata = source.get("APPDATA") or str(Path.home() / ".tiangong-v3")
        try:
            return cls(
                environment=source.get("TIANGONG_COMMUNICATION_ENVIRONMENT", "production"),
                bind_host=source.get("TIANGONG_COMMUNICATION_HOST", "127.0.0.1"),
                port=_strict_uint(source, "TIANGONG_COMMUNICATION_PORT", DEFAULT_PORT),
                state_root=Path(
                    source.get(
                        "TIANGONG_COMMUNICATION_STATE_ROOT",
                        str(Path(appdata) / "tiangong-v3-qiyuan" / "runtime" / "communication"),
                    )
                ),
                total_gateway_origin=source.get(
                    "TIANGONG_COMMUNICATION_TOTAL_GATEWAY_URL",
                    "http://127.0.0.1:7184",
                ),
                max_body_bytes=_strict_uint(
                    source,
                    "TIANGONG_COMMUNICATION_MAX_BODY_BYTES",
                    1_048_576,
                ),
                shadow_api_token=source.get("TIANGONG_COMMUNICATION_SHADOW_TOKEN", ""),
                gateway_api_token=source.get("TIANGONG_COMMUNICATION_GATEWAY_TOKEN", ""),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, CommunicationConfigurationError):
                raise
            raise CommunicationConfigurationError("invalid communication-service configuration") from exc


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


class CommunicationInstanceLease:
    def __init__(self, path: Path, stream: object) -> None:
        self.path = path
        self._stream = stream
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    @classmethod
    def acquire(cls, state_root: Path) -> "CommunicationInstanceLease":
        state_root.mkdir(parents=True, exist_ok=True)
        if state_root.is_symlink() or not state_root.is_dir():
            raise CommunicationInstanceError("communication state root is not a safe directory")
        path = state_root / "communication.instance.lock"
        stream = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            _lock_file(stream)
            os.chmod(path, 0o600)
            return cls(path, stream)
        except (OSError, IOError) as exc:
            stream.close()
            raise CommunicationInstanceError("communication service is already running") from exc

    def release(self) -> None:
        if not self._active:
            return
        try:
            _unlock_file(self._stream)
        finally:
            self._stream.close()
            self._active = False

    def __enter__(self) -> "CommunicationInstanceLease":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


__all__ = [
    "CommunicationConfig",
    "CommunicationConfigurationError",
    "CommunicationInstanceError",
    "CommunicationInstanceLease",
]
