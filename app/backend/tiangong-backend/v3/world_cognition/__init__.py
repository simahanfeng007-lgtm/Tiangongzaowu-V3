"""World Cognition Core public attachment for Tiangong V3.

External V3 systems should depend on this package only through
`WorldCognitionFacade`. Store, evidence, stability, consolidation and retrieval
modules are implementation details and must not become a second Runtime surface.
"""

from .facade import WorldCognitionFacade

__all__ = ["WorldCognitionFacade"]
