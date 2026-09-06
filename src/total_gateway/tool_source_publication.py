"""Prepare an exact-source publication review; observation never grants approval.

This is the offline Gateway lifecycle boundary between build material and a
review decision. It reconstructs Git inputs and the permission differential,
instead of accepting a bundle's self-consistent assertions as publication
authority. Candidate code, tests and hooks are never imported or executed.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contracts import canonical_json_bytes, canonical_sha256

from .tool_manifest_evolution import review_manifest_evolution
from .tool_source_bundle import (
    _read_verified_bundle, _stage_root, stage_tool_source_bundle,
    verify_staged_tool_source_bundle,
)
from .tool_source_candidate import (
    SourceCandidateError,
    _invalid_constant,
    _strict_pairs,
    inspect_tool_source_candidate,
    materialize_tool_source_candidate,
    read_tool_source_manifests,
)
from .tool_source_inputs import compile_tool_source_inputs


class SourcePublicationError(SourceCandidateError):
    pass


def prepare_tool_source_publication(
    repository: Path,
    *,
    bundle_path: Path,
    expected_sha256: str,
    base_commit: str,
    candidate_commit: str,
    requested_action_ids: tuple[str, ...],
) -> dict[str, object]:
    """Reconstruct the proposal from separately pinned Git and bundle inputs.

    The caller pins the source scope separately from the archive. The report
    identifies all remaining review and behavioral evidence obligations. It
    cannot mark an isolated build, a digest or an empty differential approved.
    No installed version, Registry, release pointer or running manifest changes.
    """
    candidate = inspect_tool_source_candidate(
        repository, base_commit=base_commit, candidate_commit=candidate_commit,
        requested_action_ids=requested_action_ids,
    )
    raw, index = _read_verified_bundle(bundle_path, expected_sha256=expected_sha256)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        report_raw = archive.read("build-report.json")
        build = json.loads(report_raw, object_pairs_hook=_strict_pairs,
                           parse_constant=_invalid_constant)
    if (build.get("schema") != "tiangong.tool-source-isolated-build-report.v1"
            or canonical_json_bytes(build.get("source_candidate"))
            != canonical_json_bytes(asdict(candidate))):
        raise SourcePublicationError("publication build candidate differs from pinned Git scope")

    process = build.get("build_process")
    checks = build.get("trusted_static_checks")
    if (not isinstance(process, dict) or process.get("ok") is not True
            or process.get("containment") != "windows-appcontainer"
            or process.get("network") != "denied"
            or not isinstance(checks, dict) or checks.get("source_topology_valid") is not True
            or type(checks.get("python_ast_files")) is not int
            or checks["python_ast_files"] < 1
            or any(key in build for key in (
                "error", "error_type", "cleanup_error", "failed_phase", "traceback",
                "source_bundle_error",
            ))):
        raise SourcePublicationError("publication build lacks successful contained static observation")

    artifact = build["build_artifact"]
    # A rehashed archive and input inventory may be internally consistent yet
    # contain bytes from another commit. Compare against newly materialized
    # native Git blobs, not the mutable checkout or the archive's own claim.
    with materialize_tool_source_candidate(repository, candidate) as snapshot:
        inputs = compile_tool_source_inputs(snapshot)
    if canonical_json_bytes(artifact["source_inputs"]) != canonical_json_bytes(asdict(inputs)):
        raise SourcePublicationError("publication source inputs differ from pinned Git bytes")

    before, committed = read_tool_source_manifests(repository, candidate)
    compiled = artifact["gateway_manifest"]
    review = review_manifest_evolution(
        before, compiled, requested_action_ids=requested_action_ids,
    )
    if canonical_json_bytes(build.get("manifest_review")) != canonical_json_bytes(asdict(review)):
        raise SourcePublicationError("publication differential differs from independent review")
    committed_matches = committed == compiled
    if type(build.get("committed_manifest_matches_build")) is not bool or (
        build["committed_manifest_matches_build"] != committed_matches
    ):
        raise SourcePublicationError("publication committed Manifest comparison is inconsistent")

    # Observations are deliberately not accepted as an attested test receipt
    # or a Human/Core review. Unknown producers cannot award those exit gates.
    blockers = {
        "TRUSTED_BUILD_ATTESTATION_REQUIRED",
        "EVIDENCE_CONTRACT_EXECUTION_REQUIRED",
        "HUMAN_OR_CORE_SOURCE_REVIEW_REQUIRED",
        "RUNNING_MANIFEST_LOCK_ACCEPTANCE_REQUIRED",
    }
    if not committed_matches:
        blockers.add("COMMITTED_MANIFEST_DIFFERS_FROM_BUILD")
    if review.unexpected_action_ids:
        blockers.add("UNREVIEWED_COLLATERAL_ACTION_CHANGES")
    if review.risk_downgraded_action_ids:
        blockers.add("EFFECTIVE_RISK_DOWNGRADE_REVIEW_REQUIRED")
    if review.newly_a0_action_ids:
        blockers.add("NEW_A0_ADMISSION_REVIEW_REQUIRED")
    executable_ids = {
        delta.action_id for delta in review.deltas
        if delta.before_permission_sha256 is not None
        or delta.after_permission_sha256 is not None
    }
    result = {
        "schema": "tiangong.tool-source-publication-proposal.v1",
        "status": "SOURCE_PUBLICATION_REVIEW_REQUIRED",
        "source_candidate": asdict(candidate),
        "bundle_sha256": expected_sha256,
        "bundle_manifest_sha256": index["bundle_manifest_sha256"],
        "build_report_sha256": hashlib.sha256(report_raw).hexdigest(),
        "source_inputs_sha256": inputs.source_inputs_sha256,
        "capability_manifest_sha256": index["capability_manifest_sha256"],
        "manifest_review": asdict(review),
        "committed_manifest_matches_build": committed_matches,
        "evidence_contract_action_ids": sorted(executable_ids | set(requested_action_ids)),
        "required_review_action_ids": sorted(
            {delta.action_id for delta in review.deltas} | set(requested_action_ids)
        ),
        "blockers": sorted(blockers),
        "source_bytes_verified_against_git": True,
        "build_attestation_verified": False,
        "evidence_contract_tests_verified": False,
        "review_approval_verified": False,
        "running_manifest_lock_verified": False,
        "may_publish": False,
        "may_authorize": False,
        "may_execute": False,
    }
    result["proposal_sha256"] = canonical_sha256(result)
    return result


_REVIEW_DOMAIN = b"tiangong.tool-source-publication-review.v1\x00"
_EVIDENCE_KINDS = {"build", "evidence_contract", "running_manifest_lock"}


def _reviewed_evidence(proposal, *, review_bytes, signature, trusted_public_key, evidence):
    """Verify the separately configured operator's exact publication decision.

    The trust key is an installation/operator input, never a key carried in a
    Source candidate or review document. The reviewer attests the referenced
    behavioral observations; matching hashes alone do not attest execution.
    This signature is domain-separated from all runtime tickets and grants.
    """
    if (type(review_bytes) is not bytes or not 0 < len(review_bytes) <= 1024 * 1024
            or type(signature) is not bytes or len(signature) != 64
            or type(trusted_public_key) is not bytes or len(trusted_public_key) != 32):
        raise SourcePublicationError("publication review signature input is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(
            signature, _REVIEW_DOMAIN + review_bytes,
        )
        review = json.loads(review_bytes, object_pairs_hook=_strict_pairs,
                            parse_constant=_invalid_constant)
        if canonical_json_bytes(review) != review_bytes:
            raise ValueError("review must use canonical bytes")
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise SourcePublicationError("publication review signature or canonical document is invalid") from exc
    expected_fields = {
        "schema", "proposal_sha256", "reviewed_action_ids",
        "approved_risk_downgraded_action_ids", "approved_new_a0_action_ids",
        "accept_generated_manifest", "evidence_sha256",
    }
    if (not isinstance(review, dict) or set(review) != expected_fields
            or review["schema"] != "tiangong.tool-source-publication-review.v1"
            or review["proposal_sha256"] != proposal["proposal_sha256"]
            or review["reviewed_action_ids"] != proposal["required_review_action_ids"]
            or review["approved_risk_downgraded_action_ids"]
            != list(proposal["manifest_review"]["risk_downgraded_action_ids"])
            or review["approved_new_a0_action_ids"]
            != list(proposal["manifest_review"]["newly_a0_action_ids"])
            or type(review["accept_generated_manifest"]) is not bool
            or (not proposal["committed_manifest_matches_build"]
                and review["accept_generated_manifest"] is not True)):
        raise SourcePublicationError("publication review does not cover the exact source and risk scope")
    if (type(evidence) is not dict or set(evidence) != _EVIDENCE_KINDS
            or type(review["evidence_sha256"]) is not dict
            or set(review["evidence_sha256"]) != _EVIDENCE_KINDS):
        raise SourcePublicationError("publication review evidence set is incomplete")
    for name, raw in evidence.items():
        if (type(raw) is not bytes or not 0 < len(raw) <= 16 * 1024 * 1024
                or hashlib.sha256(raw).hexdigest() != review["evidence_sha256"][name]):
            raise SourcePublicationError("publication review evidence bytes do not match")
    if review["evidence_sha256"]["build"] != proposal["build_report_sha256"]:
        raise SourcePublicationError("publication review attests another build")

    def observation(name, schema, status):
        try:
            value = json.loads(evidence[name], object_pairs_hook=_strict_pairs,
                               parse_constant=_invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise SourcePublicationError("publication behavioral evidence is invalid JSON") from exc
        if (not isinstance(value, dict) or value.get("schema") != schema
                or value.get("status") != status
                or value.get("candidate_sha256") != proposal["source_candidate"]["candidate_sha256"]
                or value.get("source_inputs_sha256") != proposal["source_inputs_sha256"]
                or value.get("capability_manifest_sha256") != proposal["capability_manifest_sha256"]
                or any(value.get(flag) is not False for flag in ("may_publish", "may_authorize", "may_execute"))
                or any(field in value for field in ("error", "cleanup_error", "traceback", "failed_phase"))):
            raise SourcePublicationError("publication behavioral evidence identity or outcome is invalid")
        return value

    contracts = observation("evidence_contract", "tiangong.source-evidence-contract-observation.v1",
                            "EVIDENCE_CONTRACT_OBSERVED")
    rows = contracts.get("cases")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
        raise SourcePublicationError("publication evidence contract cases are missing")
    identities = set()
    coverage = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"case_id", "action_id", "kind", "passed", "observation_sha256"}
                or not isinstance(row["case_id"], str) or not 1 <= len(row["case_id"]) <= 160
                or row["case_id"] in identities or row["passed"] is not True
                or row["action_id"] not in proposal["evidence_contract_action_ids"]
                or not isinstance(row["kind"], str) or row["kind"] not in {"positive", "negative"}
                or not isinstance(row["observation_sha256"], str)
                or len(row["observation_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in row["observation_sha256"])):
            raise SourcePublicationError("publication evidence contract case is invalid or failed")
        identities.add(row["case_id"])
        coverage.add((row["action_id"], row["kind"]))
    if coverage != {(action, kind) for action in proposal["evidence_contract_action_ids"]
                    for kind in ("positive", "negative")}:
        raise SourcePublicationError("publication evidence contract coverage is incomplete")
    lock = observation("running_manifest_lock", "tiangong.running-source-lock-observation.v1",
                       "RUNNING_SOURCE_LOCK_OBSERVED")
    if (lock.get("old_before_sha256") != lock.get("old_after_sha256")
            or not isinstance(lock.get("old_before_sha256"), str)
            or len(lock["old_before_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in lock["old_before_sha256"])
            or lock["old_before_sha256"] == proposal["capability_manifest_sha256"]
            or lock.get("new_run_sha256") != proposal["capability_manifest_sha256"]
            or lock.get("resumption_verified") is not True
            or lock.get("old_run_continued") is not True):
        raise SourcePublicationError("publication live old/new source lock is incomplete")
    return review


def _publication_receipt(proposal, review, review_bytes, trusted_public_key):
    result = {
        "schema": "tiangong.published-tool-source-revision.v1",
        "status": "SOURCE_REVISION_PUBLISHED",
        "candidate_commit": proposal["source_candidate"]["candidate_commit"],
        "candidate_sha256": proposal["source_candidate"]["candidate_sha256"],
        "proposal_sha256": proposal["proposal_sha256"],
        "bundle_sha256": proposal["bundle_sha256"],
        "source_inputs_sha256": proposal["source_inputs_sha256"],
        "capability_manifest_sha256": proposal["capability_manifest_sha256"],
        "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "review_key_sha256": hashlib.sha256(trusted_public_key).hexdigest(),
        "evidence_sha256": review["evidence_sha256"],
        "source_root": "version/source",
        "running_manifest_changed": False,
        "may_authorize": False,
        "may_execute": False,
    }
    result["publication_sha256"] = canonical_sha256(result)
    return result


def publish_tool_source_revision(
    repository: Path, *, bundle_path: Path, expected_sha256: str, base_commit: str,
    candidate_commit: str, requested_action_ids: tuple[str, ...], publication_root: Path,
    review_bytes: bytes, signature: bytes, trusted_public_key: bytes,
    evidence: dict[str, bytes],
) -> dict[str, object]:
    """Publish one separately reviewed version without selecting it for a Run.

    This operator-only offline lifecycle API has no model-facing route. Its
    configured trust key and exact signed review are prerequisites, including
    explicit per-action decisions for risk decreases and newly admitted A0.
    Existing staging verifies and preserves all source/Manifest bytes. There
    is no active pointer, Registry replacement, import or current-request use.
    A failed new directory remains partial; it is never repaired or overwritten.
    """
    root = _stage_root(publication_root)
    if root.exists() or not root.parent.is_dir():
        raise SourcePublicationError("publication destination must be a new directory")
    if type(evidence) is not dict:
        raise SourcePublicationError("publication review evidence set is incomplete")
    evidence = dict(evidence)  # Detach the container; values must be immutable bytes.
    proposal = prepare_tool_source_publication(
        repository, bundle_path=bundle_path, expected_sha256=expected_sha256,
        base_commit=base_commit, candidate_commit=candidate_commit,
        requested_action_ids=requested_action_ids,
    )
    review = _reviewed_evidence(proposal, review_bytes=review_bytes, signature=signature,
                               trusted_public_key=trusted_public_key, evidence=evidence)
    bundle_raw, _ = _read_verified_bundle(bundle_path, expected_sha256=expected_sha256)
    root.mkdir(mode=0o700, exist_ok=False)

    def write(name, raw):
        with (root / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        (root / name).chmod(0o444)

    write("bundle.zip", bundle_raw)
    write("review.json", review_bytes)
    write("review.sig", signature)
    for name, raw in evidence.items():
        write(name + ".json", raw)
    staged = stage_tool_source_bundle(root / "bundle.zip", expected_sha256=expected_sha256,
                                     staging_root=root / "version")
    if verify_staged_tool_source_bundle(root / "bundle.zip", expected_sha256=expected_sha256,
                                        staging_root=root / "version") != staged:
        raise SourcePublicationError("publication final source verification disagrees")
    result = _publication_receipt(proposal, review, review_bytes, trusted_public_key)
    receipt = canonical_json_bytes(result) + b"\n"
    write("publication.json", receipt)  # Completion marker is always last.
    if (root / "publication.json").read_bytes() != receipt:
        raise SourcePublicationError("publication receipt readback differs")
    root.chmod(0o555)
    return result


def verify_published_tool_source_revision(
    repository: Path, *, publication_root: Path, expected_publication_sha256: str,
    expected_bundle_sha256: str, base_commit: str, candidate_commit: str,
    requested_action_ids: tuple[str, ...], trusted_public_key: bytes,
) -> dict[str, object]:
    """Reopen a version against external pins and trust, never its own key/index."""
    root = _stage_root(publication_root)
    names = {"bundle.zip", "review.json", "review.sig", "publication.json",
             *{name + ".json" for name in _EVIDENCE_KINDS}}
    if not root.is_dir() or {path.name for path in root.iterdir()} != names | {"version"}:
        raise SourcePublicationError("publication version is incomplete or has unexpected files")
    for name in names:
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_mode & 0o222):
            raise SourcePublicationError("publication metadata is writable or linked")
        if path.stat().st_size > (512 if name == "bundle.zip" else 16) * 1024 * 1024:
            raise SourcePublicationError("publication input exceeds its size limit")
    proposal = prepare_tool_source_publication(
        repository, bundle_path=root / "bundle.zip", expected_sha256=expected_bundle_sha256,
        base_commit=base_commit, candidate_commit=candidate_commit,
        requested_action_ids=requested_action_ids,
    )
    review_bytes = (root / "review.json").read_bytes()
    evidence = {name: (root / (name + ".json")).read_bytes() for name in _EVIDENCE_KINDS}
    review = _reviewed_evidence(
        proposal, review_bytes=review_bytes, signature=(root / "review.sig").read_bytes(),
        trusted_public_key=trusted_public_key, evidence=evidence,
    )
    expected = _publication_receipt(proposal, review, review_bytes, trusted_public_key)
    if (expected["publication_sha256"] != expected_publication_sha256
            or (root / "publication.json").read_bytes() != canonical_json_bytes(expected) + b"\n"):
        raise SourcePublicationError("publication receipt differs from its reviewed identity")
    verify_staged_tool_source_bundle(root / "bundle.zip", expected_sha256=expected_bundle_sha256,
                                     staging_root=root / "version")
    return expected
