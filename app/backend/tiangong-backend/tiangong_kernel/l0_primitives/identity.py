from __future__ import annotations
from dataclasses import dataclass
from typing import Generic, TypeVar

@dataclass(frozen=True, slots=True)
class CoreId:
    value: str
    def __post_init__(self):
        if not str(self.value).strip(): raise ValueError("CoreId cannot be empty")
    def __str__(self): return str(self.value)

@dataclass(frozen=True, slots=True)
class RefId:
    value: str
    def __post_init__(self):
        if not str(self.value).strip(): raise ValueError("RefId cannot be empty")
    def __str__(self): return str(self.value)

T=TypeVar("T")
@dataclass(frozen=True, slots=True)
class TypedRef(Generic[T]):
    kind: str
    value: RefId
