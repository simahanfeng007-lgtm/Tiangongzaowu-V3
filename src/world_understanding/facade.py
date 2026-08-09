"""Only public physical attachment point for World Understanding."""
from __future__ import annotations

from contracts.world_understanding.ingress import WorldIngressEnvelope

from .ingress import WorldUnderstandingIngress
from .ingress.compiler_registry import CompilerRegistry
from .ingress.receipt import IngressReceipt, make_receipt


class WorldUnderstandingFacade:
    """P2 facade: one `accept` method, synchronous and side-effect bounded."""

    def __init__(self, *, enabled: bool = False, compiler_registry: CompilerRegistry | None = None) -> None:
        self._enabled = bool(enabled)
        # OFF mode is deliberately lazy: no registry, dedup gate, condition,
        # directory, store, worker or other active subsystem is instantiated.
        self._ingress = None if not self._enabled else WorldUnderstandingIngress(compiler_registry or CompilerRegistry())

    def accept(self, envelope: WorldIngressEnvelope) -> IngressReceipt:
        if not self._enabled:
            return make_receipt(
                envelope_id=str(getattr(envelope, "envelope_id", "invalid")),
                dedup_key=str(getattr(envelope, "dedup_key", "0" * 64)),
                correlation_id=str(getattr(envelope, "correlation_id", "invalid")),
                disposition="OFF_NOOP",
                reason_code="WORLD_UNDERSTANDING_DISABLED",
                processed=False,
            )
        assert self._ingress is not None
        return self._ingress.accept(envelope)


__all__ = ["WorldUnderstandingFacade"]
