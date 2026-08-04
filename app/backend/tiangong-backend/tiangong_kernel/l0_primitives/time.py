from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

@dataclass(frozen=True, slots=True)
class Timestamp:
    value: datetime
    def __post_init__(self):
        if not isinstance(self.value, datetime): raise TypeError("Timestamp.value must be datetime")
    @classmethod
    def now(cls): return cls(datetime.now(timezone.utc))
    def isoformat(self): return self.value.isoformat()

@dataclass(frozen=True, slots=True)
class Duration:
    seconds: float
    def __post_init__(self):
        if float(self.seconds) < 0: raise ValueError("Duration cannot be negative")
    def as_timedelta(self): return timedelta(seconds=float(self.seconds))
