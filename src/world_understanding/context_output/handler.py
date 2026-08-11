"""CONTEXT_REQUEST handler reached only through the existing one physical ingress."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.query import WorldQuery
from world_understanding.world_state.store import MaterializedWorldSnapshot

from .enrichment import ContextProjectionCandidate
from .output_port import ContextOutputPort
from .projection import WorldContextProjector
from .request import compile_world_query


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
        result = self.projector.project(
            query,
            snapshot,
            enrichment_candidates=enrichment,
        )
        self.output_port.emit(query, result.packet)
        return ContextRequestDisposition("CONTEXT_PACKET_EMITTED", True)


__all__ = ["ContextRequestDisposition", "WorldContextRequestHandler"]
