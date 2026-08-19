"""Restricted streaming download and AES-128-ECB decode for WeChat iLink media."""

from __future__ import annotations

import base64
import hashlib
import http.client
import os
import secrets
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_WECHAT_CDN_ORIGIN = "https://novac2c.cdn.weixin.qq.com"
DEFAULT_WECHAT_CDN_BASE_PATH = "/c2c"
DEFAULT_WECHAT_CDN_HOSTS = ("novac2c.cdn.weixin.qq.com",)


class WechatMediaError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WechatMediaReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    encrypted_query_param: str | None = Field(default=None, min_length=1, max_length=8_192)
    full_url: str | None = Field(default=None, min_length=1, max_length=16_384)
    aes_key: str = Field(min_length=1, max_length=128)
    declared_cipher_bytes: int | None = Field(default=None, ge=1, le=2_147_483_648)
    declared_plain_bytes: int | None = Field(default=None, ge=1, le=2_147_483_648)

    @model_validator(mode="after")
    def validate_location(self) -> Self:
        if (self.encrypted_query_param is None) == (self.full_url is None):
            raise ValueError("exactly one WeChat media URL representation is required")
        return self


@dataclass(frozen=True)
class WechatMediaLimits:
    max_cipher_bytes: int = 536_870_912
    max_plain_bytes: int = 536_870_912
    chunk_bytes: int = 262_144
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if not 1 <= self.max_cipher_bytes <= 2_147_483_648:
            raise ValueError("ciphertext size limit is invalid")
        if not 1 <= self.max_plain_bytes <= 2_147_483_648:
            raise ValueError("plaintext size limit is invalid")
        if not 4_096 <= self.chunk_bytes <= 4_194_304:
            raise ValueError("media chunk size is invalid")
        if not 1 <= self.timeout_seconds <= 1_800:
            raise ValueError("media timeout is invalid")


@dataclass(frozen=True)
class DownloadedWechatMedia:
    plaintext_path: Path
    plaintext_sha256: str
    plaintext_size_bytes: int
    ciphertext_sha256: str
    ciphertext_size_bytes: int
    source_url_sha256: str

    def cleanup(self) -> None:
        self.plaintext_path.unlink(missing_ok=True)


class MediaResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class MediaTransport(Protocol):
    def open(self, url: str, *, timeout_seconds: int) -> MediaResponse: ...


def validate_wechat_cdn_url(url: str, *, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    hosts = tuple(sorted(set(host.lower() for host in allowed_hosts)))
    try:
        port = parsed.port
    except ValueError as exc:
        raise WechatMediaError("wechat.media.url.port_invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise WechatMediaError("wechat.media.url.not_allowed")
    return url


def build_wechat_download_url(
    encrypted_query_param: str,
    *,
    origin: str = DEFAULT_WECHAT_CDN_ORIGIN,
    base_path: str = DEFAULT_WECHAT_CDN_BASE_PATH,
) -> str:
    if not encrypted_query_param or len(encrypted_query_param) > 8_192:
        raise WechatMediaError("wechat.media.query.invalid")
    if base_path != "/c2c":
        raise WechatMediaError("wechat.media.base_path.not_allowed")
    return f"{origin}{base_path}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def parse_wechat_aes_key(value: str) -> bytes:
    text = value.strip()
    if len(text) == 32 and all(char in "0123456789abcdefABCDEF" for char in text):
        key = bytes.fromhex(text)
    else:
        try:
            key = base64.b64decode(text, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise WechatMediaError("wechat.media.aes_key.invalid_encoding") from exc
        if len(key) == 32:
            try:
                candidate = key.decode("ascii")
            except UnicodeDecodeError:
                candidate = ""
            if len(candidate) == 32 and all(
                char in "0123456789abcdefABCDEF" for char in candidate
            ):
                key = bytes.fromhex(candidate)
    if len(key) != 16:
        raise WechatMediaError("wechat.media.aes_key.invalid_length")
    return key


class _HttpMediaResponse:
    def __init__(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse) -> None:
        self._connection = connection
        self._response = response
        self.status = response.status
        self.headers = {key.lower(): value for key, value in response.getheaders()}

    def read(self, amount: int) -> bytes:
        return self._response.read(amount)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class StrictHttpsMediaTransport:
    """One HTTPS request, no redirect handling, proxying, cookies, or ambient auth."""

    def __init__(self, *, allowed_hosts: tuple[str, ...] = DEFAULT_WECHAT_CDN_HOSTS) -> None:
        self.allowed_hosts = tuple(sorted(set(host.lower() for host in allowed_hosts)))

    def open(self, url: str, *, timeout_seconds: int) -> MediaResponse:
        validate_wechat_cdn_url(url, allowed_hosts=self.allowed_hosts)
        parsed = urlsplit(url)
        assert parsed.hostname is not None
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port or 443,
            timeout=timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "TiangongCommunication/3",
                },
            )
            return _HttpMediaResponse(connection, connection.getresponse())
        except Exception:
            connection.close()
            raise


def _content_length(headers: Mapping[str, str]) -> int | None:
    values = [value for key, value in headers.items() if key.lower() == "content-length"]
    if not values:
        return None
    if len(values) != 1 or not values[0].isdecimal():
        raise WechatMediaError("wechat.media.content_length.invalid")
    return int(values[0])


def _stream_ciphertext(
    response: MediaResponse,
    destination: Path,
    *,
    limits: WechatMediaLimits,
    declared_cipher_bytes: int | None,
) -> tuple[int, str]:
    header_length = _content_length(response.headers)
    for claimed in (header_length, declared_cipher_bytes):
        if claimed is not None and claimed > limits.max_cipher_bytes:
            raise WechatMediaError("wechat.media.ciphertext.too_large")
    if header_length is not None and declared_cipher_bytes is not None:
        if header_length != declared_cipher_bytes:
            raise WechatMediaError("wechat.media.ciphertext.declared_length_mismatch")
    total = 0
    digest = hashlib.sha256()
    with destination.open("xb") as stream:
        os.chmod(destination, 0o600)
        while True:
            chunk = response.read(limits.chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_cipher_bytes:
                raise WechatMediaError("wechat.media.ciphertext.too_large")
            digest.update(chunk)
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())
    if total == 0 or (header_length is not None and total != header_length):
        raise WechatMediaError("wechat.media.ciphertext.truncated")
    if declared_cipher_bytes is not None and total != declared_cipher_bytes:
        raise WechatMediaError("wechat.media.ciphertext.declared_length_mismatch")
    if total % 16:
        raise WechatMediaError("wechat.media.ciphertext.block_alignment_invalid")
    return total, digest.hexdigest()


def _decrypt_stream(
    source: Path,
    destination: Path,
    *,
    key: bytes,
    limits: WechatMediaLimits,
    declared_plain_bytes: int | None,
) -> tuple[int, str]:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    digest = hashlib.sha256()
    total = 0
    tail = b""
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        os.chmod(destination, 0o600)
        while True:
            chunk = incoming.read(limits.chunk_bytes)
            if not chunk:
                break
            decoded = decryptor.update(chunk)
            combined = tail + decoded
            if len(combined) > 16:
                ready, tail = combined[:-16], combined[-16:]
                total += len(ready)
                if total > limits.max_plain_bytes:
                    raise WechatMediaError("wechat.media.plaintext.too_large")
                digest.update(ready)
                outgoing.write(ready)
            else:
                tail = combined
        final = decryptor.finalize()
        tail += final
        if not tail:
            raise WechatMediaError("wechat.media.plaintext.empty")
        padding = tail[-1]
        if 1 <= padding <= 16 and tail.endswith(bytes([padding]) * padding):
            tail = tail[:-padding]
        total += len(tail)
        if total == 0 or total > limits.max_plain_bytes:
            raise WechatMediaError("wechat.media.plaintext.too_large")
        digest.update(tail)
        outgoing.write(tail)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if declared_plain_bytes is not None and total != declared_plain_bytes:
        raise WechatMediaError("wechat.media.plaintext.declared_length_mismatch")
    return total, digest.hexdigest()


class WechatMediaDownloader:
    def __init__(
        self,
        staging_root: Path,
        *,
        transport: MediaTransport | None = None,
        allowed_hosts: tuple[str, ...] = DEFAULT_WECHAT_CDN_HOSTS,
        limits: WechatMediaLimits = WechatMediaLimits(),
    ) -> None:
        if not staging_root.is_absolute() or staging_root == Path(staging_root.anchor):
            raise ValueError("WeChat media staging root is unsafe")
        if staging_root.exists() and (staging_root.is_symlink() or not staging_root.is_dir()):
            raise ValueError("WeChat media staging root is unsafe")
        self.staging_root = staging_root
        self.allowed_hosts = tuple(sorted(set(host.lower() for host in allowed_hosts)))
        if not self.allowed_hosts:
            raise ValueError("WeChat CDN allowlist cannot be empty")
        self.transport = transport or StrictHttpsMediaTransport(allowed_hosts=self.allowed_hosts)
        self.limits = limits

    def _url(self, reference: WechatMediaReference) -> str:
        url = (
            reference.full_url
            if reference.full_url is not None
            else build_wechat_download_url(reference.encrypted_query_param or "")
        )
        return validate_wechat_cdn_url(url, allowed_hosts=self.allowed_hosts)

    def download(self, reference: WechatMediaReference) -> DownloadedWechatMedia:
        key = parse_wechat_aes_key(reference.aes_key)
        url = self._url(reference)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        if self.staging_root.is_symlink():
            raise WechatMediaError("wechat.media.staging_root.unsafe")
        nonce = f"{os.getpid()}-{secrets.token_hex(16)}"
        ciphertext_path = self.staging_root / f".wechat-{nonce}.cipher.part"
        plaintext_path = self.staging_root / f"wechat-{nonce}.plain.part"
        response: MediaResponse | None = None
        try:
            response = self.transport.open(url, timeout_seconds=self.limits.timeout_seconds)
            if response.status in {301, 302, 303, 307, 308}:
                raise WechatMediaError("wechat.media.redirect.forbidden")
            if not 200 <= response.status < 300:
                raise WechatMediaError(f"wechat.media.http_status.{response.status}")
            cipher_size, cipher_sha = _stream_ciphertext(
                response,
                ciphertext_path,
                limits=self.limits,
                declared_cipher_bytes=reference.declared_cipher_bytes,
            )
            plain_size, plain_sha = _decrypt_stream(
                ciphertext_path,
                plaintext_path,
                key=key,
                limits=self.limits,
                declared_plain_bytes=reference.declared_plain_bytes,
            )
            return DownloadedWechatMedia(
                plaintext_path=plaintext_path,
                plaintext_sha256=plain_sha,
                plaintext_size_bytes=plain_size,
                ciphertext_sha256=cipher_sha,
                ciphertext_size_bytes=cipher_size,
                source_url_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(),
            )
        except Exception:
            plaintext_path.unlink(missing_ok=True)
            raise
        finally:
            if response is not None:
                response.close()
            ciphertext_path.unlink(missing_ok=True)

    def cleanup_abandoned(self) -> int:
        if not self.staging_root.exists():
            return 0
        removed = 0
        # iterdir instead of glob("*.part"): glob skips dot-prefixed names, so
        # the ".wechat-*.cipher.part" staging files would never be cleaned.
        for path in self.staging_root.iterdir():
            if path.name.endswith(".part") and path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
        return removed


__all__ = [
    "DEFAULT_WECHAT_CDN_BASE_PATH",
    "DEFAULT_WECHAT_CDN_HOSTS",
    "DEFAULT_WECHAT_CDN_ORIGIN",
    "DownloadedWechatMedia",
    "MediaResponse",
    "MediaTransport",
    "StrictHttpsMediaTransport",
    "WechatMediaDownloader",
    "WechatMediaError",
    "WechatMediaLimits",
    "WechatMediaReference",
    "build_wechat_download_url",
    "parse_wechat_aes_key",
    "validate_wechat_cdn_url",
]
