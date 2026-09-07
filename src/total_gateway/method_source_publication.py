"""Operator-only Method archive and resolver for the existing World ingress.

The archive has no current pointer. The existing WorldState cut owns selection.
Publication signatures bind both the R2 evidence and the exact target frame/head.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contracts import canonical_json_bytes
from contracts.capability_composition import SkillSourcePrimitiveV1
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.domain_contribution import FrameBindingV1
from world_understanding.skill_method_world.lifecycle import MethodSourceCandidateV1
from world_understanding.skill_method_world.models import (
    LegacySkillMethodCorpusV1, LegacySkillMethodEvidenceV1, MethodMigrationBindingV1,
    ReviewedMethodSourceBindingV1, SkillMethodRelationV1, SkillMethodWorldSnapshotV1,
)
from world_understanding.skill_method_world.publication import (
    PUBLICATION_SCHEMA, ARCHIVE_WATERMARK, SNAPSHOT_WATERMARK,
    VerifiedMethodUpdate, method_marker,
)
from world_understanding.software_world import SoftwareWorldFrame
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state.store import MaterializedWorldSnapshot
from .method_source_review import (
    MethodSimulationEvidenceV1, MethodSourceReviewError,
    prepare_reviewed_method_world_revision,
)
from .tool_source_bundle import _stage_root
from .tool_source_candidate import _commit, _tree, _read_blob, _source_policy, _classify, _strict_pairs, _invalid_constant

PUBLICATION_DOMAIN = b"tiangong.method-world-publication.v1\x00"
ARCHIVE_SCHEMA = "tiangong.method-world-publication-archive.v1"
_MAX_ARCHIVE = 32 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")


def _digest(value: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise MethodSourceReviewError("method archive digest is invalid")
    return value


def _json(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_ARCHIVE:
        raise MethodSourceReviewError("method archive exceeds byte budget")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
        if type(value) is not dict or canonical_json_bytes(value) != raw:
            raise ValueError("not canonical")
    except (ValueError, TypeError, RecursionError) as exc:
        raise MethodSourceReviewError("method archive is not canonical JSON") from exc
    return value


def _frame_payload(frame: SoftwareWorldFrame) -> dict:
    return {**asdict(frame), "scope": frame.scope.model_dump(mode="json"),
            "time": frame.time.model_dump(mode="json"),
            "world_cut": None if frame.world_cut is None else frame.world_cut.model_dump(mode="json")}


def _frame_load(value: dict) -> SoftwareWorldFrame:
    fields = dict(value)
    frame_id, revision = fields.pop("frame_id"), fields.pop("frame_revision_hash")
    fields["scope"] = WorldScope.model_validate_json(canonical_json_bytes(fields["scope"]))
    fields["time"] = WorldTime.model_validate_json(canonical_json_bytes(fields["time"]))
    fields["world_cut"] = WorldCut.model_validate_json(canonical_json_bytes(fields["world_cut"]))
    frame = SoftwareWorldFrame.build(**fields)
    if frame.frame_id != frame_id or frame.frame_revision_hash != revision:
        raise MethodSourceReviewError("method archive frame identity is invalid")
    FrameBindingV1.from_frame(frame, frame.world_cut).require_exact_frame(frame, frame.world_cut, repository_bound=True)
    return frame


def _snapshot_payload(snapshot: SkillMethodWorldSnapshotV1) -> dict:
    return {**snapshot.payload(), "snapshot_sha256": snapshot.snapshot_sha256}


def _snapshot_load(value: dict) -> SkillMethodWorldSnapshotV1:
    fields = dict(value)
    fields["primitives"] = tuple(SkillSourcePrimitiveV1.model_validate_json(canonical_json_bytes(p)) for p in fields["primitives"])
    fields["migration_bindings"] = tuple(MethodMigrationBindingV1(**{
        **b, "legacy_skill_ids": tuple(b["legacy_skill_ids"]), "required_phases": tuple(b["required_phases"]),
    }) for b in fields["migration_bindings"])
    fields["relations"] = tuple(SkillMethodRelationV1(**r) for r in fields["relations"])
    natives = fields.pop("reviewed_source_bindings", ())
    fields["reviewed_source_bindings"] = tuple(ReviewedMethodSourceBindingV1(**{
        k: v for k, v in b.items() if k != "schema"
    }) for b in natives)
    snapshot = SkillMethodWorldSnapshotV1(**fields)
    if canonical_json_bytes(_snapshot_payload(snapshot)) != canonical_json_bytes(value):
        raise MethodSourceReviewError("method archive snapshot fields drifted")
    return snapshot


def _inputs_payload(inputs: dict) -> dict:
    """Archive original R2 inputs, not its result's self-asserted trust flags."""
    return {
        "base_snapshot": _snapshot_payload(inputs["base_snapshot"]),
        "corpus": asdict(inputs["corpus"]),
        "candidates": [{**c.payload(), "candidate_sha256": c.candidate_sha256} for c in inputs["candidates"]],
        "source_documents": {k: {"path": p, "text": raw.decode("utf-8")} for k, (p, raw) in inputs["source_documents"].items()},
        "simulation_evidence": {k: {"report": e.report_bytes.decode("utf-8"), "signature": e.signature.hex(),
                                     "observations": [b.decode("utf-8") for b in e.case_observations]}
                                for k, e in inputs["simulation_evidence"].items()},
        "review": inputs["review_bytes"].decode("utf-8"), "review_signature": inputs["review_signature"].hex(),
    }


def _inputs_load(value: dict) -> dict:
    fields = dict(value)
    base = _snapshot_load(fields.pop("base_snapshot"))
    corpus = fields.pop("corpus")
    corpus = LegacySkillMethodCorpusV1(**{**corpus, "evidence": tuple(
        LegacySkillMethodEvidenceV1(**{**e, "observed_phases": tuple(e["observed_phases"])}) for e in corpus["evidence"])})
    candidates = []
    for raw in fields.pop("candidates"):
        c = dict(raw); schema = c.pop("schema")
        if schema != "tiangong.skill-method-source-lifecycle.v1":
            raise MethodSourceReviewError("method archive candidate schema is invalid")
        if c["primitive"] is not None:
            c["primitive"] = SkillSourcePrimitiveV1.model_validate_json(canonical_json_bytes(c["primitive"]))
        candidates.append(MethodSourceCandidateV1(**c))
    docs = {k: (v["path"], v["text"].encode("utf-8")) for k, v in fields.pop("source_documents").items()}
    evidence = {k: MethodSimulationEvidenceV1(v["report"].encode("utf-8"), bytes.fromhex(v["signature"]),
                                              tuple(x.encode("utf-8") for x in v["observations"]))
                for k, v in fields.pop("simulation_evidence").items()}
    result = {"base_snapshot": base, "expected_base_snapshot_sha256": base.snapshot_sha256,
              "corpus": corpus, "candidates": tuple(candidates), "source_documents": docs,
              "simulation_evidence": evidence, "review_bytes": fields.pop("review").encode("utf-8"),
              "review_signature": bytes.fromhex(fields.pop("review_signature"))}
    if fields or canonical_json_bytes(_inputs_payload(result)) != canonical_json_bytes(value):
        raise MethodSourceReviewError("method archive input fields drifted")
    return result


def build_method_publication_body(*, frame: SoftwareWorldFrame, previous: MaterializedWorldSnapshot,
                                  review_inputs: dict, publication_at_ms: int) -> bytes:
    """Prepare exact bytes for a separate operator signature; do not sign them."""
    if (previous.state.frame_ref.sha256 != frame.frame_revision_hash
            or previous.frame_id != frame.frame_id or previous.state.scope != frame.scope
            or frame.world_cut != previous.cut or type(publication_at_ms) is not int
            or publication_at_ms < previous.state.materialized_at_ms):
        raise MethodSourceReviewError("method publication target frame/head/time is invalid")
    prepared = prepare_reviewed_method_world_revision(**review_inputs)
    body = {
        "schema": ARCHIVE_SCHEMA, "frame": _frame_payload(frame),
        "expected_state_ref": previous.state_ref.model_dump(mode="json"),
        "previous_archive_sha256": method_marker(previous, ARCHIVE_WATERMARK),
        "publication_at_ms": publication_at_ms, "revision_sha256": prepared.revision_sha256,
        "inputs": _inputs_payload(review_inputs),
    }
    raw = canonical_json_bytes(body)
    _json(raw)
    return raw


def stage_method_publication(*, archive_root: Path, body: bytes, publication_signature: bytes) -> str:
    """Store immutable bytes only. No current pointer, approval or World write.

    A partial file is retained and rejected, never repaired in place. The one
    WorldState index will select the archive only after independent verification.
    """
    _json(body)
    if type(publication_signature) is not bytes or len(publication_signature) != 64:
        raise MethodSourceReviewError("method publication signature is malformed")
    raw = canonical_json_bytes({"body": body.decode("utf-8"), "signature": publication_signature.hex()})
    _json(raw)
    root = _stage_root(archive_root)
    if not root.is_dir():
        raise MethodSourceReviewError("operator method archive root must already exist")
    digest = hashlib.sha256(raw).hexdigest(); path = root / (digest + ".json")
    try:
        with path.open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        path.chmod(0o444)
    except FileExistsError:
        pass
    if _read_archive(root, digest) != raw:
        raise MethodSourceReviewError("method archive readback differs")
    return digest


def _read_archive(root: Path, digest: str) -> bytes:
    path = _stage_root(root) / (_digest(digest) + ".json")
    _stage_root(path)
    stat = path.stat()
    if (not path.is_file() or stat.st_nlink != 1 or stat.st_mode & 0o222
            or not 0 < stat.st_size <= _MAX_ARCHIVE):
        raise MethodSourceReviewError("method archive is writable, linked or oversized")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_ARCHIVE + 1)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise MethodSourceReviewError("method archive byte identity differs")
    return raw


def method_publication_envelope(*, archive_sha256: str, frame: SoftwareWorldFrame, at_ms: int) -> WorldIngressEnvelope:
    """Build the reference-only source event sent to the SAME facade.accept()."""
    _digest(archive_sha256)
    return build_post_commit_source_envelope(
        source_kind="SYSTEM_GOVERNANCE", source_native_id="method-publication:" + archive_sha256,
        producer_ref="total_gateway.method_source_publication",
        payload={"schema": PUBLICATION_SCHEMA, "archive_sha256": archive_sha256, "frame_id": frame.frame_id},
        source_time=WorldTime(valid_from_ms=at_ms, observed_at_ms=at_ms, recorded_at_ms=at_ms),
        scope=frame.scope, correlation_id="method-publication:" + archive_sha256,
    )


@dataclass(frozen=True, slots=True)
class MethodPublicationResolver:
    """Operator configuration, not a registry, writer, Runtime or model route."""
    archive_root: Path
    source_repository: Path
    repository_id: str
    worktree_id: str
    source_policy_commit: str
    bootstrap_snapshot_sha256: str
    trusted_reviewer_public_key: bytes
    trusted_observer_public_key: bytes
    clock_ms: Callable[[], int]

    def _verified_archive(self, digest: str, *, at_ms: int | None):
        outer = _json(_read_archive(self.archive_root, digest))
        if set(outer) != {"body", "signature"}:
            raise MethodSourceReviewError("method archive envelope is invalid")
        body_bytes = outer["body"].encode("utf-8")
        try:
            Ed25519PublicKey.from_public_bytes(self.trusted_reviewer_public_key).verify(
                bytes.fromhex(outer["signature"]), PUBLICATION_DOMAIN + body_bytes)
        except (ValueError, InvalidSignature) as exc:
            raise MethodSourceReviewError("method publication target signature is invalid") from exc
        body = _json(body_bytes)
        if set(body) != {"schema", "frame", "expected_state_ref", "previous_archive_sha256",
                         "publication_at_ms", "revision_sha256", "inputs"} or body["schema"] != ARCHIVE_SCHEMA:
            raise MethodSourceReviewError("method publication archive schema is invalid")
        frame = _frame_load(body["frame"])
        if frame.repository != self.repository_id or frame.worktree != self.worktree_id:
            raise MethodSourceReviewError("method publication repository/worktree is not configured")
        expected = WorldRecordRef.model_validate_json(canonical_json_bytes(body["expected_state_ref"]))
        if expected.record_type != "world_state":
            raise MethodSourceReviewError("method publication base is not a WorldState")
        timestamp = body["publication_at_ms"]
        if (type(timestamp) is not int or timestamp < frame.time.recorded_at_ms
                or (at_ms is not None and (type(at_ms) is not int or at_ms < timestamp))):
            raise MethodSourceReviewError("method publication timestamp is invalid")
        inputs = _inputs_load(body["inputs"])
        # For historical replay the signed publication time, not today's clock,
        # verifies the review window. This cannot authorize a new publication.
        inputs.update(now_ms=timestamp if at_ms is None else at_ms,
                      trusted_reviewer_public_key=self.trusted_reviewer_public_key,
                      trusted_observer_public_key=self.trusted_observer_public_key)
        revision = prepare_reviewed_method_world_revision(**inputs)
        if revision.revision_sha256 != body["revision_sha256"]:
            raise MethodSourceReviewError("method archive reconstructs another revision")
        return body, frame, expected, inputs, revision

    def __call__(self, envelope: WorldIngressEnvelope, previous: MaterializedWorldSnapshot) -> VerifiedMethodUpdate:
        payload = envelope.payload_inline
        if (envelope.source_kind != "SYSTEM_GOVERNANCE" or type(payload) is not dict
                or set(payload) != {"schema", "archive_sha256", "frame_id"}
                or payload["schema"] != PUBLICATION_SCHEMA):
            raise MethodSourceReviewError("method publication ingress payload is invalid")
        digest = _digest(payload["archive_sha256"])
        body, frame, expected, inputs, revision = self._verified_archive(digest, at_ms=self.clock_ms())
        expected_method = method_marker(previous, SNAPSHOT_WATERMARK) or self.bootstrap_snapshot_sha256
        if (expected != previous.state_ref or frame.scope != previous.state.scope
                or frame.scope != envelope.scope_hint or frame.frame_id != payload["frame_id"]
                or frame.frame_id != previous.frame_id
                or frame.frame_revision_hash != previous.state.frame_ref.sha256
                or frame.world_cut != previous.cut
                or envelope.source_time != WorldTime(valid_from_ms=body["publication_at_ms"],
                    observed_at_ms=body["publication_at_ms"], recorded_at_ms=body["publication_at_ms"])
                or body["previous_archive_sha256"] != method_marker(previous, ARCHIVE_WATERMARK)
                or inputs["base_snapshot"].snapshot_sha256 != expected_method):
            raise MethodSourceReviewError("method publication target is stale or crosses scope/frame")
        repository = _stage_root(self.source_repository)
        tree = _tree(repository, _commit(repository, frame.commit))
        policy, _ = _source_policy(repository, _tree(repository, _commit(repository, self.source_policy_commit)))
        for path, raw in inputs["source_documents"].values():
            entry = tree.get(path)
            role, _ = _classify(path, policy)
            if (role != "SOURCE" or entry is None or entry[0] not in {"100644", "100755"}
                    or _read_blob(repository, entry[1]) != raw):
                raise MethodSourceReviewError("method source is not the pinned authoritative Git bytes")
        old = {p.method_id: p for p in inputs["base_snapshot"].primitives}
        keys = set(revision.invalidation_refs)
        keys.update({"method-world:" + inputs["base_snapshot"].snapshot_sha256,
                     "method-sources:" + inputs["base_snapshot"].method_sources_sha256})
        keys.update("source:" + old[c.method_id].source_sha256 for c in inputs["candidates"] if c.method_id in old)
        return VerifiedMethodUpdate(frame, expected, digest, revision.snapshot, tuple(sorted(keys)))

    def load(self, state: MaterializedWorldSnapshot) -> SkillMethodWorldSnapshotV1:
        """Resolve only this exact WorldState archive; never choose latest."""
        digest = method_marker(state, ARCHIVE_WATERMARK)
        if digest is None:
            raise MethodSourceReviewError("WorldState has no archived Method revision")
        _body, frame, _expected, _inputs, revision = self._verified_archive(digest, at_ms=None)
        if (state.state.scope != frame.scope or state.frame_id != frame.frame_id
                or method_marker(state, SNAPSHOT_WATERMARK) != revision.snapshot.snapshot_sha256):
            raise MethodSourceReviewError("archived Method revision crosses the pinned WorldState")
        return revision.snapshot
