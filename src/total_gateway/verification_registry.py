"""P19-R2 M1 verifier registry.

The registry owns the immutable set of verifier descriptors and produces
hashable snapshots. M1 registers the three planned outcome oracles in a
*dormant* state: descriptors exist, snapshots hash, lookups validate —
but no handler runs anywhere (M2/M3 wire the real implementations).

Fail-closed rules:
* unknown verifier_id -> ``UnknownVerifierError``;
* known id + wrong version -> ``UnknownVerifierError``;
* duplicate registration of the same verifier_id -> ``ValueError``;
* descriptors with predicate types outside the allowlist are rejected.

Predicate type allowlist mirrors plan 4.3 first batch. Semantic-only
types (factuality.correct, overall_quality.good, ...) are intentionally
absent: they must not be auto-generated in the first batch.
"""

from __future__ import annotations

from total_gateway.verification_predicate_types import PREDICATE_TYPE_ALLOWLIST

from contracts.verification import (
    RegistrySnapshot,
    VerifierDescriptor,
    derive_registry_snapshot_id,
)


class UnknownVerifierError(KeyError):
    """Raised when a verifier id/version is not present in the registry."""


# ---------------------------------------------------------------------------
# Dormant default descriptors (M1: handlers do not exist yet)
# ---------------------------------------------------------------------------

def _dormant_descriptor(
    *,
    verifier_id: str,
    predicate_types: tuple[str, ...],
    subject_kinds: tuple[str, ...],
    accepted_authorities: tuple[str, ...],
    implementation_ref: str,
) -> VerifierDescriptor:
    return VerifierDescriptor(
        verifier_id=verifier_id,
        verifier_version="1",
        layer="L0_DETERMINISTIC",
        deterministic=True,
        supported_predicate_types=predicate_types,
        accepted_authorities=accepted_authorities,
        supported_subject_kinds=subject_kinds,
        max_input_bytes=64 * 1024 * 1024,
        timeout_ms=120_000,
        default_enforcement="RECORD",
        block_capable=True,
        repair_feedback_capable=True,
        producer_component_id="tiangong-gateway",
        config_sha256="0" * 64,
        implementation_ref=implementation_ref,
        descriptor_sha256="0" * 64,
    ).with_computed_sha256()


def default_descriptors() -> tuple[VerifierDescriptor, ...]:
    """Artifact oracle is ACTIVE at v2 (M2.1); effect/repository stay dormant.

    The artifact v2 descriptor declares EXACTLY the dispatch set implemented
    by ``outcome_oracles/artifact_content.py`` — both read
    ``verification_oracle_config`` so they cannot drift. Its config digest
    covers the implemented predicate set, params normalization version,
    inspector semantic version, max input bytes and format applicability.
    """
    from .verification_oracle_config import (
        ARTIFACT_IMPLEMENTED_PREDICATE_TYPES,
        ARTIFACT_INSPECTOR_SEMANTIC_VERSION,
        ARTIFACT_MAX_INPUT_BYTES,
        IMPLEMENTATION_REF,
        VERIFIER_ID,
        artifact_oracle_config_sha256,
    )

    artifact = VerifierDescriptor(
        verifier_id=VERIFIER_ID,
        verifier_version=ARTIFACT_INSPECTOR_SEMANTIC_VERSION,
        layer="L0_DETERMINISTIC",
        deterministic=True,
        supported_predicate_types=tuple(
            sorted(ARTIFACT_IMPLEMENTED_PREDICATE_TYPES)
        ),
        accepted_authorities=("OBJECT_STORE", "ARTIFACT_MANIFEST", "ARTIFACT_QC"),
        supported_subject_kinds=("artifact",),
        max_input_bytes=ARTIFACT_MAX_INPUT_BYTES,
        timeout_ms=120_000,
        default_enforcement="RECORD",
        block_capable=True,
        repair_feedback_capable=True,
        producer_component_id="tiangong-gateway",
        config_sha256=artifact_oracle_config_sha256(),
        implementation_ref=IMPLEMENTATION_REF,
        descriptor_sha256="0" * 64,
    ).with_computed_sha256()
    effect = _dormant_descriptor(
        verifier_id="verifier.effect_state",
        predicate_types=(
            "effect.idempotent_target_verified",
            "effect.no_forbidden_side_effect",
            "effect.required_change_observed",
            "effect.target_exists",
            "effect.target_sha256_matches",
            "effect.terminal_succeeded",
        ),
        subject_kinds=("effect",),
        accepted_authorities=("EFFECT_LEDGER", "FACT_LEDGER", "TOOL_RESULT_CONTRACT"),
        implementation_ref="src/total_gateway/outcome_oracles/effect_state.py",
    )
    repository = _dormant_descriptor(
        verifier_id="verifier.repository_state",
        predicate_types=(
            "repository.compile_passed",
            "repository.forbidden_paths_unchanged",
            "repository.no_generated_mirror_direct_edit",
            "repository.no_test_tampering",
            "repository.required_paths_changed",
            "repository.source_authority_valid",
            "repository.tests_passed",
        ),
        subject_kinds=("repository",),
        accepted_authorities=("REPOSITORY_PROVIDER", "FACT_LEDGER"),
        implementation_ref="src/total_gateway/outcome_oracles/repository_state.py",
    )
    return (artifact, effect, repository)


def legacy_artifact_v1_descriptor() -> VerifierDescriptor:
    """The M1 dormant wide-set descriptor (historical, superseded by v2).

    Kept so historical v1 snapshots stay constructible/readable in tests;
    the v2 oracle refuses to instantiate against snapshots that only
    carry this descriptor.
    """
    return _dormant_descriptor(
        verifier_id="verifier.artifact_content",
        predicate_types=(
            "artifact.format_matches",
            "artifact.min_visible_text_chars",
            "artifact.nonempty",
            "artifact.required_file_count",
            "artifact.required_sections",
            "csv.required_columns",
            "docx.min_body_items",
            "docx.required_headings",
            "pptx.min_nonempty_slides",
            "pptx.required_slide_titles",
            "text.required_markers",
            "xlsx.required_columns",
            "xlsx.required_sheet_names",
        ),
        subject_kinds=("artifact",),
        accepted_authorities=("OBJECT_STORE", "ARTIFACT_MANIFEST", "ARTIFACT_QC"),
        implementation_ref="src/total_gateway/outcome_oracles/artifact_content.py",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class VerifierRegistry:
    """Immutable registry; ``snapshot()`` output is deterministic."""

    def __init__(self, descriptors: tuple[VerifierDescriptor, ...]) -> None:
        if not descriptors:
            raise ValueError("registry requires at least one verifier descriptor")
        seen: set[str] = set()
        for descriptor in descriptors:
            if not descriptor.has_valid_descriptor_sha256():
                raise ValueError(
                    f"verifier descriptor hash mismatch: {descriptor.verifier_id}"
                )
            for predicate_type in descriptor.supported_predicate_types:
                if predicate_type not in PREDICATE_TYPE_ALLOWLIST:
                    raise ValueError(
                        f"predicate type outside allowlist: {predicate_type} "
                        f"({descriptor.verifier_id})"
                    )
            if descriptor.verifier_id in seen:
                raise ValueError(f"duplicate verifier_id: {descriptor.verifier_id}")
            seen.add(descriptor.verifier_id)

        ordered = tuple(sorted(descriptors, key=lambda item: item.verifier_id))
        object.__setattr__(self, "_descriptors", ordered)
        object.__setattr__(self, "_by_id", {item.verifier_id: item for item in ordered})

    @classmethod
    def with_defaults(cls) -> VerifierRegistry:
        return cls(default_descriptors())

    @property
    def descriptors(self) -> tuple[VerifierDescriptor, ...]:
        return self._descriptors  # type: ignore[attr-defined]

    def snapshot(self, *, captured_at_ms: int) -> RegistrySnapshot:
        partial = RegistrySnapshot(
            registry_snapshot_id="vrg_" + "0" * 64,
            verifiers=self.descriptors,
            captured_at_ms=captured_at_ms,
            snapshot_sha256="0" * 64,
        ).with_computed_sha256()
        return partial.model_copy(
            update={
                "registry_snapshot_id": derive_registry_snapshot_id(
                    snapshot_sha256=partial.snapshot_sha256
                )
            }
        )

    def find(self, verifier_id: str, verifier_version: str) -> VerifierDescriptor:
        descriptor = self._by_id.get(verifier_id)  # type: ignore[attr-defined]
        if descriptor is None or descriptor.verifier_version != verifier_version:
            raise UnknownVerifierError(
                f"verifier not registered: {verifier_id}@{verifier_version}"
            )
        return descriptor


__all__ = [
    "UnknownVerifierError",
    "VerifierRegistry",
    "default_descriptors",
]
