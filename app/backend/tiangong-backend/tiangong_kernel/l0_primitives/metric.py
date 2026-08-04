from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class MetricValue:
    value: int | float
    def __post_init__(self):
        if isinstance(self.value, bool) or not isinstance(self.value, (int,float)): raise TypeError("MetricValue must be numeric")
