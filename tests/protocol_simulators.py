"""Deterministic, secret-redacting protocol simulators for P6 fault matrices."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from communication_service.feishu_outbound import (
    FeishuApiResponse,
    FeishuCredentials,
    FeishuTokenResult,
)
from communication_service.wechat_file_outbound import WechatCdnUploadResponse
from communication_service.wechat_text_outbound import WechatIlinkResponse
from contracts import canonical_json_bytes, canonical_sha256


class ProtocolSimulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamScenario:
    status: int
    body: bytes
    content_type: str = "application/octet-stream"
    declared_length: int | None = None
    max_chunk_bytes: int | None = None
    fail_after_bytes: int | None = None


class ScriptedResponseStream:
    def __init__(self, scenario: StreamScenario) -> None:
        self.status = scenario.status
        length = len(scenario.body) if scenario.declared_length is None else scenario.declared_length
        self.headers = {
            "Content-Type": scenario.content_type,
            "Content-Length": str(length),
        }
        self._scenario = scenario
        self._offset = 0
        self.closed = False

    def read(self, amount: int) -> bytes:
        if self.closed:
            raise OSError("simulated response is closed")
        if amount < 1:
            raise ValueError("simulated read amount must be positive")
        if (
            self._scenario.fail_after_bytes is not None
            and self._offset >= self._scenario.fail_after_bytes
        ):
            raise OSError("simulated stream interruption")
        limit = amount
        if self._scenario.max_chunk_bytes is not None:
            limit = min(limit, self._scenario.max_chunk_bytes)
        if self._scenario.fail_after_bytes is not None:
            limit = min(limit, self._scenario.fail_after_bytes - self._offset)
        chunk = self._scenario.body[self._offset : self._offset + limit]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class SimulationCall:
    sequence: int
    operation: str
    evidence_sha256: str


class _ScriptedSimulator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scripts: dict[str, deque[Any]] = {}
        self._calls: list[SimulationCall] = []

    def script(self, operation: str, *outcomes: Any) -> None:
        if not operation or not outcomes:
            raise ValueError("simulator script requires an operation and outcomes")
        with self._lock:
            queue = self._scripts.setdefault(operation, deque())
            queue.extend(outcomes)

    def _take(self, operation: str, evidence: Mapping[str, Any]) -> Any:
        with self._lock:
            queue = self._scripts.get(operation)
            if not queue:
                raise ProtocolSimulationError(
                    f"no scripted outcome remains for {operation}"
                )
            self._calls.append(
                SimulationCall(
                    sequence=len(self._calls) + 1,
                    operation=operation,
                    evidence_sha256=canonical_sha256(dict(evidence)),
                )
            )
            outcome = queue.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def calls(self) -> tuple[SimulationCall, ...]:
        with self._lock:
            return tuple(self._calls)

    def call_count(self, operation: str) -> int:
        return sum(item.operation == operation for item in self.calls)


class WechatProtocolSimulator(_ScriptedSimulator):
    def open(self, url: str, *, timeout_seconds: int) -> ScriptedResponseStream:
        scenario = self._take(
            "media.open",
            {
                "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                "timeout_seconds": timeout_seconds,
            },
        )
        if not isinstance(scenario, StreamScenario):
            raise ProtocolSimulationError("media.open outcome is not a stream scenario")
        return ScriptedResponseStream(scenario)

    def send_message(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse:
        outcome = self._take(
            "message.send",
            {
                "body_sha256": canonical_sha256(dict(body)),
                "bot_token_sha256": hashlib.sha256(bot_token.encode()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            },
        )
        if not isinstance(outcome, WechatIlinkResponse):
            raise ProtocolSimulationError("message.send outcome has the wrong type")
        return outcome

    def get_upload_url(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse:
        outcome = self._take(
            "upload.authorize",
            {
                "body_sha256": canonical_sha256(dict(body)),
                "bot_token_sha256": hashlib.sha256(bot_token.encode()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            },
        )
        if not isinstance(outcome, WechatIlinkResponse):
            raise ProtocolSimulationError("upload.authorize outcome has the wrong type")
        return outcome

    def upload_ciphertext(
        self,
        upload_url: str,
        ciphertext_path: Path,
        *,
        ciphertext_size: int,
        timeout_seconds: int,
        max_response_bytes: int,
        progress_callback=None,
    ) -> WechatCdnUploadResponse:
        data = ciphertext_path.read_bytes()
        outcome = self._take(
            "upload.ciphertext",
            {
                "upload_url_sha256": hashlib.sha256(upload_url.encode()).hexdigest(),
                "ciphertext_sha256": hashlib.sha256(data).hexdigest(),
                "ciphertext_size": ciphertext_size,
                "actual_size": len(data),
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            },
        )
        if progress_callback is not None:
            progress_callback(len(data))
        if not isinstance(outcome, WechatCdnUploadResponse):
            raise ProtocolSimulationError("upload.ciphertext outcome has the wrong type")
        return outcome


class FeishuProtocolSimulator(_ScriptedSimulator):
    def open(
        self,
        url: str,
        *,
        access_token: str,
        timeout_seconds: int,
    ) -> ScriptedResponseStream:
        scenario = self._take(
            "resource.open",
            {
                "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                "access_token_sha256": hashlib.sha256(access_token.encode()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            },
        )
        if not isinstance(scenario, StreamScenario):
            raise ProtocolSimulationError("resource.open outcome is not a stream scenario")
        return ScriptedResponseStream(scenario)

    def fetch_tenant_token(
        self,
        credentials: FeishuCredentials,
        *,
        timeout_seconds: int,
    ) -> FeishuTokenResult:
        outcome = self._take(
            "token.fetch",
            {
                "app_id_sha256": hashlib.sha256(credentials.app_id.encode()).hexdigest(),
                "app_secret_sha256": hashlib.sha256(credentials.app_secret.encode()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            },
        )
        if not isinstance(outcome, FeishuTokenResult):
            raise ProtocolSimulationError("token.fetch outcome has the wrong type")
        return outcome

    def send_message(self, **kwargs) -> FeishuApiResponse:
        access_token = kwargs.pop("access_token")
        evidence = dict(kwargs)
        evidence["access_token_sha256"] = hashlib.sha256(
            access_token.encode()
        ).hexdigest()
        evidence["content_sha256"] = canonical_sha256(evidence.pop("content"))
        outcome = self._take("message.send", evidence)
        if not isinstance(outcome, FeishuApiResponse):
            raise ProtocolSimulationError("message.send outcome has the wrong type")
        return outcome

    def upload_artifact(
        self,
        artifact,
        grant,
        *,
        as_image: bool,
        access_token: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> FeishuApiResponse:
        outcome = self._take(
            "artifact.upload",
            {
                "artifact_sha256": artifact.sha256,
                "artifact_size": artifact.size_bytes,
                "grant_content_sha256": grant.content_sha256,
                "as_image": as_image,
                "access_token_sha256": hashlib.sha256(access_token.encode()).hexdigest(),
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            },
        )
        if not isinstance(outcome, FeishuApiResponse):
            raise ProtocolSimulationError("artifact.upload outcome has the wrong type")
        return outcome


def load_protocol_samples(path: Path) -> dict[str, dict[str, Any]]:
    def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("protocol sample contains a duplicate JSON key")
            result[key] = value
        return result

    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs)
    if (
        document.get("schema_version") != "tiangong.test.communication-samples.v1"
        or document.get("redaction_claim")
        != "synthetic-identifiers-only-no-production-credentials"
    ):
        raise ValueError("protocol sample manifest is incompatible")
    samples = document.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("protocol sample manifest is empty")
    result: dict[str, dict[str, Any]] = {}
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {
            "id",
            "channel",
            "direction",
            "payload",
        }:
            raise ValueError("protocol sample shape is invalid")
        sample_id = sample["id"]
        if not isinstance(sample_id, str) or sample_id in result:
            raise ValueError("protocol sample identity is invalid")
        result[sample_id] = sample
    canonical_json_bytes(document)
    return result


__all__ = [
    "FeishuProtocolSimulator",
    "ProtocolSimulationError",
    "ScriptedResponseStream",
    "SimulationCall",
    "StreamScenario",
    "WechatProtocolSimulator",
    "load_protocol_samples",
]
