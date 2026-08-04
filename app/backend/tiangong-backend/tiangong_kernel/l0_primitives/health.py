from enum import Enum

class HealthState(str, Enum):
    HEALTHY = 'healthy'
    DEGRADED = 'degraded'
    CRITICAL = 'critical'
    RECOVERING = 'recovering'
    OFFLINE = 'offline'

class VitalityKind(str, Enum):
    ENERGY = 'energy'
    INTEGRITY = 'integrity'
    STABILITY = 'stability'
    AVAILABILITY = 'availability'
