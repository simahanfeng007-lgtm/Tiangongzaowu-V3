"""P10 L8 Context Output. Public semantic output remains WorldContextPacket only."""
from .handler import ContextRequestDisposition, WorldContextRequestHandler
from .output_port import ContextEmission, ContextOutputPort
from .projection import ProjectionPolicy, ProjectionResult, WorldContextProjector
from .request import build_context_request_envelope, build_expansion_query, compile_world_query
from .slot import WorldContextSlot, build_world_context_slot, render_world_context_packet

__all__ = [
    "ContextEmission", "ContextOutputPort", "ContextRequestDisposition", "WorldContextRequestHandler",
    "ProjectionPolicy", "ProjectionResult", "WorldContextProjector",
    "build_context_request_envelope", "build_expansion_query", "compile_world_query",
    "WorldContextSlot", "build_world_context_slot", "render_world_context_packet",
]
