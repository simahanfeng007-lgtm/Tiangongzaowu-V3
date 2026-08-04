from enum import Enum

class LearningKind(str, Enum):
    EPISODIC_LEARNING = 'episodic_learning'
    SEMANTIC_LEARNING = 'semantic_learning'
    PROCEDURAL_LEARNING = 'procedural_learning'
    FAILURE_LEARNING = 'failure_learning'
    FEEDBACK_LEARNING = 'feedback_learning'
    PREFERENCE_LEARNING = 'preference_learning'
    POLICY_LEARNING = 'policy_learning'

class LearningState(str, Enum):
    PROPOSED = 'proposed'
    ASSESSING = 'assessing'
    APPROVED = 'approved'
    ACTIVE = 'active'
    COMMITTED = 'committed'
    REJECTED = 'rejected'
    ROLLED_BACK = 'rolled_back'
    QUARANTINED = 'quarantined'
    ARCHIVED = 'archived'

class EvolutionKind(str, Enum):
    MEMORY_EVOLUTION = 'memory_evolution'
    SKILL_EVOLUTION = 'skill_evolution'
    TOOL_EVOLUTION = 'tool_evolution'
    PLUGIN_EVOLUTION = 'plugin_evolution'
    POLICY_EVOLUTION = 'policy_evolution'
    CONTRACT_EVOLUTION = 'contract_evolution'
    SCHEMA_EVOLUTION = 'schema_evolution'
    CODE_EVOLUTION = 'code_evolution'
    ARCHITECTURE_EVOLUTION = 'architecture_evolution'

class EvolutionState(str, Enum):
    PROPOSED = 'proposed'
    ASSESSING = 'assessing'
    APPROVED = 'approved'
    ACTIVE = 'active'
    COMMITTED = 'committed'
    REJECTED = 'rejected'
    ROLLED_BACK = 'rolled_back'
    QUARANTINED = 'quarantined'
    ARCHIVED = 'archived'
