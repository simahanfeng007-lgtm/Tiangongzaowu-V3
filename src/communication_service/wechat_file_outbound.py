"""Object-reference-only WeChat iLink artifact upload and send pipeline."""

from __future__ import annotations

import base64
import hashlib
import http.client
import os
import secrets
import threading
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol
from urllib.parse import parse_qs, quote, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from runtime_security.path_identity import resolve_existing_path

from contracts import (
    DeliveryPartGrant,
    DeliveryPartReceipt,
    DeliveryReceipt,
    DeliveryTicketPayload,
    OutboundPart,
    OutboundPlan,
    canonical_sha256,
    grant_from_outbound_part,
)

from .delivery_ledger import (
    DeliveryLedger,
    DeliveryLedgerRecord,
    DeliveryPartStageFact,
)
from .wechat_session import WechatSessionLedger
from .wechat_text_outbound import (
    WECHAT_ILINK_CHANNEL_VERSION,
    HttpWechatIlinkTextTransport,
    WechatIlinkResponse,
    WechatRateGate,
    WechatTextOutboundError,
    WechatOutboundPolicy,
    classify_wechat_ilink_response,
    derive_wechat_client_id,
    wechat_response_integer,
)
from .wechat_transfer_control import (
    WechatProgressRecorder,
    WechatTransferBudget,
    WechatTransferControlError,
    compute_wechat_upload_timeout,
)


WECHAT_CDN_HOST = "novac2c.cdn.weixin.qq.com"
WECHAT_CDN_BASE_PATH = "/c2c"
WECHAT_CDN_UPLOAD_PATH = "/c2c/upload"
WECHAT_MEDIA_IMAGE = 1
WECHAT_MEDIA_VIDEO = 2
WECHAT_MEDIA_FILE = 3
WECHAT_MEDIA_VOICE = 4


class WechatFileOutboundError(RuntimeError):
    def __init__(self, code: str, *, send_outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.send_outcome_unknown = send_outcome_unknown


class ArtifactContentSource(Protocol):
    def open_artifact(
        self,
        grant: DeliveryPartGrant,
        *,
        timeout_seconds: int,
    ) -> BinaryIO: ...


@dataclass(frozen=True)
class WechatCdnUploadResponse:
    status_code: int
    encrypted_query_param: str | None
    body_sha256: str
    bytes_sent: int

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599 or self.bytes_sent < 1:
            raise ValueError("WeChat CDN response is invalid")
        if len(self.body_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.body_sha256
        ):
            raise ValueError("WeChat CDN response digest is invalid")
        if self.encrypted_query_param is not None and (
            not self.encrypted_query_param
            or "\x00" in self.encrypted_query_param
            or len(self.encrypted_query_param) > 8_192
        ):
            raise ValueError("WeChat CDN encrypted parameter is invalid")


class WechatIlinkFileTransport(Protocol):
    def get_upload_url(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse: ...

    def upload_ciphertext(
        self,
        upload_url: str,
        ciphertext_path: Path,
        *,
        ciphertext_size: int,
        timeout_seconds: int,
        max_response_bytes: int,
        progress_callback: Callable[[int], None] | None = None,
    ) -> WechatCdnUploadResponse: ...

    def send_message(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse: ...


def validate_wechat_upload_url(upload_url: str, *, filekey: str) -> str:
    parsed = urlsplit(upload_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise WechatFileOutboundError("wechat.file.upload_url.invalid") from exc
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != WECHAT_CDN_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != WECHAT_CDN_UPLOAD_PATH
        or parsed.fragment
        or set(query) != {"encrypted_query_param", "filekey"}
        or len(query["encrypted_query_param"]) != 1
        or not query["encrypted_query_param"][0]
        or len(query["encrypted_query_param"][0]) > 8_192
        or query["filekey"] != [filekey]
    ):
        raise WechatFileOutboundError("wechat.file.upload_url.not_allowed")
    return upload_url


def build_wechat_upload_url(upload_param: str, *, filekey: str) -> str:
    if (
        not upload_param
        or "\x00" in upload_param
        or len(upload_param) > 8_192
        or not filekey
        or len(filekey) > 160
    ):
        raise WechatFileOutboundError("wechat.file.upload_param.invalid")
    return validate_wechat_upload_url(
        "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param="
        + quote(upload_param, safe="")
        + "&filekey="
        + quote(filekey, safe=""),
        filekey=filekey,
    )


class HttpWechatIlinkFileTransport(HttpWechatIlinkTextTransport):
    def upload_ciphertext(
        self,
        upload_url: str,
        ciphertext_path: Path,
        *,
        ciphertext_size: int,
        timeout_seconds: int,
        max_response_bytes: int,
        progress_callback: Callable[[int], None] | None = None,
    ) -> WechatCdnUploadResponse:
        parsed = urlsplit(upload_url)
        try:
            port = parsed.port
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise WechatFileOutboundError("wechat.file.cdn_request.invalid") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != WECHAT_CDN_HOST
            or port not in {None, 443}
            or parsed.path != WECHAT_CDN_UPLOAD_PATH
            or set(query) != {"encrypted_query_param", "filekey"}
            or len(query["encrypted_query_param"]) != 1
            or not query["encrypted_query_param"][0]
            or len(query["encrypted_query_param"][0]) > 8_192
            or len(query["filekey"]) != 1
            or not query["filekey"][0]
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or not ciphertext_path.is_absolute()
            or ciphertext_path.is_symlink()
            or not ciphertext_path.is_file()
            or ciphertext_path.stat().st_size != ciphertext_size
            or not 1 <= timeout_seconds <= 3_600
            or not 1_024 <= max_response_bytes <= 8_388_608
        ):
            raise WechatFileOutboundError("wechat.file.cdn_request.invalid")
        connection = http.client.HTTPSConnection(WECHAT_CDN_HOST, 443, timeout=timeout_seconds)
        response = None
        try:
            with ciphertext_path.open("rb") as source:
                body = _ProgressReader(source, progress_callback)
                connection.request(
                    "POST",
                    parsed.path + "?" + parsed.query,
                    body=body,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(ciphertext_size),
                    },
                )
            response = connection.getresponse()
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise WechatFileOutboundError("wechat.file.cdn_response.too_large")
            encrypted_param = response.getheader("x-encrypted-param")
            if encrypted_param is not None:
                encrypted_param = encrypted_param.strip()
            return WechatCdnUploadResponse(
                status_code=response.status,
                encrypted_query_param=encrypted_param or None,
                body_sha256=hashlib.sha256(raw).hexdigest(),
                bytes_sent=ciphertext_size,
            )
        except WechatFileOutboundError:
            raise
        except Exception as exc:
            raise WechatFileOutboundError("wechat.file.cdn_transport.unknown") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


class _ProgressReader:
    def __init__(self, source: BinaryIO, callback: Callable[[int], None] | None) -> None:
        self._source = source
        self._callback = callback
        self._completed = 0

    def read(self, amount: int = -1) -> bytes:
        chunk = self._source.read(amount)
        if chunk:
            self._completed += len(chunk)
            if self._callback is not None:
                self._callback(self._completed)
        return chunk


@dataclass(frozen=True)
class FetchedArtifact:
    path: Path
    size_bytes: int
    sha256: str
    md5_hex: str


@dataclass(frozen=True)
class EncryptedArtifact:
    path: Path
    size_bytes: int
    sha256: str
    aes_key: bytes


def _artifact_only_plan(
    payload: DeliveryTicketPayload,
    plan: OutboundPlan,
    policy: WechatOutboundPolicy,
) -> tuple[tuple[OutboundPart, DeliveryPartGrant], ...]:
    if not policy.has_valid_sha256() or plan.channel_policy_hash != policy.policy_sha256:
        raise WechatFileOutboundError("wechat.file.policy.mismatch")
    if not plan.has_valid_plan_sha256() or payload.outbound_plan_sha256 != plan.plan_sha256:
        raise WechatFileOutboundError("wechat.file.plan_digest.mismatch")
    fields = (
        "delivery_id",
        "effect_id",
        "request_id",
        "run_id",
        "generation",
        "channel",
        "tenant_id",
        "link_account_id",
        "conversation_ref",
        "conversation_scope_hash",
        "recipient_scope_hash",
        "reply_to_message_ref",
        "outbound_plan_id",
        "channel_policy_hash",
    )
    if any(getattr(payload, field) != getattr(plan, field) for field in fields):
        raise WechatFileOutboundError("wechat.file.ticket_plan.mismatch")
    if payload.channel != "wechat" or payload.allow_text or not payload.allow_files:
        raise WechatFileOutboundError("wechat.file.artifact_only_ticket.required")
    expected = tuple(grant_from_outbound_part(part) for part in plan.parts)
    if payload.parts != expected or any(part.kind != "artifact" for part in plan.parts):
        raise WechatFileOutboundError("wechat.file.parts.mismatch")
    result = []
    for part, grant in zip(plan.parts, payload.parts, strict=True):
        if grant.size_bytes is None or grant.size_bytes > policy.max_file_bytes:
            raise WechatFileOutboundError("wechat.file.size_limit.exceeded")
        result.append((part, grant))
    return tuple(result)


def _filekey(effect_id: str, part_id: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.wechat-filekey.v1",
            "effect_id": effect_id,
            "part_id": part_id,
        }
    )[:32]


def _padded_size(size: int) -> int:
    return ((size // 16) + 1) * 16


def _media_type(grant: DeliveryPartGrant) -> int:
    assert grant.mime is not None and grant.filename is not None
    extension = Path(grant.filename).suffix.lower()
    if grant.mime.startswith("image/"):
        return WECHAT_MEDIA_IMAGE
    if grant.mime.startswith("video/"):
        return WECHAT_MEDIA_VIDEO
    if extension == ".silk":
        return WECHAT_MEDIA_VOICE
    return WECHAT_MEDIA_FILE


def _media_item(
    grant: DeliveryPartGrant,
    *,
    encrypted_query_param: str,
    aes_key: bytes,
    ciphertext_size: int,
    md5_hex: str,
) -> tuple[int, dict[str, Any]]:
    assert grant.filename is not None and grant.size_bytes is not None
    media_type = _media_type(grant)
    media = {
        "encrypt_query_param": encrypted_query_param,
        "aes_key": base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii"),
        "encrypt_type": 1,
    }
    if media_type == WECHAT_MEDIA_IMAGE:
        return media_type, {"type": 2, "image_item": {"media": media, "mid_size": ciphertext_size}}
    if media_type == WECHAT_MEDIA_VIDEO:
        return media_type, {
            "type": 5,
            "video_item": {
                "media": media,
                "video_size": ciphertext_size,
                "play_length": 0,
                "video_md5": md5_hex,
            },
        }
    if media_type == WECHAT_MEDIA_VOICE:
        return media_type, {
            "type": 3,
            "voice_item": {
                "media": media,
                "encode_type": 6,
                "bits_per_sample": 16,
                "sample_rate": 24_000,
                "playtime": 0,
            },
        }
    return media_type, {
        "type": 4,
        "file_item": {
            "media": media,
            "file_name": grant.filename,
            "len": str(grant.size_bytes),
        },
    }


class WechatFileDeliveryService:
    def __init__(
        self,
        ledger: DeliveryLedger,
        sessions: WechatSessionLedger,
        source: ArtifactContentSource,
        transport: WechatIlinkFileTransport,
        *,
        staging_root: Path,
        clock_ms,
        sleeper,
        rate_gate: WechatRateGate | None = None,
        transfer_budget: WechatTransferBudget | None = None,
    ) -> None:
        if not staging_root.is_absolute() or staging_root.is_symlink():
            raise ValueError("WeChat outbound staging root is unsafe")
        staging_root.mkdir(parents=True, exist_ok=True)
        self._ledger = ledger
        self._sessions = sessions
        self._source = source
        self._transport = transport
        self._staging_root = resolve_existing_path(staging_root)
        self._clock_ms = clock_ms
        self._sleeper = sleeper
        self._rate_gate = rate_gate or WechatRateGate(clock_ms=clock_ms, sleeper=sleeper)
        self._transfer_budget = transfer_budget or WechatTransferBudget()
        self._send_lock = threading.RLock()

    def _stage(self, payload, part, stage, *, attempt, evidence):
        fact = DeliveryPartStageFact(
            effect_id=payload.effect_id,
            part_id=part.part_id,
            part_index=part.index,
            kind="artifact",
            stage=stage,
            attempt=attempt,
            occurred_at_ms=self._clock_ms(),
            evidence_sha256=evidence,
            stage_fact_sha256="0" * 64,
        ).with_computed_sha256()
        return self._ledger.record_part_stage(fact)

    def _fetch(self, grant, *, payload, part, policy, timeout_seconds) -> FetchedArtifact:
        path = self._staging_root / (
            f"{payload.effect_id[-16:]}.{part.index}.{secrets.token_hex(8)}.plain.part"
        )
        digest = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        size = 0
        progress = WechatProgressRecorder(
            self._ledger,
            effect_id=payload.effect_id,
            part_id=part.part_id,
            part_index=part.index,
            phase="FETCH",
            total_bytes=grant.size_bytes,
            interval_bytes=policy.progress_interval_bytes,
            clock_ms=self._clock_ms,
        )
        try:
            stream = self._source.open_artifact(
                grant, timeout_seconds=timeout_seconds
            )
            with closing(stream), path.open("xb") as target:
                while True:
                    chunk = stream.read(policy.file_io_chunk_bytes)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise WechatFileOutboundError("wechat.file.source_chunk.invalid")
                    size += len(chunk)
                    if size > policy.max_file_bytes or size > grant.size_bytes:
                        raise WechatFileOutboundError("wechat.file.source_size.exceeded")
                    digest.update(chunk)
                    md5.update(chunk)
                    target.write(chunk)
                    progress.update(size)
                target.flush()
                os.fsync(target.fileno())
            if size != grant.size_bytes or digest.hexdigest() != grant.content_sha256:
                raise WechatFileOutboundError("wechat.file.source_identity.mismatch")
            progress.update(size, force=True)
            return FetchedArtifact(path, size, digest.hexdigest(), md5.hexdigest())
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _encrypt(self, fetched, *, payload, part, policy) -> EncryptedArtifact:
        path = self._staging_root / (
            f"{payload.effect_id[-16:]}.{part.index}.{secrets.token_hex(8)}.cipher.part"
        )
        key = secrets.token_bytes(16)
        digest = hashlib.sha256()
        size = 0
        processed = 0
        progress = WechatProgressRecorder(
            self._ledger,
            effect_id=payload.effect_id,
            part_id=part.part_id,
            part_index=part.index,
            phase="ENCRYPT",
            total_bytes=fetched.size_bytes,
            interval_bytes=policy.progress_interval_bytes,
            clock_ms=self._clock_ms,
        )
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(128).padder()
        try:
            with fetched.path.open("rb") as source, path.open("xb") as target:
                while True:
                    chunk = source.read(policy.file_io_chunk_bytes)
                    if not chunk:
                        break
                    processed += len(chunk)
                    encrypted = encryptor.update(padder.update(chunk))
                    if encrypted:
                        target.write(encrypted)
                        digest.update(encrypted)
                        size += len(encrypted)
                    progress.update(processed)
                final = encryptor.update(padder.finalize()) + encryptor.finalize()
                target.write(final)
                digest.update(final)
                size += len(final)
                target.flush()
                os.fsync(target.fileno())
            if size != _padded_size(fetched.size_bytes):
                raise WechatFileOutboundError("wechat.file.cipher_size.invalid")
            progress.update(processed, force=True)
            return EncryptedArtifact(path, size, digest.hexdigest(), key)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _upload_request_body(
        *,
        to_user_id,
        media_type,
        filekey,
        fetched,
        encrypted,
    ):
        return {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": fetched.size_bytes,
            "rawfilemd5": fetched.md5_hex,
            "filesize": encrypted.size_bytes,
            "no_need_thumb": True,
            "aeskey": encrypted.aes_key.hex(),
            "base_info": {
                "channel_version": WECHAT_ILINK_CHANNEL_VERSION,
                "bot_agent": "TiangongZaowu/3.0.0",
            },
        }

    @staticmethod
    def _send_body(*, to_user_id, item, context_token, run_id, client_id):
        return {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [item],
                "context_token": context_token or None,
                "run_id": run_id,
            },
            "base_info": {
                "channel_version": WECHAT_ILINK_CHANNEL_VERSION,
                "bot_agent": "TiangongZaowu/3.0.0",
            },
        }

    def _planned(self, parts, *, at_ms):
        result = []
        for part in parts:
            artifact = part.artifact
            assert artifact is not None
            result.append(
                DeliveryPartReceipt(
                    part_id=part.part_id,
                    index=part.index,
                    kind="artifact",
                    artifact_id=artifact.artifact_id,
                    artifact_revision_id=artifact.artifact_revision_id,
                    stage="PLANNED",
                    attempt=1,
                    started_at_ms=at_ms,
                    finished_at_ms=at_ms,
                    evidence_sha256=canonical_sha256(
                        {"part_id": part.part_id, "manifest": artifact.manifest_sha256}
                    ),
                )
            )
        return tuple(result)

    def _receipt(self, payload, *, status, parts, at_ms, error=None):
        receipt = DeliveryReceipt(
            receipt_id="wxfilereceipt_"
            + canonical_sha256(
                {
                    "effect_id": payload.effect_id,
                    "status": status,
                    "parts": [part.model_dump(mode="json") for part in parts],
                }
            ),
            ticket_id=payload.ticket_id,
            delivery_id=payload.delivery_id,
            effect_id=payload.effect_id,
            request_id=payload.request_id,
            run_id=payload.run_id,
            generation=payload.generation,
            channel="wechat",
            status=status,
            parts=parts,
            observed_at_ms=at_ms,
            error_code=error,
            receipt_sha256="0" * 64,
        )
        return receipt.with_computed_receipt_sha256()

    def _finish_failure(
        self,
        payload,
        part,
        remaining,
        completed,
        *,
        error,
        failure_stage,
        retryable,
        started_at,
        attempt,
        evidence,
    ):
        self._stage(payload, part, failure_stage, attempt=attempt, evidence=evidence)
        at_ms = self._clock_ms()
        artifact = part.artifact
        assert artifact is not None
        partial = bool(completed)
        receipt_stage = "AMBIGUOUS" if partial else failure_stage
        status = (
            "RECONCILE_REQUIRED"
            if partial
            else ("FAILED_RETRYABLE" if retryable else "FAILED_FINAL")
        )
        current = DeliveryPartReceipt(
            part_id=part.part_id,
            index=part.index,
            kind="artifact",
            artifact_id=artifact.artifact_id,
            artifact_revision_id=artifact.artifact_revision_id,
            stage=receipt_stage,
            attempt=max(1, attempt),
            started_at_ms=started_at,
            finished_at_ms=at_ms,
            evidence_sha256=evidence,
            error_code=error,
        )
        parts = tuple(completed) + (current,) + self._planned(remaining, at_ms=at_ms)
        receipt = self._receipt(payload, status=status, parts=parts, at_ms=at_ms, error=error)
        return self._ledger.record_receipt(
            receipt,
            side_effect_absent_verified=retryable and not partial,
        ).receipt or receipt

    def _recover_started(self, record: DeliveryLedgerRecord, payload, plan):
        at_ms = self._clock_ms()
        started = record.side_effect_started_at_ms or at_ms
        parts = []
        for part in plan.parts:
            artifact = part.artifact
            assert artifact is not None
            parts.append(
                DeliveryPartReceipt(
                    part_id=part.part_id,
                    index=part.index,
                    kind="artifact",
                    artifact_id=artifact.artifact_id,
                    artifact_revision_id=artifact.artifact_revision_id,
                    stage="AMBIGUOUS",
                    attempt=1,
                    started_at_ms=started,
                    finished_at_ms=at_ms,
                    evidence_sha256=canonical_sha256(
                        {"effect_id": payload.effect_id, "part_id": part.part_id, "restart": True}
                    ),
                    error_code="wechat.file.receipt_missing_after_restart",
                )
            )
        receipt = self._receipt(
            payload,
            status="RECONCILE_REQUIRED",
            parts=tuple(parts),
            at_ms=at_ms,
            error="wechat.file.receipt_missing_after_restart",
        )
        return self._ledger.record_receipt(receipt).receipt or receipt

    def _send_locked(
        self,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
        *,
        policy: WechatOutboundPolicy,
        bot_token: str,
        ilink_account_id: str,
        session_key: str,
    ) -> DeliveryReceipt:
        token = bot_token.strip()
        if not token or token != bot_token or "\x00" in token or len(token.encode()) > 8_192:
            raise WechatFileOutboundError("wechat.file.bot_token.invalid")
        bound = _artifact_only_plan(payload, plan, policy)
        to_user_id = self._sessions.resolve_reply_target(
            session_key=session_key,
            account_id=ilink_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
        )
        context_token = self._sessions.resolve_context_token(
            session_key=session_key,
            account_id=ilink_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
        )
        claimed_at = self._clock_ms()
        if claimed_at < payload.not_before_ms or claimed_at > payload.expires_at_ms:
            raise WechatFileOutboundError("wechat.file.ticket_time.invalid")
        record = self._ledger.require_verified_delivery(payload)
        if record.receipt is not None:
            return record.receipt
        if record.state == "RECONCILE_REQUIRED":
            raise WechatFileOutboundError("wechat.file.reconciliation_required")
        if record.state == "SIDE_EFFECT_STARTED":
            return self._recover_started(record, payload, plan)
        if record.state != "CLAIMED":
            raise WechatFileOutboundError("wechat.file.effect_state.invalid")

        completed = []
        account_key = f"{payload.tenant_id}:{payload.link_account_id}"
        for offset, (part, grant) in enumerate(bound):
            started = self._clock_ms()
            fetched = None
            encrypted = None
            try:
                timeout_decision = compute_wechat_upload_timeout(
                    policy,
                    payload_bytes=grant.size_bytes,
                    ticket_timeout_ms=payload.upload_timeout_ms,
                    observed_throughput_bps=self._transfer_budget.observed_throughput(
                        account_key
                    ),
                )
                if not timeout_decision.allowed:
                    error = "wechat.file.dynamic_timeout.refused"
                    evidence = canonical_sha256(
                        {
                            "error": error,
                            "part_id": part.part_id,
                            "timeout_reason": timeout_decision.reason_code,
                        }
                    )
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_RETRYABLE",
                        retryable=True,
                        started_at=started,
                        attempt=1,
                        evidence=evidence,
                    )
                transfer_timeout_seconds = max(
                    1, (timeout_decision.timeout_ms + 999) // 1_000
                )
                try:
                    fetched = self._fetch(
                        grant,
                        payload=payload,
                        part=part,
                        policy=policy,
                        timeout_seconds=transfer_timeout_seconds,
                    )
                except Exception as exc:
                    error = getattr(exc, "code", "wechat.file.fetch.failed")
                    evidence = canonical_sha256({"error": error, "part_id": part.part_id})
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_RETRYABLE",
                        retryable=True,
                        started_at=started,
                        attempt=1,
                        evidence=evidence,
                    )
                self._stage(
                    payload,
                    part,
                    "FETCHED",
                    attempt=1,
                    evidence=canonical_sha256(
                        {
                            "content_sha256": fetched.sha256,
                            "size_bytes": fetched.size_bytes,
                            "manifest_sha256": grant.artifact_manifest_sha256,
                        }
                    ),
                )
                try:
                    encrypted = self._encrypt(
                        fetched, payload=payload, part=part, policy=policy
                    )
                except Exception as exc:
                    error = getattr(exc, "code", "wechat.file.encrypt.failed")
                    evidence = canonical_sha256({"error": error, "part_id": part.part_id})
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_FINAL",
                        retryable=False,
                        started_at=started,
                        attempt=1,
                        evidence=evidence,
                    )
                self._stage(
                    payload,
                    part,
                    "ENCRYPTED",
                    attempt=1,
                    evidence=canonical_sha256(
                        {
                            "ciphertext_sha256": encrypted.sha256,
                            "ciphertext_size": encrypted.size_bytes,
                            "aes_key_sha256": hashlib.sha256(encrypted.aes_key).hexdigest(),
                        }
                    ),
                )
                side_started_at = self._clock_ms()
                self._ledger.mark_side_effect_started(
                    payload.effect_id, started_at_ms=side_started_at
                )
                filekey = _filekey(payload.effect_id, part.part_id)
                media_type = _media_type(grant)
                try:
                    upload_timeout_decision = compute_wechat_upload_timeout(
                        policy,
                        payload_bytes=encrypted.size_bytes,
                        ticket_timeout_ms=payload.upload_timeout_ms,
                        observed_throughput_bps=self._transfer_budget.observed_throughput(
                            account_key
                        ),
                    )
                    if not upload_timeout_decision.allowed:
                        raise WechatTransferControlError(
                            "wechat.file.dynamic_upload_timeout.refused"
                        )
                    upload_timeout_seconds = max(
                        1, (upload_timeout_decision.timeout_ms + 999) // 1_000
                    )
                    upload_response = self._transport.get_upload_url(
                        self._upload_request_body(
                            to_user_id=to_user_id,
                            media_type=media_type,
                            filekey=filekey,
                            fetched=fetched,
                            encrypted=encrypted,
                        ),
                        bot_token=token,
                        timeout_seconds=upload_timeout_seconds,
                    )
                    if classify_wechat_ilink_response(upload_response) != "accepted":
                        raise WechatFileOutboundError("wechat.file.getuploadurl.rejected")
                    upload_payload = upload_response.payload.get("data", upload_response.payload)
                    if not isinstance(upload_payload, Mapping):
                        raise WechatFileOutboundError("wechat.file.getuploadurl.shape.invalid")
                    upload_param = upload_payload.get("upload_param")
                    upload_full_url = upload_payload.get("upload_full_url")
                    if upload_full_url:
                        if not isinstance(upload_full_url, str):
                            raise WechatFileOutboundError("wechat.file.upload_url.invalid")
                        upload_url = validate_wechat_upload_url(upload_full_url, filekey=filekey)
                    elif upload_param:
                        if not isinstance(upload_param, str):
                            raise WechatFileOutboundError("wechat.file.upload_param.invalid")
                        upload_url = build_wechat_upload_url(upload_param, filekey=filekey)
                    else:
                        raise WechatFileOutboundError("wechat.file.getuploadurl.missing")
                except Exception as exc:
                    error = getattr(exc, "code", "wechat.file.getuploadurl.failed")
                    evidence = canonical_sha256({"error": error, "part_id": part.part_id})
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_RETRYABLE",
                        retryable=True,
                        started_at=started,
                        attempt=1,
                        evidence=evidence,
                    )
                self._stage(
                    payload,
                    part,
                    "UPLOAD_URL_GRANTED",
                    attempt=1,
                    evidence=canonical_sha256(
                        {
                            "response_sha256": upload_response.body_sha256,
                            "upload_url_sha256": hashlib.sha256(upload_url.encode()).hexdigest(),
                            "filekey": filekey,
                        }
                    ),
                )
                try:
                    upload_progress = WechatProgressRecorder(
                        self._ledger,
                        effect_id=payload.effect_id,
                        part_id=part.part_id,
                        part_index=part.index,
                        phase="UPLOAD",
                        total_bytes=encrypted.size_bytes,
                        interval_bytes=policy.progress_interval_bytes,
                        clock_ms=self._clock_ms,
                    )
                    upload_started_at = self._clock_ms()
                    cdn = self._transport.upload_ciphertext(
                        upload_url,
                        encrypted.path,
                        ciphertext_size=encrypted.size_bytes,
                        timeout_seconds=upload_timeout_seconds,
                        max_response_bytes=policy.max_cdn_response_bytes,
                        progress_callback=lambda completed_bytes: upload_progress.update(
                            completed_bytes,
                            force=completed_bytes == encrypted.size_bytes,
                        ),
                    )
                    if cdn.status_code != 200 or not cdn.encrypted_query_param:
                        raise WechatFileOutboundError("wechat.file.cdn_upload.rejected")
                    upload_progress.update(encrypted.size_bytes, force=True)
                    upload_finished_at = self._clock_ms()
                    self._transfer_budget.observe(
                        account_key,
                        bytes_transferred=encrypted.size_bytes,
                        elapsed_ms=max(1, upload_finished_at - upload_started_at),
                    )
                except Exception as exc:
                    error = getattr(exc, "code", "wechat.file.cdn_upload.failed")
                    evidence = canonical_sha256({"error": error, "part_id": part.part_id})
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_RETRYABLE",
                        retryable=True,
                        started_at=started,
                        attempt=1,
                        evidence=evidence,
                    )
                self._stage(
                    payload,
                    part,
                    "UPLOADED",
                    attempt=1,
                    evidence=canonical_sha256(
                        {
                            "cdn_response_sha256": cdn.body_sha256,
                            "ciphertext_sha256": encrypted.sha256,
                            "encrypted_param_sha256": hashlib.sha256(
                                cdn.encrypted_query_param.encode()
                            ).hexdigest(),
                        }
                    ),
                )
                _, item = _media_item(
                    grant,
                    encrypted_query_param=cdn.encrypted_query_param,
                    aes_key=encrypted.aes_key,
                    ciphertext_size=encrypted.size_bytes,
                    md5_hex=fetched.md5_hex,
                )
                client_id = derive_wechat_client_id(payload.effect_id, part.part_id, 0)
                self._stage(
                    payload,
                    part,
                    "SEND_STARTED",
                    attempt=1,
                    evidence=canonical_sha256(
                        {
                            "client_id": client_id,
                            "media_type": media_type,
                            "item_sha256": canonical_sha256(item),
                        }
                    ),
                )
                attempts = 0
                rate_retries = 0
                context_retry = False
                segment_context = context_token
                response_facts = []
                while True:
                    self._rate_gate.wait(
                        f"{payload.tenant_id}:{payload.link_account_id}",
                        minimum_interval_ms=policy.min_attempt_interval_ms,
                    )
                    attempts += 1
                    try:
                        send_response = self._transport.send_message(
                            self._send_body(
                                to_user_id=to_user_id,
                                item=item,
                                context_token=segment_context,
                                run_id=payload.run_id,
                                client_id=client_id,
                            ),
                            bot_token=token,
                            timeout_seconds=max(1, payload.send_timeout_ms // 1_000),
                        )
                        outcome = classify_wechat_ilink_response(send_response)
                        response_facts.append(
                            {
                                "http_status": send_response.status_code,
                                "ret": wechat_response_integer(send_response, "ret"),
                                "errcode": wechat_response_integer(send_response, "errcode"),
                                "body_sha256": send_response.body_sha256,
                                "context_used": bool(segment_context),
                                "context_retry": context_retry,
                                "rate_retries": rate_retries,
                                "client_id": client_id,
                            }
                        )
                    except (WechatTextOutboundError, WechatFileOutboundError) as exc:
                        error = getattr(exc, "code", "wechat.file.send.unknown")
                        evidence = canonical_sha256(
                            {"error": error, "responses": response_facts, "client_id": client_id}
                        )
                        self._stage(
                            payload,
                            part,
                            "AMBIGUOUS",
                            attempt=attempts,
                            evidence=evidence,
                        )
                        at_ms = self._clock_ms()
                        artifact = part.artifact
                        assert artifact is not None
                        current = DeliveryPartReceipt(
                            part_id=part.part_id,
                            index=part.index,
                            kind="artifact",
                            artifact_id=artifact.artifact_id,
                            artifact_revision_id=artifact.artifact_revision_id,
                            stage="AMBIGUOUS",
                            attempt=attempts,
                            started_at_ms=started,
                            finished_at_ms=at_ms,
                            evidence_sha256=evidence,
                            error_code=error,
                        )
                        parts = tuple(completed) + (current,) + self._planned(
                            tuple(item for item, _ in bound[offset + 1 :]), at_ms=at_ms
                        )
                        receipt = self._receipt(
                            payload,
                            status="RECONCILE_REQUIRED",
                            parts=parts,
                            at_ms=at_ms,
                            error=error,
                        )
                        return self._ledger.record_receipt(receipt).receipt or receipt
                    if outcome == "accepted":
                        break
                    if outcome == "context_expired" and segment_context and not context_retry:
                        self._sessions.clear_context_token(session_key=session_key)
                        context_token = None
                        segment_context = None
                        context_retry = True
                        continue
                    if outcome == "rate_limited" and rate_retries < policy.rate_limit_retries:
                        rate_retries += 1
                        self._sleeper(policy.rate_limit_delay_ms * rate_retries / 1_000)
                        continue
                    error = (
                        "wechat.file.send.rate_limit_exhausted"
                        if outcome == "rate_limited"
                        else "wechat.file.send.platform_rejected"
                    )
                    retryable = outcome == "rate_limited"
                    evidence = canonical_sha256(
                        {"error": error, "responses": response_facts, "client_id": client_id}
                    )
                    return self._finish_failure(
                        payload,
                        part,
                        tuple(item for item, _ in bound[offset + 1 :]),
                        completed,
                        error=error,
                        failure_stage="FAILED_RETRYABLE" if retryable else "FAILED_FINAL",
                        retryable=retryable,
                        started_at=started,
                        attempt=attempts,
                        evidence=evidence,
                    )
                platform_sha = canonical_sha256(
                    {
                        "responses": response_facts,
                        "cdn_response_sha256": cdn.body_sha256,
                        "client_id": client_id,
                    }
                )
                self._stage(
                    payload,
                    part,
                    "CHANNEL_ACCEPTED",
                    attempt=attempts,
                    evidence=platform_sha,
                )
                at_ms = self._clock_ms()
                artifact = part.artifact
                assert artifact is not None
                completed.append(
                    DeliveryPartReceipt(
                        part_id=part.part_id,
                        index=part.index,
                        kind="artifact",
                        artifact_id=artifact.artifact_id,
                        artifact_revision_id=artifact.artifact_revision_id,
                        stage="CHANNEL_ACCEPTED",
                        attempt=attempts,
                        started_at_ms=started,
                        finished_at_ms=at_ms,
                        channel_message_ref="wxout_" + platform_sha,
                        evidence_sha256=canonical_sha256(
                            {
                                "artifact_manifest_sha256": artifact.manifest_sha256,
                                "platform_receipt_sha256": platform_sha,
                            }
                        ),
                        platform_receipt_sha256=platform_sha,
                    )
                )
            finally:
                if encrypted is not None:
                    encrypted.path.unlink(missing_ok=True)
                if fetched is not None:
                    fetched.path.unlink(missing_ok=True)
        at_ms = self._clock_ms()
        receipt = self._receipt(
            payload,
            status="CHANNEL_ACCEPTED",
            parts=tuple(completed),
            at_ms=at_ms,
        )
        return self._ledger.record_receipt(receipt).receipt or receipt

    def send(
        self,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
        *,
        policy: WechatOutboundPolicy,
        bot_token: str,
        ilink_account_id: str,
        session_key: str,
    ) -> DeliveryReceipt:
        bound = _artifact_only_plan(payload, plan, policy)
        total_bytes = sum(grant.size_bytes or 0 for _, grant in bound)
        account_key = f"{payload.tenant_id}:{payload.link_account_id}"
        with self._transfer_budget.reserve(
            account_key,
            size_bytes=total_bytes,
            max_concurrent=policy.max_concurrent_files_per_account,
            max_reserved_bytes=policy.max_reserved_bytes_per_account,
        ):
            with self._send_lock:
                return self._send_locked(
                    payload,
                    plan,
                    policy=policy,
                    bot_token=bot_token,
                    ilink_account_id=ilink_account_id,
                    session_key=session_key,
                )


__all__ = [
    "ArtifactContentSource",
    "HttpWechatIlinkFileTransport",
    "WechatCdnUploadResponse",
    "WechatFileDeliveryService",
    "WechatFileOutboundError",
    "WechatIlinkFileTransport",
    "build_wechat_upload_url",
    "validate_wechat_upload_url",
]
