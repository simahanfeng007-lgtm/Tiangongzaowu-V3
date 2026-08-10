"""Bounded L6 state store with current heads separate from immutable history.

Persistence is optional and reference-only. ``root=None`` performs no filesystem I/O.
When a root is supplied, directories are created only on the first successful publish.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict, deque
import json
import os
from pathlib import Path
from typing import Any
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.entity import WorldEntity
from contracts.world_understanding.relation import WorldRelation
from contracts.world_understanding.state import WorldState
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.common.world_cut import compare_world_cuts
from .manifests import HeadManifest, DependencyBinding, DependencyManifest, DeltaManifest

@dataclass(frozen=True, slots=True)
class MaterializedWorldSnapshot:
    state: WorldState
    cut: WorldCut
    entity_heads: HeadManifest
    relation_heads: HeadManifest
    cognition_heads: HeadManifest | None
    active_hypotheses: HeadManifest | None
    uncertainty: HeadManifest | None
    dependencies: DependencyManifest
    delta: DeltaManifest
    frame_id: str
    entities: tuple[WorldEntity, ...] = ()
    relations: tuple[WorldRelation, ...] = ()
    @property
    def state_ref(self) -> WorldRecordRef:
        return WorldRecordRef(record_type="world_state",record_id=self.state.world_state_id,revision=self.state.world_sequence+1,sha256=self.state.state_sha256)


def _ref_dict(ref: WorldRecordRef | None) -> dict[str,Any] | None:
    return None if ref is None else ref.model_dump(mode="json")
def _head_dict(value: HeadManifest | None) -> dict[str,Any] | None:
    return None if value is None else {"kind":value.kind,"refs":[r.model_dump(mode="json") for r in value.refs],"manifest_sha256":value.manifest_sha256}
def _head_load(value: dict[str,Any] | None) -> HeadManifest | None:
    if value is None: return None
    refs=tuple(WorldRecordRef.model_validate(r) for r in value["refs"])
    built=HeadManifest.build(str(value["kind"]),refs,max_items=max(1,len(refs)))
    if built.manifest_sha256 != value["manifest_sha256"]: raise ValueError("WORLD_STATE_PERSISTED_MANIFEST_HASH_MISMATCH")
    return built
def _snapshot_dict(snapshot: MaterializedWorldSnapshot) -> dict[str,Any]:
    return {
        "schema":"tiangong.world-state-store.snapshot.v1",
        "state":snapshot.state.model_dump(mode="json"),"cut":snapshot.cut.model_dump(mode="json"),"frame_id":snapshot.frame_id,
        "entity_heads":_head_dict(snapshot.entity_heads),"relation_heads":_head_dict(snapshot.relation_heads),
        "cognition_heads":_head_dict(snapshot.cognition_heads),"active_hypotheses":_head_dict(snapshot.active_hypotheses),"uncertainty":_head_dict(snapshot.uncertainty),
        "dependencies":{"bindings":[{"ref":b.ref.model_dump(mode="json"),"source_keys":b.source_keys,"evidence_ids":b.evidence_ids} for b in snapshot.dependencies.bindings],"manifest_sha256":snapshot.dependencies.manifest_sha256},
        "delta":{
            "previous_state_ref":_ref_dict(snapshot.delta.previous_state_ref),"changed_source_keys":snapshot.delta.changed_source_keys,
            "added_refs":[r.model_dump(mode="json") for r in snapshot.delta.added_refs],"removed_refs":[r.model_dump(mode="json") for r in snapshot.delta.removed_refs],
            "changed_refs":[r.model_dump(mode="json") for r in snapshot.delta.changed_refs],"invalidated_refs":[r.model_dump(mode="json") for r in snapshot.delta.invalidated_refs],
            "revalidated_cognition_refs":[r.model_dump(mode="json") for r in snapshot.delta.revalidated_cognition_refs],
            "uncertainty_manifest_ref":_ref_dict(snapshot.delta.uncertainty_manifest_ref),"dependency_manifest_ref":snapshot.delta.dependency_manifest_ref.model_dump(mode="json"),
            "manifest_sha256":snapshot.delta.manifest_sha256,
        },
        "entities":[item.model_dump(mode="json") for item in snapshot.entities],
        "relations":[item.model_dump(mode="json") for item in snapshot.relations],
    }
def _refs(values: list[dict[str,Any]]) -> tuple[WorldRecordRef,...]: return tuple(WorldRecordRef.model_validate(v) for v in values)
def _snapshot_load(payload: dict[str,Any]) -> MaterializedWorldSnapshot:
    if payload.get("schema")!="tiangong.world-state-store.snapshot.v1": raise ValueError("WORLD_STATE_PERSISTED_SCHEMA_INVALID")
    state=WorldState.model_validate_json(json.dumps(payload["state"], ensure_ascii=False)); cut=WorldCut.model_validate_json(json.dumps(payload["cut"], ensure_ascii=False))
    entity=_head_load(payload["entity_heads"]); relation=_head_load(payload["relation_heads"])
    if entity is None or relation is None: raise ValueError("WORLD_STATE_PERSISTED_REQUIRED_MANIFEST_MISSING")
    cognition=_head_load(payload.get("cognition_heads")); hypotheses=_head_load(payload.get("active_hypotheses")); uncertainty=_head_load(payload.get("uncertainty"))
    dep_raw=payload["dependencies"]
    bindings=tuple(DependencyBinding(WorldRecordRef.model_validate(b["ref"]),tuple(b["source_keys"]),tuple(b.get("evidence_ids",()))) for b in dep_raw["bindings"])
    dependencies=DependencyManifest.build(bindings,max_items=max(1,len(bindings)))
    if dependencies.manifest_sha256!=dep_raw["manifest_sha256"]: raise ValueError("WORLD_STATE_PERSISTED_DEPENDENCY_HASH_MISMATCH")
    d=payload["delta"]
    delta=DeltaManifest.build(
        previous_state_ref=None if d["previous_state_ref"] is None else WorldRecordRef.model_validate(d["previous_state_ref"]),
        changed_source_keys=tuple(d["changed_source_keys"]),added_refs=_refs(d["added_refs"]),removed_refs=_refs(d["removed_refs"]),changed_refs=_refs(d["changed_refs"]),
        invalidated_refs=_refs(d["invalidated_refs"]),revalidated_cognition_refs=_refs(d["revalidated_cognition_refs"]),
        uncertainty_manifest_ref=None if d["uncertainty_manifest_ref"] is None else WorldRecordRef.model_validate(d["uncertainty_manifest_ref"]),
        dependency_manifest_ref=WorldRecordRef.model_validate(d["dependency_manifest_ref"]),
    )
    if delta.manifest_sha256!=d["manifest_sha256"]: raise ValueError("WORLD_STATE_PERSISTED_DELTA_HASH_MISMATCH")
    entities=tuple(WorldEntity.model_validate_json(json.dumps(item,ensure_ascii=False)) for item in payload.get("entities",()))
    relations=tuple(WorldRelation.model_validate_json(json.dumps(item,ensure_ascii=False)) for item in payload.get("relations",()))
    return MaterializedWorldSnapshot(state,cut,entity,relation,cognition,hypotheses,uncertainty,dependencies,delta,str(payload["frame_id"]),entities,relations)

class WorldStateStore:
    def __init__(self, *, root: str | os.PathLike[str] | None=None, max_history_per_frame: int=64) -> None:
        if not 2 <= max_history_per_frame <= 4096: raise ValueError("WORLD_STATE_HISTORY_LIMIT_INVALID")
        self.root=None if root is None else Path(root).expanduser().resolve(strict=False)
        self.max_history_per_frame=max_history_per_frame
        self._snapshots: dict[str,MaterializedWorldSnapshot]={}
        self._current: dict[tuple[str,str,str,str],str]={}
        self._history: dict[tuple[str,str,str,str],deque[str]]=defaultdict(deque)
        self._load_index_if_present()
    @staticmethod
    def _stream_key(snapshot: MaterializedWorldSnapshot) -> tuple[str,str,str,str]:
        s=snapshot.state.scope; return (s.life_id,s.world_scope_hash,s.principal_scope_hash,snapshot.frame_id)
    def _index_path(self) -> Path | None: return None if self.root is None else self.root/"index.json"
    def _snapshot_path(self,state_id:str) -> Path | None: return None if self.root is None else self.root/"snapshots"/f"{state_id}.json"
    @staticmethod
    def _atomic_json(path: Path, payload: dict[str,Any]) -> None:
        path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")),encoding="utf-8",newline="\n"); os.replace(tmp,path)
    def _load_index_if_present(self) -> None:
        path=self._index_path()
        if path is None or not path.is_file(): return
        payload=json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema")!="tiangong.world-state-store.index.v1": raise ValueError("WORLD_STATE_INDEX_SCHEMA_INVALID")
        for row in payload.get("streams",[]):
            key=tuple(row["key"])
            if len(key)!=4: raise ValueError("WORLD_STATE_INDEX_KEY_INVALID")
            self._current[key]=str(row["current"])
            self._history[key]=deque(str(x) for x in row.get("history",()))
    def _index_payload(self, current: dict[tuple[str,str,str,str],str], history: dict[tuple[str,str,str,str],deque[str]]) -> dict[str,Any]:
        rows=[]
        for key in sorted(current):
            rows.append({"key":key,"current":current[key],"history":tuple(history.get(key,()))})
        return {"schema":"tiangong.world-state-store.index.v1","streams":rows}
    def _persist_index(self) -> None:
        path=self._index_path()
        if path is not None:
            self._atomic_json(path,self._index_payload(self._current,self._history))
    def current(self, *, life_id: str, world_scope_hash: str, principal_scope_hash: str, frame_id: str) -> MaterializedWorldSnapshot | None:
        state_id=self._current.get((life_id,world_scope_hash,principal_scope_hash,frame_id)); return None if state_id is None else self.get(state_id)
    def current_candidates(self, *, life_id: str, principal_scope_hash: str, world_scope_hash: str | None=None) -> tuple[MaterializedWorldSnapshot,...]:
        """Return current snapshots matching an exact Life/principal partition.

        This is a read-only enumeration for P10 context projection. It never
        resolves ambiguity between frame/branch streams on the caller's behalf.
        """
        state_ids=[]
        for key,state_id in sorted(self._current.items()):
            key_life,key_world,key_principal,_frame_id=key
            if key_life!=life_id or key_principal!=principal_scope_hash: continue
            if world_scope_hash is not None and key_world!=world_scope_hash: continue
            if state_id not in state_ids: state_ids.append(state_id)
        return tuple(snapshot for state_id in state_ids if (snapshot:=self.get(state_id)) is not None)
    def get(self, state_id: str) -> MaterializedWorldSnapshot | None:
        cached=self._snapshots.get(state_id)
        if cached is not None: return cached
        path=self._snapshot_path(state_id)
        if path is None or not path.is_file(): return None
        snap=_snapshot_load(json.loads(path.read_text(encoding="utf-8"))); self._validate_snapshot(snap)
        if snap.state.world_state_id!=state_id: raise ValueError("WORLD_STATE_PERSISTED_ID_MISMATCH")
        self._snapshots[state_id]=snap; return snap
    def history(self, *, life_id: str, world_scope_hash: str, principal_scope_hash: str, frame_id: str) -> tuple[MaterializedWorldSnapshot,...]:
        key=(life_id,world_scope_hash,principal_scope_hash,frame_id)
        return tuple(s for sid in self._history.get(key,()) if (s:=self.get(sid)) is not None)
    @staticmethod
    def _validate_snapshot(snapshot: MaterializedWorldSnapshot) -> None:
        state=snapshot.state; cut_ref=WorldRecordRef(record_type="world_cut",record_id=snapshot.cut.cut_id,revision=None,sha256=snapshot.cut.cut_sha256)
        if state.world_cut_ref != cut_ref: raise ValueError("WORLD_STATE_CUT_REF_MISMATCH")
        if state.entity_head_manifest_ref != snapshot.entity_heads.ref: raise ValueError("WORLD_STATE_ENTITY_MANIFEST_MISMATCH")
        if state.relation_head_manifest_ref != snapshot.relation_heads.ref: raise ValueError("WORLD_STATE_RELATION_MANIFEST_MISMATCH")
        if state.cognition_head_manifest_ref != (None if snapshot.cognition_heads is None else snapshot.cognition_heads.ref): raise ValueError("WORLD_STATE_COGNITION_MANIFEST_MISMATCH")
        if state.active_hypothesis_manifest_ref != (None if snapshot.active_hypotheses is None else snapshot.active_hypotheses.ref): raise ValueError("WORLD_STATE_HYPOTHESIS_MANIFEST_MISMATCH")
        if state.delta_manifest_ref != snapshot.delta.ref: raise ValueError("WORLD_STATE_DELTA_MANIFEST_MISMATCH")
        if snapshot.delta.dependency_manifest_ref != snapshot.dependencies.ref: raise ValueError("WORLD_STATE_DEPENDENCY_MANIFEST_MISMATCH")
        if snapshot.delta.uncertainty_manifest_ref != (None if snapshot.uncertainty is None else snapshot.uncertainty.ref): raise ValueError("WORLD_STATE_UNCERTAINTY_MANIFEST_MISMATCH")
        if state.stale_refs != snapshot.delta.invalidated_refs: raise ValueError("WORLD_STATE_STALE_DELTA_MISMATCH")
        entity_refs=tuple(sorted((WorldRecordRef(record_type="world_entity",record_id=item.entity_id,revision=item.revision,sha256=item.entity_sha256) for item in snapshot.entities if item.lifecycle=="ACTIVE"),key=lambda ref:ref.sort_key()))
        relation_refs=tuple(sorted((WorldRecordRef(record_type="world_relation",record_id=item.relation_id,revision=item.revision,sha256=item.relation_sha256) for item in snapshot.relations),key=lambda ref:ref.sort_key()))
        if entity_refs != snapshot.entity_heads.refs: raise ValueError("WORLD_STATE_ENTITY_BODY_MISMATCH")
        if relation_refs != snapshot.relation_heads.refs: raise ValueError("WORLD_STATE_RELATION_BODY_MISMATCH")
    def publish(self, snapshot: MaterializedWorldSnapshot) -> MaterializedWorldSnapshot:
        if not snapshot.state.has_valid_hash(): raise ValueError("WORLD_STATE_HASH_INVALID")
        self._validate_snapshot(snapshot); key=self._stream_key(snapshot)
        old=self.current(life_id=key[0],world_scope_hash=key[1],principal_scope_hash=key[2],frame_id=key[3])
        if old is None:
            if snapshot.state.world_sequence!=0: raise ValueError("WORLD_STATE_GENESIS_SEQUENCE")
        else:
            relation=compare_world_cuts(snapshot.cut,old.cut)
            if relation=="INCOMPATIBLE": raise ValueError("WORLD_CUT_INCOMPATIBLE")
            if relation=="RIGHT_DOMINATES": raise ValueError("WORLD_STATE_CURRENT_REGRESSION")
            if relation=="DISJOINT": raise ValueError("WORLD_CUT_CONTINUITY_UNKNOWN")
            if snapshot.state.world_sequence != old.state.world_sequence+1: raise ValueError("WORLD_STATE_SEQUENCE_GAP")

        proposed_current=dict(self._current); proposed_current[key]=snapshot.state.world_state_id
        proposed_history={k:deque(v) for k,v in self._history.items()}
        hist=proposed_history.setdefault(key,deque()); hist.append(snapshot.state.world_state_id)
        evicted=[]
        while len(hist)>self.max_history_per_frame:
            old_id=hist.popleft()
            if proposed_current.get(key)!=old_id: evicted.append(old_id)

        # Durable publication is snapshot-first, index-second. In-memory heads move only
        # after both atomic replacements succeed, so an I/O error cannot advance the live head.
        snapshot_path=self._snapshot_path(snapshot.state.world_state_id)
        if snapshot_path is not None:
            self._atomic_json(snapshot_path,_snapshot_dict(snapshot))
            index_path=self._index_path(); assert index_path is not None
            try:
                self._atomic_json(index_path,self._index_payload(proposed_current,proposed_history))
            except Exception:
                try: snapshot_path.unlink(missing_ok=True)
                except Exception: pass
                raise

        self._snapshots[snapshot.state.world_state_id]=snapshot
        self._current=proposed_current
        self._history=defaultdict(deque,{k:deque(v) for k,v in proposed_history.items()})
        for state_id in evicted:
            self._snapshots.pop(state_id,None)
            path=self._snapshot_path(state_id)
            if path is not None and path.is_file():
                try: path.unlink()
                except OSError: pass
        return snapshot

__all__=["MaterializedWorldSnapshot","WorldStateStore"]
