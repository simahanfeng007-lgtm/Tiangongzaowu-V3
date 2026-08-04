from enum import Enum

class EventState(str, Enum):
    RECORDED = 'recorded'
    PENDING = 'pending'
    APPLIED = 'applied'
    REJECTED = 'rejected'

class EventType(str, Enum):
    MESSAGE_ADDED = 'message_added'
    STATE_CHANGED = 'state_changed'
    TOOL_CALLED = 'tool_called'
    MEMORY_CHANGED = 'memory_changed'
    CONTEXT_COMPILED = 'context_compiled'
