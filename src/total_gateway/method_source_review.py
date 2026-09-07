"""Gateway-owned review of Method Source data, never execution or World cutover.

Trust keys and the current snapshot pin come from the operator, not a proposal.
This boundary verifies independent signed observations and an exact signed
review, then uses the existing Method World compiler. There is no key creation,
model-facing route, filesystem write, current pointer, Runtime or Memory access.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contracts import canonical_sha256
from world_understanding.skill_method_world.compiler import (
    compile_native_method_source, compile_skill_method_world, read_method_json,
)
from world_understanding.skill_method_world.lifecycle import (
    MethodSourceCandidateV1, compile_method_source_lifecycle,
)
from world_understanding.skill_method_world.models import (
    LegacySkillMethodCorpusV1, ReviewedMethodSourceBindingV1,
    SkillMethodWorldError, SkillMethodWorldSnapshotV1,
)

METHOD_SIMULATION_DOMAIN = b"tiangong.method-source-simulation.v1\x00"
METHOD_REVIEW_DOMAIN = b"tiangong.method-source-review.v1\x00"


class MethodSourceReviewError(SkillMethodWorldError):
    pass


def method_simulation_subject_sha256(candidate: MethodSourceCandidateV1) -> str:
    """Bind every proposed change without a candidate/evidence hash cycle."""
    payload = candidate.payload()
    del payload["simulation_evidence_sha256"]
    return canonical_sha256({"domain": "tiangong.method-simulation-subject.v1", "candidate": payload})


@dataclass(frozen=True, slots=True)
class MethodSimulationEvidenceV1:
    report_bytes: bytes
    signature: bytes
    case_observations: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class ReviewedMethodWorldRevisionV1:
    """Prepared semantic revision. A self-hashed instance is NOT a receipt of trust."""
    base_snapshot_sha256: str
    lifecycle_plan_sha256: str
    snapshot: SkillMethodWorldSnapshotV1
    review_sha256: str
    reviewer_key_sha256: str
    observer_key_sha256: str
    simulation_evidence_sha256s: tuple[str, ...]
    invalidation_refs: tuple[str, ...]
    revision_sha256: str
    may_publish: bool = False
    may_authorize: bool = False
    may_execute: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "schema": "tiangong.reviewed-method-world-revision.v1",
            "status": "METHOD_WORLD_REVISION_PREPARED",
            "base_snapshot_sha256": self.base_snapshot_sha256,
            "lifecycle_plan_sha256": self.lifecycle_plan_sha256,
            "snapshot": {**self.snapshot.payload(), "snapshot_sha256": self.snapshot.snapshot_sha256},
            "review_sha256": self.review_sha256,
            "reviewer_key_sha256": self.reviewer_key_sha256,
            "observer_key_sha256": self.observer_key_sha256,
            "simulation_evidence_sha256s": list(self.simulation_evidence_sha256s),
            "invalidation_refs": list(self.invalidation_refs),
            "current_world_changed": False,
            "may_publish": self.may_publish,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())


def _signed_document(raw: bytes, signature: bytes, key: bytes, domain: bytes) -> dict:
    if (type(signature) is not bytes or len(signature) != 64
            or type(key) is not bytes or len(key) != 32):
        raise MethodSourceReviewError("method review signature input is invalid")
    value = read_method_json(raw)
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, domain + raw)
    except (ValueError, InvalidSignature) as exc:
        raise MethodSourceReviewError("method review signature is invalid") from exc
    return value


def _verify_simulation(candidate, evidence, observer_key, reviewed_at_ms):
    if type(evidence) is not MethodSimulationEvidenceV1:
        raise MethodSourceReviewError("method simulation evidence type is invalid")
    report = _signed_document(evidence.report_bytes, evidence.signature, observer_key, METHOD_SIMULATION_DOMAIN)
    digest = hashlib.sha256(evidence.report_bytes).hexdigest()
    subject = method_simulation_subject_sha256(candidate)
    if (set(report) != {"schema", "subject_sha256", "status", "observed_at_ms", "cases"}
            or report["schema"] != "tiangong.method-source-simulation.v1"
            or report["subject_sha256"] != subject
            or report["status"] != "METHOD_SIMULATION_OBSERVED"
            or type(report["observed_at_ms"]) is not int
            or not 0 <= report["observed_at_ms"] <= reviewed_at_ms
            or digest != candidate.simulation_evidence_sha256):
        raise MethodSourceReviewError("method simulation identity, time or status is invalid")
    rows = report["cases"]
    if (type(rows) is not list or not 2 <= len(rows) <= 256
            or type(evidence.case_observations) is not tuple
            or len(evidence.case_observations) != len(rows)
            or any(type(raw) is not bytes or not 0 < len(raw) <= 1024 * 1024
                   for raw in evidence.case_observations)
            or sum(map(len, evidence.case_observations)) > 16 * 1024 * 1024):
        raise MethodSourceReviewError("method simulation case set is invalid")
    observations = {hashlib.sha256(raw).hexdigest(): raw for raw in evidence.case_observations}
    used, ids, kinds = set(), set(), set()
    for row in rows:
        if (type(row) is not dict
                or set(row) != {"case_id", "kind", "passed", "observation_sha256"}
                or type(row["case_id"]) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", row["case_id"]) is None
                or row["case_id"] in ids or row["passed"] is not True
                or type(row["kind"]) is not str or row["kind"] not in {"positive", "negative"}
                or type(row["observation_sha256"]) is not str
                or row["observation_sha256"] not in observations):
            raise MethodSourceReviewError("method simulation case failed or is malformed")
        raw = observations[row["observation_sha256"]]
        case = read_method_json(raw)
        outcome = "ACCEPT" if row["kind"] == "positive" else "REJECT"
        if (set(case) != {"schema", "subject_sha256", "case_id", "kind", "expected", "observed"}
                or case["schema"] != "tiangong.method-source-case-observation.v1"
                or case["subject_sha256"] != subject or case["case_id"] != row["case_id"]
                or case["kind"] != row["kind"]
                or case["expected"] != outcome or case["observed"] != outcome):
            raise MethodSourceReviewError("method simulation raw observation contradicts PASS")
        ids.add(row["case_id"])
        kinds.add(row["kind"])
        used.add(row["observation_sha256"])
    if kinds != {"positive", "negative"} or used != set(observations) or len(used) != len(rows):
        raise MethodSourceReviewError("method simulation positive/negative coverage is incomplete")
    return digest


def prepare_reviewed_method_world_revision(
    base_snapshot: SkillMethodWorldSnapshotV1,
    candidates: tuple[MethodSourceCandidateV1, ...], *,
    expected_base_snapshot_sha256: str,
    corpus: LegacySkillMethodCorpusV1,
    source_documents: dict[str, tuple[str, bytes]],
    simulation_evidence: dict[str, MethodSimulationEvidenceV1],
    review_bytes: bytes, review_signature: bytes,
    trusted_reviewer_public_key: bytes, trusted_observer_public_key: bytes,
    now_ms: int,
) -> ReviewedMethodWorldRevisionV1:
    """Validate signed inputs and reconstruct a revision for the SAME domain.

    Caller pins come from the trusted World/operator, never the model. Two
    separately configured keys attest simulation and review. Signatures do not
    mathematically prove semantic correctness; a dishonest trusted observer is
    outside this boundary. Actual World ingress/swap is the P9 R3 boundary.
    """
    if (type(now_ms) is not int or now_ms < 0
            or type(trusted_reviewer_public_key) is not bytes or len(trusted_reviewer_public_key) != 32
            or type(trusted_observer_public_key) is not bytes or len(trusted_observer_public_key) != 32
            or trusted_reviewer_public_key == trusted_observer_public_key):
        raise MethodSourceReviewError("method review needs a clock and distinct external trust keys")
    if (type(base_snapshot) is not SkillMethodWorldSnapshotV1
            or not base_snapshot.has_valid_sha256()
            or base_snapshot.snapshot_sha256 != expected_base_snapshot_sha256):
        raise MethodSourceReviewError("method review base does not match the trusted current snapshot")
    # Hash checks alone cannot establish internal graph/provenance consistency.
    reconstructed = compile_skill_method_world(
        base_snapshot.primitives, corpus=corpus, migration_bindings=base_snapshot.migration_bindings,
        reviewed_source_bindings=base_snapshot.reviewed_source_bindings,
    )
    if reconstructed != base_snapshot:
        raise MethodSourceReviewError("method review base graph or provenance is inconsistent")
    if (type(candidates) is not tuple or not 1 <= len(candidates) <= 128
            or any(type(c) is not MethodSourceCandidateV1 for c in candidates)
            or type(source_documents) is not dict or type(simulation_evidence) is not dict):
        raise MethodSourceReviewError("method review inputs are invalid")
    candidates = tuple(replace(c) for c in candidates)
    if any(c.may_authorize is not False or c.may_execute is not False for c in candidates):
        raise MethodSourceReviewError("method review candidates must be non-authorizing")
    plan = compile_method_source_lifecycle(base_snapshot, candidates)
    if len({c.candidate_id for c in candidates}) != len(candidates):
        raise MethodSourceReviewError("method review candidate IDs are duplicated")
    source_documents, simulation_evidence = dict(source_documents), dict(simulation_evidence)
    changed = {c.method_id for c in candidates}
    if (set(simulation_evidence) != changed
            or set(source_documents) != {c.method_id for c in candidates if c.operation != "REMOVE"}):
        raise MethodSourceReviewError("method review source/evidence scope is incomplete or extraneous")
    review = _signed_document(review_bytes, review_signature, trusted_reviewer_public_key, METHOD_REVIEW_DOMAIN)
    if (set(review) != {"schema", "decision", "base_snapshot_sha256", "plan_sha256",
                       "candidate_sha256s", "reviewed_at_ms", "expires_at_ms"}
            or review["schema"] != "tiangong.method-source-review.v1" or review["decision"] != "APPROVE"
            or review["base_snapshot_sha256"] != expected_base_snapshot_sha256
            or review["plan_sha256"] != plan.plan_sha256
            or review["candidate_sha256s"] != list(plan.candidate_sha256s)
            or type(review["reviewed_at_ms"]) is not int or type(review["expires_at_ms"]) is not int
            or not 0 <= review["reviewed_at_ms"] <= now_ms < review["expires_at_ms"]):
        raise MethodSourceReviewError("method review decision, scope or validity window is invalid")
    review_digest = hashlib.sha256(review_bytes).hexdigest()
    natives = {b.method_id: b for b in base_snapshot.reviewed_source_bindings if b.method_id not in changed}
    by_id = {p.method_id: p for p in base_snapshot.primitives}
    evidence_digests = []
    for c in candidates:
        evidence_digest = _verify_simulation(c, simulation_evidence[c.method_id],
                                             trusted_observer_public_key, review["reviewed_at_ms"])
        evidence_digests.append(evidence_digest)
        if c.operation == "REMOVE":
            continue
        document = source_documents[c.method_id]
        if type(document) is not tuple or len(document) != 2:
            raise MethodSourceReviewError("method source document binding is invalid")
        path, raw = document
        primitive = compile_native_method_source(path, raw, expected_source_sha256=c.primitive.source_sha256)
        if primitive != c.primitive:
            raise MethodSourceReviewError("method candidate differs from independently parsed source bytes")
        if c.operation == "UPDATE":
            previous = by_id[c.method_id].version
            if (re.fullmatch(r"v[1-9][0-9]{0,8}", previous) is None
                    or int(primitive.version[1:]) <= int(previous[1:])):
                raise MethodSourceReviewError("method update must advance, not roll back, its version")
        binding = ReviewedMethodSourceBindingV1(
            method_id=c.method_id, version=primitive.version, source_path=path,
            source_sha256=primitive.source_sha256, descriptor_sha256=primitive.descriptor_sha256,
            review_sha256=review_digest, simulation_evidence_sha256=evidence_digest, binding_sha256="0" * 64,
        )
        natives[c.method_id] = replace(binding, binding_sha256=binding.computed_sha256())
    snapshot = compile_skill_method_world(
        plan.next_primitives, corpus=corpus,
        migration_bindings=tuple(b for b in base_snapshot.migration_bindings if b.method_id not in changed),
        reviewed_source_bindings=tuple(natives.values()),
    )
    if snapshot.method_sources_sha256 != plan.next_method_sources_sha256:
        raise MethodSourceReviewError("method review compiled another source set")
    result = ReviewedMethodWorldRevisionV1(
        base_snapshot_sha256=expected_base_snapshot_sha256, lifecycle_plan_sha256=plan.plan_sha256,
        snapshot=snapshot, review_sha256=review_digest,
        reviewer_key_sha256=hashlib.sha256(trusted_reviewer_public_key).hexdigest(),
        observer_key_sha256=hashlib.sha256(trusted_observer_public_key).hexdigest(),
        simulation_evidence_sha256s=tuple(sorted(evidence_digests)), invalidation_refs=plan.invalidation_refs,
        revision_sha256="0" * 64,
    )
    return replace(result, revision_sha256=result.computed_sha256())


def verify_reviewed_method_world_revision(
    *, expected_revision_sha256: str, **review_inputs,
) -> ReviewedMethodWorldRevisionV1:
    """Reconstruct from source/evidence/signatures, never trust a receipt's flags."""
    result = prepare_reviewed_method_world_revision(**review_inputs)
    if result.revision_sha256 != expected_revision_sha256:
        raise MethodSourceReviewError("reviewed method revision differs from its external pin")
    return result
