"""Reference retention metadata, not task state or execution authority."""
from __future__ import annotations
from dataclasses import dataclass
import re

from contracts import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import WorldScope


@dataclass(frozen=True, slots=True)
class RetainedWorldState:
    owner_id: str
    state_ref: WorldRecordRef
    scope: WorldScope

    def __post_init__(self) -> None:
        if (type(self.owner_id) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", self.owner_id) is None
                or type(self.state_ref) is not WorldRecordRef
                or self.state_ref.record_type != "world_state"
                or re.fullmatch(r"wst_[0-9a-f]{64}", self.state_ref.record_id) is None
                or type(self.state_ref.revision) is not int or self.state_ref.revision < 1
                or self.state_ref.sha256 is None or type(self.scope) is not WorldScope):
            raise ValueError("WORLD_RETENTION_IDENTITY_INVALID")
        WorldRecordRef.model_validate_json(self.state_ref.model_dump_json())
        WorldScope.model_validate_json(self.scope.model_dump_json())

    def payload(self) -> dict:
        return {"owner_id": self.owner_id, "state_ref": self.state_ref.model_dump(mode="json"),
                "scope": self.scope.model_dump(mode="json")}

    def to_dict(self) -> dict:
        value = self.payload()
        return {**value, "retention_sha256": canonical_sha256(value)}

    @classmethod
    def from_dict(cls, row: dict) -> RetainedWorldState:
        if type(row) is not dict or set(row) != {"owner_id", "state_ref", "scope", "retention_sha256"}:
            raise ValueError("WORLD_RETENTION_SCHEMA_INVALID")
        import json
        item = cls(row["owner_id"], WorldRecordRef.model_validate_json(json.dumps(row["state_ref"])),
                   WorldScope.model_validate_json(json.dumps(row["scope"])))
        if item.to_dict() != row:
            raise ValueError("WORLD_RETENTION_HASH_INVALID")
        return item
