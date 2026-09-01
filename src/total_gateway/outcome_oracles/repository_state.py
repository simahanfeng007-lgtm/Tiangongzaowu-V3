"""P19-R2 M3.1 Gateway RepositoryStateOracle — RECORD ONLY.

Status: implementation-present / descriptor-registered /
production-unwired.

M3.1 trust-boundary closure:
* observations are CONTENT (identity = the provider's content hash);
  verification LINEAGE lives in the separate binding table — the same
  observation may legally bind as PRE/POST across many requests and
  effects;
* the oracle consumes bound pre/post pairs tied to ONE subject effect
  (the verification window): pre/post from different subject effects is
  ERROR, so changes made by "some other step of the run" can never
  satisfy this effect's required_paths_changed;
* source-authority validation runs through the trusted
  ``source_authority.validator`` module (moved out of the checked repo's
  scripts/ in M3.1) — the repository under inspection is pure data, no
  Python inside it is ever executed;
* live-state binding (option A): for authority/ownership predicates the
  oracle re-samples the working tree through the read-only provider and
  requires reality to STILL equal the pinned post observation — drift
  after the post capture is ERROR, never PASS;
* mirror targets are matched on canonical repo-path boundaries
  (exact or directory-rooted prefix), not substring luck;
* without a trusted lineage (unknown binding/observation) the oracle
  raises ``OracleInvocationError`` — it never fabricates a
  VerificationRecord with invented ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from contracts.canonical import canonical_sha256
from contracts.verification import AcceptancePredicate, RegistrySnapshot, VerificationRecord
from total_gateway.outcome_oracles._common import (
    OracleSnapshotInvalid,
    assemble_record,
    bind_snapshot_and_descriptor,
)
from total_gateway.verification_oracle_config import (
    REPOSITORY_DESCRIPTOR_EXPECTATIONS,
    REPOSITORY_IMPLEMENTED_PREDICATE_TYPES,
    REPOSITORY_INSPECTOR_SEMANTIC_VERSION,
    REPOSITORY_VERIFIER_ID,
    repository_oracle_config_sha256,
)

_REPOSITORY_IMPLEMENTATION_REF = (
    "src/total_gateway/outcome_oracles/repository_state.py"
)


class OracleInvocationError(RuntimeError):
    """No trusted lineage — refusing to fabricate a VerificationRecord."""


def _trusted_authority_validator(repo_root: str) -> list[str]:
    """Reuse the authoritative in-repo validator module (M3.1 §7)."""
    from source_authority.validator import load_config, validate_source_authority

    root = Path(repo_root)
    config = load_config(root / "source-ownership.json")
    return validate_source_authority(
        config, repo_root=root, require_sources=False
    )


def _default_authority_validator(repo_root: str) -> list[str]:
    return _trusted_authority_validator(repo_root)


def _resample_observation_sha256(worktree_root: str) -> str:
    """Re-observe the working tree via the trusted read-only provider."""
    import sys

    repo_src = str(Path(__file__).resolve().parents[3] / "src")
    backend_src = str(
        Path(__file__).resolve().parents[4] / "app/backend/tiangong-backend"
    )
    for candidate in (repo_src, backend_src):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from v3.repository_perception import LocalGitRepositoryProvider

    provider = LocalGitRepositoryProvider()
    identity = provider.discover(worktree_root)
    if identity is None:
        raise ValueError("worktree is not a discoverable git repository")
    observation = provider.observe(identity)
    return observation.observation_sha256


def _path_hits_target(path: str, target: str) -> bool:
    """Canonical repo-path boundary match (M3.1 §6).

    A target names a file or a directory root: hit means exact equality
    or a true directory-prefix ('foo/bar' does NOT hit 'foo/barista').
    """
    if path == target:
        return True
    root = target.rstrip("/")
    return path.startswith(root + "/")


class RepositoryStateOracle:
    """Deterministic repository oracle over bound pre/post observations."""

    def __init__(
        self,
        *,
        snapshot: RegistrySnapshot,
        store,
        authority_validator: Callable[[str], list[str]] | None = None,
        resampler: Callable[[str], str] | None = None,
    ) -> None:
        snapshot, descriptor = bind_snapshot_and_descriptor(
            snapshot,
            verifier_id=REPOSITORY_VERIFIER_ID,
            verifier_version=REPOSITORY_INSPECTOR_SEMANTIC_VERSION,
            config_sha256=repository_oracle_config_sha256(),
            supported_predicate_types=REPOSITORY_IMPLEMENTED_PREDICATE_TYPES,
            expectations=REPOSITORY_DESCRIPTOR_EXPECTATIONS,
            implementation_ref=_REPOSITORY_IMPLEMENTATION_REF,
            timeout_ms=REPOSITORY_DESCRIPTOR_EXPECTATIONS["timeout_ms"],
        )
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_store", store)
        object.__setattr__(
            self,
            "_authority_validator",
            authority_validator or _default_authority_validator,
        )
        object.__setattr__(
            self, "_resampler", resampler or _resample_observation_sha256
        )

    @property
    def descriptor(self):
        return self._descriptor  # type: ignore[attr-defined]

    def evaluate(
        self,
        *,
        subject_effect_id: str,
        pre_binding_id: str,
        post_binding_id: str,
        predicate: AcceptancePredicate,
        evaluated_at_ms: int,
        evaluation_phase: str = "POST_EXECUTION",
    ) -> VerificationRecord:
        if predicate.subject_kind != "repository" or not predicate.has_valid_identity():
            raise ValueError("predicate failed full semantic identity validation")
        status, reason_codes, observation, lineage, repo_id = self._evaluate_to_status(
            subject_effect_id, pre_binding_id, post_binding_id, predicate
        )
        # M5 Final P0-2: the record subject is the STABLE mutation effect
        # id — the same identity space the plan entry binds, the Store
        # successor chain advances, and the executor resolves bindings
        # with. The observation window identity (repo_id + post binding)
        # stays fully auditable in evidence_refs below.
        subject_identity = subject_effect_id
        return assemble_record(
            descriptor=self._descriptor,  # type: ignore[attr-defined]
            snapshot=self._snapshot,  # type: ignore[attr-defined]
            predicate=predicate,
            subject_kind="repository",
            subject_identity=subject_identity,
            request_id=lineage["request_id"],
            run_id=lineage["run_id"],
            generation=lineage["generation"],
            status=status,
            reason_codes=reason_codes,
            evidence_refs=(
                f"repo_observation_binding_pre:{pre_binding_id}",
                f"repo_observation_binding_post:{post_binding_id}",
            ),
            observation=observation,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=evaluation_phase,
        )

    # -- internals ---------------------------------------------------------

    def _load_binding(self, binding_id: str) -> dict:
        try:
            row = self._store.get_repository_observation_binding(  # type: ignore[attr-defined]
                binding_id
            )
        except Exception as exc:
            raise OracleInvocationError(
                "repository observation binding store failure"
            ) from exc
        if row is None:
            raise OracleInvocationError(
                "repository observation binding not found; refusing to"
                " fabricate a record"
            )
        return row

    def _load_content(self, observation_sha256: str) -> dict:
        row = self._store.get_repository_observation(  # type: ignore[attr-defined]
            observation_sha256
        )
        if row is None:
            raise OracleInvocationError(
                "repository observation content not found; refusing to"
                " fabricate a record"
            )
        return row

    @staticmethod
    def _delta_paths(post_payload: dict) -> set[str]:
        """Authoritative pre→post delta from the provider's git diff."""
        delta: set[str] = set()
        for change in post_payload.get("changes") or []:
            if not isinstance(change, dict):
                continue
            new_path = str(change.get("new_path") or "").strip()
            old_path = str(change.get("old_path") or "").strip()
            if new_path:
                delta.add(new_path)
            if old_path and (
                not new_path
                or change.get("change_kind") in ("DELETE", "RENAME", "MOVE")
            ):
                delta.add(old_path)
        return delta

    def _live_state_matches_post(self, post_content: dict) -> tuple[bool, str]:
        """M3.1 §8 option A: re-sample and require reality == pinned post."""
        worktree = str(
            (post_content["observation"].get("identity") or {}).get(
                "worktree_root_ref"
            )
            or ""
        )
        if not worktree:
            return False, "authority:worktree_root_missing"
        try:
            resampled_sha = self._resampler(worktree)  # type: ignore[attr-defined]
        except Exception:
            return False, "authority:resample_failed"
        if resampled_sha != post_content["observation_sha256"]:
            return False, "authority:post_state_drifted"
        return True, ""

    def _evaluate_to_status(self, subject_effect_id, pre_binding_id, post_binding_id, predicate):
        pre_binding = self._load_binding(pre_binding_id)
        post_binding = self._load_binding(post_binding_id)
        if pre_binding["observation_role"] != "PRE":
            return self._binding_error(pre_binding, "authority:pre_role_invalid", post_binding)
        if post_binding["observation_role"] != "POST":
            return self._binding_error(pre_binding, "authority:post_role_invalid", post_binding)
        # §5 verification window: both ends must belong to THIS subject effect
        if pre_binding["subject_effect_id"] != subject_effect_id:
            return self._binding_error(pre_binding, "authority:pre_subject_mismatch", post_binding)
        if post_binding["subject_effect_id"] != subject_effect_id:
            return self._binding_error(pre_binding, "authority:post_subject_mismatch", post_binding)
        if (
            pre_binding["request_id"] != post_binding["request_id"]
            or pre_binding["run_id"] != post_binding["run_id"]
            or pre_binding["generation"] != post_binding["generation"]
        ):
            return self._binding_error(pre_binding, "authority:binding_lineage_mismatch", post_binding)
        lineage = {
            "request_id": post_binding["request_id"],
            "run_id": post_binding["run_id"],
            "generation": post_binding["generation"],
        }
        pre_content = self._load_content(pre_binding["observation_sha256"])
        post_content = self._load_content(post_binding["observation_sha256"])
        if pre_content["repository_id"] != post_content["repository_id"]:
            return (
                "ERROR",
                ("authority:observation_repository_mismatch",),
                {},
                lineage,
                post_content["repository_id"],
            )
        if post_content["observed_at_ms"] < pre_content["observed_at_ms"]:
            return (
                "ERROR",
                ("authority:observation_time_inverted",),
                {},
                lineage,
                post_content["repository_id"],
            )
        repo_id = post_content["repository_id"]
        repo_root = str(
            (post_content["observation"].get("identity") or {}).get(
                "worktree_root_ref"
            )
            or ""
        )
        delta = self._delta_paths(post_content["observation"])
        observation: dict[str, Any] = {
            "repository_id": repo_id,
            "head_commit": post_content["head_commit"],
            "subject_effect_id": subject_effect_id,
            "delta_count": len(delta),
            "delta_paths_digest": canonical_sha256(sorted(delta)),
            "verifier_version": self._descriptor.verifier_version,  # type: ignore[attr-defined]
        }
        kind = predicate.predicate_type
        if kind == "repository.required_paths_changed":
            required = tuple(predicate.param_mapping()["paths"])
            missing = [p for p in required if p not in delta]
            observation["missing_count"] = len(missing)
            observation["missing_items_sha256"] = canonical_sha256(missing)
            if not missing:
                return "PASS", (), observation, lineage, repo_id
            return (
                "FAIL",
                ("repository.required_paths_not_changed",),
                observation,
                lineage,
                repo_id,
            )
        if kind == "repository.forbidden_paths_unchanged":
            forbidden = tuple(predicate.param_mapping()["paths"])
            touched = [p for p in forbidden if p in delta]
            observation["touched_count"] = len(touched)
            observation["touched_items_sha256"] = canonical_sha256(touched)
            if not touched:
                return "PASS", (), observation, lineage, repo_id
            return (
                "FAIL",
                ("repository.forbidden_paths_touched",),
                observation,
                lineage,
                repo_id,
            )
        if kind in (
            "repository.no_generated_mirror_direct_edit",
            "repository.source_authority_valid",
        ):
            # §8: live state must still equal the pinned post observation
            matches, drift_code = self._live_state_matches_post(post_content)
            if not matches:
                return (
                    "ERROR",
                    (drift_code,),
                    observation,
                    lineage,
                    repo_id,
                )
            observation["resample_sha256"] = post_content["observation_sha256"]
        if kind == "repository.no_generated_mirror_direct_edit":
            try:
                ownership = json.loads(
                    (Path(repo_root) / "source-ownership.json").read_text(
                        encoding="utf-8"
                    )
                )
                targets = {
                    str(target)
                    for mapping in ownership.get("mappings", [])
                    for target in mapping.get("targets", [])
                }
            except Exception:
                return (
                    "ERROR",
                    ("authority:source_ownership_unreadable",),
                    observation,
                    lineage,
                    repo_id,
                )
            observation["ownership_digest"] = canonical_sha256(sorted(targets))
            mirror_edits = sorted(
                path
                for path in delta
                if any(_path_hits_target(path, target) for target in targets)
            )
            observation["mirror_edit_count"] = len(mirror_edits)
            observation["mirror_edit_items_sha256"] = canonical_sha256(mirror_edits)
            if not mirror_edits:
                return "PASS", (), observation, lineage, repo_id
            return (
                "FAIL",
                ("repository.generated_mirror_direct_edit",),
                observation,
                lineage,
                repo_id,
            )
        if kind == "repository.source_authority_valid":
            try:
                errors = self._authority_validator(repo_root)  # type: ignore[attr-defined]
            except Exception:
                return (
                    "ERROR",
                    ("authority:source_authority_check_failed",),
                    observation,
                    lineage,
                    repo_id,
                )
            observation["authority_error_count"] = len(errors)
            observation["authority_errors_sha256"] = canonical_sha256(list(errors))
            if not errors:
                return "PASS", (), observation, lineage, repo_id
            return (
                "FAIL",
                ("repository.source_authority_invalid",),
                observation,
                lineage,
                repo_id,
            )
        return (
            "INCONCLUSIVE",
            ("predicate_not_implemented",),
            observation,
            lineage,
            repo_id,
        )

    @staticmethod
    def _binding_error(pre_binding, code, post_binding):
        lineage = {
            "request_id": pre_binding["request_id"],
            "run_id": pre_binding["run_id"],
            "generation": pre_binding["generation"],
        }
        return "ERROR", (code,), {}, lineage, ""


__all__ = [
    "OracleInvocationError",
    "RepositoryStateOracle",
    "OracleSnapshotInvalid",
]
