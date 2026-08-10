"""The only two semantic output ports exposed by World Understanding."""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from .context_packet import WorldContextPacket
from .inquiry import WorldInquiry

@runtime_checkable
class WorldContextOutputPort(Protocol):
    def emit(self, packet: WorldContextPacket) -> None: ...

@runtime_checkable
class WorldInquiryOutputPort(Protocol):
    def emit(self, inquiry: WorldInquiry) -> None: ...
