"""Shared stateless source-compiler registry."""
from __future__ import annotations
from collections.abc import Mapping
from threading import RLock
from typing import Protocol
from contracts.world_understanding.ingress import WorldIngressEnvelope

class SourceCompiler(Protocol):
    def __call__(self,envelope:WorldIngressEnvelope)->object|None: ...

class CompilerRegistry:
    __slots__=("_lock","_compilers")
    def __init__(self,compilers:Mapping[str,SourceCompiler]|None=None)->None:
        self._lock=RLock(); self._compilers=dict(compilers or {})
    def register(self,source_kind:str,compiler:SourceCompiler,*,replace:bool=False)->None:
        if not source_kind or not callable(compiler): raise ValueError("source_kind and callable compiler are required")
        with self._lock:
            if source_kind in self._compilers and not replace: raise ValueError(f"compiler already registered for {source_kind}")
            self._compilers[source_kind]=compiler
    def resolve(self,source_kind:str)->SourceCompiler|None:
        with self._lock: return self._compilers.get(source_kind)
    def registered_source_kinds(self)->tuple[str,...]:
        with self._lock: return tuple(sorted(self._compilers))

__all__=["CompilerRegistry","SourceCompiler"]
