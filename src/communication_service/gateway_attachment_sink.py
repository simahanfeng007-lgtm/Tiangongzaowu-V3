"""Streaming 7176 attachment sink into the authenticated 7184 object store."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

from contracts import canonical_json_bytes

from .wechat_attachment import StoredAttachmentObject, WechatAttachmentError


class LoopbackGatewayAttachmentSink:
    def __init__(self, origin: str, token: str) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 7184
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or len(token) < 32
        ):
            raise ValueError("gateway attachment sink configuration is invalid")
        self._token = token

    @staticmethod
    def _metadata_header(payload: dict[str, object]) -> str:
        import base64

        return base64.urlsafe_b64encode(canonical_json_bytes(payload)).decode("ascii").rstrip("=")

    def put_attachment(
        self,
        source: Path,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        content_sha256: str,
        size_bytes: int,
        mime: str,
        filename: str,
        created_at_ms: int,
    ) -> StoredAttachmentObject:
        if source.is_symlink() or not source.is_file() or source.stat().st_size != size_bytes:
            raise WechatAttachmentError("wechat.attachment.gateway_source_invalid")
        metadata = self._metadata_header(
            {
                "content_sha256": content_sha256,
                "conversation_scope_hash": conversation_scope_hash,
                "created_at_ms": created_at_ms,
                "filename": filename,
                "link_account_id": link_account_id,
                "mime": mime,
                "size_bytes": size_bytes,
                "tenant_id": tenant_id,
            }
        )
        connection = http.client.HTTPConnection("127.0.0.1", 7184, timeout=120)
        response = None
        try:
            connection.putrequest("POST", "/api/v1/internal/channel/attachments", skip_accept_encoding=True)
            connection.putheader("Accept", "application/json")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(size_bytes))
            connection.putheader("X-Tiangong-Communication-Token", self._token)
            connection.putheader("X-Tiangong-Attachment-Metadata", metadata)
            connection.endheaders()
            with source.open("rb") as stream:
                while chunk := stream.read(262_144):
                    connection.send(chunk)
            response = connection.getresponse()
            raw = response.read(262_145)
            if len(raw) > 262_144:
                raise WechatAttachmentError("wechat.attachment.gateway_response_too_large")
            try:
                value = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WechatAttachmentError("wechat.attachment.gateway_response_invalid") from exc
            if response.status != 200 or not isinstance(value, dict) or value.get("ok") is not True:
                raise WechatAttachmentError("wechat.attachment.gateway_rejected")
            if canonical_json_bytes(value) != raw:
                raise WechatAttachmentError("wechat.attachment.gateway_response_noncanonical")
            stored = StoredAttachmentObject(
                object_id=value["object_id"],
                revision=value["revision"],
                sha256=value["sha256"],
                size_bytes=value["size_bytes"],
            )
            if stored.sha256 != content_sha256 or stored.size_bytes != size_bytes:
                raise WechatAttachmentError("wechat.attachment.gateway_binding_mismatch")
            return stored
        except WechatAttachmentError:
            raise
        except Exception as exc:
            raise WechatAttachmentError("wechat.attachment.gateway_transport_failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


__all__ = ["LoopbackGatewayAttachmentSink"]
