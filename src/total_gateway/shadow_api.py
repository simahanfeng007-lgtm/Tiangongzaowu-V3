"""Private observe-only HTTP surface for P8 migration shadow comparisons."""

from __future__ import annotations

import hmac
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from contracts import ShadowObservationBatch, canonical_json_bytes

from .run_observation import (
    ObservationConflictError,
    ObservationStoreError,
    RunObservation,
    RunObservationStore,
)
from .store import GatewayStateStore, StoreConflictError, StoreError


MAX_SHADOW_REQUEST_BYTES = 2 * 1024 * 1024
SHADOW_OBSERVE_PATH = "/api/v1/migration/shadow/observations"
SHADOW_COMPARISON_PATH = "/api/v1/migration/shadow/comparison"
# G4 路由影子 RunObservation（草案 §5.2）：独立隔离存储，不碰既有 shadow 表。
RUN_OBSERVATION_PATH = "/api/v1/shadow/observations"
RUN_OBSERVATION_STATS_PATH = "/api/v1/shadow/observations/stats"
_SHADOW_ID = re.compile(r"^shd_[0-9a-f]{64}$")
_COHORT_ID = re.compile(r"^coh_[0-9a-f]{64}$")


class ShadowApiError(RuntimeError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _parse_batch(body: bytes) -> ShadowObservationBatch:
    if not body or len(body) > MAX_SHADOW_REQUEST_BYTES:
        raise ShadowApiError(413 if body else 400, "shadow_api.request_size.invalid")
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
        batch = ShadowObservationBatch.model_validate_json(body, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowApiError(400, "shadow_api.batch.invalid") from exc
    if not batch.has_valid_sha256():
        raise ShadowApiError(400, "shadow_api.batch.digest_invalid")
    if body != canonical_json_bytes(batch.model_dump(mode="json")):
        raise ShadowApiError(400, "shadow_api.batch.noncanonical")
    return batch


def _parse_run_observation(body: bytes) -> RunObservation:
    # 与 _parse_batch 同一模式：限长、拒重复键/非有限数、严格校验、
    # record_sha256 自校验、canonical digest 一致性校验。
    if not body or len(body) > MAX_SHADOW_REQUEST_BYTES:
        raise ShadowApiError(413 if body else 400, "shadow_api.request_size.invalid")
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
        observation = RunObservation.model_validate_json(body, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowApiError(400, "shadow_api.observation.invalid") from exc
    if not observation.has_valid_sha256():
        raise ShadowApiError(400, "shadow_api.observation.digest_invalid")
    if body != canonical_json_bytes(observation.model_dump(mode="json")):
        raise ShadowApiError(400, "shadow_api.observation.noncanonical")
    return observation


@dataclass(frozen=True)
class ShadowApiResponse:
    status: int
    payload: dict[str, object]


class ShadowApiRouter:
    def __init__(
        self,
        store: GatewayStateStore,
        token: str,
        observation_store: RunObservationStore | None = None,
    ) -> None:
        if not 32 <= len(token) <= 512:
            raise ValueError("shadow API token length is invalid")
        self._store = store
        self._token = token
        # G4 RunObservation 隔离存储（可选注入；未注入时对应端点 503）。
        # 该存储与上面的 GatewayStateStore 完全隔离，互不读写。
        self._observation_store = observation_store

    @staticmethod
    def handles_path(raw_target: str) -> bool:
        parsed = urlsplit(raw_target)
        return (
            not parsed.scheme
            and not parsed.netloc
            and not parsed.fragment
            and parsed.path
            in {
                SHADOW_OBSERVE_PATH,
                SHADOW_COMPARISON_PATH,
                RUN_OBSERVATION_PATH,
                RUN_OBSERVATION_STATS_PATH,
            }
        )

    def authorize(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(
            token.encode("utf-8"), self._token.encode("utf-8")
        )

    def dispatch(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now_ms: int | None = None,
    ) -> ShadowApiResponse:
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        parsed = urlsplit(raw_target)
        if headers.get("Origin"):
            raise ShadowApiError(403, "shadow_api.browser_origin.forbidden")
        try:
            if parsed.path == SHADOW_OBSERVE_PATH:
                if method != "POST" or parsed.query:
                    raise ShadowApiError(405, "shadow_api.method.invalid")
                content_type = str(headers.get("Content-Type") or "").lower()
                if content_type not in {"application/json", "application/json; charset=utf-8"}:
                    raise ShadowApiError(415, "shadow_api.content_type.invalid")
                batch = _parse_batch(body)
                registration = self._store.record_shadow_batch(
                    batch,
                    compared_at_ms=observed_ms,
                )
                return ShadowApiResponse(
                    200,
                    {
                        "status": "OBSERVED",
                        "mode": "OBSERVE_ONLY",
                        "request_created": False,
                        "effects_permitted": False,
                        "copy_created": registration.copy_created,
                        "observations_created": registration.observations_created,
                        "duplicate": registration.duplicate,
                        "comparison": registration.comparison.model_dump(mode="json"),
                    },
                )
            if parsed.path == SHADOW_COMPARISON_PATH:
                if method != "GET" or body:
                    raise ShadowApiError(405, "shadow_api.method.invalid")
                try:
                    pairs = parse_qsl(
                        parsed.query,
                        keep_blank_values=True,
                        strict_parsing=True,
                    )
                except ValueError as exc:
                    raise ShadowApiError(400, "shadow_api.query.invalid") from exc
                if len(pairs) != 1 or pairs[0][0] != "shadow_id" or not _SHADOW_ID.fullmatch(
                    pairs[0][1]
                ):
                    raise ShadowApiError(400, "shadow_api.query.invalid")
                comparison = self._store.get_shadow_comparison(
                    pairs[0][1], compared_at_ms=observed_ms
                )
                if comparison is None:
                    raise ShadowApiError(404, "shadow_api.comparison.not_found")
                return ShadowApiResponse(
                    200,
                    {
                        "status": "OBSERVED",
                        "mode": "OBSERVE_ONLY",
                        "request_created": False,
                        "effects_permitted": False,
                        "comparison": comparison.model_dump(mode="json"),
                    },
                )
            if parsed.path == RUN_OBSERVATION_PATH:
                if method != "POST" or parsed.query:
                    raise ShadowApiError(405, "shadow_api.method.invalid")
                if self._observation_store is None:
                    raise ShadowApiError(503, "shadow_api.observation_store.unavailable")
                content_type = str(headers.get("Content-Type") or "").lower()
                if content_type not in {"application/json", "application/json; charset=utf-8"}:
                    raise ShadowApiError(415, "shadow_api.content_type.invalid")
                observation = _parse_run_observation(body)
                # 只写隔离 RunObservationStore；request/effect 等业务写入恒为 0。
                self._observation_store.append(observation)
                return ShadowApiResponse(
                    200,
                    {
                        "status": "OBSERVED",
                        "mode": "OBSERVE_ONLY",
                        "request_created": False,
                        "effects_permitted": False,
                        "observation_id": observation.observation_id,
                        "cohort_id": observation.cohort_id,
                        "pair_key": observation.pair_key,
                        "terminal_state": observation.terminal_state,
                    },
                )
            if parsed.path == RUN_OBSERVATION_STATS_PATH:
                if method != "GET" or body:
                    raise ShadowApiError(405, "shadow_api.method.invalid")
                if self._observation_store is None:
                    raise ShadowApiError(503, "shadow_api.observation_store.unavailable")
                try:
                    pairs = parse_qsl(
                        parsed.query,
                        keep_blank_values=True,
                        strict_parsing=True,
                    )
                except ValueError as exc:
                    raise ShadowApiError(400, "shadow_api.query.invalid") from exc
                if len(pairs) != 1 or pairs[0][0] != "cohort_id" or not _COHORT_ID.fullmatch(
                    pairs[0][1]
                ):
                    raise ShadowApiError(400, "shadow_api.query.invalid")
                cohort_id = pairs[0][1]
                return ShadowApiResponse(
                    200,
                    {
                        "status": "OBSERVED",
                        "mode": "OBSERVE_ONLY",
                        "request_created": False,
                        "effects_permitted": False,
                        "cohort_id": cohort_id,
                        "complete_pair_count": self._observation_store.complete_pair_count(
                            cohort_id
                        ),
                        "incomplete_observation_count": (
                            self._observation_store.incomplete_observation_count(cohort_id)
                        ),
                        "timeout_observation_count": (
                            self._observation_store.timeout_observation_count(cohort_id)
                        ),
                    },
                )
        except ObservationConflictError as exc:
            raise ShadowApiError(409, "shadow_api.observation.conflict") from exc
        except ObservationStoreError as exc:
            raise ShadowApiError(503, "shadow_api.observation_store.unavailable") from exc
        except StoreConflictError as exc:
            raise ShadowApiError(409, "shadow_api.observation.conflict") from exc
        except (StoreError, sqlite3.DatabaseError) as exc:
            raise ShadowApiError(503, "shadow_api.store.unavailable") from exc
        except ValueError as exc:
            raise ShadowApiError(400, "shadow_api.observation.invalid") from exc
        raise ShadowApiError(404, "shadow_api.route.not_found")


__all__ = [
    "MAX_SHADOW_REQUEST_BYTES",
    "RUN_OBSERVATION_PATH",
    "RUN_OBSERVATION_STATS_PATH",
    "SHADOW_COMPARISON_PATH",
    "SHADOW_OBSERVE_PATH",
    "ShadowApiError",
    "ShadowApiResponse",
    "ShadowApiRouter",
]
