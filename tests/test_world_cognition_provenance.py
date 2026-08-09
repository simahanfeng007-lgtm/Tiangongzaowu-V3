from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))

from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.cognition_statement import CognitionValue
from v3.world_cognition.consolidator import CognitionProposal
from v3.world_cognition.evidence import CognitionEvidenceLedger
from v3.world_cognition.stability import evaluate_evidence, highest_eligible_level
from v3.world_cognition.store import CognitionIntegrityError, WorldCognitionStore

LIFE = "life.main"
WORLD = "a" * 64
PRINCIPAL = "b" * 64
NOW = 80_000_000
ZERO = "0" * 64


def h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cid() -> str:
    return CognitionProposal(
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
        claim_kind="component_role",
        subject_ref="tiangong.component",
        predicate="role",
        value=CognitionValue(kind="string", string_value="core"),
    ).cognition_id


def make_evidence(
    tag: str,
    *,
    group: str,
    roots: tuple[str, ...],
    credibility: int = 600,
    authority: int = 600,
    provenance: int = 600,
    derived_from: tuple[str, ...] = (),
    ancestors: tuple[str, ...] = (),
    source_kind: str = "code_perception",
    evidence_class: str = "observed",
    extractor_kind: str = "deterministic",
) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind=source_kind,
        object_id=f"prov.{tag}",
        object_revision=1,
        sha256=h("source:" + tag),
    )
    kwargs = dict(
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
        source_ref=source,
        evidence_class=evidence_class,
        source_credibility_milli=credibility,
        authority_ceiling_milli=authority,
        provenance_integrity_milli=provenance,
        observation_mode="positive",
        observation="provenance observation " + tag,
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=h("group:" + group),
        lineage_root_hashes=tuple(sorted(roots)),
        derived_from_evidence_ids=tuple(sorted(derived_from)),
        ancestor_cognition_ids=tuple(sorted(ancestors)),
        content_object_id="prov.content." + tag,
        content_sha256=h("content:" + tag),
        extractor_kind=extractor_kind,
        observed_at_ms=NOW,
        valid_from_ms=0,
        valid_until_ms=None,
        volatility_class="structural",
    )
    item = CognitionEvidence(
        evidence_id=derive_cognition_evidence_id(**kwargs),
        **kwargs,
        evidence_sha256=ZERO,
    )
    return item.with_computed_evidence_sha256()


def test_shared_lineage_root_cannot_fake_independent_quorum_with_different_group_hashes():
    root = h("same-origin")
    items = (
        make_evidence("a", group="fake-independent-a", roots=(root,), credibility=1000, authority=1000, provenance=1000),
        make_evidence("b", group="fake-independent-b", roots=(root,), credibility=1000, authority=1000, provenance=1000),
        make_evidence("c", group="fake-independent-c", roots=(root,), credibility=1000, authority=1000, provenance=1000),
    )
    report = evaluate_evidence(
        cognition_id=cid(),
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        support=items,
        now_ms=NOW,
    )
    assert report.support_group_count == 1
    assert highest_eligible_level(report) == "C1"


def test_derived_evidence_cannot_drop_parent_lineage_roots():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        ledger = CognitionEvidenceLedger(store)
        parent_root = h("parent-root")
        parent = make_evidence("parent", group="parent", roots=(parent_root,))
        ledger.ingest(parent)
        child = make_evidence(
            "child-drop-root",
            group="new-group",
            roots=(h("invented-root"),),
            credibility=500,
            authority=500,
            provenance=500,
            derived_from=(parent.evidence_id,),
        )
        try:
            ledger.ingest(child)
            assert False, "expected lineage-root rejection"
        except CognitionIntegrityError:
            pass


def test_derived_evidence_cannot_amplify_parent_authority_or_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        ledger = CognitionEvidenceLedger(store)
        root = h("authority-root")
        parent = make_evidence(
            "authority-parent",
            group="parent",
            roots=(root,),
            credibility=500,
            authority=500,
            provenance=500,
        )
        ledger.ingest(parent)
        amplified = make_evidence(
            "authority-child",
            group="child",
            roots=(root,),
            credibility=500,
            authority=900,
            provenance=900,
            derived_from=(parent.evidence_id,),
        )
        try:
            ledger.ingest(amplified)
            assert False, "expected authority non-amplification rejection"
        except CognitionIntegrityError:
            pass


def test_valid_derived_evidence_preserves_lineage_and_remains_correlated():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        ledger = CognitionEvidenceLedger(store)
        root = h("valid-root")
        parent = make_evidence(
            "valid-parent",
            group="parent",
            roots=(root,),
            credibility=700,
            authority=700,
            provenance=700,
        )
        ledger.ingest(parent)
        child = make_evidence(
            "valid-child",
            group="different-declared-group",
            roots=(root,),
            credibility=500,
            authority=500,
            provenance=500,
            derived_from=(parent.evidence_id,),
            source_kind="memory",
        )
        assert ledger.ingest(child)
        report = evaluate_evidence(
            cognition_id=cid(),
            life_id=LIFE,
            domain="software",
            world_scope_hash=WORLD,
            principal_scope_hash=PRINCIPAL,
            support=(parent, child),
            now_ms=NOW,
        )
        assert report.support_group_count == 1


def test_derived_evidence_cannot_drop_parent_cognition_ancestor_chain():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        ledger = CognitionEvidenceLedger(store)
        root = h("ancestor-root")
        ancestor = cid()
        parent = make_evidence(
            "ancestor-parent",
            group="parent",
            roots=(root,),
            ancestors=(ancestor,),
        )
        ledger.ingest(parent)
        child = make_evidence(
            "ancestor-child",
            group="child",
            roots=(root,),
            credibility=500,
            authority=500,
            provenance=500,
            derived_from=(parent.evidence_id,),
            ancestors=(),
        )
        try:
            ledger.ingest(child)
            assert False, "expected ancestor-chain rejection"
        except CognitionIntegrityError:
            pass
