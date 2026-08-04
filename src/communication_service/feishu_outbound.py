"""Ticket-plan-bound Feishu text/card/image/file delivery with durable receipts."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol, Self
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    DeliveryPartGrant,
    DeliveryPartReceipt,
    DeliveryReceipt,
    DeliveryTicketPayload,
    OutboundPart,
    OutboundPlan,
    OutboundScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_outbound_scope_keys,
    grant_from_outbound_part,
)

from .delivery_ledger import DeliveryLedger, DeliveryLedgerRecord, DeliveryPartStageFact
from .feishu_route import FeishuRouteLedger, derive_feishu_route_key
from .wechat_file_outbound import ArtifactContentSource


FEISHU_OPEN_API_HOST = "open.feishu.cn"
FEISHU_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGES_PATH = "/open-apis/im/v1/messages"
FEISHU_IMAGES_PATH = "/open-apis/im/v1/images"
FEISHU_FILES_PATH = "/open-apis/im/v1/files"
_AUTH_ERROR_CODES = frozenset({99991661, 99991663, 99991664, 99991668})
_RATE_ERROR_CODES = frozenset({99991400, 99991401})


class FeishuOutboundError(RuntimeError):
    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown


class FeishuOutboundPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_id: str = Field(default="feishu.im.delivery.v1", pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    text_mode: Literal["text", "interactive_card"] = "text"
    max_text_bytes: int = Field(default=30_000, ge=1, le=100_000)
    max_image_bytes: int = Field(default=10_485_760, ge=1, le=30_000_000)
    max_file_bytes: int = Field(default=30_000_000, ge=1, le=2_147_483_648)
    max_parts: int = Field(default=20, ge=1, le=50)
    file_chunk_bytes: int = Field(default=262_144, ge=4_096, le=4_194_304)
    max_response_bytes: int = Field(default=1_048_576, ge=1_024, le=8_388_608)
    token_refresh_skew_ms: int = Field(default=60_000, ge=1_000, le=600_000)
    auth_retries: int = Field(default=1, ge=0, le=2)
    rate_limit_retries: int = Field(default=2, ge=0, le=5)
    rate_limit_delay_ms: int = Field(default=1_000, ge=100, le=60_000)
    reply_in_thread: bool = True
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.max_image_bytes > self.max_file_bytes:
            raise ValueError("Feishu image limit exceeds file limit")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


def default_feishu_outbound_policy() -> FeishuOutboundPolicy:
    return FeishuOutboundPolicy(policy_sha256="0" * 64).with_computed_sha256()


@dataclass(frozen=True)
class FeishuCredentials:
    app_id: str
    app_secret: str

    def __post_init__(self) -> None:
        for value, name, limit in (
            (self.app_id, "app_id", 512),
            (self.app_secret, "app_secret", 8_192),
        ):
            if (
                not value
                or value != value.strip()
                or "\x00" in value
                or "\r" in value
                or "\n" in value
                or len(value.encode("utf-8")) > limit
            ):
                raise ValueError(f"Feishu {name} is invalid")


@dataclass(frozen=True)
class FeishuTokenResult:
    status_code: int
    code: int | None
    access_token: str | None
    expires_in_seconds: int | None
    body_sha256: str
    retry_after_ms: int | None = None


@dataclass(frozen=True)
class FeishuApiResponse:
    status_code: int
    code: int | None
    data: Mapping[str, Any]
    body_sha256: str
    retry_after_ms: int | None = None

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("Feishu HTTP status is invalid")
        if self.code is not None and (isinstance(self.code, bool) or not isinstance(self.code, int)):
            raise ValueError("Feishu response code is invalid")
        if len(self.body_sha256) != 64:
            raise ValueError("Feishu response digest is invalid")
        object.__setattr__(self, "data", dict(self.data))


class FeishuApiTransport(Protocol):
    def fetch_tenant_token(
        self, credentials: FeishuCredentials, *, timeout_seconds: int
    ) -> FeishuTokenResult: ...

    def send_message(
        self,
        *,
        chat_id: str,
        reply_to_message_id: str | None,
        reply_in_thread: bool,
        msg_type: str,
        content: Mapping[str, Any],
        dedup_uuid: str,
        access_token: str,
        timeout_seconds: int,
    ) -> FeishuApiResponse: ...

    def upload_artifact(
        self,
        artifact: "FetchedFeishuArtifact",
        grant: DeliveryPartGrant,
        *,
        as_image: bool,
        access_token: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> FeishuApiResponse: ...


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _retry_after_ms(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value.isdecimal():
        return None
    seconds = int(value)
    return min(seconds * 1_000, 3_600_000)


class HttpFeishuApiTransport:
    """Direct fixed-host Feishu HTTPS transport without redirects, proxies, or cookies."""

    def __init__(self, *, max_response_bytes: int = 1_048_576) -> None:
        if not 1_024 <= max_response_bytes <= 8_388_608:
            raise ValueError("Feishu response limit is invalid")
        self._max_response_bytes = max_response_bytes

    def _read_response(
        self,
        response: http.client.HTTPResponse,
        *,
        max_bytes: int | None = None,
    ) -> tuple[Mapping[str, Any], str, int | None]:
        limit = self._max_response_bytes if max_bytes is None else max_bytes
        declared = response.getheader("Content-Length")
        if declared is not None and (not declared.isdecimal() or int(declared) > limit):
            raise FeishuOutboundError("feishu.response.size.invalid", outcome_unknown=True)
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise FeishuOutboundError("feishu.response.too_large", outcome_unknown=True)
        if not raw:
            payload: Mapping[str, Any] = {}
        else:
            try:
                parsed = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise FeishuOutboundError(
                    "feishu.response.json.invalid", outcome_unknown=True
                ) from exc
            if not isinstance(parsed, Mapping):
                raise FeishuOutboundError(
                    "feishu.response.shape.invalid", outcome_unknown=True
                )
            payload = parsed
        return (
            payload,
            hashlib.sha256(raw).hexdigest(),
            _retry_after_ms(response.getheader("Retry-After")),
        )

    @staticmethod
    def _code(payload: Mapping[str, Any]) -> int | None:
        value = payload.get("code")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise FeishuOutboundError("feishu.response.code.invalid", outcome_unknown=True)
        return value

    def _post_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        access_token: str | None,
        timeout_seconds: int,
    ) -> FeishuApiResponse:
        if (
            not path.startswith("/open-apis/")
            or "\r" in path
            or "\n" in path
            or not 1 <= timeout_seconds <= 3_600
        ):
            raise FeishuOutboundError("feishu.request.route_or_timeout.invalid")
        request = canonical_json_bytes(dict(body))
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(request)),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "TiangongCommunication/3",
        }
        if access_token is not None:
            if not access_token or access_token != access_token.strip() or "\n" in access_token:
                raise FeishuOutboundError("feishu.access_token.invalid")
            headers["Authorization"] = "Bearer " + access_token
        connection = http.client.HTTPSConnection(
            FEISHU_OPEN_API_HOST, 443, timeout=timeout_seconds
        )
        response = None
        try:
            connection.request("POST", path, body=request, headers=headers)
            response = connection.getresponse()
            payload, digest, retry_after = self._read_response(response)
            data = payload.get("data", {})
            if not isinstance(data, Mapping):
                data = {}
            return FeishuApiResponse(
                response.status,
                self._code(payload),
                data,
                digest,
                retry_after,
            )
        except FeishuOutboundError:
            raise
        except Exception as exc:
            raise FeishuOutboundError(
                "feishu.transport.unknown", outcome_unknown=True
            ) from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def fetch_tenant_token(
        self, credentials: FeishuCredentials, *, timeout_seconds: int
    ) -> FeishuTokenResult:
        connection = http.client.HTTPSConnection(
            FEISHU_OPEN_API_HOST, 443, timeout=timeout_seconds
        )
        http_response = None
        request = canonical_json_bytes(
            {"app_id": credentials.app_id, "app_secret": credentials.app_secret}
        )
        try:
            connection.request(
                "POST",
                FEISHU_TOKEN_PATH,
                body=request,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(request)),
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "TiangongCommunication/3",
                },
            )
            http_response = connection.getresponse()
            payload, digest, retry_after = self._read_response(http_response)
            token = payload.get("tenant_access_token")
            expire = payload.get("expire")
            return FeishuTokenResult(
                status_code=http_response.status,
                code=self._code(payload),
                access_token=token if isinstance(token, str) else None,
                expires_in_seconds=expire
                if isinstance(expire, int) and not isinstance(expire, bool)
                else None,
                body_sha256=digest,
                retry_after_ms=retry_after,
            )
        except FeishuOutboundError:
            raise
        except Exception as exc:
            raise FeishuOutboundError(
                "feishu.token.transport.unknown", outcome_unknown=False
            ) from exc
        finally:
            if http_response is not None:
                http_response.close()
            connection.close()

    def send_message(
        self,
        *,
        chat_id: str,
        reply_to_message_id: str | None,
        reply_in_thread: bool,
        msg_type: str,
        content: Mapping[str, Any],
        dedup_uuid: str,
        access_token: str,
        timeout_seconds: int,
    ) -> FeishuApiResponse:
        if (
            not dedup_uuid
            or len(dedup_uuid) > 50
            or dedup_uuid != dedup_uuid.strip()
            or "\x00" in dedup_uuid
        ):
            raise FeishuOutboundError("feishu.send.dedup_uuid.invalid")
        content_json = canonical_json_bytes(dict(content)).decode("utf-8")
        if reply_to_message_id is None:
            path = FEISHU_MESSAGES_PATH + "?receive_id_type=chat_id"
            body = {
                "receive_id": chat_id,
                "msg_type": msg_type,
                "content": content_json,
                "uuid": dedup_uuid,
            }
        else:
            path = (
                FEISHU_MESSAGES_PATH
                + "/"
                + quote(reply_to_message_id, safe="")
                + "/reply"
            )
            body = {
                "msg_type": msg_type,
                "content": content_json,
                "reply_in_thread": reply_in_thread,
                "uuid": dedup_uuid,
            }
        return self._post_json(
            path,
            body,
            access_token=access_token,
            timeout_seconds=timeout_seconds,
        )

    def upload_artifact(
        self,
        artifact: "FetchedFeishuArtifact",
        grant: DeliveryPartGrant,
        *,
        as_image: bool,
        access_token: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> FeishuApiResponse:
        assert grant.filename is not None and grant.mime is not None
        if (
            not access_token
            or access_token != access_token.strip()
            or "\r" in access_token
            or "\n" in access_token
            or len(access_token.encode("utf-8")) > 8_192
            or not artifact.path.is_absolute()
            or artifact.path.is_symlink()
            or not artifact.path.is_file()
            or artifact.path.stat().st_size != artifact.size_bytes
        ):
            raise FeishuOutboundError("feishu.upload.source.invalid")
        boundary = "Tiangong" + secrets.token_hex(16)
        fields = (
            (("image_type", "message"),)
            if as_image
            else (("file_type", "stream"), ("file_name", grant.filename))
        )
        prefix = bytearray()
        for name, value in fields:
            prefix.extend(f"--{boundary}\r\n".encode())
            prefix.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(
                    "utf-8"
                )
            )
        upload_name = "image" if as_image else "file"
        prefix.extend(f"--{boundary}\r\n".encode())
        prefix.extend(
            (
                f'Content-Disposition: form-data; name="{upload_name}"; '
                f'filename="artifact"\r\nContent-Type: {grant.mime}\r\n\r\n'
            ).encode("utf-8")
        )
        suffix = f"\r\n--{boundary}--\r\n".encode()
        total = len(prefix) + artifact.size_bytes + len(suffix)
        connection = http.client.HTTPSConnection(
            FEISHU_OPEN_API_HOST, 443, timeout=timeout_seconds
        )
        response = None
        try:
            connection.putrequest("POST", FEISHU_IMAGES_PATH if as_image else FEISHU_FILES_PATH)
            connection.putheader("Authorization", "Bearer " + access_token)
            connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
            connection.putheader("Content-Length", str(total))
            connection.putheader("Accept", "application/json")
            connection.putheader("Accept-Encoding", "identity")
            connection.putheader("Connection", "close")
            connection.putheader("User-Agent", "TiangongCommunication/3")
            connection.endheaders()
            connection.send(prefix)
            with artifact.path.open("rb") as source:
                while True:
                    chunk = source.read(262_144)
                    if not chunk:
                        break
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            payload, digest, retry_after = self._read_response(
                response, max_bytes=max_response_bytes
            )
            data = payload.get("data", {})
            return FeishuApiResponse(
                response.status,
                self._code(payload),
                data if isinstance(data, Mapping) else {},
                digest,
                retry_after,
            )
        except FeishuOutboundError:
            raise
        except Exception as exc:
            raise FeishuOutboundError(
                "feishu.upload.transport.unknown", outcome_unknown=True
            ) from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


@dataclass
class _TokenState:
    token: str | None = None
    expires_at_ms: int = 0
    refreshing: bool = False
    credentials_sha256: str | None = None


class FeishuTokenProvider:
    def __init__(
        self,
        transport: FeishuApiTransport,
        *,
        clock_ms: Callable[[], int] | None = None,
        refresh_skew_ms: int = 60_000,
    ) -> None:
        if not 1_000 <= refresh_skew_ms <= 600_000:
            raise ValueError("Feishu token refresh skew is invalid")
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._skew_ms = refresh_skew_ms
        self._condition = threading.Condition()
        self._states: dict[str, _TokenState] = {}

    def get_token(
        self,
        account_key: str,
        credentials: FeishuCredentials,
        *,
        timeout_seconds: int,
        force_if_token: str | None = None,
    ) -> str:
        if not account_key or len(account_key) > 512:
            raise ValueError("Feishu token account key is invalid")
        credentials_sha256 = canonical_sha256(
            {
                "app_id": credentials.app_id,
                "app_secret_sha256": hashlib.sha256(
                    credentials.app_secret.encode("utf-8")
                ).hexdigest(),
            }
        )
        while True:
            with self._condition:
                state = self._states.setdefault(account_key, _TokenState())
                if (
                    state.credentials_sha256 is not None
                    and state.credentials_sha256 != credentials_sha256
                ):
                    raise FeishuOutboundError("feishu.token.credentials_changed")
                state.credentials_sha256 = credentials_sha256
                now = self._clock_ms()
                current_is_newer = (
                    force_if_token is not None
                    and state.token is not None
                    and state.token != force_if_token
                    and now + self._skew_ms < state.expires_at_ms
                )
                if current_is_newer or (
                    force_if_token is None
                    and state.token is not None
                    and now + self._skew_ms < state.expires_at_ms
                ):
                    return state.token
                if state.refreshing:
                    self._condition.wait(timeout=max(1, timeout_seconds))
                    continue
                state.refreshing = True
                break
        try:
            result = self._transport.fetch_tenant_token(
                credentials, timeout_seconds=timeout_seconds
            )
            token = result.access_token
            expires = result.expires_in_seconds
            if (
                result.status_code != 200
                or result.code != 0
                or token is None
                or token != token.strip()
                or not token
                or "\x00" in token
                or expires is None
                or not 60 <= expires <= 86_400
            ):
                raise FeishuOutboundError("feishu.token.refresh.rejected")
            expires_at = self._clock_ms() + expires * 1_000
            with self._condition:
                state = self._states[account_key]
                state.token = token
                state.expires_at_ms = expires_at
                state.refreshing = False
                self._condition.notify_all()
            return token
        except Exception:
            with self._condition:
                self._states[account_key].refreshing = False
                self._condition.notify_all()
            raise


@dataclass(frozen=True)
class FetchedFeishuArtifact:
    path: Path
    size_bytes: int
    sha256: str


def _message_ref(message_id: str) -> str:
    return "fsmsg_" + hashlib.sha256(message_id.encode("utf-8")).hexdigest()


def derive_feishu_dedup_uuid(effect_id: str, part_id: str) -> str:
    return "tg-" + canonical_sha256(
        {
            "domain": "tiangong.communication.feishu-dedup-uuid.v1",
            "effect_id": effect_id,
            "part_id": part_id,
        }
    )[:47]


def _bind_plan(
    payload: DeliveryTicketPayload,
    plan: OutboundPlan,
    policy: FeishuOutboundPolicy,
) -> tuple[tuple[OutboundPart, DeliveryPartGrant], ...]:
    if not policy.has_valid_sha256() or plan.channel_policy_hash != policy.policy_sha256:
        raise FeishuOutboundError("feishu.send.policy.mismatch")
    if not plan.has_valid_plan_sha256() or payload.outbound_plan_sha256 != plan.plan_sha256:
        raise FeishuOutboundError("feishu.send.plan_digest.mismatch")
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
        raise FeishuOutboundError("feishu.send.ticket_plan.mismatch")
    if payload.channel != "feishu" or len(plan.parts) > policy.max_parts:
        raise FeishuOutboundError("feishu.send.channel_or_part_limit.invalid")
    expected = tuple(grant_from_outbound_part(part) for part in plan.parts)
    if payload.parts != expected:
        raise FeishuOutboundError("feishu.send.parts.mismatch")
    scope = derive_outbound_scope_keys(
        OutboundScope(
            channel="feishu",
            tenant_id=plan.tenant_id,
            link_account_id=plan.link_account_id,
            conversation_ref=plan.conversation_ref,
            recipient_ref=plan.conversation_ref,
            reply_to_message_ref=plan.reply_to_message_ref,
        )
    )
    if (
        scope.conversation_scope_hash != plan.conversation_scope_hash
        or scope.recipient_scope_hash != plan.recipient_scope_hash
    ):
        raise FeishuOutboundError("feishu.send.recipient_scope.mismatch")
    result = []
    for part, grant in zip(plan.parts, payload.parts, strict=True):
        if part.kind == "text":
            assert part.text is not None
            if len(part.text.encode("utf-8")) > policy.max_text_bytes:
                raise FeishuOutboundError("feishu.send.text_limit.exceeded")
        else:
            assert grant.size_bytes is not None and grant.mime is not None
            maximum = policy.max_image_bytes if grant.mime.startswith("image/") else policy.max_file_bytes
            if grant.size_bytes > maximum:
                raise FeishuOutboundError("feishu.send.file_limit.exceeded")
        result.append((part, grant))
    return tuple(result)


def _classification(response: FeishuApiResponse) -> Literal[
    "accepted", "unauthorized", "rate_limited", "rejected_final", "ambiguous"
]:
    if response.status_code == 429 or response.code in _RATE_ERROR_CODES:
        return "rate_limited"
    if response.status_code in {401, 403} or response.code in _AUTH_ERROR_CODES:
        return "unauthorized"
    if 200 <= response.status_code < 300 and response.code == 0:
        return "accepted"
    if 400 <= response.status_code < 500 or response.code is not None:
        return "rejected_final"
    return "ambiguous"


class FeishuDeliveryService:
    def __init__(
        self,
        ledger: DeliveryLedger,
        routes: FeishuRouteLedger,
        source: ArtifactContentSource,
        transport: FeishuApiTransport,
        tokens: FeishuTokenProvider,
        *,
        staging_root: Path,
        clock_ms: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not staging_root.is_absolute() or staging_root.is_symlink():
            raise ValueError("Feishu outbound staging root is unsafe")
        staging_root.mkdir(parents=True, exist_ok=True)
        self._ledger = ledger
        self._routes = routes
        self._source = source
        self._transport = transport
        self._tokens = tokens
        self._staging_root = staging_root.resolve(strict=True)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._sleeper = sleeper
        self._lock = threading.RLock()

    def _stage(self, payload, part, stage, *, evidence, attempt=1):
        return self._ledger.record_part_stage(
            DeliveryPartStageFact(
                effect_id=payload.effect_id,
                part_id=part.part_id,
                part_index=part.index,
                kind=part.kind,
                stage=stage,
                attempt=attempt,
                occurred_at_ms=self._clock_ms(),
                evidence_sha256=evidence,
                stage_fact_sha256="0" * 64,
            ).with_computed_sha256()
        )

    def _fetch(self, payload, part, grant, policy) -> FetchedFeishuArtifact:
        assert grant.size_bytes is not None and grant.content_sha256 is not None
        path = self._staging_root / (
            f"{payload.effect_id[-16:]}.{part.index}.{secrets.token_hex(8)}.plain.part"
        )
        digest = hashlib.sha256()
        total = 0
        try:
            stream = self._source.open_artifact(
                grant, timeout_seconds=max(1, payload.upload_timeout_ms // 1_000)
            )
            with closing(stream), path.open("xb") as target:
                os.chmod(path, 0o600)
                while True:
                    chunk = stream.read(policy.file_chunk_bytes)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > grant.size_bytes:
                        raise FeishuOutboundError("feishu.file.source_size.mismatch")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if total != grant.size_bytes or digest.hexdigest() != grant.content_sha256:
                raise FeishuOutboundError("feishu.file.source_digest.mismatch")
            return FetchedFeishuArtifact(path, total, digest.hexdigest())
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _content(part: OutboundPart, policy: FeishuOutboundPolicy) -> tuple[str, Mapping[str, Any]]:
        assert part.text is not None
        if policy.text_mode == "text":
            return "text", {"text": part.text}
        return (
            "interactive",
            {
                "config": {"wide_screen_mode": True},
                "elements": [{"tag": "markdown", "content": part.text}],
            },
        )

    def _call(
        self,
        callback,
        *,
        account_key: str,
        credentials: FeishuCredentials,
        token: str,
        policy: FeishuOutboundPolicy,
        timeout_seconds: int,
        deadline_ms: int,
    ) -> tuple[FeishuApiResponse, str, int]:
        auth_retries = 0
        rate_retries = 0
        attempts = 0
        while True:
            if self._clock_ms() > deadline_ms:
                raise FeishuOutboundError("feishu.send.ticket_expired_before_attempt")
            attempts += 1
            response = callback(token)
            classification = _classification(response)
            if classification == "unauthorized" and auth_retries < policy.auth_retries:
                auth_retries += 1
                token = self._tokens.get_token(
                    account_key,
                    credentials,
                    timeout_seconds=timeout_seconds,
                    force_if_token=token,
                )
                continue
            if classification == "rate_limited" and rate_retries < policy.rate_limit_retries:
                rate_retries += 1
                delay = max(
                    response.retry_after_ms or 0,
                    policy.rate_limit_delay_ms * rate_retries,
                )
                self._sleeper(delay / 1_000)
                continue
            return response, token, attempts

    @staticmethod
    def _planned(parts, *, at_ms):
        result = []
        for part in parts:
            artifact = part.artifact
            result.append(
                DeliveryPartReceipt(
                    part_id=part.part_id,
                    index=part.index,
                    kind=part.kind,
                    artifact_id=None if artifact is None else artifact.artifact_id,
                    artifact_revision_id=None
                    if artifact is None
                    else artifact.artifact_revision_id,
                    stage="PLANNED",
                    attempt=1,
                    started_at_ms=at_ms,
                    finished_at_ms=at_ms,
                    evidence_sha256=canonical_sha256(
                        {"part_id": part.part_id, "stage": "PLANNED"}
                    ),
                )
            )
        return tuple(result)

    @staticmethod
    def _receipt(payload, *, status, parts, at_ms, error=None):
        return DeliveryReceipt(
            receipt_id="fsreceipt_"
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
            channel="feishu",
            status=status,
            parts=parts,
            observed_at_ms=at_ms,
            error_code=error,
            receipt_sha256="0" * 64,
        ).with_computed_receipt_sha256()

    def _recover(self, record: DeliveryLedgerRecord, payload, plan):
        now = self._clock_ms()
        started = record.side_effect_started_at_ms or now
        parts = []
        for part in plan.parts:
            artifact = part.artifact
            parts.append(
                DeliveryPartReceipt(
                    part_id=part.part_id,
                    index=part.index,
                    kind=part.kind,
                    artifact_id=None if artifact is None else artifact.artifact_id,
                    artifact_revision_id=None
                    if artifact is None
                    else artifact.artifact_revision_id,
                    stage="AMBIGUOUS",
                    attempt=1,
                    started_at_ms=started,
                    finished_at_ms=now,
                    evidence_sha256=canonical_sha256(
                        {"effect_id": payload.effect_id, "part_id": part.part_id, "restart": True}
                    ),
                    error_code="feishu.send.receipt_missing_after_restart",
                )
            )
        receipt = self._receipt(
            payload,
            status="RECONCILE_REQUIRED",
            parts=tuple(parts),
            at_ms=now,
            error="feishu.send.receipt_missing_after_restart",
        )
        return self._ledger.record_receipt(receipt).receipt or receipt

    def _failure(
        self,
        payload,
        current,
        remaining,
        completed,
        *,
        started,
        attempts,
        error,
        requested_status,
        evidence,
        side_effect_absent,
    ):
        partial = bool(completed)
        ambiguous = requested_status == "RECONCILE_REQUIRED" or partial
        stage = (
            "AMBIGUOUS"
            if ambiguous
            else "FAILED_RETRYABLE"
            if requested_status == "FAILED_RETRYABLE"
            else "FAILED_FINAL"
        )
        try:
            self._stage(payload, current, stage, evidence=evidence, attempt=max(1, attempts))
        except Exception:
            if stage != "AMBIGUOUS":
                raise
        artifact = current.artifact
        now = self._clock_ms()
        current_receipt = DeliveryPartReceipt(
            part_id=current.part_id,
            index=current.index,
            kind=current.kind,
            artifact_id=None if artifact is None else artifact.artifact_id,
            artifact_revision_id=None if artifact is None else artifact.artifact_revision_id,
            stage=stage,
            attempt=max(1, attempts),
            started_at_ms=started,
            finished_at_ms=now,
            evidence_sha256=evidence,
            error_code=error,
        )
        status = "RECONCILE_REQUIRED" if ambiguous else requested_status
        parts = tuple(completed) + (current_receipt,) + self._planned(remaining, at_ms=now)
        receipt = self._receipt(
            payload, status=status, parts=parts, at_ms=now, error=error
        )
        return self._ledger.record_receipt(
            receipt,
            side_effect_absent_verified=(
                status == "FAILED_RETRYABLE" and side_effect_absent
            ),
        ).receipt or receipt

    def send(
        self,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
        *,
        policy: FeishuOutboundPolicy,
        credentials: FeishuCredentials,
    ) -> DeliveryReceipt:
        bound = _bind_plan(payload, plan, policy)
        route_key = derive_feishu_route_key(
            payload.tenant_id,
            payload.link_account_id,
            payload.conversation_scope_hash,
        )
        route = self._routes.resolve(
            route_key=route_key,
            tenant_id=payload.tenant_id,
            link_account_id=payload.link_account_id,
            conversation_scope_hash=payload.conversation_scope_hash,
        )
        reply_to = None
        if plan.reply_to_message_ref is not None:
            if plan.reply_to_message_ref != _message_ref(route.message_id):
                raise FeishuOutboundError("feishu.send.reply_target.mismatch")
            reply_to = route.message_id
        account_key = f"{payload.tenant_id}:{payload.link_account_id}"
        timeout_seconds = max(1, payload.send_timeout_ms // 1_000)
        with self._lock:
            claimed_at = self._clock_ms()
            if claimed_at < payload.not_before_ms or claimed_at > payload.expires_at_ms:
                raise FeishuOutboundError("feishu.send.ticket_time.invalid")
            record = self._ledger.require_verified_delivery(payload)
            if record.receipt is not None:
                return record.receipt
            if record.state == "RECONCILE_REQUIRED":
                raise FeishuOutboundError("feishu.send.reconciliation_required")
            if record.state == "SIDE_EFFECT_STARTED":
                return self._recover(record, payload, plan)
            if record.state != "CLAIMED":
                raise FeishuOutboundError("feishu.send.effect_state.invalid")
            token = self._tokens.get_token(
                account_key, credentials, timeout_seconds=timeout_seconds
            )
            completed = []
            side_effect_started = False
            for offset, (part, grant) in enumerate(bound):
                started = self._clock_ms()
                fetched = None
                attempts = 0
                try:
                    if part.kind == "artifact":
                        try:
                            fetched = self._fetch(payload, part, grant, policy)
                        except Exception as exc:
                            error = getattr(exc, "code", "feishu.file.fetch.failed")
                            evidence = canonical_sha256(
                                {"part_id": part.part_id, "error": error}
                            )
                            return self._failure(
                                payload,
                                part,
                                tuple(item for item, _ in bound[offset + 1 :]),
                                completed,
                                started=started,
                                attempts=1,
                                error=error,
                                requested_status="FAILED_RETRYABLE",
                                evidence=evidence,
                                side_effect_absent=True,
                            )
                        self._stage(
                            payload,
                            part,
                            "FETCHED",
                            evidence=canonical_sha256(
                                {
                                    "part_id": part.part_id,
                                    "sha256": fetched.sha256,
                                    "size": fetched.size_bytes,
                                }
                            ),
                        )
                        self._stage(
                            payload,
                            part,
                            "READY_TO_UPLOAD",
                            evidence=canonical_sha256(
                                {"part_id": part.part_id, "manifest": grant.artifact_manifest_sha256}
                            ),
                        )
                    if self._clock_ms() > payload.expires_at_ms:
                        error = "feishu.send.ticket_expired_before_attempt"
                        evidence = canonical_sha256(
                            {"part_id": part.part_id, "error": error}
                        )
                        return self._failure(
                            payload,
                            part,
                            tuple(item for item, _ in bound[offset + 1 :]),
                            completed,
                            started=started,
                            attempts=1,
                            error=error,
                            requested_status="FAILED_RETRYABLE",
                            evidence=evidence,
                            side_effect_absent=True,
                        )
                    if not side_effect_started:
                        self._ledger.mark_side_effect_started(
                            payload.effect_id, started_at_ms=self._clock_ms()
                        )
                        side_effect_started = True
                    if part.kind == "artifact":
                        assert fetched is not None and grant.mime is not None
                        as_image = grant.mime.startswith("image/")
                        try:
                            upload, token, attempts = self._call(
                                lambda current: self._transport.upload_artifact(
                                    fetched,
                                    grant,
                                    as_image=as_image,
                                    access_token=current,
                                    timeout_seconds=max(1, payload.upload_timeout_ms // 1_000),
                                    max_response_bytes=policy.max_response_bytes,
                                ),
                                account_key=account_key,
                                credentials=credentials,
                                token=token,
                                policy=policy,
                                timeout_seconds=timeout_seconds,
                                deadline_ms=payload.expires_at_ms,
                            )
                        except FeishuOutboundError as exc:
                            evidence = canonical_sha256(
                                {"part_id": part.part_id, "error": exc.code, "phase": "upload"}
                            )
                            return self._failure(
                                payload,
                                part,
                                tuple(item for item, _ in bound[offset + 1 :]),
                                completed,
                                started=started,
                                attempts=max(1, attempts),
                                error=exc.code,
                                requested_status="FAILED_RETRYABLE",
                                evidence=evidence,
                                side_effect_absent=True,
                            )
                        upload_class = _classification(upload)
                        if upload_class != "accepted":
                            error = (
                                "feishu.upload.rate_limit_exhausted"
                                if upload_class == "rate_limited"
                                else "feishu.upload.rejected"
                            )
                            status = (
                                "FAILED_RETRYABLE"
                                if upload_class in {"rate_limited", "ambiguous"}
                                else "FAILED_FINAL"
                            )
                            evidence = canonical_sha256(
                                {"part_id": part.part_id, "response": upload.body_sha256, "error": error}
                            )
                            return self._failure(
                                payload,
                                part,
                                tuple(item for item, _ in bound[offset + 1 :]),
                                completed,
                                started=started,
                                attempts=attempts,
                                error=error,
                                requested_status=status,
                                evidence=evidence,
                                side_effect_absent=True,
                            )
                        key_name = "image_key" if as_image else "file_key"
                        resource_key = upload.data.get(key_name)
                        if not isinstance(resource_key, str) or not resource_key:
                            error = "feishu.upload.resource_key.missing"
                            evidence = canonical_sha256(
                                {"part_id": part.part_id, "response": upload.body_sha256, "error": error}
                            )
                            return self._failure(
                                payload,
                                part,
                                tuple(item for item, _ in bound[offset + 1 :]),
                                completed,
                                started=started,
                                attempts=attempts,
                                error=error,
                                requested_status="FAILED_RETRYABLE",
                                evidence=evidence,
                                side_effect_absent=True,
                            )
                        self._stage(
                            payload,
                            part,
                            "UPLOADED",
                            evidence=canonical_sha256(
                                {"part_id": part.part_id, "response": upload.body_sha256, "key_sha256": hashlib.sha256(resource_key.encode()).hexdigest()}
                            ),
                        )
                        msg_type = "image" if as_image else "file"
                        content = {key_name: resource_key}
                    else:
                        msg_type, content = self._content(part, policy)
                    self._stage(
                        payload,
                        part,
                        "SEND_STARTED",
                        evidence=canonical_sha256(
                            {"part_id": part.part_id, "msg_type": msg_type, "reply": bool(reply_to)}
                        ),
                    )
                    try:
                        response, token, send_attempts = self._call(
                            lambda current: self._transport.send_message(
                                chat_id=route.chat_id,
                                reply_to_message_id=reply_to,
                                reply_in_thread=policy.reply_in_thread
                                and bool(route.thread_id or route.root_id),
                                msg_type=msg_type,
                                content=content,
                                dedup_uuid=derive_feishu_dedup_uuid(
                                    payload.effect_id, part.part_id
                                ),
                                access_token=current,
                                timeout_seconds=timeout_seconds,
                            ),
                            account_key=account_key,
                            credentials=credentials,
                            token=token,
                                policy=policy,
                                timeout_seconds=timeout_seconds,
                                deadline_ms=payload.expires_at_ms,
                        )
                        attempts += send_attempts
                    except FeishuOutboundError as exc:
                        evidence = canonical_sha256(
                            {"part_id": part.part_id, "error": exc.code, "phase": "send"}
                        )
                        return self._failure(
                            payload,
                            part,
                            tuple(item for item, _ in bound[offset + 1 :]),
                            completed,
                            started=started,
                            attempts=max(1, attempts),
                            error=exc.code,
                            requested_status=(
                                "RECONCILE_REQUIRED"
                                if exc.outcome_unknown
                                else "FAILED_RETRYABLE"
                            ),
                            evidence=evidence,
                            side_effect_absent=not exc.outcome_unknown,
                        )
                    send_class = _classification(response)
                    if send_class != "accepted":
                        if send_class == "ambiguous":
                            status = "RECONCILE_REQUIRED"
                            error = "feishu.send.platform_outcome_unknown"
                            absent = False
                        elif send_class == "rate_limited":
                            status = "FAILED_RETRYABLE"
                            error = "feishu.send.rate_limit_exhausted"
                            absent = True
                        else:
                            status = "FAILED_FINAL"
                            error = "feishu.send.platform_rejected"
                            absent = True
                        evidence = canonical_sha256(
                            {"part_id": part.part_id, "response": response.body_sha256, "error": error}
                        )
                        return self._failure(
                            payload,
                            part,
                            tuple(item for item, _ in bound[offset + 1 :]),
                            completed,
                            started=started,
                            attempts=max(1, attempts),
                            error=error,
                            requested_status=status,
                            evidence=evidence,
                            side_effect_absent=absent,
                        )
                    message_id = response.data.get("message_id")
                    if not isinstance(message_id, str) or not message_id:
                        error = "feishu.send.message_id.missing"
                        evidence = canonical_sha256(
                            {"part_id": part.part_id, "response": response.body_sha256, "error": error}
                        )
                        return self._failure(
                            payload,
                            part,
                            tuple(item for item, _ in bound[offset + 1 :]),
                            completed,
                            started=started,
                            attempts=max(1, attempts),
                            error=error,
                            requested_status="RECONCILE_REQUIRED",
                            evidence=evidence,
                            side_effect_absent=False,
                        )
                    evidence = canonical_sha256(
                        {"part_id": part.part_id, "response": response.body_sha256, "message_ref": _message_ref(message_id)}
                    )
                    self._stage(
                        payload, part, "CHANNEL_ACCEPTED", evidence=evidence, attempt=max(1, attempts)
                    )
                    artifact = part.artifact
                    completed.append(
                        DeliveryPartReceipt(
                            part_id=part.part_id,
                            index=part.index,
                            kind=part.kind,
                            artifact_id=None if artifact is None else artifact.artifact_id,
                            artifact_revision_id=None
                            if artifact is None
                            else artifact.artifact_revision_id,
                            stage="CHANNEL_ACCEPTED",
                            attempt=max(1, attempts),
                            started_at_ms=started,
                            finished_at_ms=self._clock_ms(),
                            channel_message_ref=_message_ref(message_id),
                            evidence_sha256=evidence,
                            platform_receipt_sha256=response.body_sha256,
                        )
                    )
                finally:
                    if fetched is not None:
                        fetched.path.unlink(missing_ok=True)
            now = self._clock_ms()
            receipt = self._receipt(
                payload,
                status="CHANNEL_ACCEPTED",
                parts=tuple(completed),
                at_ms=now,
            )
            return self._ledger.record_receipt(receipt).receipt or receipt


__all__ = [
    "FEISHU_FILES_PATH",
    "FEISHU_IMAGES_PATH",
    "FEISHU_MESSAGES_PATH",
    "FEISHU_OPEN_API_HOST",
    "FEISHU_TOKEN_PATH",
    "FeishuApiResponse",
    "FeishuApiTransport",
    "FeishuCredentials",
    "FeishuDeliveryService",
    "FeishuOutboundError",
    "FeishuOutboundPolicy",
    "FeishuTokenProvider",
    "FeishuTokenResult",
    "FetchedFeishuArtifact",
    "HttpFeishuApiTransport",
    "default_feishu_outbound_policy",
    "derive_feishu_dedup_uuid",
]
