"""Single source of truth for the artifact content oracle configuration.

Both the ``VerifierRegistry`` (descriptor + its ``config_sha256``) and
the oracle itself consume THESE constants, so the declared capability and
the implemented dispatch can never drift apart silently. The M2.2 review
invariant ``descriptor.supported_predicate_types == oracle dispatch set``
holds by construction — both sides read this module.

The config digest covers exactly what the review requires:
implemented predicate set, params normalization version, inspector
semantic version, max input bytes, and the format applicability rules.

All exported mappings are deeply immutable (MappingProxyType).
"""

from __future__ import annotations

from types import MappingProxyType

from contracts.canonical import canonical_sha256

#: Inspector semantic version. Bump on ANY behavioural change of an
#: inspector, a normalization rule, or this config (M1_DESIGN D7).
#: v3 (M2.2): authority-before-status ordering, parser-failure taxonomy,
#: deterministic reason codes, formula/text normalization semantics.
ARTIFACT_INSPECTOR_SEMANTIC_VERSION = "3"

#: Version of the predicate-params normalization contract in use.
ARTIFACT_PARAMS_NORMALIZATION_VERSION = "1"

ARTIFACT_MAX_INPUT_BYTES = 64 * 1024 * 1024

#: Predicate types the oracle can deterministically evaluate and leave
#: evidence for. Everything else must yield INCONCLUSIVE
#: (predicate_not_implemented) — never a fake verdict.
ARTIFACT_IMPLEMENTED_PREDICATE_TYPES: frozenset[str] = frozenset(
    {
        "artifact.nonempty",
        "artifact.min_visible_text_chars",
        "xlsx.required_columns",
        "xlsx.min_data_rows",
        "text.required_markers",
        "pptx.min_nonempty_slides",
    }
)

#: predicate-type prefix -> manifest format ids it can apply to.
#: Absent prefixes (e.g. ``artifact.*``) have NO format restriction —
#: they apply to every artifact subject, and formats without an inspector
#: then yield INCONCLUSIVE (format_not_inspectable), not NOT_APPLICABLE.
ARTIFACT_APPLICABLE_FORMATS: MappingProxyType = MappingProxyType(
    {
        "docx": frozenset({"docx"}),
        "xlsx": frozenset({"xlsx"}),
        "pptx": frozenset({"pptx"}),
        "text": frozenset({"text"}),
        # csv.* has no gate format policy in this repository — csv manifests
        # cannot reach the oracle with authority, so csv predicates stay
        # unimplemented (INCONCLUSIVE), never fake-verified.
        "csv": frozenset(),
    }
)

ARTIFACT_INSPECTABLE_FORMATS: frozenset[str] = frozenset(
    {"docx", "xlsx", "pptx", "text"}
)

#: Expected descriptor facts. The oracle's constructor compares the
#: pinned descriptor against these EXACTLY — a same-id/same-version
#: descriptor with a different config or capability is rejected.
ARTIFACT_DESCRIPTOR_EXPECTATIONS = MappingProxyType(
    {
        "verifier_id": "verifier.artifact_content",
        "accepted_authorities": ("ARTIFACT_MANIFEST", "ARTIFACT_QC", "OBJECT_STORE"),
        "supported_subject_kinds": ("artifact",),
        "producer_component_id": "tiangong-gateway",
        "layer": "L0_DETERMINISTIC",
        "deterministic": True,
        "default_enforcement": "RECORD",
    }
)

VERIFIER_ID = "verifier.artifact_content"

IMPLEMENTATION_REF = "src/total_gateway/outcome_oracles/artifact_content.py"


def artifact_oracle_config_payload() -> dict:
    """Canonical config payload hashed into the descriptor's config_sha256."""
    return {
        "inspector_semantic_version": ARTIFACT_INSPECTOR_SEMANTIC_VERSION,
        "params_normalization_version": ARTIFACT_PARAMS_NORMALIZATION_VERSION,
        "max_input_bytes": ARTIFACT_MAX_INPUT_BYTES,
        "implemented_predicate_types": sorted(ARTIFACT_IMPLEMENTED_PREDICATE_TYPES),
        "applicable_formats": {
            prefix: sorted(formats)
            for prefix, formats in sorted(ARTIFACT_APPLICABLE_FORMATS.items())
        },
    }


def artifact_oracle_config_sha256() -> str:
    return canonical_sha256(artifact_oracle_config_payload())


__all__ = [
    "ARTIFACT_APPLICABLE_FORMATS",
    "ARTIFACT_IMPLEMENTED_PREDICATE_TYPES",
    "ARTIFACT_INSPECTABLE_FORMATS",
    "ARTIFACT_INSPECTOR_SEMANTIC_VERSION",
    "ARTIFACT_MAX_INPUT_BYTES",
    "ARTIFACT_PARAMS_NORMALIZATION_VERSION",
    "IMPLEMENTATION_REF",
    "VERIFIER_ID",
    "artifact_oracle_config_payload",
    "artifact_oracle_config_sha256",
]
