"""Source-owned life scheduler boundary: emit intents, never execute actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from contracts import ActionIntent, canonical_sha256


class ActionIntentTransport(Protocol):
    def submit(self, intent: ActionIntent) -> "ActionIntentReceipt": ...


@dataclass(frozen=True, slots=True)
class ActionIntentReceipt:
    intent_id: str
    intent_sha256: str
    status: str
    policy_decision_id: str | None
    receipt_sha256: str

    def computed_sha256(self) -> str:
        return canonical_sha256(
            {
                "intent_id": self.intent_id,
                "intent_sha256": self.intent_sha256,
                "status": self.status,
                "policy_decision_id": self.policy_decision_id,
            }
        )


class LifeActionIntentEmitter:
    """The life process owns proposals only; the transport points to Gateway."""

    def __init__(self, transport: ActionIntentTransport) -> None:
        self._transport = transport

    def submit(self, intent: ActionIntent) -> ActionIntentReceipt:
        if intent.source != "life_scheduler" or not intent.has_valid_sha256():
            raise ValueError("life scheduler action intent is invalid")
        receipt = self._transport.submit(intent)
        if (
            receipt.intent_id != intent.intent_id
            or receipt.intent_sha256 != intent.intent_sha256
            or receipt.status not in {"REJECTED", "CONFIRMATION_REQUIRED", "AUTHORIZED"}
            or receipt.receipt_sha256 != receipt.computed_sha256()
        ):
            raise ValueError("Gateway returned an invalid action-intent receipt")
        return receipt

    def submit_self_will(
        self,
        intent: ActionIntent,
        *,
        source_inquiry_id: str,
        source_inquiry_sha256: str,
    ) -> ActionIntentReceipt:
        """Submit a Self-Will accepted inquiry through the existing Gateway transport.

        The inquiry is provenance data only. It must be bound as EXTERNAL_DATA
        and can never be presented as user/preauthorized/directory authority.
        """
        if intent.source != "life_scheduler" or not intent.has_valid_sha256():
            raise ValueError("self-will Gateway intent must remain a life_scheduler ActionIntent")
        exact = [
            ref for ref in intent.source_refs
            if ref.object_id == source_inquiry_id and ref.sha256 == source_inquiry_sha256
        ]
        if len(exact) != 1 or exact[0].source_type != "EXTERNAL_DATA":
            raise ValueError("self-will inquiry provenance is missing or authoritative")
        if any(
            ref.object_id == source_inquiry_id
            and ref.source_type in {"CURRENT_USER_INSTRUCTION", "PREAUTHORIZED_USER_FACT", "AUTHENTICATED_DIRECTORY"}
            for ref in intent.source_refs
        ):
            raise ValueError("WorldInquiry cannot be represented as user authorization")
        return self.submit(intent)


__all__ = [
    "ActionIntentReceipt",
    "ActionIntentTransport",
    "LifeActionIntentEmitter",
]
