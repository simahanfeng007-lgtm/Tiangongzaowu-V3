"""World Understanding public attachment surface.

P2 exposes exactly one physical ingress method: WorldUnderstandingFacade.accept.
The returned IngressReceipt is an acknowledgement/control object, not a semantic
World Understanding output.
"""
from .facade import WorldUnderstandingFacade
from .ingress.receipt import IngressDisposition, IngressReceipt

__all__ = ["WorldUnderstandingFacade", "IngressDisposition", "IngressReceipt"]
