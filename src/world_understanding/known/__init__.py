from .set import KnownSet,KnownSetSnapshot,KnownRecord,ActiveCutOverflow,InvalidKnownRecord,StaleKnownDependency,known_ref,proposition_signature
from .freshness import KnownFreshnessDecision,VOLATILE_SOURCE_KINDS,evaluate_known_freshness,record_source_versions,source_key,source_version
from .rule import RuleSpec,DerivedCandidate,DeterministicRule,ClosureDiagnostic
from .registry import RuleRegistry
from .authority_matrix import AuthorityIntersectionError,DerivedEnvelope,intersect_authority
from .closure import KnownClosureEngine,ClosureResult,ClosureLimitExceeded
from .rules import build_p4_rules

def build_default_p4_engine(*,max_rounds:int=64,max_records:int=100_000)->KnownClosureEngine:
    return KnownClosureEngine(RuleRegistry(build_p4_rules()),max_rounds=max_rounds,max_records=max_records)
