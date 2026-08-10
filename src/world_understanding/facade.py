"""Only public physical attachment point for World Understanding."""
from __future__ import annotations
from typing import Callable, Protocol
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord
from .ingress import WorldUnderstandingIngress
from .ingress.compiler_registry import CompilerRegistry
from .ingress.receipt import IngressReceipt,make_receipt

class _ContextDisposition(Protocol):
    reason_code: str
    processed: bool

class _SourceDisposition(Protocol):
    reason_code: str
    processed: bool

class WorldUnderstandingFacade:
    """Shared engine facade. No life-private engine instance or mutable life state."""
    __slots__=("_enabled","_ingress")
    def __init__(
        self,
        *,
        enabled: bool = False,
        compiler_registry: CompilerRegistry | None = None,
        context_request_handler: Callable[[WorldIngressEnvelope], _ContextDisposition] | None = None,
        source_handler: Callable[[WorldIngressEnvelope, tuple[DirectKnownRecord, ...]], _SourceDisposition] | None = None,
    ) -> None:
        self._enabled=bool(enabled)
        if not self._enabled:
            self._ingress=None
        else:
            if compiler_registry is None:
                from .source_compilers import build_p3_compilers
                compiler_registry=CompilerRegistry(build_p3_compilers())
            self._ingress=WorldUnderstandingIngress(
                compiler_registry,
                context_request_handler=context_request_handler,
                source_handler=source_handler,
            )
    def accept(self,envelope:WorldIngressEnvelope)->IngressReceipt:
        if not self._enabled:
            return make_receipt(envelope_id=str(getattr(envelope,'envelope_id','invalid')),dedup_key=str(getattr(envelope,'dedup_key','0'*64)),correlation_id=str(getattr(envelope,'correlation_id','invalid')),disposition='OFF_NOOP',reason_code='WORLD_UNDERSTANDING_DISABLED',processed=False)
        assert self._ingress is not None
        return self._ingress.accept(envelope)
__all__=['WorldUnderstandingFacade']
