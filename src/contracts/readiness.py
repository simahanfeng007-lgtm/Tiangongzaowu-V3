"""Fail-closed readiness evidence and digest-consistency decisions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_sha256
from .delivery import ComponentManifest
from .models import (
    ContractModel,
    OpaqueId,
    ReasonCode,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
)


REQUIRED_SERVICE_ROLES = {
    "tiangong-backend": "execution",
    "tiangong-communication-service": "communication",
    "tiangong-life-service": "life",
    "tiangong-total-gateway": "orchestrator",
}


def _schema_config(name: str) -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:{name}",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )


class ExpectedServiceComponent(ContractModel):
    component_id: OpaqueId
    role: Literal["orchestrator", "execution", "life", "communication"]
    version: OpaqueId
    build_id: OpaqueId
    executable_sha256: Sha256
    schema_bundle_sha256: Sha256


class ReadinessExpectation(ContractModel):
    model_config = _schema_config("ReadinessExpectation")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    expectation_id: OpaqueId
    gateway_epoch: int = Field(ge=1)
    component_manifest_sha256: Sha256
    schema_bundle_sha256: Sha256
    capability_manifest_sha256: Sha256
    skill_index_sha256: Sha256
    release_policy_sha256: Sha256
    contract_artifact_manifest_sha256: Sha256
    components: tuple[ExpectedServiceComponent, ...] = Field(min_length=4, max_length=4)
    expectation_sha256: Sha256

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        ids = tuple(item.component_id for item in self.components)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("readiness components must be sorted and unique")
        if set(ids) != set(REQUIRED_SERVICE_ROLES):
            raise ValueError("readiness expectation requires the four service components")
        for item in self.components:
            if item.role != REQUIRED_SERVICE_ROLES[item.component_id]:
                raise ValueError("readiness component role is inconsistent")
            if item.schema_bundle_sha256 != self.schema_bundle_sha256:
                raise ValueError("service component carries a different schema bundle")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"expectation_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.expectation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"expectation_sha256": self.computed_sha256()})


class ComponentReadinessEvidence(ContractModel):
    model_config = _schema_config("ComponentReadinessEvidence")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    evidence_id: OpaqueId
    component_id: OpaqueId
    component_role: Literal["orchestrator", "execution", "life", "communication"]
    instance_id: OpaqueId
    version: OpaqueId
    build_id: OpaqueId
    executable_sha256: Sha256
    gateway_epoch: int = Field(ge=1)
    component_manifest_sha256: Sha256
    schema_bundle_sha256: Sha256
    capability_manifest_sha256: Sha256
    skill_index_sha256: Sha256
    release_policy_sha256: Sha256
    contract_artifact_manifest_sha256: Sha256
    health_check_passed: bool
    observed_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    evidence_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_sha256()})


class ReadinessFailure(ContractModel):
    component_id: OpaqueId | None = None
    reason_code: ReasonCode
    expected_sha256: Sha256 | None = None
    observed_sha256: Sha256 | None = None


class ReadinessDecision(ContractModel):
    model_config = _schema_config("ReadinessDecision")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    decision_id: OpaqueId
    expectation_sha256: Sha256
    evaluated_at_ms: int = Field(ge=0)
    status: Literal["READY", "NOT_READY"]
    http_status: Literal[200, 503]
    required_component_ids: tuple[OpaqueId, ...] = Field(min_length=4, max_length=4)
    verified_component_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4)
    failures: tuple[ReadinessFailure, ...] = Field(default=(), max_length=256)
    decision_sha256: Sha256

    @field_validator("required_component_ids", "verified_component_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("readiness component IDs must be sorted and unique")
        return value

    @field_validator("failures")
    @classmethod
    def validate_failures(
        cls,
        value: tuple[ReadinessFailure, ...],
    ) -> tuple[ReadinessFailure, ...]:
        ordered = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.component_id or "",
                    item.reason_code,
                    item.expected_sha256 or "",
                    item.observed_sha256 or "",
                ),
            )
        )
        if value != ordered or len(set(ordered)) != len(ordered):
            raise ValueError("readiness failures must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if set(self.verified_component_ids) - set(self.required_component_ids):
            raise ValueError("verified readiness component is not required")
        ready = (
            self.status == "READY"
            and self.http_status == 200
            and not self.failures
            and self.verified_component_ids == self.required_component_ids
        )
        not_ready = (
            self.status == "NOT_READY"
            and self.http_status == 503
            and bool(self.failures)
        )
        if not (ready or not_ready):
            raise ValueError("readiness status, HTTP status, evidence, and failures disagree")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"decision_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_sha256()})


def readiness_expectation_from_manifest(
    manifest: ComponentManifest,
    *,
    expectation_id: str,
    gateway_epoch: int,
    contract_artifact_manifest_sha256: str,
    allow_development_manifest: bool = False,
) -> ReadinessExpectation:
    if not manifest.has_valid_manifest_sha256():
        raise ValueError("readiness requires a valid component manifest")
    if not manifest.production_claim and not allow_development_manifest:
        raise ValueError("readiness requires a production component manifest")
    by_id = {item.component_id: item for item in manifest.components}
    if not set(REQUIRED_SERVICE_ROLES).issubset(by_id):
        raise ValueError("component manifest is missing a required service")
    if any(
        by_id[component_id].role != role
        for component_id, role in REQUIRED_SERVICE_ROLES.items()
    ):
        raise ValueError("component manifest assigns a required service to the wrong role")
    components = tuple(
        ExpectedServiceComponent(
            component_id=component_id,
            role=REQUIRED_SERVICE_ROLES[component_id],
            version=by_id[component_id].version,
            build_id=by_id[component_id].build_id,
            executable_sha256=by_id[component_id].sha256,
            schema_bundle_sha256=by_id[component_id].schema_bundle_hash,
        )
        for component_id in sorted(REQUIRED_SERVICE_ROLES)
    )
    return ReadinessExpectation(
        expectation_id=expectation_id,
        gateway_epoch=gateway_epoch,
        component_manifest_sha256=manifest.manifest_sha256,
        schema_bundle_sha256=manifest.contract_schema_bundle_hash,
        capability_manifest_sha256=manifest.capability_manifest_hash,
        skill_index_sha256=manifest.skill_index_hash,
        release_policy_sha256=manifest.release_policy_hash,
        contract_artifact_manifest_sha256=contract_artifact_manifest_sha256,
        components=components,
        expectation_sha256="0" * 64,
    ).with_computed_sha256()


def _failure(
    reason_code: str,
    component_id: str | None = None,
    expected_sha256: str | None = None,
    observed_sha256: str | None = None,
) -> ReadinessFailure:
    return ReadinessFailure(
        component_id=component_id,
        reason_code=reason_code,
        expected_sha256=expected_sha256,
        observed_sha256=observed_sha256,
    )


def evaluate_readiness_contract(
    expectation: ReadinessExpectation,
    evidence: Sequence[ComponentReadinessEvidence],
    *,
    decision_id: str,
    now_ms: int,
    authenticated_component_ids: Collection[str],
    binary_verified_component_ids: Collection[str],
    max_evidence_age_ms: int = 5_000,
    clock_skew_ms: int = 1_000,
) -> ReadinessDecision:
    if now_ms < 0 or not 1 <= max_evidence_age_ms <= 60_000 or not 0 <= clock_skew_ms <= 5_000:
        raise ValueError("readiness evaluation timing policy is invalid")

    failures: list[ReadinessFailure] = []
    if len(evidence) > 64:
        failures.append(_failure("readiness.evidence.limit_exceeded"))
        evidence = evidence[:64]
    if not expectation.has_valid_sha256():
        failures.append(_failure("readiness.expectation.digest_invalid"))
    expected_by_id = {item.component_id: item for item in expectation.components}
    grouped: dict[str, list[ComponentReadinessEvidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.component_id].append(item)
    for component_id in sorted(set(grouped) - set(expected_by_id)):
        failures.append(_failure("readiness.component.unexpected", component_id))

    verified: list[str] = []
    digest_fields = (
        (
            "component_manifest_sha256",
            expectation.component_manifest_sha256,
            "readiness.component_manifest.mismatch",
        ),
        (
            "schema_bundle_sha256",
            expectation.schema_bundle_sha256,
            "readiness.schema_bundle.mismatch",
        ),
        (
            "capability_manifest_sha256",
            expectation.capability_manifest_sha256,
            "readiness.capability_manifest.mismatch",
        ),
        (
            "skill_index_sha256",
            expectation.skill_index_sha256,
            "readiness.skill_index.mismatch",
        ),
        (
            "release_policy_sha256",
            expectation.release_policy_sha256,
            "readiness.release_policy.mismatch",
        ),
        (
            "contract_artifact_manifest_sha256",
            expectation.contract_artifact_manifest_sha256,
            "readiness.contract_artifacts.mismatch",
        ),
    )
    for component_id, expected in expected_by_id.items():
        component_failures: list[ReadinessFailure] = []
        candidates = grouped.get(component_id, ())
        if not candidates:
            failures.append(_failure("readiness.component.missing", component_id))
            continue
        if len(candidates) != 1:
            failures.append(_failure("readiness.component.duplicate", component_id))
            continue
        item = candidates[0]
        if component_id not in authenticated_component_ids:
            component_failures.append(_failure("readiness.transport.unauthenticated", component_id))
        if component_id not in binary_verified_component_ids:
            component_failures.append(_failure("readiness.binary.unverified", component_id))
        if not item.has_valid_sha256():
            component_failures.append(_failure("readiness.evidence.digest_invalid", component_id))
        if item.observed_at_ms > now_ms + clock_skew_ms:
            component_failures.append(_failure("readiness.evidence.from_future", component_id))
        elif now_ms - item.observed_at_ms > max_evidence_age_ms:
            component_failures.append(_failure("readiness.evidence.stale", component_id))
        if not item.health_check_passed:
            component_failures.append(_failure("readiness.health.failed", component_id))
        if item.schema_version != SCHEMA_VERSION:
            component_failures.append(_failure("readiness.schema_version.mismatch", component_id))
        if item.gateway_epoch != expectation.gateway_epoch:
            component_failures.append(_failure("readiness.gateway_epoch.mismatch", component_id))
        if item.component_role != expected.role:
            component_failures.append(_failure("readiness.component.role_mismatch", component_id))
        if item.version != expected.version:
            component_failures.append(_failure("readiness.component.version_mismatch", component_id))
        if item.build_id != expected.build_id:
            component_failures.append(_failure("readiness.component.build_mismatch", component_id))
        if item.executable_sha256 != expected.executable_sha256:
            component_failures.append(
                _failure(
                    "readiness.component.binary_mismatch",
                    component_id,
                    expected.executable_sha256,
                    item.executable_sha256,
                )
            )
        if item.schema_bundle_sha256 != expected.schema_bundle_sha256:
            component_failures.append(
                _failure(
                    "readiness.component.schema_mismatch",
                    component_id,
                    expected.schema_bundle_sha256,
                    item.schema_bundle_sha256,
                )
            )
        for field_name, expected_digest, reason_code in digest_fields:
            observed = getattr(item, field_name)
            if observed != expected_digest:
                component_failures.append(
                    _failure(reason_code, component_id, expected_digest, observed)
                )
        if component_failures:
            failures.extend(component_failures)
        else:
            verified.append(component_id)

    ordered_failures = tuple(
        sorted(
            set(failures),
            key=lambda item: (
                item.component_id or "",
                item.reason_code,
                item.expected_sha256 or "",
                item.observed_sha256 or "",
            ),
        )
    )
    required = tuple(sorted(expected_by_id))
    verified_ids = tuple(sorted(verified))
    status = "READY" if not ordered_failures and verified_ids == required else "NOT_READY"
    return ReadinessDecision(
        decision_id=decision_id,
        expectation_sha256=expectation.expectation_sha256,
        evaluated_at_ms=now_ms,
        status=status,
        http_status=200 if status == "READY" else 503,
        required_component_ids=required,
        verified_component_ids=verified_ids,
        failures=ordered_failures,
        decision_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "ComponentReadinessEvidence",
    "ExpectedServiceComponent",
    "ReadinessDecision",
    "ReadinessExpectation",
    "ReadinessFailure",
    "evaluate_readiness_contract",
    "readiness_expectation_from_manifest",
]
