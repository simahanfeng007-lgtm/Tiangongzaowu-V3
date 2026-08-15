"""P18-M3 version-drift governance for regenerative resume.

Pure policy only: no Store, Runtime, provider dispatch, or side effects live
here.  The existing RegenerativeExecutionAuthority consumes these decisions
immediately before it may append ``run.resumed``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CheckpointVersionVector:
    checkpoint_schema_version: str = ""
    runtime_version: str = ""
    provider_profile_hash: str = ""
    model_version: str = ""
    tool_registry_version: str = ""
    skill_version: str = ""
    task_contract_version: str = ""


@dataclass(frozen=True)
class VersionCompatibilityDecision:
    resume_allowed: bool
    reconcile_required: bool
    migration_required: bool
    revalidation_required: bool
    mismatches: tuple[str, ...]
    reasons: tuple[str, ...]


def version_vector_from_mapping(payload: Mapping[str, object]) -> CheckpointVersionVector:
    """Normalize M2 durable aliases into the canonical M3 version vector."""

    return CheckpointVersionVector(
        checkpoint_schema_version=str(
            payload.get("checkpoint_schema_version") or payload.get("schema_version") or ""
        ),
        runtime_version=str(payload.get("runtime_version") or ""),
        provider_profile_hash=str(
            payload.get("provider_profile_hash") or payload.get("provider_version") or ""
        ),
        model_version=str(payload.get("model_version") or ""),
        tool_registry_version=str(
            payload.get("tool_registry_version") or payload.get("tool_contract_version") or ""
        ),
        skill_version=str(payload.get("skill_version") or payload.get("skill_contract_version") or ""),
        task_contract_version=str(payload.get("task_contract_version") or ""),
    )


def evaluate_checkpoint_version_compatibility(
    checkpoint: CheckpointVersionVector,
    current: CheckpointVersionVector,
    *,
    compatible_mismatches: Iterable[str] = (),
    migratable_schema_pairs: Iterable[tuple[str, str]] = (),
    migration_completed: bool = False,
    revalidated: bool = False,
) -> VersionCompatibilityDecision:
    """Fail closed on version drift; never silently resume.

    A caller may explicitly identify dimensions that are compatible, but any
    such drift still requires reality revalidation.  A declared schema
    migration additionally requires the migration to complete.  Unknown drift
    is ``RECONCILE_REQUIRED``.
    """

    fields = (
        "checkpoint_schema_version",
        "runtime_version",
        "provider_profile_hash",
        "model_version",
        "tool_registry_version",
        "skill_version",
        "task_contract_version",
    )
    mismatches = tuple(
        name
        for name in fields
        if str(getattr(checkpoint, name) or "") != str(getattr(current, name) or "")
    )
    if not mismatches:
        return VersionCompatibilityDecision(True, False, False, False, (), ())

    compatible = {str(item) for item in compatible_mismatches}
    schema_pair = (
        str(checkpoint.checkpoint_schema_version or ""),
        str(current.checkpoint_schema_version or ""),
    )
    migratable_pairs = {(str(first), str(second)) for first, second in migratable_schema_pairs}
    schema_mismatch = "checkpoint_schema_version" in mismatches
    migration_required = schema_mismatch and schema_pair in migratable_pairs

    unknown = tuple(
        name
        for name in mismatches
        if name != "checkpoint_schema_version" and name not in compatible
    )
    if schema_mismatch and not migration_required:
        unknown = ("checkpoint_schema_version",) + unknown

    if unknown:
        return VersionCompatibilityDecision(
            resume_allowed=False,
            reconcile_required=True,
            migration_required=migration_required,
            revalidation_required=True,
            mismatches=mismatches,
            reasons=tuple(f"version_mismatch:{name}" for name in unknown),
        )

    needs_migration = migration_required and not migration_completed
    needs_revalidation = not revalidated
    if needs_migration or needs_revalidation:
        reasons: list[str] = []
        if needs_migration:
            reasons.append("checkpoint_migration_required")
        if needs_revalidation:
            reasons.append("version_revalidation_required")
        return VersionCompatibilityDecision(
            resume_allowed=False,
            reconcile_required=False,
            migration_required=migration_required,
            revalidation_required=needs_revalidation,
            mismatches=mismatches,
            reasons=tuple(reasons),
        )

    return VersionCompatibilityDecision(
        resume_allowed=True,
        reconcile_required=False,
        migration_required=migration_required,
        revalidation_required=False,
        mismatches=mismatches,
        reasons=("explicit_version_compatibility_revalidated",),
    )


__all__ = [
    "CheckpointVersionVector",
    "VersionCompatibilityDecision",
    "evaluate_checkpoint_version_compatibility",
    "version_vector_from_mapping",
]
