"""Life-scoped semi-naive least-fixed-point engine for deterministic Known closure."""
from __future__ import annotations
from dataclasses import dataclass, field
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.derivation import DerivationRef, DerivationEdge, derive_derivation_id, derive_derivation_edge_id
from contracts.world_understanding.known import DerivedKnownRecord, derive_derived_known_id
from world_understanding.scope_guard import require_same_scope_parents
from .authority_matrix import AuthorityIntersectionError, intersect_authority
from .rule import ClosureDiagnostic, DerivedCandidate, DeterministicRule
from .registry import RuleRegistry
from .set import KnownSet, KnownRecord, ActiveCutOverflow, known_ref, proposition_signature

class ClosureLimitExceeded(RuntimeError): pass

@dataclass(frozen=True, slots=True)
class ClosureResult:
    known: KnownSet
    derivations: tuple[DerivationRef, ...]
    edges: tuple[DerivationEdge, ...]
    diagnostics: tuple[ClosureDiagnostic, ...]
    rounds: int
    terminated: bool
    added_record_hashes: tuple[str, ...]
    ancestry_by_hash: dict[str, frozenset[str]] = field(default_factory=dict, repr=False, compare=False)


def _sorted_parent_refs(parents: tuple[KnownRecord, ...]) -> tuple[WorldRecordRef, ...]:
    refs = tuple(known_ref(parent) for parent in parents)
    return tuple(sorted(refs, key=lambda ref: ref.sort_key()))


def _materialize(rule: DeterministicRule, candidate: DerivedCandidate) -> tuple[DerivedKnownRecord, DerivationRef, tuple[DerivationEdge, ...]]:
    if not candidate.parents:
        raise ValueError("deterministic derivation requires parents")
    scope = candidate.parents[0].world_scope
    require_same_scope_parents(scope, candidate.parents)
    if any(parent.truth_state != "TRUE" for parent in candidate.parents):
        raise ValueError("PARENT_NOT_TRUE")
    envelope = intersect_authority(rule.spec, candidate.parents)
    parent_refs = _sorted_parent_refs(candidate.parents)
    object_value = candidate.object_value
    object_ref = candidate.object_ref
    known_id = derive_derived_known_id(
        world_scope_hash=scope.world_scope_hash,
        proposition_type=candidate.proposition_type,
        subject_ref=candidate.subject_ref,
        predicate=candidate.predicate,
        object_value=object_value,
        object_ref=object_ref,
        transform_id=rule.spec.rule_id,
        parent_known_refs=parent_refs,
    )
    derivation_hash = canonical_sha256({
        "transform_id": rule.spec.rule_id,
        "transform_version": rule.spec.version,
        "parent_known_refs": [ref.model_dump(mode="json") for ref in parent_refs],
    })
    child = DerivedKnownRecord(
        known_id=known_id,
        proposition_type=candidate.proposition_type,
        subject_ref=candidate.subject_ref,
        predicate=candidate.predicate,
        object_value=object_value,
        object_ref=object_ref,
        world_scope=scope,
        time=envelope.time,
        authority_domain=envelope.authority_domain,
        authority_ceiling_milli=envelope.authority_ceiling_milli,
        observability_state=envelope.observability_state,
        coverage_milli=envelope.coverage_milli,
        truth_state="TRUE",
        epistemic_state=envelope.epistemic_state,
        provenance_refs=envelope.provenance_refs,
        empirical_evidence_weight_milli=envelope.empirical_evidence_weight_milli,
        record_hash="0" * 64,
        parent_known_refs=parent_refs,
        transform_id=rule.spec.rule_id,
        transform_version=rule.spec.version,
        derivation_hash=derivation_hash,
    ).with_computed_hash()
    child_ref = known_ref(child)
    roots = tuple(sorted({ref.sha256 for ref in envelope.provenance_refs}))
    if not roots:
        raise ValueError("PROVENANCE_ROOTS_EMPTY")
    derivation_id = derive_derivation_id(
        world_scope_hash=scope.world_scope_hash,
        source_refs=parent_refs,
        target_refs=(child_ref,),
        transform_type=rule.spec.rule_id,
        transform_version=rule.spec.version,
    )
    derivation = DerivationRef(
        derivation_id=derivation_id,
        scope=scope,
        source_refs=parent_refs,
        target_refs=(child_ref,),
        transform_type=rule.spec.rule_id,
        transform_version=rule.spec.version,
        model_assisted=False,
        lineage_root_hashes=roots,
        authority_ceiling_milli=envelope.authority_ceiling_milli,
        created_at_ms=envelope.time.recorded_at_ms,
        derivation_sha256="0" * 64,
    ).with_computed_hash()
    derivation_ref = WorldRecordRef(record_type="world_derivation", record_id=derivation.derivation_id, revision=None, sha256=derivation.derivation_sha256)
    edges: list[DerivationEdge] = []
    for parent_ref in parent_refs:
        edge_id = derive_derivation_edge_id(world_scope_hash=scope.world_scope_hash, derivation_ref=derivation_ref, source_ref=parent_ref, target_ref=child_ref, edge_kind="SOURCE_TO_DERIVATION")
        edges.append(DerivationEdge(edge_id=edge_id, scope=scope, derivation_ref=derivation_ref, source_ref=parent_ref, target_ref=child_ref, edge_kind="SOURCE_TO_DERIVATION", created_at_ms=envelope.time.recorded_at_ms, edge_sha256="0"*64).with_computed_hash())
    edge_id = derive_derivation_edge_id(world_scope_hash=scope.world_scope_hash, derivation_ref=derivation_ref, source_ref=derivation_ref, target_ref=child_ref, edge_kind="DERIVATION_TO_TARGET")
    edges.append(DerivationEdge(edge_id=edge_id, scope=scope, derivation_ref=derivation_ref, source_ref=derivation_ref, target_ref=child_ref, edge_kind="DERIVATION_TO_TARGET", created_at_ms=envelope.time.recorded_at_ms, edge_sha256="0"*64).with_computed_hash())
    return child, derivation, tuple(edges)

class KnownClosureEngine:
    __slots__ = ("registry", "max_rounds", "max_records")
    def __init__(self, registry: RuleRegistry, *, max_rounds: int = 64, max_records: int = 100_000) -> None:
        if max_rounds < 1: raise ValueError("max_rounds must be positive")
        self.registry = registry
        self.max_rounds = int(max_rounds)
        self.max_records = int(max_records)

    def close(self, direct_known: tuple[KnownRecord, ...], *, prior: ClosureResult | None = None) -> ClosureResult:
        if not direct_known and prior is None:
            raise ValueError("closure requires at least one Known record or a prior closure")
        scope = prior.known.scope if prior is not None else direct_known[0].world_scope
        known = prior.known.fork() if prior is not None else KnownSet(scope, (), max_records=self.max_records)
        known.max_records = self.max_records
        derivations = list(prior.derivations if prior is not None else ())
        edges = list(prior.edges if prior is not None else ())
        diagnostics = list(prior.diagnostics if prior is not None else ())
        ancestry: dict[str, frozenset[str]] = dict(prior.ancestry_by_hash) if prior is not None else {}
        delta: list[KnownRecord] = []
        added: list[str] = []
        for record in direct_known:
            if known.add(record):
                delta.append(record); added.append(record.record_hash)
                ancestry[record.record_hash] = frozenset()
        if not delta:
            return ClosureResult(known, tuple(derivations), tuple(edges), tuple(diagnostics), 0, True, (), ancestry)
        rounds = 0
        while delta:
            if rounds >= self.max_rounds:
                raise ClosureLimitExceeded("deterministic closure did not reach a fixed point within max_rounds")
            rounds += 1
            next_delta: list[KnownRecord] = []
            delta_tuple = tuple(delta)
            for rule in self.registry.rules():
                try:
                    candidates = rule.apply(known, delta_tuple)
                except Exception as exc:
                    diagnostics.append(ClosureDiagnostic(rule.spec.rule_id, "RULE_ERROR", type(exc).__name__))
                    continue
                for candidate in candidates:
                    try:
                        child, derivation, new_edges = _materialize(rule, candidate)
                        child_sig = proposition_signature(child)
                        ancestor_sigs: set[str] = set()
                        for parent in candidate.parents:
                            ancestor_sigs.add(proposition_signature(parent))
                            ancestor_sigs.update(ancestry.get(parent.record_hash, ()))
                        if child_sig in ancestor_sigs:
                            diagnostics.append(ClosureDiagnostic(rule.spec.rule_id, "SAME_REVISION_CYCLE", child.known_id))
                            continue
                        if known.add(child):
                            ancestry[child.record_hash] = frozenset(ancestor_sigs)
                            next_delta.append(child); added.append(child.record_hash)
                            derivations.append(derivation); edges.extend(new_edges)
                    except ActiveCutOverflow:
                        raise
                    except AuthorityIntersectionError as exc:
                        diagnostics.append(ClosureDiagnostic(rule.spec.rule_id, str(exc), ""))
                    except Exception as exc:
                        diagnostics.append(ClosureDiagnostic(rule.spec.rule_id, str(exc) or type(exc).__name__, ""))
            delta = next_delta
        derivations_sorted = tuple(sorted(derivations, key=lambda item: item.derivation_sha256))
        edges_sorted = tuple(sorted(edges, key=lambda item: item.edge_sha256))
        diagnostics_sorted = tuple(sorted(diagnostics, key=lambda item: (item.rule_id, item.reason_code, item.detail)))
        return ClosureResult(known, derivations_sorted, edges_sorted, diagnostics_sorted, rounds, True, tuple(sorted(added)), ancestry)
