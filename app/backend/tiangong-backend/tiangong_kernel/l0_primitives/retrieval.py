from enum import Enum

class RetrievalKind(str, Enum):
    EVENT_RETRIEVAL = 'event_retrieval'
    MEMORY_RETRIEVAL = 'memory_retrieval'
    SEMANTIC_SEARCH = 'semantic_search'
    KEYWORD_SEARCH = 'keyword_search'
