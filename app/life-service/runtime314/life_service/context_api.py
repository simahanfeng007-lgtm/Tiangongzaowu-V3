"""Strict API boundary for one-call life context compilation and authorization."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from contracts import (
    CausalContextItem,
    LifeRevisionVector,
    TaskContinuityCapsule,
    canonical_json_bytes,
    canonical_sha256,
)

from .context_authority import LifeContextAuthority, LifeContextAuthorityError
from .context import conservative_token_count
from .store import LifeShadowStore, LifeShadowStoreError


class LifeContextApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LifeProjectionInputs:
    life_id: str
    writer_epoch: int
    identity_revision: int
    soul: Mapping[str, Any]
    capabilities: Mapping[str, Any]
    revision_floor: LifeRevisionVector | None = None
    external_items: tuple[CausalContextItem, ...] = ()


class LifeContextCompileAuthorizeApi:
    """Owns request validation and returns only revision/source-bound facts."""

    _FIELDS = frozenset(
        {
            "request_id",
            "run_id",
            "generation",
            "current_request",
            "current_context_tokens",
            "principal_scope_hash",
            "issued_at_ms",
        }
    )

    def __init__(self, store: LifeShadowStore) -> None:
        self.store = store
        self.authority = LifeContextAuthority(store)

    @staticmethod
    def _working_capsule(
        projection: LifeProjectionInputs,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        current_request: str,
        issued_at_ms: int,
    ) -> TaskContinuityCapsule:
        token = canonical_sha256(
            {
                "domain": "tiangong.life.atomic-context-continuation.v1",
                "generation": generation,
                "request_id": request_id,
                "run_id": run_id,
            }
        )
        identity = {
            "capsule_kind": "WORKING_CHECKPOINT",
            "created_at_ms": issued_at_ms,
            "generation": generation,
            "life_id": projection.life_id,
            "request_id": request_id,
            "run_id": run_id,
            "user_goal": current_request,
        }
        capsule = TaskContinuityCapsule(
            capsule_id="lcp_" + canonical_sha256(
                {"domain": "tiangong.life.atomic-context-capsule.v1", **identity}
            ),
            life_id=projection.life_id,
            capsule_kind="WORKING_CHECKPOINT",
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            episode_id="cep_" + canonical_sha256(
                {
                    "domain": "tiangong.life.atomic-context-episode.v1",
                    "generation": generation,
                    "request_id": request_id,
                    "run_id": run_id,
                }
            ),
            user_goal=current_request,
            active_plan=("compile and authorize the current causal context",),
            latest_safe_step="current request and generation are authority-bound",
            next_step="consume the authorized context through the Gateway ticket path",
            recovery_preconditions=("reuse the exact request, run, generation, and principal binding",),
            continuation_token_sha256=token,
            retention_class="ACTIVE_WORKING",
            created_at_ms=issued_at_ms,
            capsule_sha256="0" * 64,
        )
        return capsule.with_computed_capsule_sha256()

    def compile_and_authorize(
        self,
        payload: Mapping[str, Any],
        projection: LifeProjectionInputs,
    ) -> dict[str, Any]:
        if (
            not isinstance(payload, Mapping)
            or set(payload) not in {self._FIELDS, self._FIELDS - {"current_context_tokens"}}
        ):
            raise LifeContextApiError("life.context.atomic.request_shape_invalid")
        request_id = payload.get("request_id")
        run_id = payload.get("run_id")
        generation = payload.get("generation")
        current_request = payload.get("current_request")
        current_context_tokens = payload.get("current_context_tokens")
        if current_context_tokens is None and isinstance(current_request, str):
            current_context_tokens = conservative_token_count(current_request)
        principal_scope_hash = payload.get("principal_scope_hash")
        issued_at_ms = payload.get("issued_at_ms")
        if (
            not isinstance(request_id, str)
            or not isinstance(run_id, str)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
            or not isinstance(current_request, str)
            or not current_request.strip()
            or current_request != unicodedata.normalize("NFC", current_request)
            or isinstance(issued_at_ms, bool)
            or not isinstance(issued_at_ms, int)
            or issued_at_ms < 0
            or not isinstance(principal_scope_hash, str)
            or len(principal_scope_hash) != 64
            or any(character not in "0123456789abcdef" for character in principal_scope_hash)
            or not projection.life_id
            or projection.writer_epoch < 1
            or projection.identity_revision < 1
            or isinstance(current_context_tokens, bool)
            or not isinstance(current_context_tokens, int)
            or not 0 <= current_context_tokens <= 10_000_000
        ):
            raise LifeContextApiError("life.context.atomic.request_invalid")
        try:
            soul = dict(projection.soul)
            capabilities = dict(projection.capabilities)
            canonical_json_bytes(soul)
            canonical_json_bytes(capabilities)
            soul_revision = soul.get("revision")
            if (
                soul.get("life_id") != projection.life_id
                or not isinstance(soul_revision, int)
                or isinstance(soul_revision, bool)
                or soul_revision < 1
                or not isinstance(soul.get("name"), str)
                or not soul["name"]
            ):
                raise LifeContextApiError("life.context.atomic.soul_invalid")
            capsule = self._working_capsule(
                projection,
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                current_request=current_request,
                issued_at_ms=issued_at_ms,
            )
            self.store.put_context_capsule(capsule)
            result = self.authority.compile_and_authorize(
                capsule,
                current_request=current_request,
                principal_scope_hash=principal_scope_hash,
                writer_epoch=projection.writer_epoch,
                identity_revision=projection.identity_revision,
                soul_revision=soul_revision,
                current_context_tokens=current_context_tokens,
                issued_at_ms=issued_at_ms,
                revision_floor=projection.revision_floor,
                external_items=projection.external_items,
            )
        except LifeContextApiError:
            raise
        except (LifeContextAuthorityError, LifeShadowStoreError, TypeError, ValueError) as exc:
            # Keep the public failure code bounded and non-sensitive while
            # preserving the failing authority phase for callers that need to
            # distinguish an unavailable projection from a rejected binding.
            phase = type(exc).__name__.replace(" ", "_")[:64]
            if phase == "ValidationError":
                errors = getattr(exc, "errors", None)
                if callable(errors):
                    try:
                        first = errors()[0]
                        location = first.get("loc", ()) if isinstance(first, Mapping) else ()
                        field = ".".join(str(part) for part in location if str(part))
                        if field:
                            phase = f"{phase}.{field}"[:120]
                    except (IndexError, TypeError, ValueError):
                        pass
            raise LifeContextApiError(f"life.context.atomic.failed:{phase}") from exc
        revisions = result.authorization.revisions
        return {
            "ok": True,
            "api_contract": "tiangong.life.api.v2",
            "projection": {
                "life_id": projection.life_id,
                "context_pack": result.context_pack.model_dump(mode="json"),
                "authorization": result.authorization.model_dump(mode="json"),
                "soul": soul,
                "capabilities": capabilities,
                "projection_authority": {
                    "schema": "tiangong.gateway.life-view-authority.v1",
                    "revisions": revisions.model_dump(mode="json"),
                    "source_refs": {
                        "identity": [f"life:{projection.life_id}:identity:{revisions.identity_revision}"],
                        "soul": [f"life:{projection.life_id}:soul:{revisions.soul_revision}"],
                        "memory": [f"life:{projection.life_id}:memory:{revisions.memory_revision}"],
                        "affect": [f"life:{projection.life_id}:affect:{revisions.affect_revision}"],
                        "causal": [f"life:{projection.life_id}:causal:{revisions.causal_revision}"],
                        "viability": [f"life:{projection.life_id}:viability:{revisions.viability_revision}"],
                        "policy": [f"life:{projection.life_id}:policy:{revisions.policy_revision}"],
                        "reflection": [f"life:{projection.life_id}:reflection:{revisions.reflection_revision}"],
                        "capability": [f"life:{projection.life_id}:capability:{revisions.capability_revision}"],
                    },
                    "vector_sha256": revisions.vector_sha256,
                },
            },
        }


__all__ = [
    "LifeContextApiError",
    "LifeContextCompileAuthorizeApi",
    "LifeProjectionInputs",
]
