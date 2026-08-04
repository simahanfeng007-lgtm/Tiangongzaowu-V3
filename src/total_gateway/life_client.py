"""Read-only, fail-closed client for pinning one 7175 life projection."""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from contracts import (
    CausalContextPack,
    LifeContextAuthorization,
    LifeRevisionVector,
    LifeSnapshot,
    canonical_json_bytes,
    canonical_sha256,
)

from .object_store import ContentAddressedObjectStore, derive_object_reference_id
from .context_projection import estimate_projected_context_tokens


LIFE_API_CONTRACT = "tiangong.life.api.v2"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_READ_PATHS = frozenset(
    {
        "/health",
        "/api/v1/v3/life/identity/active",
        "/api/v1/v3/life/soul",
        "/api/v1/v3/life/context/latest",
        "/api/v1/v3/state",
    }
)

_POST_PATHS = frozenset({"/api/v1/v3/life/context/compile-and-authorize"})


class LifeClientError(RuntimeError):
    def __init__(self, code: str, *, status: int = 0, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable


class LifeJsonTransport(Protocol):
    def get_json(self, path: str) -> dict[str, Any]: ...

    def post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, *args: object, **kwargs: object) -> None:
        raise LifeClientError("life.http.redirect_forbidden")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LifeClientError("life.http.duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise LifeClientError("life.http.non_finite_json")


class LoopbackLifeJsonTransport:
    """Bounded JSON GET transport that never follows redirects or sends secrets off-host."""

    def __init__(
        self,
        base_url: str,
        *,
        desktop_token: str,
        timeout_seconds: float = 3.0,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port is None
        ):
            raise ValueError("life service base URL must be an explicit loopback HTTP origin")
        if not desktop_token or any(ord(char) < 33 for char in desktop_token):
            raise ValueError("desktop token is missing or malformed")
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("life service timeout is out of bounds")
        if not 1024 <= max_response_bytes <= 32 * 1024 * 1024:
            raise ValueError("life service response limit is out of bounds")
        self._base_url = base_url.rstrip("/")
        self._desktop_token = desktop_token
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def _decode(self, body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except LifeClientError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LifeClientError("life.http.invalid_json") from exc
        if not isinstance(value, dict):
            raise LifeClientError("life.http.non_object_json")
        return value

    def _read_bounded(self, response: Any) -> bytes:
        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise LifeClientError("life.http.response_too_large")
        return body

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: bytes | None = None
        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "X-Tiangong-Token": self._desktop_token,
        }
        if method == "POST":
            body = canonical_json_bytes(dict(payload or {}))
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        request = urllib.request.Request(
            self._base_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
                if content_type not in {"application/json", "application/problem+json"}:
                    raise LifeClientError("life.http.content_type_invalid", status=int(response.status))
                return self._decode(self._read_bounded(response))
        except LifeClientError:
            raise
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                response_payload = self._decode(self._read_bounded(exc))
                code = str(response_payload.get("error_code") or "life.http.upstream_error")
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", code):
                    code = "life.http.upstream_error"
            except LifeClientError:
                code = "life.http.upstream_error"
            raise LifeClientError(code, status=status, retryable=status in {429, 502, 503, 504}) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise LifeClientError("life.http.unavailable", retryable=True) from exc

    def get_json(self, path: str) -> dict[str, Any]:
        if path not in _READ_PATHS:
            raise LifeClientError("life.http.path_not_allowed")
        return self._request_json("GET", path)

    def post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if path not in _POST_PATHS:
            raise LifeClientError("life.http.path_not_allowed")
        if not isinstance(payload, Mapping):
            raise LifeClientError("life.http.request_invalid")
        return self._request_json("POST", path, payload)




class InProcessLifeJsonTransport:
    """Life transport for the embedded LifeKernel; no listener or token."""

    def __init__(self, service: object) -> None:
        request = getattr(service, "request", None)
        if not callable(request):
            raise ValueError("embedded life service does not implement request")
        self._service = service

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        status, value, _content_type = self._service.request(
            method, path, payload, timeout_seconds=30.0
        )
        if status >= 400:
            code = str(value.get("error_code") or value.get("reason_code") or "life.embedded.upstream_error")
            raise LifeClientError(code, status=status, retryable=status in {429, 502, 503, 504})
        if not isinstance(value, dict):
            raise LifeClientError("life.embedded.non_object_json")
        return value

    def get_json(self, path: str) -> dict[str, Any]:
        if path not in _READ_PATHS:
            raise LifeClientError("life.embedded.path_not_allowed")
        return self._request("GET", path)

    def post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if path not in _POST_PATHS:
            raise LifeClientError("life.embedded.path_not_allowed")
        return self._request("POST", path, payload)


def _validate_opaque(value: str | None, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _OPAQUE_ID.fullmatch(value):
        raise ValueError(f"{field} must be a safe opaque identifier")
    return value


@dataclass(frozen=True)
class LifeProfileBindings:
    """Gateway-owned user/persona presentation settings, separate from the life projection."""

    user_callsign: str
    user_occupation: str = ""
    persona_avatar_ref: str | None = None
    persona_voice_ref: str | None = None
    user_avatar_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.user_callsign, str)
            or not self.user_callsign.strip()
            or self.user_callsign != self.user_callsign.strip()
            or len(self.user_callsign) > 128
        ):
            raise ValueError("user callsign is missing or malformed")
        if not isinstance(self.user_occupation, str) or len(self.user_occupation) > 512:
            raise ValueError("user occupation is malformed")
        if any(ord(char) < 32 and char not in "\t\n\r" for char in self.user_callsign + self.user_occupation):
            raise ValueError("profile text contains a control character")
        _validate_opaque(self.persona_avatar_ref, "persona_avatar_ref", optional=True)
        _validate_opaque(self.persona_voice_ref, "persona_voice_ref", optional=True)
        _validate_opaque(self.user_avatar_ref, "user_avatar_ref", optional=True)


@dataclass(frozen=True)
class PinnedLifeSnapshot:
    snapshot: LifeSnapshot
    projection_anchor_sha256: str
    upstream_context_sha256: str
    object_reference_sha256: str

    @property
    def context_authorization_id(self) -> str | None:
        return self.snapshot.context_authorization_id

    @property
    def revision_vector_sha256(self) -> str | None:
        return self.snapshot.revision_vector_sha256


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifeClientError(f"life.contract.{field}_invalid")
    return value


def _string(value: object, field: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or (pattern is not None and not pattern.fullmatch(value)):
        raise LifeClientError(f"life.contract.{field}_invalid")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LifeClientError(f"life.contract.{field}_invalid")
    return value


def _stable_json_bytes(value: object) -> bytes:
    def validate(item: object) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise LifeClientError("life.contract.non_finite_number")
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise LifeClientError("life.contract.non_string_key")
                validate(child)
            return
        raise LifeClientError("life.contract.unsupported_json_type")

    validate(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LifeClientError("life.contract.invalid_json_value") from exc


def _stable_sha256(value: object) -> str:
    return hashlib.sha256(_stable_json_bytes(value)).hexdigest()


def _utc_epoch_ms(value: object) -> int:
    text = _string(value, "context_created_at")
    if not text.endswith("Z"):
        raise LifeClientError("life.contract.context_created_at_not_utc")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise LifeClientError("life.contract.context_created_at_invalid") from exc
    if parsed.tzinfo != UTC:
        raise LifeClientError("life.contract.context_created_at_not_utc")
    delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def life_snapshot_sha256(snapshot: LifeSnapshot) -> str:
    return canonical_sha256(snapshot.model_dump(mode="json", exclude={"sha256"}))


def _require_response(payload: dict[str, Any], *, allow_available: bool = False) -> dict[str, Any]:
    if payload.get("api_contract") != LIFE_API_CONTRACT:
        raise LifeClientError("life.contract.api_contract_mismatch")
    if payload.get("ok") is not True:
        raise LifeClientError("life.contract.upstream_not_ok")
    if not allow_available and payload.get("setup_required") is True:
        raise LifeClientError("life.setup_required", status=428)
    return payload


def _state_anchor(payload: dict[str, Any]) -> dict[str, Any]:
    _require_response(payload)
    life_id = _string(payload.get("life_id"), "state_life_id")
    identity = _object(payload.get("identity"), "state_identity")
    soul = _object(payload.get("soul"), "state_soul")
    life = _object(payload.get("life"), "state_life")
    ui = _object(payload.get("ui"), "state_ui")
    lifecycle = _object(ui.get("lifecycle"), "state_lifecycle")
    context = _object(ui.get("context"), "state_context")
    capabilities = _object(ui.get("capabilities"), "state_capabilities")
    source_sequence = _positive_int(lifecycle.get("source_sequence"), "source_sequence")
    writer_epoch = _positive_int(identity.get("writer_epoch"), "writer_epoch")
    context_hash = _string(context.get("context_hash"), "state_context_hash", pattern=_SHA256)
    if (
        identity.get("life_id") != life_id
        or soul.get("life_id") != life_id
        or context.get("life_id") != life_id
        or identity.get("active") is not True
        or identity.get("integrity") != "valid"
        or identity.get("soul_integrity") != "valid"
        or life.get("ready") is not True
        or life.get("available") is not True
        or lifecycle.get("available") is not True
        or lifecycle.get("projection_status") != "ready"
        or context.get("available") is not True
        or context.get("current") is not True
        or context.get("verified") is not True
        or context.get("writer_epoch") != writer_epoch
        or context.get("current_writer_epoch") != writer_epoch
    ):
        raise LifeClientError("life.contract.state_not_ready")
    return {
        "life_id": life_id,
        "identity": {
            "name": identity.get("name"),
            "writer_epoch": writer_epoch,
            "soul_revision_id": identity.get("soul_revision_id"),
        },
        "soul": {
            "revision": soul.get("revision"),
            "revision_id": soul.get("revision_id"),
            "name": soul.get("name"),
        },
        "source_sequence": source_sequence,
        "context_hash": context_hash,
        "capabilities": capabilities,
    }


class LifeClient:
    """Pins one immutable run snapshot from stable, read-only 7175 projections."""

    def __init__(
        self,
        transport: LifeJsonTransport,
        object_store: ContentAddressedObjectStore,
        *,
        max_context_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not 1024 <= max_context_bytes <= 32 * 1024 * 1024:
            raise ValueError("compiled life context limit is out of bounds")
        self._transport = transport
        self._object_store = object_store
        self._max_context_bytes = max_context_bytes

    def acquire_snapshot(
        self,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        profile: LifeProfileBindings,
        expected_revision: int | None = None,
        expected_sha256: str | None = None,
    ) -> PinnedLifeSnapshot:
        if expected_revision is not None and expected_revision < 1:
            raise ValueError("expected life revision is invalid")
        if expected_sha256 is not None and not _SHA256.fullmatch(expected_sha256):
            raise ValueError("expected life snapshot digest is invalid")

        health = _require_response(self._transport.get_json("/health"), allow_available=True)
        if health.get("life_ready") is not True or health.get("setup_required") is not False:
            raise LifeClientError("life.setup_required", status=428)

        state_before = _state_anchor(self._transport.get_json("/api/v1/v3/state"))
        active_payload = _require_response(self._transport.get_json("/api/v1/v3/life/identity/active"))
        active = _object(active_payload.get("active"), "active_identity")
        soul_payload = _require_response(self._transport.get_json("/api/v1/v3/life/soul"))
        soul = _object(soul_payload.get("soul"), "soul")
        context_payload = _require_response(
            self._transport.get_json("/api/v1/v3/life/context/latest"), allow_available=True
        )
        if context_payload.get("available") is not True:
            raise LifeClientError("life.context_unavailable")
        context_meta = _object(context_payload.get("meta"), "context_meta")
        context_envelope = _object(context_payload.get("envelope"), "context_envelope")
        state_after = _state_anchor(self._transport.get_json("/api/v1/v3/state"))
        if state_before != state_after:
            raise LifeClientError("life.projection_changed_during_read", retryable=True)

        life_id = state_after["life_id"]
        writer_epoch = state_after["identity"]["writer_epoch"]
        source_sequence = state_after["source_sequence"]
        context_hash = state_after["context_hash"]
        soul_revision = _positive_int(soul.get("revision"), "soul_revision")
        soul_revision_id = _string(soul.get("revision_id"), "soul_revision_id")
        persona_name = _string(soul.get("name"), "persona_name")
        if (
            active.get("life_id") != life_id
            or active.get("writer_epoch") != writer_epoch
            or active.get("active") is not True
            or active.get("integrity") != "valid"
            or active.get("soul_integrity") != "valid"
            or active.get("soul_revision_id") != soul_revision_id
            or soul_payload.get("life_id") != life_id
            or soul.get("life_id") != life_id
            or state_after["identity"]["soul_revision_id"] != soul_revision_id
            or state_after["soul"]["revision"] != soul_revision
            or state_after["soul"]["revision_id"] != soul_revision_id
            or state_after["soul"]["name"] != persona_name
            or context_meta.get("life_id") != life_id
            or context_envelope.get("life_id") != life_id
            or context_envelope.get("writer_epoch") != writer_epoch
            or context_meta.get("context_hash") != context_hash
            or context_envelope.get("context_hash") != context_hash
            or context_envelope.get("soul_revision") != soul_revision_id
        ):
            raise LifeClientError("life.contract.cross_response_mismatch")

        context_bytes = _stable_json_bytes(context_envelope)
        if not context_bytes or len(context_bytes) > self._max_context_bytes:
            raise LifeClientError("life.context_size_invalid")
        context_object_sha256 = hashlib.sha256(context_bytes).hexdigest()
        predicted_object_id = derive_object_reference_id(
            kind="payload",
            content_sha256=context_object_sha256,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
        )
        soul_sha256 = _stable_sha256(soul)
        capability_profile_hash = _stable_sha256(state_after["capabilities"])
        created_at_ms = _utc_epoch_ms(context_meta.get("created_at"))
        snapshot_id = "life_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.life-snapshot.v1",
                "life_id": life_id,
                "writer_epoch": writer_epoch,
                "source_sequence": source_sequence,
                "context_hash": context_hash,
                "context_object_sha256": context_object_sha256,
                "soul_sha256": soul_sha256,
                "capability_profile_hash": capability_profile_hash,
                "profile": {
                    "persona_avatar_ref": profile.persona_avatar_ref,
                    "persona_voice_ref": profile.persona_voice_ref,
                    "user_callsign": profile.user_callsign,
                    "user_avatar_ref": profile.user_avatar_ref,
                    "user_occupation": profile.user_occupation,
                },
            }
        )
        snapshot = LifeSnapshot(
            snapshot_id=snapshot_id,
            revision=source_sequence,
            sha256="0" * 64,
            created_at_ms=created_at_ms,
            identity_ref=life_id,
            identity_revision=writer_epoch,
            persona_name=persona_name,
            persona_avatar_ref=profile.persona_avatar_ref,
            persona_voice_ref=profile.persona_voice_ref,
            user_callsign=profile.user_callsign,
            user_avatar_ref=profile.user_avatar_ref,
            user_occupation=profile.user_occupation,
            compiled_context_object_id=predicted_object_id,
            compiled_context_sha256=context_object_sha256,
            soul_sha256=soul_sha256,
            memory_revision=source_sequence,
            affect_revision=source_sequence,
            capability_profile_hash=capability_profile_hash,
        )
        snapshot = snapshot.model_copy(update={"sha256": life_snapshot_sha256(snapshot)})
        if expected_revision is not None and snapshot.revision != expected_revision:
            raise LifeClientError("life.pinned_revision_mismatch")
        if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
            raise LifeClientError("life.pinned_sha256_mismatch")

        stored = self._object_store.put_bytes(
            context_bytes,
            kind="payload",
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
            created_at_ms=created_at_ms,
        ).reference
        if stored.object_id != predicted_object_id or stored.sha256 != context_object_sha256:
            raise LifeClientError("life.context_object_store_mismatch")
        return PinnedLifeSnapshot(
            snapshot=snapshot,
            projection_anchor_sha256=_stable_sha256(state_after),
            upstream_context_sha256=context_hash,
            object_reference_sha256=stored.reference_sha256,
        )

    def compile_and_authorize_snapshot(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        current_request: str,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        profile: LifeProfileBindings,
        observed_at_ms: int,
        current_context_tokens: int | None = None,
    ) -> PinnedLifeSnapshot:
        if current_context_tokens is None:
            current_context_tokens = estimate_projected_context_tokens((), current_request)
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(run_id, str)
            or not run_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
            or not isinstance(current_request, str)
            or not current_request.strip()
            or isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
            or isinstance(current_context_tokens, bool)
            or not isinstance(current_context_tokens, int)
            or not 0 <= current_context_tokens <= 10_000_000
        ):
            raise ValueError("atomic life snapshot input is invalid")
        principal_scope_hash = canonical_sha256(
            {
                "domain": "tiangong.gateway.life-principal-scope.v1",
                "tenant_id": tenant_id,
                "link_account_id": link_account_id,
                "conversation_scope_hash": conversation_scope_hash,
            }
        )
        response = _require_response(
            self._transport.post_json(
                "/api/v1/v3/life/context/compile-and-authorize",
                {
                    "request_id": request_id,
                    "run_id": run_id,
                    "generation": generation,
                    "current_request": current_request,
                    "current_context_tokens": current_context_tokens,
                    "principal_scope_hash": principal_scope_hash,
                    "issued_at_ms": observed_at_ms,
                },
            )
        )
        projection = _object(response.get("projection"), "atomic_projection")
        life_id = _string(projection.get("life_id"), "atomic_life_id")
        soul = _object(projection.get("soul"), "atomic_soul")
        capabilities = _object(projection.get("capabilities"), "atomic_capabilities")
        try:
            context_pack = CausalContextPack.model_validate_json(
                canonical_json_bytes(projection.get("context_pack")), strict=True
            )
            authorization = LifeContextAuthorization.model_validate_json(
                canonical_json_bytes(projection.get("authorization")), strict=True
            )
            revisions = authorization.revisions
        except Exception as exc:
            raise LifeClientError("life.contract.atomic_projection_invalid") from exc
        if (
            not context_pack.has_valid_pack_sha256()
            or not authorization.has_valid_authorization_sha256()
            or not revisions.has_valid_vector_sha256()
            or authorization.life_id != life_id
            or authorization.request_id != request_id
            or authorization.run_id != run_id
            or authorization.generation != generation
            or authorization.principal_scope_hash != principal_scope_hash
            or authorization.context_pack_id != context_pack.pack_id
            or authorization.context_pack_sha256 != context_pack.pack_sha256
            or authorization.expires_at_ms <= observed_at_ms
            or soul.get("life_id") != life_id
            or soul.get("revision") != revisions.soul_revision
        ):
            raise LifeClientError("life.contract.atomic_projection_binding_mismatch")
        projection_bytes = canonical_json_bytes(projection)
        if not projection_bytes or len(projection_bytes) > self._max_context_bytes:
            raise LifeClientError("life.context_size_invalid")
        projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
        predicted_object_id = derive_object_reference_id(
            kind="payload",
            content_sha256=projection_sha256,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
        )
        persona_name = _string(soul.get("name"), "persona_name")
        soul_sha256 = _stable_sha256(soul)
        capability_profile_hash = _stable_sha256(capabilities)
        snapshot_id = "life_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.atomic-life-snapshot.v1",
                "authorization_sha256": authorization.authorization_sha256,
                "context_pack_sha256": context_pack.pack_sha256,
                "life_id": life_id,
                "profile": {
                    "persona_avatar_ref": profile.persona_avatar_ref,
                    "persona_voice_ref": profile.persona_voice_ref,
                    "user_callsign": profile.user_callsign,
                    "user_avatar_ref": profile.user_avatar_ref,
                    "user_occupation": profile.user_occupation,
                },
                "projection_sha256": projection_sha256,
                "revision_vector_sha256": revisions.vector_sha256,
            }
        )
        snapshot = LifeSnapshot(
            snapshot_id=snapshot_id,
            revision=max(1, revisions.source_sequence),
            sha256="0" * 64,
            created_at_ms=authorization.issued_at_ms,
            identity_ref=life_id,
            identity_revision=revisions.identity_revision,
            persona_name=persona_name,
            persona_avatar_ref=profile.persona_avatar_ref,
            persona_voice_ref=profile.persona_voice_ref,
            user_callsign=profile.user_callsign,
            user_avatar_ref=profile.user_avatar_ref,
            user_occupation=profile.user_occupation,
            compiled_context_object_id=predicted_object_id,
            compiled_context_sha256=projection_sha256,
            context_authorization_id=authorization.authorization_id,
            context_authorization_sha256=authorization.authorization_sha256,
            revision_vector_sha256=revisions.vector_sha256,
            soul_sha256=soul_sha256,
            memory_revision=revisions.memory_revision,
            affect_revision=revisions.affect_revision,
            causal_revision=revisions.causal_revision,
            viability_revision=revisions.viability_revision,
            policy_revision=revisions.policy_revision,
            reflection_revision=revisions.reflection_revision,
            capability_revision=revisions.capability_revision,
            capability_profile_hash=capability_profile_hash,
        )
        snapshot = snapshot.model_copy(update={"sha256": life_snapshot_sha256(snapshot)})
        stored = self._object_store.put_bytes(
            projection_bytes,
            kind="payload",
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
            created_at_ms=authorization.issued_at_ms,
        ).reference
        if stored.object_id != predicted_object_id or stored.sha256 != projection_sha256:
            raise LifeClientError("life.context_object_store_mismatch")
        projection_authority = _object(
            projection.get("projection_authority"), "projection_authority"
        )
        return PinnedLifeSnapshot(
            snapshot=snapshot,
            projection_anchor_sha256=_stable_sha256(projection_authority),
            upstream_context_sha256=context_pack.pack_sha256,
            object_reference_sha256=stored.reference_sha256,
        )


__all__ = [
    "LIFE_API_CONTRACT",
    "LifeClient",
    "LifeClientError",
    "LifeJsonTransport",
    "LifeProfileBindings",
    "InProcessLifeJsonTransport",
    "LoopbackLifeJsonTransport",
    "PinnedLifeSnapshot",
    "life_snapshot_sha256",
]
