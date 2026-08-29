"""P19-R2 M1 predicate type allowlist.

First-batch allowlist from plan 4.3. Semantic-only types
(factuality.correct / overall_quality.good / helpfulness.good /
style.professional / reasoning.correct) are deliberately absent: they
belong to a later semantic layer and must never be auto-generated in
the first batch.
"""

from __future__ import annotations

PREDICATE_TYPE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Artifact
        "artifact.nonempty",
        "artifact.min_visible_text_chars",
        "artifact.required_sections",
        "artifact.required_file_count",
        "artifact.format_matches",
        "docx.min_body_items",
        "docx.required_headings",
        "xlsx.required_columns",
        "xlsx.min_data_rows",
        "xlsx.required_sheet_names",
        "pptx.min_nonempty_slides",
        "pptx.required_slide_titles",
        "csv.required_columns",
        "text.required_markers",
        # Effect
        "effect.terminal_succeeded",
        "effect.target_exists",
        "effect.target_sha256_matches",
        "effect.required_change_observed",
        "effect.no_forbidden_side_effect",
        "effect.idempotent_target_verified",
        # Repository
        "repository.required_paths_changed",
        "repository.forbidden_paths_unchanged",
        "repository.source_authority_valid",
        "repository.tests_passed",
        "repository.compile_passed",
        "repository.no_test_tampering",
        "repository.no_generated_mirror_direct_edit",
    }
)

__all__ = ["PREDICATE_TYPE_ALLOWLIST"]
