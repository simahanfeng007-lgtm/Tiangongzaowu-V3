"""ACK-only ingress receipts.

IngressReceipt deliberately carries no semantic world data and no authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from contracts.canonical import canonical_sha256

IngressDisposition = Literal["ACCEPTED", "QUARANTINED", "REJECTED", "OFF_NOOP"]


def derive_receipt_id(*, envelope_id: str, dedup_key: str, disposition: str, reason_code: str) -> str:
    return "wrcpt_" + canonical_sha256({
        "domain": "tiangong.world.ingress-receipt-id.v1",
        "envelope_id": envelope_id,
        "dedup_key": dedup_key,
        "disposition": disposition,
        "reason_code": reason_code,
    })


@dataclass(frozen=True, slots=True)
class IngressReceipt:
    schema_version: Literal["tiangong.world-understanding.ingress-receipt.v1"]
    receipt_id: str
    envelope_id: str
    dedup_key: str
    correlation_id: str
    disposition: IngressDisposition
    reason_code: str
    processed: bool
    ack_only: Literal[True] = True
    semantic_output: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    empirical_evidence_weight_milli: Literal[0] = 0
    receipt_sha256: str = ""

    def payload_for_hash(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("receipt_sha256", None)
        return payload

    def has_valid_receipt_sha256(self) -> bool:
        return self.receipt_sha256 == canonical_sha256(self.payload_for_hash())


def make_receipt(*, envelope_id: str, dedup_key: str, correlation_id: str,
                 disposition: IngressDisposition, reason_code: str, processed: bool) -> IngressReceipt:
    receipt = IngressReceipt(
        schema_version="tiangong.world-understanding.ingress-receipt.v1",
        receipt_id=derive_receipt_id(
            envelope_id=envelope_id,
            dedup_key=dedup_key,
            disposition=disposition,
            reason_code=reason_code,
        ),
        envelope_id=envelope_id,
        dedup_key=dedup_key,
        correlation_id=correlation_id,
        disposition=disposition,
        reason_code=reason_code,
        processed=processed,
    )
    return IngressReceipt(**{**asdict(receipt), "receipt_sha256": canonical_sha256(receipt.payload_for_hash())})


__all__ = ["IngressDisposition", "IngressReceipt", "derive_receipt_id", "make_receipt"]
