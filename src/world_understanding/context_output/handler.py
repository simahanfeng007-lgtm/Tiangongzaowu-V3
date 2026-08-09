"""CONTEXT_REQUEST handler reached only through the existing one physical ingress."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.query import WorldQuery
from world_understanding.world_state.store import MaterializedWorldSnapshot

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
    ) -> None:
        self.state_resolver = state_resolver
        self.projector = projector
        self.output_port = output_port

    def __call__(self, envelope: WorldIngressEnvelope) -> ContextRequestDisposition:
        query = compile_world_query(envelope)
        snapshot = self.state_resolver(query)
        if snapshot is None:
            return ContextRequestDisposition("CONTEXT_STATE_UNAVAILABLE", False)
        result = self.projector.project(query, snapshot)
        self.output_port.emit(query, result.packet)
        return ContextRequestDisposition("CONTEXT_PACKET_EMITTED", True)


__all__ = ["ContextRequestDisposition", "WorldContextRequestHandler"]
