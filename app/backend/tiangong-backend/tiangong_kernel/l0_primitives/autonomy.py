from enum import Enum

class AutonomyLevel(str, Enum):
    DORMANT = 'dormant'
    ASSISTED = 'assisted'
    SEMI_AUTONOMOUS = 'semi_autonomous'
    AUTONOMOUS = 'autonomous'
    FULLY_AUTONOMOUS = 'fully_autonomous'
