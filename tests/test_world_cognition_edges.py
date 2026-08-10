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
from v3.world_cognition.consolidator import CognitionConsolidator, CognitionProposal
from v3.world_cognition.facade import WorldCognitionFacade
from v3.world_cognition.priors import install_default_software_priors
from v3.world_cognition.store import WorldCognitionStore

LIFE = "life.main"
WORLD = "a" * 64
PRINCIPAL = "b" * 64
NOW = 70_000_000
ZERO = "0" * 64


def h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def evidence(tag: str) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind="code_perception",
        object_id=f"edge.{tag}",
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
        evidence_class="observed",
        source_credibility_milli=1000,
        authority_ceiling_milli=1000,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation="edge observation " + tag,
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=h("group:" + tag),
        lineage_root_hashes=(h("root:" + tag),),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id="edge.content." + tag,
        content_sha256=h("content:" + tag),
        extractor_kind="deterministic",
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


def proposal() -> CognitionProposal:
    return CognitionProposal(
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
        claim_kind="component_role",
        subject_ref="tiangong.zongdiaodu",
        predicate="role",
        value=CognitionValue(kind="entity_ref", entity_ref="authoritative_orchestrator"),
    )


def test_protected_c4_can_refresh_same_value_without_losing_protection():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        items = [evidence(f"c4-{i}") for i in range(4)]
        for item in items:
            store.put_evidence(item)
        core = CognitionConsolidator(store)
        created = core.consolidate(
            proposal(),
            support_evidence_ids=tuple(item.evidence_id for item in items[:3]),
            now_ms=NOW,
        )
        assert created.head.stability_level == "C3"
        protected = core.protect(
            created.head.cognition_id,
            now_ms=NOW + 1,
            decision_authority="explicit_system_authority",
        )
        assert protected.stability_level == "C4"
        refreshed = core.consolidate(
            proposal(),
            support_evidence_ids=(items[3].evidence_id,),
            now_ms=NOW + 2,
        )
        assert refreshed.transitions == ("REFRESH",)
        assert refreshed.head.stability_level == "C4"
        assert refreshed.head.status == "CORE"
        assert items[3].evidence_id in refreshed.head.supporting_evidence_ids


def test_default_priors_have_zero_empirical_evidence_weight():
    with tempfile.TemporaryDirectory() as tmp:
        store = WorldCognitionStore(Path(tmp) / "wc")
        priors = install_default_software_priors(store, life_id=LIFE, created_at_ms=NOW)
        assert len(priors) == 7
        assert all(prior.empirical_evidence_weight_milli == 0 for prior in priors)
        assert all(prior.projection_authority == "interpretation_only" for prior in priors)
        persisted = store.list_priors(life_id=LIFE, domain="software")
        assert tuple(item.prior_id for item in persisted) == tuple(sorted(item.prior_id for item in priors))


def test_enabled_facade_read_only_path_remains_lazy_and_creates_no_storage():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "尚未创建的世界认知"
        facade = WorldCognitionFacade(enabled=True, root=root)
        context = facade.project_context(
            life_id=LIFE,
            domain="software",
            world_scope_hash=WORLD,
            principal_scope_hash=PRINCIPAL,
            query="zongdiaodu",
            now_ms=NOW,
        )
        assert context == ""
        assert facade.counts() == {"priors": 0, "evidence": 0, "statements": 0, "revisions": 0, "heads": 0}
        assert not root.exists()


def test_sqlite_store_supports_unicode_windows_style_project_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "天工造物" / "世界认知系统" / "代码认知"
        store = WorldCognitionStore(root)
        item = evidence("unicode-path")
        assert store.put_evidence(item)
        loaded = store.get_evidence(item.evidence_id)
        assert loaded == item
        assert root.is_dir()
        assert (root / "cognition.sqlite3").is_file()


def test_projection_failure_degrades_to_empty_context_instead_of_runtime_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wc-corrupt"
        root.mkdir(parents=True)
        (root / "cognition.sqlite3").write_bytes(b"not-a-sqlite-database")
        facade = WorldCognitionFacade(enabled=True, root=root)
        assert facade.project_context(
            life_id=LIFE,
            domain="software",
            world_scope_hash=WORLD,
            principal_scope_hash=PRINCIPAL,
            now_ms=NOW,
        ) == ""
