"""Single-file release authority binding components, contracts, actions, and Skills."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_sha256
from .delivery import ComponentManifest
from .models import ContractModel, OpaqueId, SCHEMA_BASE, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Sha256


def _portable_relative_path(value: str) -> str:
    if "\\" in value or value.startswith("/") or value.endswith("/") or ":" in value:
        raise ValueError("release input path must be portable and relative")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("release input path contains an unsafe segment")
    return value


class ReleaseInputDigest(ContractModel):
    input_id: OpaqueId
    relative_path: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=1, le=4_398_046_511_104)
    sha256: Sha256

    _validate_path = field_validator("relative_path")(_portable_relative_path)


class ReleaseSourceTree(ContractModel):
    tree_id: OpaqueId
    roots: tuple[str, ...] = Field(min_length=1, max_length=64)
    file_count: int = Field(ge=1, le=1_000_000)
    size_bytes: int = Field(ge=1, le=4_398_046_511_104)
    tree_sha256: Sha256

    @field_validator("roots")
    @classmethod
    def validate_roots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("release source roots must be sorted and unique")
        return tuple(_portable_relative_path(item) for item in value)


class ReleaseManifest(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ReleaseManifest",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    release_schema: Literal["tiangong.release-manifest.v1"] = "tiangong.release-manifest.v1"
    release_id: OpaqueId
    product: Literal["tiangong-desktop"] = "tiangong-desktop"
    product_version: OpaqueId
    build_id: OpaqueId
    release_channel: Literal["development", "candidate", "stable"]
    generated_at_ms: int = Field(ge=0)
    component_manifest: ComponentManifest
    contract_artifact_manifest_file_sha256: Sha256
    contract_artifact_manifest_sha256: Sha256
    contract_schema_bundle_sha256: Sha256
    action_registry_sha256: Sha256
    capability_manifest_sha256: Sha256
    skill_index_sha256: Sha256
    skill_catalog_sha256: Sha256
    release_policy_sha256: Sha256
    inputs: tuple[ReleaseInputDigest, ...] = Field(min_length=1, max_length=128)
    source_trees: tuple[ReleaseSourceTree, ...] = Field(min_length=1, max_length=16)
    production_claim: bool = False
    release_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        component = self.component_manifest
        if not component.has_valid_manifest_sha256():
            raise ValueError("release component manifest digest is invalid")
        if (
            component.product_version != self.product_version
            or component.generated_at_ms != self.generated_at_ms
            or component.contract_schema_bundle_hash != self.contract_schema_bundle_sha256
            or component.capability_manifest_hash != self.capability_manifest_sha256
            or component.skill_index_hash != self.skill_index_sha256
            or component.release_policy_hash != self.release_policy_sha256
            or component.production_claim != self.production_claim
        ):
            raise ValueError("release manifest disagrees with its component manifest")
        components = {item.component_id: item for item in component.components}
        expected_roles = {
            "tiangong-backend": "execution",
            "tiangong-communication-service": "communication",
            "tiangong-desktop": "desktop",
            "tiangong-life-service": "life",
            "tiangong-total-gateway": "orchestrator",
        }
        if set(components) != set(expected_roles) or any(
            components[component_id].role != role
            for component_id, role in expected_roles.items()
        ):
            raise ValueError("release manifest component set or roles are invalid")
        if (
            components["tiangong-desktop"].version != self.product_version
            or components["tiangong-desktop"].build_id != self.build_id
        ):
            raise ValueError("release desktop identity disagrees with release identity")
        input_ids = tuple(item.input_id for item in self.inputs)
        input_paths = tuple(item.relative_path for item in self.inputs)
        if input_ids != tuple(sorted(set(input_ids))) or len(set(input_paths)) != len(input_paths):
            raise ValueError("release inputs must have sorted unique identities and paths")
        required = {
            "action-registry",
            "backend-release",
            "capability-manifest",
            "desktop-build-info",
            "desktop-package",
            "python-project",
            "skill-index",
            "source-snapshot",
        }
        if not required.issubset(input_ids):
            raise ValueError("release manifest is missing a required source input")
        by_id = {item.input_id: item for item in self.inputs}
        if (
            by_id["action-registry"].sha256 != self.action_registry_sha256
            or by_id["capability-manifest"].sha256 != self.capability_manifest_sha256
            or by_id["skill-index"].sha256 != self.skill_index_sha256
        ):
            raise ValueError("release source input digest disagrees with authority fields")
        tree_ids = tuple(item.tree_id for item in self.source_trees)
        if tree_ids != tuple(sorted(set(tree_ids))):
            raise ValueError("release source trees must be sorted and unique")
        if set(tree_ids) != {
            "communication-source",
            "desktop-source",
            "gateway-source",
            "life-source",
        }:
            raise ValueError("release source tree set is incomplete")
        if self.production_claim and self.release_channel != "stable":
            raise ValueError("production release must use the stable channel")
        if not self.production_claim and self.release_channel == "stable":
            raise ValueError("stable release cannot disclaim production")
        return self

    def computed_release_manifest_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"release_manifest_sha256"})
        )

    def has_valid_release_manifest_sha256(self) -> bool:
        return self.release_manifest_sha256 == self.computed_release_manifest_sha256()

    def with_computed_release_manifest_sha256(self) -> Self:
        return self.model_copy(
            update={"release_manifest_sha256": self.computed_release_manifest_sha256()}
        )


__all__ = ["ReleaseInputDigest", "ReleaseManifest", "ReleaseSourceTree"]
