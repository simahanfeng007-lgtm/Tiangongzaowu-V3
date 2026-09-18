"""CONTEXT_REQUEST handler reached only through the existing one physical ingress."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Callable

from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.query import WorldQuery
from world_understanding.world_state.store import MaterializedWorldSnapshot

from .enrichment import ContextProjectionCandidate
from .output_port import ContextOutputPort
from .projection import WorldContextProjector
from .request import compile_world_query
from .world_reference_context import build_world_reference_context_packet
from .capability_context import capability_context_reserved_tokens

_log = logging.getLogger("tiangong.world_context")


@dataclass(frozen=True, slots=True)
class ContextRequestDisposition:
    reason_code: str
    processed: bool


class WorldContextRequestHandler:
    def __init__(
        self,
        *,
        state_resolver: Callable[[WorldQuery], MaterializedWorldSnapshot | None],
        projector: WorldContextProjector,
        output_port: ContextOutputPort,
        projection_enricher: Callable[
            [WorldQuery, MaterializedWorldSnapshot], tuple[ContextProjectionCandidate, ...]
        ] | None = None,
    ) -> None:
        self.state_resolver = state_resolver
        self.projector = projector
        self.output_port = output_port
        self.projection_enricher = projection_enricher

    def __call__(self, envelope: WorldIngressEnvelope) -> ContextRequestDisposition:
        query = compile_world_query(envelope)
        snapshot = self.state_resolver(query)
        if snapshot is None:
            return ContextRequestDisposition("CONTEXT_STATE_UNAVAILABLE", False)
        enrichment: tuple[ContextProjectionCandidate, ...] = ()
        if self.projection_enricher is not None:
            try:
                enrichment = tuple(self.projection_enricher(query, snapshot))
            except Exception:
                # Repository/context enrichment is an optional read-only projection
                # improvement. It must never make the canonical P10 context path
                # unavailable when its cache/live-frame preconditions are absent.
                enrichment = ()
        capability = None
        reserved = 0
        try:
            capability = build_world_reference_context_packet(
                snapshot, query, token_estimator=self.projector.token_estimator)
            if capability is not None:
                reserved = capability_context_reserved_tokens(capability, token_estimator=self.projector.token_estimator)
        except ValueError as exc:
            # R1C1 is observational SHADOW, not a dynamic execution switch.
            # Never synthesize missing addresses or turn display failure into authority.
            reason = str(exc)
            code = reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", reason) else "CAPABILITY_CONTEXT_INVALID_RECORD"
            _log.warning("CAPABILITY_REFERENCE_CONTEXT_UNAVAILABLE: %s", code)
        result = self.projector.project(
            query,
            snapshot,
            enrichment_candidates=enrichment,
            reserved_tokens=reserved,
        )
        self.output_port.emit(query, result.packet, capability_packet=capability)
        return ContextRequestDisposition("CONTEXT_PACKET_EMITTED", True)


__all__ = ["ContextRequestDisposition", "WorldContextRequestHandler"]
