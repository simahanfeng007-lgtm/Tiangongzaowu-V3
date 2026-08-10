"""World Understanding L5 Cognition.

The deterministic Cognition Core is absorbed here as an internal L5 subsystem.
The only public World Understanding attachment remains WorldUnderstandingFacade.
Legacy v3.world_cognition imports are compatibility re-exports and do not own state.
"""
from .bridge import AdaptedWorldEvidence, CognitionL5Bridge, CognitionWorldEvidenceError, adapt_world_record_to_evidence
from .l5 import CognitionL5View, to_l5_view
from .consolidator import CognitionConsolidator, CognitionProposal, ConsolidationResult
from .evidence import CognitionEvidenceLedger
from .retrieval import CognitionRetriever
from .stability import StabilityPolicy, StabilityReport
from .store import WorldCognitionStore, CognitionIntegrityError, CognitionConflictError

__all__ = [
    "AdaptedWorldEvidence", "CognitionL5Bridge", "CognitionWorldEvidenceError", "adapt_world_record_to_evidence",
    "CognitionL5View", "to_l5_view", "CognitionConsolidator", "CognitionProposal", "ConsolidationResult",
    "CognitionEvidenceLedger", "CognitionRetriever", "StabilityPolicy", "StabilityReport",
    "WorldCognitionStore", "CognitionIntegrityError", "CognitionConflictError",
]
