from enum import Enum

class ObservationKind(str, Enum):
    METRIC = 'metric'
    EVENT = 'event'
    STATE = 'state'
    FEEDBACK = 'feedback'

class ObservationQuality(str, Enum):
    RAW = 'raw'
    NORMALIZED = 'normalized'
    PARTIAL = 'partial'
    LOW_CONFIDENCE = 'low_confidence'
    CONFLICTED = 'conflicted'

from dataclasses import dataclass
from .identity import RefId
@dataclass(frozen=True, slots=True)
class ObservationRef:
    value: RefId
