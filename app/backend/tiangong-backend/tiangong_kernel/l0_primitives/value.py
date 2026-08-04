from enum import Enum

class ValueKind(str, Enum):
    SAFETY = 'safety'
    HELPFULNESS = 'helpfulness'
    EXECUTION_POWER = 'execution_power'
    TRUTHFULNESS = 'truthfulness'
    STABILITY = 'stability'

class ObjectiveKind(str, Enum):
    TASK_SUCCESS = 'task_success'
    RISK_MINIMIZATION = 'risk_minimization'
    USER_SATISFACTION = 'user_satisfaction'
    RESOURCE_OPTIMIZATION = 'resource_optimization'
