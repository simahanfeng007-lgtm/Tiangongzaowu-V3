"""Scope-bound Feishu message-resource download and shared attachment admission."""

from __future__ import annotations

import hashlib
import http.client
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, quote, urlsplit

from contracts import AttachmentRef

from .feishu_route import FeishuRouteLedger
from .wechat_attachment import (
    WechatAttachmentCandidate,
    WechatAttachmentError,
    WechatAttachmentGate,
    expected_attachment_mime,
)
from .wechat_media import DownloadedWechatMedia


FEISHU_OPEN_API_ORIGIN = "https://open.feishu.cn"
FEISHU_OPEN_API_HOST = "open.feishu.cn"
_GENERIC_BINARY_MIME = "application/octet-stream"
_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
}


class FeishuAttachmentError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FeishuResourceLimits:
    max_bytes: int = 536_870_912
    chunk_bytes: int = 262_144
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if not 1 <= self.max_bytes <= 2_147_483_648:
            raise ValueError("Feishu resource size limit is invalid")
        if not 4_096 <= self.chunk_bytes <= 4_194_304:
            raise ValueError("Feishu resource chunk size is invalid")
        if not 1 <= self.timeout_seconds <= 1_800:
            raise ValueError("Feishu resource timeout is invalid")


@dataclass(frozen=True)
class DownloadedFeishuResource:
    plaintext_path: Path
    plaintext_sha256: str
    plaintext_size_bytes: int
    response_mime: str
    source_url_sha256: str

    def cleanup(self) -> None:
        self.plaintext_path.unlink(missing_ok=True)


class FeishuResourceResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class FeishuResourceTransport(Protocol):
    def open(
        self,
        url: str,
        *,
        access_token: str,
        timeout_seconds: int,
    ) -> FeishuResourceResponse: ...


def _opaque_segment(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value.encode("utf-8")) > 2_048
        or any(character in value for character in ("/", "\\", "?", "#", "%", "\x00"))
        or any(character.isspace() for character in value)
    ):
        raise FeishuAttachmentError(f"feishu.attachment.{name}.invalid")
    return value


def build_feishu_resource_url(
    message_id: str,
    resource_key: str,
    resource_type: str,
    *,
    origin: str = FEISHU_OPEN_API_ORIGIN,
) -> str:
    if origin != FEISHU_OPEN_API_ORIGIN or resource_type not in {"image", "file"}:
        raise FeishuAttachmentError("feishu.attachment.resource_route.invalid")
    message = quote(_opaque_segment(message_id, "message_id"), safe="")
    resource = quote(_opaque_segment(resource_key, "resource_key"), safe="")
    return (
        f"{origin}/open-apis/im/v1/messages/{message}/resources/{resource}"
        f"?type={resource_type}"
    )


def validate_feishu_resource_url(url: str) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise FeishuAttachmentError("feishu.attachment.url.port_invalid") from exc
    segments = parsed.path.split("/")
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != FEISHU_OPEN_API_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or len(segments) != 8
        or segments[:5] != ["", "open-apis", "im", "v1", "messages"]
        or segments[6] != "resources"
        or not segments[5]
        or not segments[7]
        or tuple(query) not in {(("type", "image"),), (("type", "file"),)}
    ):
        raise FeishuAttachmentError("feishu.attachment.url.not_allowed")
    _opaque_segment(segments[5], "message_id")
    _opaque_segment(segments[7], "resource_key")
    return url


class _HttpResourceResponse:
    def __init__(
        self,
        connection: http.client.HTTPSConnection,
        response: http.client.HTTPResponse,
    ) -> None:
        self._connection = connection
        self._response = response
        self.status = response.status
        headers: dict[str, str] = {}
        for key, value in response.getheaders():
            normalized = key.lower()
            if normalized in headers and normalized in {
                "content-length",
                "content-type",
                "content-disposition",
            }:
                response.close()
                connection.close()
                raise FeishuAttachmentError("feishu.attachment.response_header.duplicate")
            headers[normalized] = value
        self.headers = headers

    def read(self, amount: int) -> bytes:
        return self._response.read(amount)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class StrictFeishuResourceTransport:
    """Fixed Feishu OpenAPI HTTPS request with no proxy, redirect, cookie, or ambient auth."""

    def open(
        self,
        url: str,
        *,
        access_token: str,
        timeout_seconds: int,
    ) -> FeishuResourceResponse:
        validate_feishu_resource_url(url)
        if (
            not isinstance(access_token, str)
            or not access_token
            or access_token != access_token.strip()
            or len(access_token.encode("utf-8")) > 8_192
            or "\r" in access_token
            or "\n" in access_token
        ):
            raise FeishuAttachmentError("feishu.attachment.access_token.invalid")
        parsed = urlsplit(url)
        assert parsed.hostname is not None
        target = parsed.path + "?" + parsed.query
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
                    "Authorization": "Bearer " + access_token,
                    "Connection": "close",
                    "User-Agent": "TiangongCommunication/3",
                },
            )
            return _HttpResourceResponse(connection, connection.getresponse())
        except Exception:
            connection.close()
            raise


def _response_mime(headers: Mapping[str, str]) -> str:
    raw = headers.get("content-type", "")
    if not isinstance(raw, str) or len(raw) > 512 or "\x00" in raw:
        raise FeishuAttachmentError("feishu.attachment.content_type.invalid")
    mime = raw.split(";", 1)[0].strip().casefold()
    if not mime:
        raise FeishuAttachmentError("feishu.attachment.content_type.missing")
    return mime


def _content_length(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.isdecimal():
        raise FeishuAttachmentError("feishu.attachment.content_length.invalid")
    return int(raw)


def _status_error(status: int) -> FeishuAttachmentError:
    if 300 <= status < 400:
        return FeishuAttachmentError("feishu.attachment.redirect_forbidden")
    if status == 401:
        return FeishuAttachmentError("feishu.attachment.access_token.rejected")
    if status == 403:
        return FeishuAttachmentError("feishu.attachment.scope_denied")
    if status == 404:
        return FeishuAttachmentError("feishu.attachment.resource_missing")
    if status == 429:
        return FeishuAttachmentError("feishu.attachment.rate_limited")
    if 500 <= status <= 599:
        return FeishuAttachmentError("feishu.attachment.platform_unavailable")
    return FeishuAttachmentError("feishu.attachment.response_rejected")


def download_feishu_resource(
    *,
    url: str,
    access_token: str,
    staging_root: Path,
    transport: FeishuResourceTransport,
    limits: FeishuResourceLimits,
) -> DownloadedFeishuResource:
    validate_feishu_resource_url(url)
    if not staging_root.is_absolute() or staging_root == Path(staging_root.anchor):
        raise ValueError("Feishu staging root is unsafe")
    staging_root.mkdir(parents=True, exist_ok=True)
    os.chmod(staging_root, 0o700)
    destination = staging_root / ("feishu-" + secrets.token_hex(16) + ".plain.part")
    response: FeishuResourceResponse | None = None
    try:
        response = transport.open(
            url,
            access_token=access_token,
            timeout_seconds=limits.timeout_seconds,
        )
        if response.status != 200:
            raise _status_error(response.status)
        mime = _response_mime(response.headers)
        declared = _content_length(response.headers)
        if declared is not None and (declared < 1 or declared > limits.max_bytes):
            raise FeishuAttachmentError("feishu.attachment.resource_too_large")
        digest = hashlib.sha256()
        total = 0
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            while True:
                chunk = response.read(limits.chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_bytes:
                    raise FeishuAttachmentError("feishu.attachment.resource_too_large")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if total < 1 or (declared is not None and total != declared):
            raise FeishuAttachmentError("feishu.attachment.resource_truncated")
        return DownloadedFeishuResource(
            plaintext_path=destination,
            plaintext_sha256=digest.hexdigest(),
            plaintext_size_bytes=total,
            response_mime=mime,
            source_url_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(),
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()


class FeishuAttachmentIngestor:
    def __init__(
        self,
        staging_root: Path,
        routes: FeishuRouteLedger,
        gate: WechatAttachmentGate,
        *,
        transport: FeishuResourceTransport | None = None,
        limits: FeishuResourceLimits = FeishuResourceLimits(),
    ) -> None:
        if not staging_root.is_absolute() or staging_root == Path(staging_root.anchor):
            raise ValueError("Feishu staging root is unsafe")
        self._staging_root = staging_root.resolve(strict=False)
        self._routes = routes
        self._gate = gate
        self._transport = transport or StrictFeishuResourceTransport()
        self._limits = limits

    def ingest(
        self,
        resource_id: str,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        access_token: str,
    ) -> AttachmentRef:
        resource = self._routes.resolve_resource(
            resource_id=resource_id,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
        )
        url = build_feishu_resource_url(
            resource.message_id,
            resource.resource_key,
            resource.resource_type,
        )
        downloaded = download_feishu_resource(
            url=url,
            access_token=access_token,
            staging_root=self._staging_root,
            transport=self._transport,
            limits=self._limits,
        )
        try:
            if resource.resource_type == "image":
                extension = _IMAGE_EXTENSIONS.get(downloaded.response_mime)
                if extension is None:
                    raise FeishuAttachmentError("feishu.attachment.image_mime.invalid")
                filename = "feishu-image-" + hashlib.sha256(
                    resource.resource_key.encode("utf-8")
                ).hexdigest()[:16] + extension
                declared_mime = downloaded.response_mime
            else:
                if resource.filename is None:
                    raise FeishuAttachmentError("feishu.attachment.filename.missing")
                filename = resource.filename
                try:
                    declared_mime = expected_attachment_mime(filename)
                except WechatAttachmentError as exc:
                    raise FeishuAttachmentError(
                        exc.code.replace("wechat.attachment.", "feishu.attachment.", 1)
                    ) from exc
                if downloaded.response_mime not in {declared_mime, _GENERIC_BINARY_MIME}:
                    raise FeishuAttachmentError("feishu.attachment.response_mime_mismatch")
            media = DownloadedWechatMedia(
                plaintext_path=downloaded.plaintext_path,
                plaintext_sha256=downloaded.plaintext_sha256,
                plaintext_size_bytes=downloaded.plaintext_size_bytes,
                ciphertext_sha256=downloaded.plaintext_sha256,
                ciphertext_size_bytes=downloaded.plaintext_size_bytes,
                source_url_sha256=downloaded.source_url_sha256,
            )
            candidate = WechatAttachmentCandidate(
                media=media,
                filename=filename,
                declared_mime=declared_mime,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                conversation_scope_hash=conversation_scope_hash,
                source_message_ref=resource.source_message_ref,
                created_at_ms=resource.created_at_ms,
            )
            try:
                return self._gate.accept(candidate)
            except WechatAttachmentError as exc:
                raise FeishuAttachmentError(
                    exc.code.replace("wechat.attachment.", "feishu.attachment.", 1)
                ) from exc
        finally:
            downloaded.cleanup()


__all__ = [
    "DownloadedFeishuResource",
    "FEISHU_OPEN_API_HOST",
    "FEISHU_OPEN_API_ORIGIN",
    "FeishuAttachmentError",
    "FeishuAttachmentIngestor",
    "FeishuResourceLimits",
    "FeishuResourceResponse",
    "FeishuResourceTransport",
    "StrictFeishuResourceTransport",
    "build_feishu_resource_url",
    "download_feishu_resource",
    "validate_feishu_resource_url",
]
