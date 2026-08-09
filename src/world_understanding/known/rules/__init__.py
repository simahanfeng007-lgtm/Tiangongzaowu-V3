"""Conservative deterministic P4 rule set. No LLM, tools, network, or semantic role guessing."""
from __future__ import annotations
from collections import deque
from contracts.world_understanding._base import WorldValue
from ..rule import RuleSpec, DerivedCandidate
from ..set import KnownSet, KnownRecord

def _text(record: KnownRecord) -> str | None:
    value = record.object_value
    return value.string_value if value is not None and value.kind == "string" else None

def _ordered(records):
    return sorted(records, key=lambda r: (r.time.recorded_at_ms, r.time.observed_at_ms or -1, r.record_hash))

class FileStateTransitionRule:
    spec = RuleSpec("wu.rule.filesystem.transition", "v0.1", "FILESYSTEM_ARTIFACT", ("FILESYSTEM_ARTIFACT",))
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]; touched={(r.proposition_type,r.subject_ref) for r in delta if r.proposition_type in {"FILE_EXISTS","FILE_HASH_AT"}}
        for ptype,subject in sorted(touched):
            rows=_ordered(known.by_proposition_subject(ptype,subject))
            for left,right in zip(rows,rows[1:]):
                a,b=_text(left),_text(right)
                if a is None or b is None: continue
                if ptype=="FILE_EXISTS" and a!=b and (a,b) in {("false","true"),("true","false")}:
                    kind="FILE_CREATED" if b=="true" else "FILE_DELETED"
                    out.append(DerivedCandidate((left,right),kind,subject,"filesystem.transition",WorldValue(kind="string",string_value="created" if b=="true" else "deleted")))
                if ptype=="FILE_HASH_AT":
                    kind="FILE_VERIFIED_UNCHANGED" if a==b else "FILE_CONTENT_CHANGED"
                    out.append(DerivedCandidate((left,right),kind,subject,"filesystem.hash_transition",WorldValue(kind="string",string_value=f"{a}->{b}")))
        return tuple(out)

class HashEqualityRule:
    spec = RuleSpec("wu.rule.filesystem.hash-equality", "v0.1", "FILESYSTEM_ARTIFACT", ("FILESYSTEM_ARTIFACT",))
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]; hashes={_text(r) for r in delta if r.proposition_type=="FILE_HASH_AT" and _text(r)}
        for digest in sorted(hashes):
            rows=sorted((r for r in known.by_proposition("FILE_HASH_AT") if _text(r)==digest),key=lambda r:(r.subject_ref,r.record_hash))
            for i,left in enumerate(rows):
                for right in rows[i+1:]:
                    if left.subject_ref==right.subject_ref: continue
                    out.append(DerivedCandidate((left,right),"HASH_EQUAL",left.subject_ref,"filesystem.hash_equal",WorldValue(kind="string",string_value=f"{right.subject_ref}:{digest}")))
        return tuple(out)

class EventOrderRule:
    spec = RuleSpec("wu.rule.event.order", "v0.1", "TASK_RUN_LIFECYCLE", ("TASK_RUN_LIFECYCLE",))
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]; subjects=sorted({r.subject_ref for r in delta if r.proposition_type=="CHAIN_EVENT_RECORDED"})
        for subject in subjects:
            rows=sorted(known.by_proposition_subject("CHAIN_EVENT_RECORDED",subject),key=lambda r:(r.time.recorded_at_ms,r.record_hash))
            for left,right in zip(rows,rows[1:]):
                if left.record_hash!=right.record_hash:
                    out.append(DerivedCandidate((left,right),"EVENT_PRECEDES",subject,"event.precedes",WorldValue(kind="string",string_value=f"{left.known_id}->{right.known_id}")))
        return tuple(out)

class SameSourceRootGroupingRule:
    spec = RuleSpec("wu.rule.provenance.same-root", "v0.1", None, ())
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]; roots=sorted({ref.sha256 for record in delta if record.derivation_type=="DIRECT" for ref in record.provenance_refs})
        for root in roots:
            rows=sorted((r for r in known.by_provenance_root(root) if r.derivation_type=="DIRECT"),key=lambda r:(r.authority_domain,r.known_id,r.record_hash))
            for i,left in enumerate(rows):
                for right in rows[i+1:]:
                    if left.authority_domain!=right.authority_domain: continue
                    out.append(DerivedCandidate((left,right),"SHARES_SOURCE_ROOT",left.known_id,"provenance.same_root",WorldValue(kind="string",string_value=f"{right.known_id}:{root}")))
        return tuple(out)

class GitStructuralNormalizationRule:
    spec = RuleSpec("wu.rule.git.structural-normalize", "v0.1", "GIT_CODE", ("GIT_CODE",))
    mapping={"GIT_CONTAINS":("CONTAINS","code.contains"),"GIT_IMPORTS":("IMPORTS","code.imports"),"GIT_DIRECT_CALLS":("DIRECT_CALLS","code.direct_calls")}
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]
        for r in delta:
            mapped=self.mapping.get(r.proposition_type)
            if mapped and r.object_value is not None:
                out.append(DerivedCandidate((r,),mapped[0],r.subject_ref,mapped[1],r.object_value,r.object_ref))
        return tuple(out)

def _reachable_payload(target:str,path:tuple[str,...])->str: return f"{target}|path={'>'.join(path)}"

class CallReachabilityRule:
    spec = RuleSpec("wu.rule.code.call-reachability", "v0.1", "GIT_CODE", ("GIT_CODE",), allows_transitivity=True)
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        if not any(r.proposition_type=="DIRECT_CALLS" for r in delta): return ()
        edges=[]
        for record in known.by_proposition("DIRECT_CALLS"):
            target=_text(record)
            if target: edges.append((record.subject_ref,target,record))
        adjacency={}
        for source,target,record in sorted(edges,key=lambda item:(item[0],item[1],item[2].record_hash)):
            adjacency.setdefault(source,[]).append((target,record))
        out=[]
        for source in sorted(adjacency):
            queue=deque([(source,(source,))]); best={source:(source,)}
            while queue:
                node,path=queue.popleft()
                for target,_ in adjacency.get(node,()):
                    if target in path: continue
                    new_path=path+(target,); prior=best.get(target)
                    if prior is not None and (len(prior),prior) <= (len(new_path),new_path): continue
                    best[target]=new_path; queue.append((target,new_path))
            for target,path in sorted(best.items()):
                if target==source or len(path)<2: continue
                parents=[]
                for left,right in zip(path,path[1:]):
                    candidates=[r for t,r in adjacency.get(left,()) if t==right]
                    parents.append(sorted(candidates,key=lambda r:r.record_hash)[0])
                out.append(DerivedCandidate(tuple(parents),"CALL_REACHABLE",source,"code.call_reachable",WorldValue(kind="string",string_value=_reachable_payload(target,path))))
        return tuple(out)

class ScopeContainmentRule:
    spec = RuleSpec("wu.rule.scope.containment", "v0.1", None, ())
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        return tuple(DerivedCandidate((r,),"SCOPE_CONTAINS",r.world_scope.world_id,"scope.contains",r.object_value,r.object_ref) for r in delta if r.proposition_type=="SCOPE_BINDING_OBSERVED" and r.object_value is not None)

class WorldFrameIdentityRule:
    spec = RuleSpec("wu.rule.world-frame.identity", "v0.1", None, ())
    mapping={"RUN_BELONGS_TO_LIFE":"WORLD_FRAME_LIFE","RUN_HAS_WORKSPACE":"WORLD_FRAME_WORKSPACE","RUN_HAS_PRINCIPAL_SCOPE":"WORLD_FRAME_PRINCIPAL_SCOPE","REPO_HEAD":"WORLD_FRAME_REPO_HEAD","BRANCH_CURRENT":"WORLD_FRAME_BRANCH"}
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]):
        out=[]
        for r in delta:
            target=self.mapping.get(r.proposition_type)
            if target is not None and r.object_value is not None:
                out.append(DerivedCandidate((r,),target,r.world_scope.world_id,"world_frame.identity",r.object_value,r.object_ref))
        return tuple(out)

def build_p4_rules():
    return (FileStateTransitionRule(),HashEqualityRule(),EventOrderRule(),SameSourceRootGroupingRule(),GitStructuralNormalizationRule(),CallReachabilityRule(),ScopeContainmentRule(),WorldFrameIdentityRule())

__all__=["FileStateTransitionRule","HashEqualityRule","EventOrderRule","SameSourceRootGroupingRule","GitStructuralNormalizationRule","CallReachabilityRule","ScopeContainmentRule","WorldFrameIdentityRule","build_p4_rules"]
