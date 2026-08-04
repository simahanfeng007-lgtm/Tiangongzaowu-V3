from enum import Enum

class DecisionKind(str, Enum):
    ALLOW = 'allow'
    WARN = 'warn'
    BLOCK = 'block'
    DEFER = 'defer'
