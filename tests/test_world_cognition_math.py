from __future__ import annotations

import hashlib
import itertools
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))

from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.cognition_statement import CognitionValue
from v3.world_cognition.consolidator import CognitionProposal
from v3.world_cognition.stability import evaluate_evidence, highest_eligible_level

LIFE = "life.main"
WORLD = "a" * 64
PRINCIPAL = "b" * 64
NOW = 50_000_000
ZERO = "0" * 64


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cognition_id() -> str:
    return CognitionProposal(
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
        claim_kind="component_role",
        subject_ref="tiangong.component",
        predicate="role",
        value=CognitionValue(kind="string", string_value="x"),
    ).cognition_id


def make_evidence(
    tag: str,
    *,
    group: str | None = None,
    credibility: int = 950,
    authority: int = 1000,
    provenance: int = 1000,
    evidence_class: str = "observed",
    source_kind: str = "code_perception",
    extractor_kind: str = "deterministic",
    mode: str = "positive",
    coverage: int = 1000,
    observed_at: int = NOW,
    volatility: str = "structural",
) -> CognitionEvidence:
    source = CognitionSourceRef(
        source_kind=source_kind,
        object_id=f"math.{tag}",
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
        observation_mode=mode,
        observation="math observation " + tag,
        coverage_milli=coverage,
        search_scope_hash=h("scope:" + tag) if mode in {"negative", "aggregate"} else None,
        independence_group_hash=h("group:" + (group or tag)),
        lineage_root_hashes=(h("root:" + tag),),
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id="content." + tag,
        content_sha256=h("content:" + tag),
        extractor_kind=extractor_kind,
        observed_at_ms=observed_at,
        valid_from_ms=0,
        valid_until_ms=None,
        volatility_class=volatility,
    )
    item = CognitionEvidence(
        evidence_id=derive_cognition_evidence_id(**kwargs),
        **kwargs,
        evidence_sha256=ZERO,
    )
    return item.with_computed_evidence_sha256()


def report(support=(), counter=(), now=NOW):
    return evaluate_evidence(
        cognition_id=cognition_id(),
        life_id=LIFE,
        domain="software",
        world_scope_hash=WORLD,
        principal_scope_hash=PRINCIPAL,
        support=support,
        counter=counter,
        now_ms=now,
    )


def test_score_is_bounded_and_net_identity_holds_over_randomized_sets():
    rng = random.Random(20260809)
    classes = ["observed", "execution_verified", "user_asserted", "model_inference", "reflection"]
    sources = ["code_perception", "fact_execution", "user_instruction", "memory", "model_synthesis"]
    for case in range(600):
        support = []
        counter = []
        for i in range(rng.randint(0, 8)):
            cls = rng.choice(classes)
            src = rng.choice(sources)
            if src == "model_synthesis":
                cls = rng.choice(["model_inference", "reflection"])
                extractor = "llm_synthesis"
            else:
                extractor = "deterministic"
            authority = rng.randint(0, 1000)
            credibility = rng.randint(0, authority)
            item = make_evidence(
                f"r{case}-{i}",
                group=f"g{rng.randint(0, 3)}",
                credibility=credibility,
                authority=authority,
                provenance=rng.randint(0, 1000),
                evidence_class=cls,
                source_kind=src,
                extractor_kind=extractor,
                observed_at=NOW - rng.randint(0, 30_000_000),
                volatility=rng.choice(["short", "medium", "long", "structural"]),
            )
            (support if rng.random() < 0.7 else counter).append(item)
        r = report(support, counter)
        assert 0 <= r.support_milli <= 1000
        assert 0 <= r.counter_milli <= 1000
        assert 0 <= r.net_milli <= 1000
        assert r.net_milli == max(0, r.support_milli - r.counter_milli)
        assert 0 <= r.correlation_discount_milli <= 1000
        assert 0 <= r.staleness_penalty_milli <= 1000


def test_evidence_order_is_mathematically_irrelevant():
    items = [make_evidence(f"perm{i}", group=f"g{i % 3}", credibility=700 + i * 30) for i in range(5)]
    baseline = report(items)
    for permutation in itertools.permutations(items[:4]):
        r = report((*permutation, items[4]))
        assert r.support_milli == baseline.support_milli
        assert r.support_groups == baseline.support_groups
        assert r.direct_support_groups == baseline.direct_support_groups


def test_lower_provenance_or_authority_never_increases_support():
    provenance_scores = [report((make_evidence(f"prov{p}", provenance=p),)).support_milli for p in range(100, 1001, 100)]
    authority_scores = [report((make_evidence(f"auth{a}", credibility=a, authority=a),)).support_milli for a in range(100, 1001, 100)]
    assert provenance_scores == sorted(provenance_scores)
    assert authority_scores == sorted(authority_scores)


def test_negative_search_coverage_is_monotone():
    scores = [
        report((make_evidence(f"cov{c}", mode="negative", coverage=c),)).support_milli
        for c in range(100, 1001, 100)
    ]
    assert scores == sorted(scores)


def test_non_structural_freshness_is_monotone_nonincreasing():
    item = make_evidence("aging", observed_at=NOW, volatility="medium")
    scores = [report((item,), now=NOW + age).support_milli for age in (0, 1_000, 100_000, 1_000_000, 10_000_000, 100_000_000)]
    assert scores == sorted(scores, reverse=True)


def test_adding_independent_positive_evidence_never_reduces_support():
    items = [make_evidence(f"ind{i}", group=f"ind{i}", credibility=500 + i * 50) for i in range(6)]
    scores = [report(items[:count]).support_milli for count in range(1, len(items) + 1)]
    assert scores == sorted(scores)


def test_same_source_replication_never_creates_independent_quorum():
    items = [make_evidence(f"clone{i}", group="one-root", credibility=900) for i in range(30)]
    r = report(items)
    assert r.support_group_count == 1
    assert highest_eligible_level(r) == "C1"


def test_duplicate_same_evidence_id_is_idempotent():
    item = make_evidence("same-id")
    assert report((item,)).support_milli == report((item, item, item, item)).support_milli


def test_model_only_evidence_cannot_auto_promote_at_any_multiplicity():
    items = [
        make_evidence(
            f"model{i}", group=f"m{i}", evidence_class="model_inference",
            source_kind="model_synthesis", extractor_kind="llm_synthesis",
        )
        for i in range(50)
    ]
    r = report(items)
    assert r.support_milli == 0
    assert highest_eligible_level(r) == "C0"


def test_stable_requires_two_independent_groups_and_one_direct_group():
    one_direct = report((make_evidence("d1", credibility=1000),))
    assert highest_eligible_level(one_direct) == "C1"
    asserted = report((
        make_evidence("u1", group="u1", evidence_class="user_asserted", source_kind="user_instruction", credibility=1000),
        make_evidence("u2", group="u2", evidence_class="user_asserted", source_kind="user_instruction", credibility=1000),
        make_evidence("u3", group="u3", evidence_class="user_asserted", source_kind="user_instruction", credibility=1000),
    ))
    assert asserted.direct_support_group_count == 0
    assert highest_eligible_level(asserted) == "C1"


def test_core_requires_three_independent_groups():
    two = report((make_evidence("core1", credibility=1000), make_evidence("core2", credibility=1000)))
    assert highest_eligible_level(two) == "C2"
    three = report((make_evidence("core3", credibility=1000), make_evidence("core4", credibility=1000), make_evidence("core5", credibility=1000)))
    assert highest_eligible_level(three) == "C3"


def test_support_counter_same_independence_root_cancels_both_sides():
    positive = make_evidence("same-root-a", group="same-root")
    negative = make_evidence("same-root-b", group="same-root")
    r = report((positive,), (negative,))
    assert r.support_milli == 0
    assert r.counter_milli == 0
    assert len(r.conflicted_groups) == 1


def test_empirical_eligibility_has_no_automatic_c4_state():
    items = [make_evidence(f"max{i}", credibility=1000) for i in range(10)]
    assert highest_eligible_level(report(items)) == "C3"
