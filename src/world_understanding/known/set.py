"""Finite, life-scoped active Known cut with canonical indexes."""
from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding.known import DirectKnownRecord, DerivedKnownRecord
from contracts.world_understanding.scope import WorldScope
from world_understanding.scope_guard import require_same_world_scope

from .freshness import KnownFreshnessDecision, evaluate_known_freshness

KnownRecord = DirectKnownRecord | DerivedKnownRecord

class ActiveCutOverflow(ValueError):
    pass

class InvalidKnownRecord(ValueError):
    pass

class StaleKnownDependency(ValueError):
    """Raised when a caller attempts to depend on Known without revalidation."""

    def __init__(self, record_hash: str, decision: KnownFreshnessDecision) -> None:
        self.record_hash = str(record_hash)
        self.decision = decision
        super().__init__(
            "KNOWN_REVALIDATION_REQUIRED:"
            + self.record_hash
            + ":"
            + ",".join(decision.reasons)
        )


def known_ref(record: KnownRecord):
    from contracts.world_understanding._base import WorldRecordRef
    return WorldRecordRef(record_type="world_known", record_id=record.known_id, revision=None, sha256=record.record_hash)


def proposition_signature(record: KnownRecord) -> str:
    return canonical_sha256({
        "domain": "tiangong.world.known-proposition-signature.v1",
        "world_scope_hash": record.world_scope.world_scope_hash,
        "proposition_type": record.proposition_type,
        "subject_ref": record.subject_ref,
        "predicate": record.predicate,
        "object_value": None if record.object_value is None else record.object_value.model_dump(mode="json"),
        "object_ref": None if record.object_ref is None else record.object_ref.model_dump(mode="json"),
    })

@dataclass(frozen=True, slots=True)
class KnownSetSnapshot:
    scope: WorldScope
    records: tuple[KnownRecord, ...]
    cut_sha256: str

class KnownSet:
    __slots__ = ("scope", "max_records", "_by_hash", "_by_prop", "_by_prop_subject", "_by_root")
    def __init__(self, scope: WorldScope, records: Iterable[KnownRecord] = (), *, max_records: int = 100_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.scope = scope
        self.max_records = int(max_records)
        self._by_hash: dict[str, KnownRecord] = {}
        self._by_prop: dict[str, list[KnownRecord]] = defaultdict(list)
        self._by_prop_subject: dict[tuple[str, str], list[KnownRecord]] = defaultdict(list)
        self._by_root: dict[str, list[KnownRecord]] = defaultdict(list)
        for record in records:
            self.add(record)

    def __len__(self) -> int: return len(self._by_hash)
    def __iter__(self) -> Iterator[KnownRecord]: return iter(self.records())

    def add(self, record: KnownRecord) -> bool:
        if not isinstance(record, (DirectKnownRecord, DerivedKnownRecord)):
            raise InvalidKnownRecord("only DirectKnownRecord/DerivedKnownRecord may enter KnownSet")
        require_same_world_scope(self.scope, record.world_scope)
        if not record.has_valid_hash():
            raise InvalidKnownRecord("known record hash is not canonical")
        if record.record_hash in self._by_hash:
            return False
        if len(self._by_hash) >= self.max_records:
            raise ActiveCutOverflow("finite active Known cut exceeded")
        self._by_hash[record.record_hash] = record
        self._by_prop[record.proposition_type].append(record)
        self._by_prop_subject[(record.proposition_type, record.subject_ref)].append(record)
        for ref in record.provenance_refs:
            self._by_root[ref.sha256].append(record)
        return True

    def records(self) -> tuple[KnownRecord, ...]:
        return tuple(self._by_hash[key] for key in sorted(self._by_hash))

    def get_hash(self, record_hash: str) -> KnownRecord | None:
        """Introspection lookup; does not imply the fact is safe to depend on."""
        return self._by_hash.get(record_hash)

    def require_dependency_reusable(
        self,
        record: KnownRecord,
        *,
        now_ms: int,
        current_source_versions: Mapping[str, str] | None = None,
        revalidated_source_keys: frozenset[str] = frozenset(),
    ) -> KnownRecord:
        """Canonical M3.8 dependency gate for stale/volatile Known facts."""
        require_same_world_scope(self.scope, record.world_scope)
        decision = evaluate_known_freshness(
            record,
            now_ms=now_ms,
            current_source_versions=current_source_versions,
            revalidated_source_keys=revalidated_source_keys,
        )
        if not decision.reusable:
            raise StaleKnownDependency(record.record_hash, decision)
        return record

    def get_hash_for_dependency(
        self,
        record_hash: str,
        *,
        now_ms: int,
        current_source_versions: Mapping[str, str] | None = None,
        revalidated_source_keys: frozenset[str] = frozenset(),
    ) -> KnownRecord | None:
        """Lookup used by execution/derivation dependency reuse.

        Missing remains ``None``; a present but stale fact fails closed rather
        than being silently reused.
        """
        record = self._by_hash.get(record_hash)
        if record is None:
            return None
        return self.require_dependency_reusable(
            record,
            now_ms=now_ms,
            current_source_versions=current_source_versions,
            revalidated_source_keys=revalidated_source_keys,
        )

    def by_proposition(self, proposition_type: str) -> tuple[KnownRecord, ...]:
        return tuple(self._by_prop.get(proposition_type, ()))

    def by_proposition_subject(self, proposition_type: str, subject_ref: str) -> tuple[KnownRecord, ...]:
        return tuple(self._by_prop_subject.get((proposition_type, subject_ref), ()))

    def by_provenance_root(self, sha256: str) -> tuple[KnownRecord, ...]:
        return tuple(self._by_root.get(sha256, ()))

    def fork(self) -> "KnownSet":
        clone = object.__new__(KnownSet)
        clone.scope = self.scope
        clone.max_records = self.max_records
        clone._by_hash = dict(self._by_hash)
        clone._by_prop = defaultdict(list, {key: list(value) for key, value in self._by_prop.items()})
        clone._by_prop_subject = defaultdict(list, {key: list(value) for key, value in self._by_prop_subject.items()})
        clone._by_root = defaultdict(list, {key: list(value) for key, value in self._by_root.items()})
        return clone

    def snapshot(self) -> KnownSetSnapshot:
        records = self.records()
        return KnownSetSnapshot(
            scope=self.scope,
            records=records,
            cut_sha256=canonical_sha256({
                "domain": "tiangong.world.known-active-cut.v1",
                "life_id": self.scope.life_id,
                "world_scope_hash": self.scope.world_scope_hash,
                "record_hashes": [record.record_hash for record in records],
            }),
        )
