"""Routing inside the single ingress path."""
from __future__ import annotations
from typing import Callable, Protocol
from contracts.world_understanding.ingress import WorldIngressEnvelope
from .compiler_boundary import validate_compiler_output
from .compiler_registry import CompilerRegistry
from .receipt import IngressReceipt,make_receipt

class _ContextDisposition(Protocol):
    reason_code: str
    processed: bool

class IngressRouter:
    def __init__(self,registry:CompilerRegistry,context_request_handler:Callable[[WorldIngressEnvelope],_ContextDisposition]|None=None)->None:
        self._registry=registry
        self._context_request_handler=context_request_handler
    def route(self,envelope:WorldIngressEnvelope)->IngressReceipt:
        if envelope.envelope_kind=="CONTEXT_REQUEST":
            if self._context_request_handler is None:
                # Preserve exact P2 behavior when no L8 output port is attached.
                return make_receipt(envelope_id=envelope.envelope_id,dedup_key=envelope.dedup_key,correlation_id=envelope.correlation_id,disposition="ACCEPTED",reason_code="CONTEXT_REQUEST_ACCEPTED",processed=True)
            outcome=self._context_request_handler(envelope)
            return make_receipt(
                envelope_id=envelope.envelope_id,
                dedup_key=envelope.dedup_key,
                correlation_id=envelope.correlation_id,
                disposition="ACCEPTED",
                reason_code=str(outcome.reason_code),
                processed=bool(outcome.processed),
            )
        if envelope.source_kind=="UNCLASSIFIED_SOURCE":
            return make_receipt(envelope_id=envelope.envelope_id,dedup_key=envelope.dedup_key,correlation_id=envelope.correlation_id,disposition="QUARANTINED",reason_code="UNCLASSIFIED_SOURCE",processed=False)
        compiler=self._registry.resolve(envelope.source_kind)
        if compiler is None:
            return make_receipt(envelope_id=envelope.envelope_id,dedup_key=envelope.dedup_key,correlation_id=envelope.correlation_id,disposition="QUARANTINED",reason_code="NO_COMPILER_REGISTERED",processed=False)
        validate_compiler_output(envelope,compiler(envelope))
        return make_receipt(envelope_id=envelope.envelope_id,dedup_key=envelope.dedup_key,correlation_id=envelope.correlation_id,disposition="ACCEPTED",reason_code="SOURCE_ACCEPTED",processed=True)
__all__=["IngressRouter"]
