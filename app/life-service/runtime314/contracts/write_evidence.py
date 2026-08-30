"""P19-R2 M3.1 WriteEvidenceV2 — the authoritative write-evidence contract.

Shared single validation authority for the v3 builder
(``v3/tool_result_contract.py``) and the Gateway store: both sides
validate through THIS model, so wire dicts cannot drift from the
contract. Strict / frozen / extra-forbid; every nested digest and the
top-level ``evidence_sha256`` must recompute — a ``model_copy`` with a
recomputed total hash but inconsistent inner digests still fails
``has_valid_identity()``.

Semantics locked by the M3.1 review:
* source ↔ strength consistency (broker/codex sources can never claim
  verified_final_state; verified strength requires authoritative post
  rows);
* observed_mutation_only must not smuggle verified post state;
* changed/deleted/verified_unchanged are canonical, sorted, unique and
  mutually non-overlapping; all three digests recompute;
* verified_final_state post rows are unique by path with a valid
  exists/sha256/size shape; post_state_sha256 recomputes;
* evidence_sha256 covers the FULL canonical semantic payload.
"""

from __future__ import annotations

import json as _json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from .canonical import canonical_sha256
from .models import SCHEMA_BASE, ContractModel, Sha256

WRITE_EVIDENCE_V2_SCHEMA = "tiangong.v3.write_evidence.v2"

RequestIdPattern = StringConstraints(pattern=r"^req_[0-9a-f]{64}$")
RunIdPattern = StringConstraints(pattern=r"^run_[0-9a-f]{64}$")
EffectIdPattern = StringConstraints(pattern=r"^eff_[0-9a-f]{64}$")

PathString = StringConstraints(min_length=1, max_length=512)

_VERIFIED_SOURCES = frozenset({"tool_pre_post", "tool_post_readback"})
_MUTATION_ONLY_SOURCES = frozenset({"sandbox_broker", "codex_tool_evidence"})


class WriteEvidenceV2Error(ValueError):
    """Raised when a payload cannot be a valid WriteEvidenceV2."""


def _digest_of_paths(paths: tuple[str, ...]) -> str:
    return canonical_sha256(list(paths))


class WriteEvidenceProvenance(ContractModel):
    upgraded_from: Literal["tiangong.v3.write_evidence.v1"]
    source: Literal[
        "sandbox_broker",
        "codex_tool_evidence",
        "tool_pre_post",
        "tool_post_readback",
    ]
    strength: Literal["observed_mutation_only", "verified_final_state"]


class WriteEvidencePlanned(ContractModel):
    target_paths: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _validate_paths(self) -> WriteEvidencePlanned:
        if list(self.target_paths) != sorted(set(self.target_paths)):
            raise ValueError("planned target_paths must be sorted and unique")
        if any(not path.strip() for path in self.target_paths):
            raise ValueError("planned target_paths items must be non-empty")
        return self


class WriteEvidenceMutation(ContractModel):
    changed_paths: tuple[str, ...] = Field(default=())
    deleted_paths: tuple[str, ...] = Field(default=())
    verified_unchanged_paths: tuple[str, ...] = Field(default=())
    changed_paths_digest: Sha256
    deleted_paths_digest: Sha256
    verified_unchanged_digest: Sha256

    @model_validator(mode="after")
    def _validate_mutation(self) -> WriteEvidenceMutation:
        for name in (
            "changed_paths",
            "deleted_paths",
            "verified_unchanged_paths",
        ):
            paths = getattr(self, name)
            if list(paths) != sorted(set(paths)):
                raise ValueError(f"{name} must be sorted and unique")
            if any(not path.strip() for path in paths):
                raise ValueError(f"{name} items must be non-empty")
        changed = set(self.changed_paths)
        deleted = set(self.deleted_paths)
        unchanged = set(self.verified_unchanged_paths)
        if changed & deleted:
            raise ValueError("a path cannot be both changed and deleted")
        if unchanged & (changed | deleted):
            raise ValueError(
                "verified_unchanged paths cannot appear in changed/deleted"
            )
        if self.changed_paths_digest != _digest_of_paths(self.changed_paths):
            raise ValueError("changed_paths_digest does not recompute")
        if self.deleted_paths_digest != _digest_of_paths(self.deleted_paths):
            raise ValueError("deleted_paths_digest does not recompute")
        if self.verified_unchanged_digest != _digest_of_paths(
            self.verified_unchanged_paths
        ):
            raise ValueError("verified_unchanged_digest does not recompute")
        return self


class WriteEvidencePostRow(ContractModel):
    path: Annotated[str, PathString]
    exists: bool
    sha256: str = Field(default="", max_length=64)
    size_bytes: int = Field(default=0, ge=0)
    # v1 readback rows carry classification flags; keep accepting them.
    is_file: bool | None = None
    is_dir: bool | None = None

    @model_validator(mode="after")
    def _validate_row(self) -> WriteEvidencePostRow:
        if self.sha256 and (
            len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("post row sha256 must be empty or 64-hex")
        return self


class WriteEvidenceFinalState(ContractModel):
    post_rows: tuple[WriteEvidencePostRow, ...] = Field(default=())
    post_state_sha256: Sha256

    @model_validator(mode="after")
    def _validate_final_state(self) -> WriteEvidenceFinalState:
        paths = [row.path for row in self.post_rows]
        if paths != sorted(set(paths)):
            raise ValueError("post rows must be unique and sorted by path")
        expected = canonical_sha256(
            [
                [row.path, row.exists, row.sha256, row.size_bytes]
                for row in self.post_rows
            ]
        )
        if self.post_state_sha256 != expected:
            raise ValueError("post_state_sha256 does not recompute")
        return self


class WriteEvidenceV2(ContractModel):
    """Authoritative bound write-evidence fact (evolved from v1)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:WriteEvidenceV2",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema: Literal["tiangong.v3.write_evidence.v2"] = WRITE_EVIDENCE_V2_SCHEMA
    request_id: Annotated[str, RequestIdPattern]
    run_id: Annotated[str, RunIdPattern]
    generation: int = Field(ge=0)
    effect_id: Annotated[str, EffectIdPattern]
    tool_name: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=160)
    provenance: WriteEvidenceProvenance
    planned: WriteEvidencePlanned = Field(default_factory=WriteEvidencePlanned)
    observed_mutation: WriteEvidenceMutation
    verified_final_state: WriteEvidenceFinalState
    observed_at_ms: int = Field(ge=0)
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def _validate_evidence(self) -> WriteEvidenceV2:
        source = self.provenance.source
        strength = self.provenance.strength
        rows = self.verified_final_state.post_rows
        if source in _MUTATION_ONLY_SOURCES and strength != "observed_mutation_only":
            raise ValueError(
                f"{source} evidence can never claim verified_final_state"
            )
        if strength == "verified_final_state":
            if source not in _VERIFIED_SOURCES:
                raise ValueError(
                    "verified_final_state requires tool_pre_post or"
                    " tool_post_readback provenance"
                )
            if not rows:
                raise ValueError(
                    "verified_final_state requires authoritative post rows"
                )
        else:
            # observed_mutation_only must not smuggle verified post state
            if rows:
                raise ValueError(
                    "observed_mutation_only evidence must not carry post rows"
                )
        if not self.has_valid_evidence_sha256():
            raise ValueError("evidence_sha256 does not recompute")
        return self

    def semantic_payload(self) -> dict:
        payload = self.model_dump(mode="json")
        payload.pop("evidence_sha256", None)
        return payload

    def computed_evidence_sha256(self) -> str:
        return canonical_sha256(self.semantic_payload())

    def has_valid_evidence_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_evidence_sha256()

    def has_valid_identity(self) -> bool:
        """Full trust-boundary check: the model validator already locks
        every nested digest and the source/strength rules; the remaining
        identity obligation is the total-hash recompute (also enforced at
        construction — this method exists for model_copy survivors)."""
        if not self.has_valid_evidence_sha256():
            return False
        try:
            clone = WriteEvidenceV2.model_validate_json(
                _json.dumps(
                    self.model_dump(mode="json"),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                strict=True,
            )
            return clone == self
        except Exception:
            return False

    def with_computed_sha256(self) -> WriteEvidenceV2:
        return self.model_copy(
            update={"evidence_sha256": self.computed_evidence_sha256()}
        )

    @classmethod
    def from_wire(cls, payload: dict) -> WriteEvidenceV2:
        """Validate an untrusted wire dict through the full contract.

        Goes through canonical JSON so list<->tuple coercion follows the
        same strict rules as every stored contract payload.
        """
        try:
            encoded = _json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            )
            return cls.model_validate_json(encoded, strict=True)
        except WriteEvidenceV2Error:
            raise
        except Exception as exc:
            raise WriteEvidenceV2Error(
                f"write_evidence.v2 payload failed contract validation:"
                f" {type(exc).__name__}"
            ) from exc


__all__ = [
    "WRITE_EVIDENCE_V2_SCHEMA",
    "WriteEvidenceFinalState",
    "WriteEvidenceMutation",
    "WriteEvidencePlanned",
    "WriteEvidencePostRow",
    "WriteEvidenceProvenance",
    "WriteEvidenceV2",
    "WriteEvidenceV2Error",
]
