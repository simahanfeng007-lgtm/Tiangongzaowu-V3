from enum import Enum

class MessageRole(str, Enum):
    SYSTEM = 'system'
    USER = 'user'
    ASSISTANT = 'assistant'
    TOOL = 'tool'

class MessageState(str, Enum):
    RECORDED = 'recorded'
    PENDING = 'pending'
    STREAMING = 'streaming'
    COMPLETED = 'completed'
    FAILED = 'failed'
