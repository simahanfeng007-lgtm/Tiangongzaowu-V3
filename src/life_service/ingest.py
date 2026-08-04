"""Authenticated single-writer ingestion for source-owned life events."""

from __future__ import annotations

from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from contracts import (
    LifeEventEnvelope,
    LifeEventIngress,
    LifeEventIngressReceipt,
    derive_life_event_id,
    derive_life_ingress_receipt_id,
)

from .store import LifeIngressCommit, LifeShadowStore, LifeShadowStoreError


class LifeIngressAuthenticationError(LifeShadowStoreError):
    pass


TrustedSourceKeys = Mapping[tuple[str, int, str], Ed25519PublicKey]


def verify_life_event_signature(
    event: LifeEventEnvelope,
    public_key: Ed25519PublicKey,
) -> bool:
    if not event.has_valid_event_hash():
        return False
    try:
        public_key.verify(bytes.fromhex(event.signature), event.event_hash.encode("ascii"))
    except (InvalidSignature, ValueError):
        return False
    return True


class LifeEventIngestor:
    """Verifies source facts while retaining sole authority over the life chain."""

    def __init__(
        self,
        store: LifeShadowStore,
        *,
        writer_epoch: int,
        writer_key_id: str,
        writer_private_key: Ed25519PrivateKey,
        trusted_source_keys: TrustedSourceKeys,
    ) -> None:
        if writer_epoch < 1 or not writer_key_id or not trusted_source_keys:
            raise ValueError("life writer configuration is invalid")
        self._store = store
        self._writer_epoch = writer_epoch
        self._writer_key_id = writer_key_id
        self._writer_private_key = writer_private_key
        self._trusted_source_keys = dict(trusted_source_keys)

    def _authenticate(self, ingress: LifeEventIngress) -> None:
        if not ingress.has_valid_ingress_sha256():
            raise LifeIngressAuthenticationError("life ingress digest is invalid")
        public_key = self._trusted_source_keys.get(
            (
                ingress.source_component_id,
                ingress.source_epoch,
                ingress.signer_key_id,
            )
        )
        if public_key is None:
            raise LifeIngressAuthenticationError("life ingress source key is not trusted")
        try:
            public_key.verify(
                bytes.fromhex(ingress.signature),
                ingress.ingress_sha256.encode("ascii"),
            )
        except (InvalidSignature, ValueError) as exc:
            raise LifeIngressAuthenticationError(
                "life ingress signature is invalid"
            ) from exc

    def ingest(
        self,
        ingress: LifeEventIngress,
        *,
        received_at_ms: int,
    ) -> LifeIngressCommit:
        self._authenticate(ingress)

        def event_factory(
            sequence: int,
            previous_event_hash: str | None,
        ) -> LifeEventEnvelope:
            event_id = derive_life_event_id(
                life_id=ingress.life_id,
                writer_epoch=self._writer_epoch,
                sequence=sequence,
                ingress_id=ingress.ingress_id,
            )
            unsigned = LifeEventEnvelope(
                event_id=event_id,
                life_id=ingress.life_id,
                sequence=sequence,
                writer_epoch=self._writer_epoch,
                source_service=ingress.source_component_id,
                source_kind=ingress.source_kind,
                event_kind=ingress.event_kind,
                occurred_at_ms=ingress.occurred_at_ms,
                observed_at_ms=ingress.observed_at_ms,
                principal_ref=ingress.principal_ref,
                subject_refs=ingress.subject_refs,
                evidence_class=ingress.evidence_class,
                source_credibility_milli=ingress.source_credibility_milli,
                privacy_scope=ingress.privacy_scope,
                content_object_id=ingress.content_object_id,
                content_sha256=ingress.content_sha256,
                dedupe_key=ingress.dedupe_key,
                causation_id=ingress.causation_id,
                correlation_id=ingress.correlation_id,
                previous_event_hash=previous_event_hash,
                event_hash="0" * 64,
                signer_key_id=self._writer_key_id,
                signature="0" * 128,
            ).with_computed_event_hash()
            signature = self._writer_private_key.sign(
                unsigned.event_hash.encode("ascii")
            ).hex()
            return unsigned.model_copy(update={"signature": signature})

        def receipt_factory(
            event: LifeEventEnvelope,
            duplicate: bool,
        ) -> LifeEventIngressReceipt:
            receipt_id = derive_life_ingress_receipt_id(
                ingress_id=ingress.ingress_id,
                source_sequence=ingress.source_sequence,
                event_hash=event.event_hash,
            )
            return LifeEventIngressReceipt(
                receipt_id=receipt_id,
                ingress_id=ingress.ingress_id,
                life_id=ingress.life_id,
                source_component_id=ingress.source_component_id,
                source_epoch=ingress.source_epoch,
                source_sequence=ingress.source_sequence,
                accepted=True,
                duplicate=duplicate,
                event_id=event.event_id,
                event_hash=event.event_hash,
                consumer_offset=ingress.source_sequence,
                received_at_ms=received_at_ms,
                receipt_sha256="0" * 64,
            ).with_computed_receipt_identity()

        commit = self._store.ingest_source_event(
            ingress,
            received_at_ms=received_at_ms,
            event_factory=event_factory,
            receipt_factory=receipt_factory,
        )
        if not verify_life_event_signature(
            commit.event, self._writer_private_key.public_key()
        ):
            raise LifeShadowStoreError("committed life event signature is invalid")
        return commit


__all__ = [
    "LifeEventIngestor",
    "LifeIngressAuthenticationError",
    "TrustedSourceKeys",
    "verify_life_event_signature",
]
