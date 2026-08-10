"""Legacy import compatibility for the absorbed L5 Cognition core.

This module owns no implementation or state. The canonical implementation is
`world_understanding.cognition` and WorldUnderstandingFacade remains the only
World Understanding physical attachment.
"""
from world_understanding.cognition.facade import WorldCognitionFacade
__all__ = ["WorldCognitionFacade"]
