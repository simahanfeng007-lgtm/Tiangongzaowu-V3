"""Bounded same-root proofs preserve full source evidence and incremental identity."""
from __future__ import annotations

import pytest

from world_understanding.known import KnownClosureEngine, RuleRegistry, build_p4_rules
from world_understanding.known.rules import SameSourceRootGroupingRule
from world_understanding.known.set import InvalidKnownRecord
from world_understanding.known.rule import DerivedCandidate, RuleSpec
from world_understanding.scope_guard import ScopeMismatchError
from contracts.world_understanding._base import WorldValue
from tests.test_world_understanding_p4_known_closure import fact


def _identities(result):
    return (
        {r.record_hash for r in result.known.records()},
        {d.derivation_sha256 for d in result.derivations},
        {e.edge_sha256 for e in result.edges},
    )


def test_one_source_538_direct_facts_keep_every_fact_and_linear_proof():
    # The actual dirty-source genesis had 538 direct observations in one root.
    # v0.1 would derive 144453 pairs, overflowing the unchanged 100000 limit.
    rows = tuple(fact(f"row.{i}", obj="same-observed-source") for i in range(538))
    result = KnownClosureEngine(RuleRegistry(build_p4_rules())).close(rows)
    assert result.terminated and len(result.known) == 1076
    assert len(result.derivations) == 538 and len(result.edges) == 1076
    assert {r.record_hash for r in rows} <= _identities(result)[0]
    root = rows[0].provenance_refs[0].sha256
    assert {r.record_hash for r in result.known.by_provenance_root(root)
            if r.derivation_type == "DIRECT"} == {r.record_hash for r in rows}
    assert len(result.known.by_proposition("SOURCE_ROOT_MEMBER")) == 538


@pytest.mark.parametrize("order", ["ascending", "descending", "interleaved"])
def test_incremental_full_equivalence_including_later_smaller_identity(order):
    rows = sorted((fact(f"item.{i}", obj="one-source") for i in range(23)),
                  key=lambda r: (r.known_id, r.record_hash))
    full = KnownClosureEngine(RuleRegistry((SameSourceRootGroupingRule(),))).close(tuple(rows))
    if order == "descending":
        rows.reverse()  # Every next member sorts before all prior members.
    elif order == "interleaved":
        rows = rows[1::2] + rows[::2]
    engine = KnownClosureEngine(RuleRegistry((SameSourceRootGroupingRule(),)))
    incremental = None
    for offset in range(0, len(rows), 3):
        incremental = engine.close(tuple(rows[offset:offset + 3]), prior=incremental)
    assert _identities(incremental) == _identities(full)
    repeated = engine.close(tuple(reversed(rows)), prior=incremental)
    assert _identities(repeated) == _identities(full)
    assert repeated.added_record_hashes == ()


def test_root_memberships_do_not_join_authority_domains_or_raise_authority():
    rows = (fact("a", obj="root-one", domain="EXECUTION_ACTION", ceiling=400, weight=300),
            fact("b", obj="root-one", domain="FILESYSTEM_ARTIFACT", ceiling=700, weight=600),
            fact("c", obj="root-two", domain="EXECUTION_ACTION", ceiling=200, weight=100))
    result = KnownClosureEngine(RuleRegistry((SameSourceRootGroupingRule(),))).close(rows)
    direct = {r.known_id: r for r in rows}
    members = result.known.by_proposition("SOURCE_ROOT_MEMBER")
    assert len(members) == 3
    for member in members:
        parent = direct[member.subject_ref]
        assert len(member.parent_known_refs) == 1
        assert member.parent_known_refs[0].sha256 == parent.record_hash
        assert member.world_scope == parent.world_scope
        assert member.authority_domain == parent.authority_domain
        assert member.authority_ceiling_milli <= parent.authority_ceiling_milli
        assert member.empirical_evidence_weight_milli <= parent.empirical_evidence_weight_milli
        assert member.provenance_refs == parent.provenance_refs
        assert member.object_value.string_value in {r.sha256 for r in parent.provenance_refs}
    assert not result.known.by_proposition("SHARES_SOURCE_ROOT")


def test_tampered_record_and_cross_scope_still_fail_before_membership():
    row = fact("valid", obj="source")
    bad = row.model_copy(update={"subject_ref": "tampered"})
    engine = KnownClosureEngine(RuleRegistry((SameSourceRootGroupingRule(),)))
    with pytest.raises(InvalidKnownRecord):
        engine.close((bad,))
    with pytest.raises(ScopeMismatchError):
        engine.close((row, fact("other", life="life.other", obj="source")))


def test_existing_derived_members_or_legacy_pair_never_become_member_parents():
    a, b = fact("a", obj="one-source"), fact("b", obj="one-source")
    class LegacyPair:
        spec = RuleSpec("wu.rule.provenance.same-root", "v0.1", None, ())
        def apply(self, known, delta):
            if not any(r.derivation_type == "DIRECT" for r in delta):
                return ()
            return (DerivedCandidate((a, b), "SHARES_SOURCE_ROOT", a.known_id,
                    "provenance.same_root", WorldValue(kind="string",
                    string_value=f"{b.known_id}:{a.provenance_refs[0].sha256}")),)
    old = KnownClosureEngine(RuleRegistry((LegacyPair(),))).close((a, b))
    legacy = old.known.by_proposition("SHARES_SOURCE_ROOT")
    assert len(legacy) == 1
    rule = SameSourceRootGroupingRule()
    assert rule.apply(old.known, legacy) == ()
    current = KnownClosureEngine(RuleRegistry((rule,))).close(tuple(old.known.records()))
    members = current.known.by_proposition("SOURCE_ROOT_MEMBER")
    assert len(members) == 2
    assert rule.apply(current.known, members) == ()
    assert all(m.parent_known_refs[0].sha256 in {a.record_hash, b.record_hash}
               for m in members)
