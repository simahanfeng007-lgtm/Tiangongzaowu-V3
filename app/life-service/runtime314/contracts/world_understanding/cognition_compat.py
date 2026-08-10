"""Reference-only compatibility seam for the existing off-main Cognition contracts."""
from __future__ import annotations
from typing import Self
from pydantic import Field, model_validator
from ._base import PrivacyScope, WorldContractModel, WorldRecordRef
from ..models import OpaqueId, Sha256

class CognitionStatementRef(WorldContractModel):
    cognition_id:OpaqueId
    revision:int=Field(ge=1,le=9_007_199_254_740_991,strict=True)
    statement_sha256:Sha256
    life_id:OpaqueId
    world_scope_hash:Sha256
    principal_scope_hash:Sha256
    privacy_scope:PrivacyScope
    record_ref:WorldRecordRef
    @model_validator(mode="after")
    def check(self)->Self:
        if self.record_ref.record_id!=self.cognition_id: raise ValueError("cognition record ref id mismatch")
        if self.record_ref.revision!=self.revision: raise ValueError("cognition record ref revision mismatch")
        if self.record_ref.sha256!=self.statement_sha256: raise ValueError("cognition record ref hash mismatch")
        return self
