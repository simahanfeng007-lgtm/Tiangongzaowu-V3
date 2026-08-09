"""Internal implementation of the one World Understanding physical ingress."""
from __future__ import annotations

from contracts.world_understanding.ingress import WorldIngressEnvelope

from .compiler_registry import CompilerRegistry
from .dedup import DedupGate
from .receipt import IngressReceipt, make_receipt
from .router import IngressRouter
from .validation import IngressValidationError, validate_ingress_envelope
from world_understanding.scope_guard import ScopeMismatchError


class WorldUnderstandingIngress:
    """Single synchronous ingress pipeline. Not exported from the package root."""

    def __init__(self, registry: CompilerRegistry) -> None:
        self._dedup = DedupGate()
        self._router = IngressRouter(registry)

    def accept(self, envelope: WorldIngressEnvelope) -> IngressReceipt:
        try:
            validated = validate_ingress_envelope(envelope)
        except IngressValidationError as exc:
            return make_receipt(
                envelope_id=str(getattr(envelope, "envelope_id", "invalid")),
                dedup_key=str(getattr(envelope, "dedup_key", "0" * 64)),
                correlation_id=str(getattr(envelope, "correlation_id", "invalid")),
                disposition="REJECTED",
                reason_code=exc.reason_code,
                processed=False,
            )

        try:
            return self._dedup.run_once(validated.dedup_key, lambda: self._router.route(validated))
        except ScopeMismatchError as exc:
            return make_receipt(
                envelope_id=validated.envelope_id,
                dedup_key=validated.dedup_key,
                correlation_id=validated.correlation_id,
                disposition="REJECTED",
                reason_code=exc.reason_code,
                processed=False,
            )
        except (TypeError, ValueError):
            return make_receipt(
                envelope_id=validated.envelope_id,
                dedup_key=validated.dedup_key,
                correlation_id=validated.correlation_id,
                disposition="REJECTED",
                reason_code="COMPILER_OUTPUT_INVALID",
                processed=False,
            )
        except Exception:
            return make_receipt(
                envelope_id=validated.envelope_id,
                dedup_key=validated.dedup_key,
                correlation_id=validated.correlation_id,
                disposition="REJECTED",
                reason_code="COMPILER_FAILURE",
                processed=False,
            )


__all__ = ["WorldUnderstandingIngress"]
