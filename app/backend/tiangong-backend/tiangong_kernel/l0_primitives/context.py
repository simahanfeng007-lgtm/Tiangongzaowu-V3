from enum import Enum

class ContextKind(str, Enum):
    CONVERSATION = 'conversation'
    TASK = 'task'
    MEMORY = 'memory'
    SYSTEM = 'system'
    TOOL = 'tool'
