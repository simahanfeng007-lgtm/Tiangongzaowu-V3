from enum import Enum

class LifecyclePhase(str, Enum):
    DORMANT = 'dormant'
    AWAKENING = 'awakening'
    ASSISTED = 'assisted'
    SEMI_AUTONOMOUS = 'semi_autonomous'
    AUTONOMOUS = 'autonomous'
    SUSPENDED = 'suspended'
