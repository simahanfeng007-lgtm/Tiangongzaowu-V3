"""Atomic source-owned context compilation and authorization."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

from contracts import (
    CausalContextItem,
    CausalContextPack,
    LifeContextAuthorization,
    LifeRevisionVector,
    TaskContinuityCapsule,
    canonical_sha256,
)

from .context import CausalContextBuilder, ContextBuildError
from .store import LifeShadowStore, LifeShadowStoreError


class LifeContextAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LifeContextAuthorityResult:
    context_pack: CausalContextPack
    authorization: LifeContextAuthorization
    initial_context: bool


class LifeContextAuthority:
    """Compiles and commits one generation-bound context in one authority call."""

    def __init__(self, store: LifeShadowStore) -> None:
        self.store = store
        self.builder = CausalContextBuilder(store)

    @staticmethod
    def _request_sha256(current_request: str) -> str:
        if (
            not isinstance(current_request, str)
            or not current_request.strip()
            or current_request != unicodedata.normalize("NFC", current_request)
            or len(current_request) > 50_000
            or "\x00" in current_request
            or any(
                ord(character) < 32 and character not in "\t\n\r"
                for character in current_request
            )
        ):
            raise LifeContextAuthorityError("current life request is malformed")
        return hashlib.sha256(current_request.encode("utf-8")).hexdigest()

    def compile_and_authorize(
        self,
        continuity: TaskContinuityCapsule,
        *,
        current_request: str,
        principal_scope_hash: str,
        writer_epoch: int,
        identity_revision: int,
        soul_revision: int,
        current_context_tokens: int,
        issued_at_ms: int,
        authorization_ttl_ms: int = 60_000,
        seed_refs: tuple[str, ...] = (),
        revision_floor: LifeRevisionVector | None = None,
        external_items: tuple[CausalContextItem, ...] = (),
    ) -> LifeContextAuthorityResult:
        if (
            not continuity.has_valid_capsule_sha256()
            or len(principal_scope_hash) != 64
            or any(character not in "0123456789abcdef" for character in principal_scope_hash)
            or isinstance(current_context_tokens, bool)
            or current_context_tokens < 0
            or isinstance(issued_at_ms, bool)
            or issued_at_ms < continuity.created_at_ms
            or not 1 <= authorization_ttl_ms <= 300_000
        ):
            raise LifeContextAuthorityError("context authority input is invalid")
        request_sha256 = self._request_sha256(current_request)
        existing = self.store.get_context_authorization(
            continuity.request_id,
            run_id=continuity.run_id,
            generation=continuity.generation,
        )
        if existing is not None:
            if (
                existing.life_id != continuity.life_id
                or existing.principal_scope_hash != principal_scope_hash
                or existing.current_request_sha256 != request_sha256
                or existing.continuity_capsule_sha256
                != continuity.capsule_sha256
                or existing.revisions.writer_epoch != writer_epoch
                or existing.revisions.identity_revision != identity_revision
                or existing.revisions.soul_revision != soul_revision
            ):
                raise LifeContextAuthorityError(
                    "context generation was rebound to different authority"
                )
            try:
                pack = self.store.read_causal_context_pack(
                    existing.context_pack_id
                )
            except LifeShadowStoreError as exc:
                raise LifeContextAuthorityError(
                    "authorized context is no longer readable"
                ) from exc
            return LifeContextAuthorityResult(
                context_pack=pack,
                authorization=existing,
                initial_context=existing.initial_context,
            )

        previous = self.store.get_latest_causal_context_pack(
            continuity.request_id,
            run_id=continuity.run_id,
            generation=continuity.generation,
        )
        initial = previous is None
        try:
            pack = self.builder.build(
                continuity,
                current_context_tokens=current_context_tokens,
                created_at_ms=issued_at_ms,
                seed_refs=seed_refs,
                external_items=external_items,
            )
        except (ContextBuildError, LifeShadowStoreError, ValueError) as exc:
            raise LifeContextAuthorityError("life context compilation failed") from exc
        store_revisions = self.store.build_revision_vector(
            continuity.life_id,
            writer_epoch=writer_epoch,
            identity_revision=identity_revision,
            soul_revision=soul_revision,
        )
        revisions = merge_revision_floor(store_revisions, revision_floor)
        authorization_id = "lca_" + canonical_sha256(
            {
                "context_pack_sha256": pack.pack_sha256,
                "domain": "tiangong.life.context-authorization.v1",
                "generation": continuity.generation,
                "principal_scope_hash": principal_scope_hash,
                "request_id": continuity.request_id,
                "revisions_sha256": revisions.vector_sha256,
                "run_id": continuity.run_id,
            }
        )
        authorization = LifeContextAuthorization(
            authorization_id=authorization_id,
            life_id=continuity.life_id,
            request_id=continuity.request_id,
            run_id=continuity.run_id,
            generation=continuity.generation,
            principal_scope_hash=principal_scope_hash,
            current_request_sha256=request_sha256,
            continuity_capsule_sha256=continuity.capsule_sha256,
            context_pack_id=pack.pack_id,
            context_pack_sha256=pack.pack_sha256,
            revisions=revisions,
            initial_context=initial,
            issued_at_ms=issued_at_ms,
            expires_at_ms=issued_at_ms + authorization_ttl_ms,
            authorization_sha256="0" * 64,
        )
        authorization = authorization.model_copy(
            update={
                "authorization_sha256": authorization.computed_authorization_sha256()
            }
        )
        try:
            self.store.put_causal_context_pack(
                pack,
                privacy_scope="private",
                authorization=authorization,
                expected_revisions=store_revisions,
            )
            stored = self.store.get_context_authorization(
                continuity.request_id,
                run_id=continuity.run_id,
                generation=continuity.generation,
            )
        except Exception as exc:
            raise LifeContextAuthorityError(
                "life context authorization commit failed"
            ) from exc
        if stored != authorization:
            raise LifeContextAuthorityError(
                "life context authorization readback diverged"
            )
        return LifeContextAuthorityResult(pack, authorization, initial)


def merge_revision_floor(
    store_revisions: LifeRevisionVector,
    revision_floor: LifeRevisionVector | None,
) -> LifeRevisionVector:
    """Add immutable legacy revisions to mutable overlay revisions."""

    if revision_floor is None:
        return store_revisions
    if (
        not revision_floor.has_valid_vector_sha256()
        or revision_floor.life_id != store_revisions.life_id
        or revision_floor.writer_epoch != store_revisions.writer_epoch
        or revision_floor.identity_revision != store_revisions.identity_revision
        or revision_floor.soul_revision != store_revisions.soul_revision
    ):
        raise LifeContextAuthorityError("legacy revision floor is invalid")
    combined = LifeRevisionVector(
        life_id=store_revisions.life_id,
        writer_epoch=store_revisions.writer_epoch,
        source_sequence=revision_floor.source_sequence + store_revisions.source_sequence,
        identity_revision=store_revisions.identity_revision,
        soul_revision=store_revisions.soul_revision,
        memory_revision=revision_floor.memory_revision + store_revisions.memory_revision,
        affect_revision=revision_floor.affect_revision + store_revisions.affect_revision,
        causal_revision=revision_floor.causal_revision + store_revisions.causal_revision,
        viability_revision=revision_floor.viability_revision + store_revisions.viability_revision,
        policy_revision=revision_floor.policy_revision + store_revisions.policy_revision,
        reflection_revision=revision_floor.reflection_revision + store_revisions.reflection_revision,
        capability_revision=revision_floor.capability_revision + store_revisions.capability_revision,
        vector_sha256="0" * 64,
    )
    return combined.with_computed_vector_sha256()


__all__ = [
    "LifeContextAuthority",
    "LifeContextAuthorityError",
    "LifeContextAuthorityResult",
    "merge_revision_floor",
]
