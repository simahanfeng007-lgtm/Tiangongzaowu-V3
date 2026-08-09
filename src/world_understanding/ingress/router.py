"""Routing inside the single ingress path."""
from __future__ import annotations

from contracts.world_understanding.ingress import WorldIngressEnvelope

from .compiler_registry import CompilerRegistry
from .receipt import IngressReceipt, make_receipt


class IngressRouter:
    def __init__(self, registry: CompilerRegistry) -> None:
        self._registry = registry

    def route(self, envelope: WorldIngressEnvelope) -> IngressReceipt:
        if envelope.envelope_kind == "CONTEXT_REQUEST":
            return make_receipt(
                envelope_id=envelope.envelope_id,
                dedup_key=envelope.dedup_key,
                correlation_id=envelope.correlation_id,
                disposition="ACCEPTED",
                reason_code="CONTEXT_REQUEST_ACCEPTED",
                processed=True,
            )

        if envelope.source_kind == "UNCLASSIFIED_SOURCE":
            return make_receipt(
                envelope_id=envelope.envelope_id,
                dedup_key=envelope.dedup_key,
                correlation_id=envelope.correlation_id,
                disposition="QUARANTINED",
                reason_code="UNCLASSIFIED_SOURCE",
                processed=False,
            )

        compiler = self._registry.resolve(envelope.source_kind)
        if compiler is None:
            return make_receipt(
                envelope_id=envelope.envelope_id,
                dedup_key=envelope.dedup_key,
                correlation_id=envelope.correlation_id,
                disposition="QUARANTINED",
                reason_code="NO_COMPILER_REGISTERED",
                processed=False,
            )

        compiler(envelope)
        return make_receipt(
            envelope_id=envelope.envelope_id,
            dedup_key=envelope.dedup_key,
            correlation_id=envelope.correlation_id,
            disposition="ACCEPTED",
            reason_code="SOURCE_ACCEPTED",
            processed=True,
        )


__all__ = ["IngressRouter"]
