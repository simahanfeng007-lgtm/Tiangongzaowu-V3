"""P19-R2 M3 Gateway RepositoryStateOracle — RECORD ONLY.

Status: implementation-present / descriptor-registered /
production-unwired.

Evaluates explicit ``AcceptancePredicate``s against lineage-bound
repository observations persisted in ``GatewayStateStore``. The
observations themselves are captured by the EXISTING read-only git
provider (``v3/repository_perception.LocalGitRepositoryProvider`` —
whitelisted read-only git, timeout/output caps); this oracle never runs
git itself, never shells out, and never opens a second git path.

Discipline:
* only store-bound pre/post observations count as authority — a WU
  committed-frame snapshot has no path into this oracle and an unknown
  observation hash is ERROR, never PASS;
* pre/post must share repository identity and lineage, and post must be
  sampled at or after pre;
* the pre→post delta is recomputed deterministically from the stored
  file inventories (added/removed/modified), so untracked additions and
  renames count as changes;
* ``no_generated_mirror_direct_edit`` combines the recomputed delta with
  source-ownership generated targets (structural authority, not filename
  guessing);
* ``source_authority_valid`` reuses scripts/check-source-authority.py's
  ``validate_source_authority`` via import (no duplicated logic);
* tests_passed / compile_passed / no_test_tampering stay dormant: their
  authority (real command receipts) does not exist yet.
"""

from __future__ import annotations

import importlib.util
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

#: Placeholder lineage for authority failures where no bound observation
#: exists (unknown hash / store failure). All-zero ids satisfy the record
#: contract while carrying no lineage claims.
_UNBOUND_LINEAGE = {
    "request_id": "req_" + "0" * 64,
    "run_id": "run_" + "0" * 64,
    "generation": 0,
}


def _default_authority_validator(repo_root: str) -> list[str]:
    """Run the REAL check-source-authority.py validator (reused, not
    duplicated). Returns the error list (empty == valid)."""
    script = Path(repo_root) / "scripts" / "check-source-authority.py"
    if not script.is_file():
        raise FileNotFoundError("check-source-authority.py not found in repo")
    spec = importlib.util.spec_from_file_location(
        "tiangong_check_source_authority", script
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load check-source-authority.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.load_config(Path(repo_root) / "source-ownership.json")
    return module.validate_source_authority(
        config, repo_root=Path(repo_root), require_sources=False
    )


class RepositoryStateOracle:
    """Deterministic repository oracle over bound pre/post observations."""

    def __init__(
        self,
        *,
        snapshot: RegistrySnapshot,
        store,
        authority_validator: Callable[[str], list[str]] | None = None,
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

    @property
    def descriptor(self):
        return self._descriptor  # type: ignore[attr-defined]

    def evaluate(
        self,
        *,
        pre_observation_sha256: str,
        post_observation_sha256: str,
        predicate: AcceptancePredicate,
        evaluated_at_ms: int,
        evaluation_phase: str = "POST_EXECUTION",
    ) -> VerificationRecord:
        if predicate.subject_kind != "repository" or not predicate.has_valid_identity():
            raise ValueError("predicate failed full semantic identity validation")
        status, reason_codes, observation, lineage, repo_id = self._evaluate_to_status(
            pre_observation_sha256, post_observation_sha256, predicate
        )
        subject_identity = f"{repo_id}:{post_observation_sha256}"
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
                f"repo_observation_pre:{pre_observation_sha256}",
                f"repo_observation_post:{post_observation_sha256}",
            ),
            observation=observation,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=evaluation_phase,
        )

    # -- internals ---------------------------------------------------------

    def _load_observation(self, sha: str):
        try:
            row = self._store.get_repository_observation(sha)  # type: ignore[attr-defined]
        except Exception:
            return None, "authority:observation_store_failure"
        if row is None:
            return None, "authority:observation_not_found"
        return row, None

    @staticmethod
    def _delta_paths(pre_payload: dict, post_payload: dict) -> set[str]:
        """Authoritative pre→post delta from the provider's git diff.

        The post observation's ``changes`` (captured via
        ``observe_delta`` with the pre revision) is the whitelisted-git
        diff between the two revisions plus the working-tree overlay.
        Added, removed, modified, renamed and untracked paths all land in
        the delta set. The pre payload is kept for identity binding even
        though the diff authority lives in the post capture.
        """
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

    def _evaluate_to_status(self, pre_sha, post_sha, predicate):
        pre_row, pre_error = self._load_observation(pre_sha)
        if pre_error:
            return (
                "ERROR",
                (pre_error,),
                {},
                dict(_UNBOUND_LINEAGE),
                "",
            )
        post_row, post_error = self._load_observation(post_sha)
        if post_error:
            return (
                "ERROR",
                (post_error,),
                {},
                dict(_UNBOUND_LINEAGE),
                "",
            )
        # Authority checks: shared lineage, shared repository identity,
        # monotonic sampling time.
        lineage = {
            "request_id": post_row["request_id"],
            "run_id": post_row["run_id"],
            "generation": post_row["generation"],
        }
        if (
            pre_row["request_id"] != post_row["request_id"]
            or pre_row["run_id"] != post_row["run_id"]
            or pre_row["generation"] != post_row["generation"]
        ):
            return (
                "ERROR",
                ("authority:observation_lineage_mismatch",),
                {},
                lineage,
                post_row["repository_id"],
            )
        if pre_row["repository_id"] != post_row["repository_id"]:
            return (
                "ERROR",
                ("authority:observation_repository_mismatch",),
                {},
                lineage,
                post_row["repository_id"],
            )
        if post_row["observed_at_ms"] < pre_row["observed_at_ms"]:
            return (
                "ERROR",
                ("authority:observation_time_inverted",),
                {},
                lineage,
                post_row["repository_id"],
            )
        repo_id = post_row["repository_id"]
        repo_root = str(
            (post_row["observation"].get("identity") or {}).get(
                "worktree_root_ref"
            )
            or ""
        )
        delta = self._delta_paths(pre_row["observation"], post_row["observation"])
        observation: dict[str, Any] = {
            "repository_id": repo_id,
            "head_commit": post_row["head_commit"],
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
        if kind == "repository.no_generated_mirror_direct_edit":
            try:
                ownership = json.loads(
                    (Path(repo_root) / "source-ownership.json").read_text(
                        encoding="utf-8"
                    )
                )
                targets = {
                    target
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
            mirror_edits = sorted(p for p in delta if p in targets)
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
            observation["authority_errors_sha256"] = canonical_sha256(
                list(errors)
            )
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


__all__ = ["RepositoryStateOracle", "OracleSnapshotInvalid"]
