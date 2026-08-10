from __future__ import annotations

import unittest
from pydantic import ValidationError
from contracts.canonical import canonical_sha256
from contracts.world_understanding import *

Z='0'*64; A='a'*64; B='b'*64

def rr(t='entity', i='entity.a', h=Z): return WorldRecordRef(record_type=t, record_id=i, revision=1, sha256=h)
def sc():
    b=(ScopeBinding(key='branch',value='main'), ScopeBinding(key='repository',value='repo.main'))
    w=derive_world_id(life_id='life.main',namespace_anchor='primary')
    h=derive_world_scope_hash(life_id='life.main',world_id=w,domain_id='software',scope_bindings=b)
    return WorldScope(life_id='life.main',world_id=w,domain_id='software',scope_bindings=b,world_scope_hash=h,principal_scope_hash=A,privacy_scope='system')
def wt(): return WorldTime(valid_from_ms=1,observed_at_ms=2,recorded_at_ms=3)
def ob(): return ObservabilityState(mode='OBSERVED',access_milli=1000,scope_coverage_milli=1000,time_coverage_milli=1000,adapter_quality_milli=1000,measurement_quality_milli=1000,combined_quality_milli=1000)
def sr(k='FACT_EXECUTION',d='EXECUTION_ACTION'): return WorldSourceRef(source_kind=k,object_id='n.1',object_revision=1,sha256=B,authority_domain=d,authority_ceiling_milli=1000,provenance_integrity_milli=1000)
def env(kind='SOURCE_RECORD'):
    s=sc(); sk='CONTEXT_REQUEST' if kind=='CONTEXT_REQUEST' else 'FACT_EXECUTION'; p={'kind':kind}; ph=canonical_sha256(p)
    dk=derive_ingress_dedup_key(envelope_kind=kind,source_kind=sk,source_native_id='n.1',payload_sha256=ph,world_scope_hash=s.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dk),envelope_kind=kind,source_kind=sk,source_native_id='n.1',producer_ref='producer',payload_inline=p,payload_sha256=ph,source_time=wt(),life_id=s.life_id,principal_scope_hash=s.principal_scope_hash,scope_hint=s,correlation_id='c.1',dedup_key=dk)
def cl(): return WorldClaim(subject_ref=rr(),predicate='status',value=WorldValue(kind='string',string_value='active'))

class P1Contracts(unittest.TestCase):
    def test_public_surface(self):
        required=[WorldIngressEnvelope,DirectKnownRecord,DerivedKnownRecord,WorldScope,WorldTime,WorldCut,WorldEvent,ObservabilityState,WorldEntity,EntityResolutionCandidate,WorldRelation,WorldHypothesis,WorldState,WorldPrediction,PredictionOutcome,WorldQuery,WorldContextPacket,WorldContextItem,ExpansionHandle,WorldCuriosity,KnowledgeGap,WorldInquiry,InquiryOutcome,DerivationRef,DerivationEdge,TransformCostObservation,TransformQualityProfile,WorldContextOutputPort,WorldInquiryOutputPort]
        self.assertEqual(len(required),29)

    def test_roundtrip_and_canonical_hash(self):
        x=env(); self.assertEqual(x,WorldIngressEnvelope.model_validate_json(x.model_dump_json()))
        self.assertEqual(canonical_sha256({'b':2,'a':1}),canonical_sha256({'a':1,'b':2}))

    def test_invalid_enum_and_time_rejected(self):
        d=env().model_dump(); d['envelope_kind']='MAGIC'
        with self.assertRaises(ValidationError): WorldIngressEnvelope(**d)
        with self.assertRaises(ValidationError): WorldTime(valid_from_ms=5,valid_until_ms=4,recorded_at_ms=6)

    def test_context_request_is_control_only(self):
        q=env('CONTEXT_REQUEST'); self.assertFalse(q.may_authorize); self.assertFalse(q.may_execute); self.assertEqual(q.empirical_evidence_weight_milli,0)
        v=WorldValue(kind='string',string_value='query')
        kid=derive_direct_known_id(world_scope_hash=sc().world_scope_hash,proposition_type='CONTEXT_REQUEST',subject_ref='q',predicate='requested',object_value=v,object_ref=None,source_envelope_id=q.envelope_id)
        with self.assertRaises(ValidationError):
            DirectKnownRecord(known_id=kid,proposition_type='CONTEXT_REQUEST',subject_ref='q',predicate='requested',object_value=v,world_scope=sc(),time=wt(),authority_domain='CONTEXT_CONTINUITY',authority_ceiling_milli=0,observability_state=ob(),truth_state='TRUE',epistemic_state='CURRENT',record_hash=Z,source_envelope_id=q.envelope_id,source_kind='CONTEXT_REQUEST',source_native_id='n.1',source_payload_hash=q.payload_sha256,compiler_id='c',compiler_version='v1')

    def test_unclassified_and_authority_laundering_rejected(self):
        with self.assertRaises(ValidationError): WorldSourceRef(source_kind='UNCLASSIFIED_SOURCE',object_id='x',sha256=Z,authority_domain='EXECUTION_ACTION',authority_ceiling_milli=1)
        with self.assertRaises(ValidationError): AuthorityBinding(domain='USER_HUMAN_INPUT',proposition_type='FILE_EXISTS',world_scope_hash=sc().world_scope_hash,valid_from_ms=0,authority_ceiling_milli=10,empirical_evidence_weight_milli=11)

    def test_truth_and_epistemic_are_independent(self):
        self.assertNotEqual('TRUE','STALE')
        self.assertIn('truth_state',WorldRelation.model_fields); self.assertIn('epistemic_state',WorldRelation.model_fields)

    def test_context_packet_cannot_authorize(self):
        frame=rr('frame','frame.1'); pid=derive_world_packet_id(world_scope_hash=sc().world_scope_hash,frame_ref=frame,basis_world_state_ref=None,task_ref='task.1',task_sha256=Z,generated_at_ms=10,projection_policy_sha256=B)
        p=WorldContextPacket(packet_id=pid,scope=sc(),frame_ref=frame,task_ref='task.1',task_sha256=Z,generated_at_ms=10,token_budget=256,projection_policy_ref='policy',projection_policy_sha256=B,packet_sha256=Z).with_computed_hash()
        self.assertTrue(p.context_only); self.assertFalse(p.authorizes); self.assertFalse(p.confirms); self.assertFalse(p.changes_risk); self.assertFalse(p.may_execute); self.assertEqual(p.empirical_evidence_weight_milli,0)

    def test_inquiry_defaults_to_no_authority(self):
        gid='wgap_'+canonical_sha256({'g':1}); cid='wcur_'+canonical_sha256({'c':1}); iid=derive_inquiry_id(world_scope_hash=sc().world_scope_hash,question='what changed?',knowledge_gap_id=gid,subject_refs=())
        q=WorldInquiry(inquiry_id=iid,correlation_id='corr',curiosity_id=cid,knowledge_gap_id=gid,scope=sc(),question='what changed?',inquiry_kind='verification',dedup_key=Z,expected_information_gain_milli=0,impact_milli=0,urgency_milli=0,created_at_ms=10,inquiry_sha256=Z)
        self.assertEqual(q.authorization,'NONE'); self.assertFalse(q.may_execute); self.assertFalse(q.may_call_tools); self.assertFalse(q.may_authorize); self.assertEqual(q.empirical_evidence_weight_milli,0)

    def test_prediction_is_not_evidence(self):
        state=rr('state','state.1'); c=cl(); pid=derive_prediction_id(world_scope_hash=sc().world_scope_hash,basis_world_state_ref=state,predicted_claim=c,condition_claim=None,horizon_start_ms=10,horizon_end_ms=20)
        p=WorldPrediction(prediction_id=pid,scope=sc(),basis_world_state_ref=state,predicted_claim=c,prediction_kind='transition',horizon_start_ms=10,horizon_end_ms=20,prediction_score_milli=700,basis_refs=(state,),created_at_ms=9,prediction_sha256=Z)
        self.assertEqual(p.evidence_authority,'none'); self.assertEqual(p.empirical_evidence_weight_milli,0)
        with self.assertRaises(ValidationError): WorldPrediction(**{**p.model_dump(),'status':'RESOLVED'})

    def test_model_assisted_relation_has_zero_empirical_weight(self):
        value=WorldValue(kind='string',string_value='boundary'); rid=derive_relation_id(world_scope_hash=sc().world_scope_hash,subject_ref=rr(),predicate='role',value=value,condition_sha256=None)
        with self.assertRaises(ValidationError): WorldRelation(relation_id=rid,scope=sc(),subject_ref=rr(),predicate='role',value=value,extraction_mode='model_assisted',materialization_class='EPHEMERAL',truth_state='UNKNOWN',epistemic_state='CURRENT',empirical_evidence_weight_milli=1,revision=1,time=wt(),relation_sha256=Z)

    def test_cognition_compat_is_reference_only_and_exact(self):
        r=rr('cognition','cog.1')
        c=CognitionStatementRef(cognition_id='cog.1',revision=1,statement_sha256=Z,life_id='life.main',world_scope_hash=sc().world_scope_hash,principal_scope_hash=A,privacy_scope='system',record_ref=r)
        self.assertEqual(c.record_ref.sha256,c.statement_sha256)

    def test_all_public_models_generate_json_schema(self):
        models=[v for v in globals().values() if isinstance(v,type) and hasattr(v,'model_json_schema') and getattr(v,'__module__','').startswith('contracts.world_understanding')]
        self.assertGreaterEqual(len(models),30)
        for m in models: m.model_json_schema()

if __name__=='__main__': unittest.main()
