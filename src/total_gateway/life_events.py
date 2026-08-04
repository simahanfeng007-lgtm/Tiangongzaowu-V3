"""Gateway publication of signed external facts to the single life writer."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    LifeEventIngress,
    TransitionEvent,
    canonical_json_bytes,
    canonical_sha256,
    derive_effect_identity,
    derive_life_ingress_id,
)

from .object_store import ContentAddressedObjectStore
from .outbox import OutboxIntent, derive_outbox_id
from .store import GatewayStateStore, OutboxRecord, StoreNotFoundError


@dataclass(frozen=True, slots=True)
class LifePublication:
    ingress: LifeEventIngress
    outbox: OutboxRecord
    created_by_this_call: bool


class LifeIngressTransport(Protocol):
    def ingest(
        self,
        ingress: LifeEventIngress,
        *,
        received_at_ms: int,
    ): ...


class GatewayLifeEventPublisher:
    """Converts durable gateway transitions into source-signed life ingress facts."""

    SOURCE_COMPONENT_ID = "tiangong-total-gateway"

    def __init__(
        self,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        *,
        life_id: str,
        source_epoch: int,
        signer_key_id: str,
        signing_key: Ed25519PrivateKey,
    ) -> None:
        if not life_id or source_epoch < 1 or not signer_key_id:
            raise ValueError("gateway life publisher configuration is invalid")
        self._store = store
        self._objects = objects
        self._life_id = life_id
        self._source_epoch = source_epoch
        self._signer_key_id = signer_key_id
        self._signing_key = signing_key

    @staticmethod
    def _source_kind(event: TransitionEvent) -> str:
        if event.machine == "delivery":
            return "tool_receipt"
        if event.machine in {"execution", "artifact"}:
            return "execution"
        if event.event_type in {"request.received", "request.ingress_accepted"}:
            return "user_message"
        return "execution"

    def _build_ingress(
        self,
        event: TransitionEvent,
        *,
        source_sequence: int,
        content_object_id: str,
        content_sha256: str,
        principal_ref: str,
        observed_at_ms: int,
    ) -> LifeEventIngress:
        dedupe_key = canonical_sha256(
            {
                "domain": "tiangong.gateway.life-event-dedupe.v1",
                "event_id": event.event_id,
                "event_sha256": event.event_sha256,
            }
        )
        ingress_id = derive_life_ingress_id(
            life_id=self._life_id,
            source_component_id=self.SOURCE_COMPONENT_ID,
            source_epoch=self._source_epoch,
            source_sequence=source_sequence,
            dedupe_key=dedupe_key,
        )
        unsigned = LifeEventIngress(
            ingress_id=ingress_id,
            life_id=self._life_id,
            source_component_id=self.SOURCE_COMPONENT_ID,
            source_epoch=self._source_epoch,
            source_sequence=source_sequence,
            source_kind=self._source_kind(event),
            event_kind=f"gateway.{event.event_type}",
            occurred_at_ms=event.occurred_at_ms,
            observed_at_ms=observed_at_ms,
            principal_ref=principal_ref,
            subject_refs=tuple(sorted({event.request_id, event.run_id})),
            evidence_class="execution_verified",
            source_credibility_milli=1000,
            privacy_scope="private",
            content_object_id=content_object_id,
            content_sha256=content_sha256,
            dedupe_key=dedupe_key,
            request_id=event.request_id,
            run_id=event.run_id,
            generation=event.generation,
            causation_id=event.event_id,
            correlation_id=event.request_id,
            signer_key_id=self._signer_key_id,
            ingress_sha256="0" * 64,
            signature="0" * 128,
        )
        hashed = unsigned.model_copy(
            update={"ingress_sha256": unsigned.computed_ingress_sha256()}
        )
        return hashed.model_copy(
            update={
                "signature": self._signing_key.sign(
                    hashed.ingress_sha256.encode("ascii")
                ).hex()
            }
        )

    def publish_state_event(
        self,
        event: TransitionEvent,
        *,
        published_at_ms: int,
    ) -> LifePublication:
        if published_at_ms < event.occurred_at_ms:
            raise ValueError("life publication predates its state event")
        sequence = self._store.get_life_source_sequence(event.event_id)
        if sequence is None:
            raise StoreNotFoundError("life publication state event does not exist")
        inbound = self._store.get_request_envelope(event.request_id)
        if inbound is None:
            raise StoreNotFoundError("life publication request envelope is unavailable")
        run_sequence = self._store.get_run_sequence_for_binding(
            event.request_id,
            run_id=event.run_id,
            generation=event.generation,
        )
        if run_sequence is None:
            raise StoreNotFoundError("life publication generation fence is unavailable")

        event_payload = canonical_json_bytes(event.model_dump(mode="json"))
        content = self._objects.put_bytes(
            event_payload,
            kind="payload",
            tenant_id=inbound.tenant_id,
            link_account_id=inbound.link_account_id,
            conversation_scope_hash=inbound.conversation_scope_hash,
            created_at_ms=published_at_ms,
        ).reference
        self._store.record_object_owner(
            object_id=content.object_id,
            object_sha256=content.sha256,
            owner_kind="LIFE_EVENT",
            owner_id=event.event_id,
            request_id=event.request_id,
            run_id=event.run_id,
            generation=event.generation,
            created_at_ms=published_at_ms,
        )
        ingress = self._build_ingress(
            event,
            source_sequence=sequence,
            content_object_id=content.object_id,
            content_sha256=content.sha256,
            principal_ref=inbound.sender_ref,
            observed_at_ms=published_at_ms,
        )
        ingress_payload = canonical_json_bytes(ingress.model_dump(mode="json"))
        payload = self._objects.put_bytes(
            ingress_payload,
            kind="payload",
            tenant_id=inbound.tenant_id,
            link_account_id=inbound.link_account_id,
            conversation_scope_hash=inbound.conversation_scope_hash,
            created_at_ms=published_at_ms,
        ).reference
        effect = derive_effect_identity(
            request_id=event.request_id,
            run_id=event.run_id,
            run_sequence=run_sequence,
            generation=event.generation,
            effect_kind="control",
            ordinal=sequence,
            intent_sha256=ingress.ingress_sha256,
        )
        outbox = OutboxIntent(
            outbox_id=derive_outbox_id(
                effect.effect_id,
                "tiangong-life-service",
                payload.sha256,
            ),
            effect_id=effect.effect_id,
            request_id=event.request_id,
            run_id=event.run_id,
            generation=event.generation,
            destination_component_id="tiangong-life-service",
            intent_kind="LIFE_EVENT",
            payload_object_id=payload.object_id,
            payload_sha256=payload.sha256,
            created_at_ms=published_at_ms,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        record, created = self._store.attach_life_event_outbox(event.event_id, outbox)
        return LifePublication(ingress, record, created)

    def recover_missing(
        self,
        *,
        published_at_ms: int,
        limit: int = 100,
    ) -> tuple[LifePublication, ...]:
        events = self._store.list_state_events_missing_life_outbox(limit=limit)
        return tuple(
            self.publish_state_event(event, published_at_ms=published_at_ms)
            for event in events
        )


class LifeEventOutboxWorker:
    """Retries an idempotent life ingress until the durable receipt is acknowledged."""

    def __init__(
        self,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        transport: LifeIngressTransport,
        *,
        worker_id: str,
    ) -> None:
        if not worker_id:
            raise ValueError("life outbox worker identity is invalid")
        self._store = store
        self._objects = objects
        self._transport = transport
        self._worker_id = worker_id

    def _load_ingress(self, record: OutboxRecord) -> LifeEventIngress:
        raw = self._objects.read_bytes(record.intent.payload_object_id)
        if hashlib.sha256(raw).hexdigest() != record.intent.payload_sha256:
            raise ValueError("life outbox payload digest is invalid")
        ingress = LifeEventIngress.model_validate_json(raw, strict=True)
        if canonical_json_bytes(ingress.model_dump(mode="json")) != raw:
            raise ValueError("life outbox payload is not canonical")
        return ingress

    def dispatch_next(self, *, now_ms: int) -> bool:
        candidates = self._store.list_unfinished_life_outbox(limit=10_000)
        if not candidates:
            return False
        loaded = tuple((self._load_ingress(record), record) for record in candidates)
        ingress, candidate = min(
            loaded,
            key=lambda item: (
                item[0].source_epoch,
                item[0].source_sequence,
                item[1].intent.outbox_id,
            ),
        )
        claimable = (
            candidate.state == "PENDING" and candidate.available_at_ms <= now_ms
        ) or (
            candidate.state == "CLAIMED"
            and candidate.claim_expires_at_ms is not None
            and candidate.claim_expires_at_ms <= now_ms
        )
        if not claimable:
            return False
        claimed = self._store.claim_outbox(
            candidate.intent.outbox_id,
            worker_id=self._worker_id,
            now_ms=now_ms,
            lease_ms=30_000,
        )
        receipt = self._transport.ingest(ingress, received_at_ms=now_ms)
        if hasattr(receipt, "receipt"):
            receipt = receipt.receipt
        if (
            receipt.ingress_id != ingress.ingress_id
            or receipt.source_sequence != ingress.source_sequence
            or not receipt.has_valid_receipt_sha256()
        ):
            raise ValueError("life ingress transport returned an invalid receipt")
        self._store.record_outbox_result(
            claimed.intent.outbox_id,
            worker_id=self._worker_id,
            outcome="ACKED",
            result_sha256=receipt.receipt_sha256,
            dispatched_at_ms=now_ms,
        )
        return True


__all__ = [
    "GatewayLifeEventPublisher",
    "LifeEventOutboxWorker",
    "LifeIngressTransport",
    "LifePublication",
]
