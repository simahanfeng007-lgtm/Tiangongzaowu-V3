"""P9 L6 coherent WorldState materializer. No tools, runtime, LLM, or raw source copies."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.hypothesis import WorldHypothesis
from contracts.world_understanding.state import WorldState
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.common.scope import require_exact_scope
from world_understanding.common.world_cut import compare_world_cuts
from world_understanding.software_world.frame import SoftwareWorldFrame
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.cognition.l5 import CognitionL5View
from .invalidation import changed_watermark_keys, precise_invalidations
from .manifests import HeadManifest, DependencyBinding, DependencyManifest, DeltaManifest
from .store import MaterializedWorldSnapshot, WorldStateStore
from .support import CognitionSupportEvaluator

@dataclass(frozen=True, slots=True)
class WorldStateMaterializerConfig:
    max_entities: int=2048
    max_relations: int=4096
    max_cognition: int=1024
    max_hypotheses: int=1024
    max_uncertainty: int=2048
    max_dependencies: int=8192
    max_conflicts: int=4096
    max_stale: int=4096
    def __post_init__(self) -> None:
        if any(v<=0 for v in (self.max_entities,self.max_relations,self.max_cognition,self.max_hypotheses,self.max_uncertainty,self.max_dependencies,self.max_conflicts,self.max_stale)):
            raise ValueError("WORLD_STATE_CONFIG_LIMIT_INVALID")

@dataclass(frozen=True, slots=True)
class MaterializationInput:
    frame: SoftwareWorldFrame
    cut: WorldCut
    graph: SparseWorldGraph
    stable_cognition: tuple[CognitionL5View,...]=()
    active_hypotheses: tuple[WorldHypothesis,...]=()
    uncertainty_refs: tuple[WorldRecordRef,...]=()
    conflict_refs: tuple[WorldRecordRef,...]=()
    dependency_bindings: tuple[DependencyBinding,...]=()
    source_transaction_id: str="worldstate.tx"
    materialized_at_ms: int=0


def _frame_ref(frame: SoftwareWorldFrame) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_frame",record_id=frame.frame_id,revision=None,sha256=frame.frame_revision_hash)
def _cut_ref(cut: WorldCut) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_cut",record_id=cut.cut_id,revision=None,sha256=cut.cut_sha256)
def _entity_ref(e: object) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_entity",record_id=e.entity_id,revision=e.revision,sha256=e.entity_sha256)
def _relation_ref(r: object) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_relation",record_id=r.relation_id,revision=r.revision,sha256=r.relation_sha256)
def _hyp_ref(h: WorldHypothesis) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_hypothesis",record_id=h.hypothesis_id,revision=None,sha256=h.hypothesis_sha256)
def _state_ref(state: WorldState) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_state",record_id=state.world_state_id,revision=state.world_sequence+1,sha256=state.state_sha256)
def _identity(ref: WorldRecordRef) -> tuple[str,str]: return (ref.record_type,ref.record_id)

def _head_delta(old_refs: tuple[WorldRecordRef,...], new_refs: tuple[WorldRecordRef,...]) -> tuple[tuple[WorldRecordRef,...],tuple[WorldRecordRef,...],tuple[WorldRecordRef,...],frozenset[tuple[str,str]]]:
    old={_identity(r):r for r in old_refs}; new={_identity(r):r for r in new_refs}
    added=[]; removed=[]; changed=[]; refreshed=set()
    for key in sorted(set(old)|set(new)):
        a,b=old.get(key),new.get(key)
        if a is None: added.append(b); refreshed.add(key)
        elif b is None: removed.append(a)
        elif a.sort_key()!=b.sort_key(): changed.append(b); refreshed.add(key)
    return tuple(added),tuple(removed),tuple(changed),frozenset(refreshed)

class WorldStateMaterializer:
    def __init__(self, store: WorldStateStore, *, config: WorldStateMaterializerConfig|None=None, support_evaluator: CognitionSupportEvaluator|None=None) -> None:
        self.store=store; self.config=config or WorldStateMaterializerConfig(); self.support_evaluator=support_evaluator
    def materialize(self, data: MaterializationInput) -> MaterializedWorldSnapshot:
        require_exact_scope(data.frame.scope,data.cut.scope); require_exact_scope(data.frame.scope,data.graph.scope)
        data.graph.require_frame(data.frame)
        if data.graph.frame_revision_hash != data.frame.frame_revision_hash:
            raise ValueError("WORLD_STATE_GRAPH_FRAME_REVISION_MISMATCH")
        if data.frame.world_cut is not None:
            relation=compare_world_cuts(data.cut,data.frame.world_cut)
            if relation!="SAME": raise ValueError("WORLD_FRAME_CUT_MISMATCH")
        scope=data.frame.scope
        previous=self.store.current(life_id=scope.life_id,world_scope_hash=scope.world_scope_hash,principal_scope_hash=scope.principal_scope_hash,frame_id=data.frame.frame_id)
        if previous is not None:
            relation=compare_world_cuts(data.cut,previous.cut)
            if relation=="INCOMPATIBLE": raise ValueError("WORLD_CUT_INCOMPATIBLE")
            if relation=="RIGHT_DOMINATES": raise ValueError("WORLD_STATE_CURRENT_REGRESSION")
            if relation=="DISJOINT": raise ValueError("WORLD_CUT_CONTINUITY_UNKNOWN")
        entity_refs=tuple(_entity_ref(e) for e in data.graph.entities() if e.lifecycle=="ACTIVE")
        relation_refs=tuple(_relation_ref(r) for r in data.graph.relations())
        entity_manifest=HeadManifest.build("entity_heads",entity_refs,max_items=self.config.max_entities)
        relation_manifest=HeadManifest.build("relation_heads",relation_refs,max_items=self.config.max_relations)
        changed_sources=changed_watermark_keys(None if previous is None else previous.cut,data.cut)
        dependency_by_ref={binding.ref.sort_key():binding for binding in data.dependency_bindings}
        cognition_refs=[]; revalidated=[]; cognition_stale=[]
        for view in data.stable_cognition:
            require_exact_scope(scope,view.scope)
            st=view.statement
            if st.status not in {"STABLE","CORE"} or st.stability_level not in {"C2","C3","C4"}: raise ValueError("WORLD_STATE_COGNITION_NOT_STABLE")
            if not st.has_valid_statement_sha256(): raise ValueError("WORLD_STATE_COGNITION_HASH_INVALID")
            ref=view.statement_ref.record_ref
            binding=dependency_by_ref.get(ref.sort_key())
            cognition_keys=set(() if binding is None else binding.source_keys)
            touched=bool(set(changed_sources).intersection(cognition_keys))
            if previous is not None and touched:
                if self.support_evaluator is None or binding is None or not binding.evidence_ids:
                    cognition_stale.append(ref); continue
                decision=self.support_evaluator.evaluate(view,invalidated_evidence_ids=binding.evidence_ids,now_ms=data.materialized_at_ms)
                if decision.remains_stable: revalidated.append(ref)
                else: cognition_stale.append(ref); continue
            cognition_refs.append(ref)
        cognition_manifest=None if not cognition_refs else HeadManifest.build("cognition_heads",tuple(cognition_refs),max_items=self.config.max_cognition)
        hyp_refs=[]
        for hyp in data.active_hypotheses:
            require_exact_scope(scope,hyp.scope)
            if not hyp.has_valid_hash(): raise ValueError("WORLD_STATE_HYPOTHESIS_HASH_INVALID")
            hyp_refs.append(_hyp_ref(hyp))
        hypothesis_manifest=None if not hyp_refs else HeadManifest.build("active_hypotheses",tuple(hyp_refs),max_items=self.config.max_hypotheses)
        uncertainty_manifest=None if not data.uncertainty_refs else HeadManifest.build("uncertainty",data.uncertainty_refs,max_items=self.config.max_uncertainty)
        all_current_refs=tuple(sorted((*entity_refs,*relation_refs,*cognition_refs,*hyp_refs),key=lambda r:r.sort_key()))
        dependencies=DependencyManifest.build(data.dependency_bindings,max_items=self.config.max_dependencies)
        old_refs=() if previous is None else tuple(sorted((*previous.entity_heads.refs,*previous.relation_heads.refs,*(() if previous.cognition_heads is None else previous.cognition_heads.refs),*(() if previous.active_hypotheses is None else previous.active_hypotheses.refs)),key=lambda r:r.sort_key()))
        added,removed,changed,refreshed=_head_delta(old_refs,all_current_refs)
        revalidated_identities=frozenset(_identity(ref) for ref in revalidated)
        invalidated=precise_invalidations(previous_dependencies=None if previous is None else previous.dependencies,changed_source_keys=changed_sources,current_refs=all_current_refs,refreshed_identity_keys=frozenset(set(refreshed)|set(revalidated_identities)))
        stale=tuple(sorted({r.sort_key():r for r in (*invalidated,*cognition_stale)}.values(),key=lambda r:r.sort_key()))
        if len(stale)>self.config.max_stale: raise ValueError("WORLD_STATE_STALE_LIMIT")
        conflicts=tuple(sorted({r.sort_key():r for r in data.conflict_refs}.values(),key=lambda r:r.sort_key()))
        if len(conflicts)>self.config.max_conflicts: raise ValueError("WORLD_STATE_CONFLICT_LIMIT")
        previous_state_ref=None if previous is None else _state_ref(previous.state)
        delta=DeltaManifest.build(previous_state_ref=previous_state_ref,changed_source_keys=changed_sources,added_refs=added,removed_refs=removed,changed_refs=changed,invalidated_refs=stale,revalidated_cognition_refs=tuple(revalidated),uncertainty_manifest_ref=None if uncertainty_manifest is None else uncertainty_manifest.ref,dependency_manifest_ref=dependencies.ref)
        sequence=0 if previous is None else previous.state.world_sequence+1
        cut_ref=_cut_ref(data.cut)
        state_id="wst_"+canonical_sha256({"domain":"tiangong.world.state-id.v1","world_scope_hash":scope.world_scope_hash,"world_cut_ref":cut_ref.model_dump(mode="json"),"world_sequence":sequence,"source_transaction_id":data.source_transaction_id})
        state=WorldState(world_state_id=state_id,scope=scope,frame_ref=_frame_ref(data.frame),world_cut_ref=cut_ref,world_sequence=sequence,observation_cutoff_ref=None,entity_head_manifest_ref=entity_manifest.ref,relation_head_manifest_ref=relation_manifest.ref,cognition_head_manifest_ref=None if cognition_manifest is None else cognition_manifest.ref,active_hypothesis_manifest_ref=None if hypothesis_manifest is None else hypothesis_manifest.ref,delta_manifest_ref=delta.ref,unresolved_conflict_refs=conflicts,stale_refs=stale,materialized_at_ms=data.materialized_at_ms,source_transaction_id=data.source_transaction_id,state_sha256="0"*64).with_computed_hash()
        snapshot=MaterializedWorldSnapshot(state,data.cut,entity_manifest,relation_manifest,cognition_manifest,hypothesis_manifest,uncertainty_manifest,dependencies,delta,data.frame.frame_id)
        return self.store.publish(snapshot)

__all__=["WorldStateMaterializerConfig","MaterializationInput","WorldStateMaterializer"]
