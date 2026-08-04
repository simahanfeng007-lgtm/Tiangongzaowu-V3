from enum import Enum

class MemoryKind(str, Enum):
    WORKING = 'working'
    EPISODIC = 'episodic'
    SEMANTIC = 'semantic'
    RELATIONAL = 'relational'
    PROCEDURAL = 'procedural'
    RULE = 'rule'

class MemoryState(str, Enum):
    ACTIVE = 'active'
    CANDIDATE = 'candidate'
    ARCHIVED = 'archived'
    QUARANTINED = 'quarantined'
    DELETED = 'deleted'
