"""P3 deterministic source compiler set. Compiler instances hold configuration only, never life state."""
from __future__ import annotations
from typing import Any
from contracts.world_understanding.ingress import WorldIngressEnvelope
from .base import CompilerSpec,DeterministicSourceCompiler,make_direct_known,payload_text
from .git_code import GitCodeCompiler

SPECS={
"RUN_CONTEXT":CompilerSpec("RUN_CONTEXT","wu.compiler.run-context","v0.1","RUN_CONTEXT_OBSERVED","runtime.run_context","IDENTITY_RUN_CONTEXT",1000,1000),
"USER_CONVERSATION":CompilerSpec("USER_CONVERSATION","wu.compiler.user-conversation","v0.1","USER_SAID","conversation.user_said","USER_HUMAN_INPUT",1000,1000),
"SYSTEM_GOVERNANCE":CompilerSpec("SYSTEM_GOVERNANCE","wu.compiler.system-governance","v0.1","SYSTEM_GOVERNANCE_STATED","governance.statement","SYSTEM_GOVERNANCE",1000,1000),
"RUNTIME_ENVIRONMENT":CompilerSpec("RUNTIME_ENVIRONMENT","wu.compiler.runtime-environment","v0.1","RUNTIME_ENVIRONMENT_OBSERVED","runtime.environment","RUNTIME_ENVIRONMENT",1000,1000),
"AUTHORIZATION":CompilerSpec("AUTHORIZATION","wu.compiler.authorization","v0.1","AUTHORIZATION_DECISION_RECORDED","authorization.decision","AUTHORIZATION",1000,1000),
"FACT_EXECUTION":CompilerSpec("FACT_EXECUTION","wu.compiler.fact-execution","v0.1","FACT_EXECUTION_RECORDED","execution.fact","EXECUTION_ACTION",1000,1000),
"TOOL_RESULT":CompilerSpec("TOOL_RESULT","wu.compiler.tool-result","v0.1","TOOL_RESULT_RECORDED","tool.result","EXECUTION_ACTION",1000,1000),
"FILESYSTEM":CompilerSpec("FILESYSTEM","wu.compiler.filesystem","v0.1","FILESYSTEM_OBSERVED","filesystem.observation","FILESYSTEM_ARTIFACT",1000,1000),
"GIT_CODE":CompilerSpec("GIT_CODE","wu.compiler.git-code","v0.1","GIT_OBSERVED","git.observation","GIT_CODE",1000,1000),
"WEB_EXTERNAL":CompilerSpec("WEB_EXTERNAL","wu.compiler.web-external","v0.1","WEB_SOURCE_CLAIMS","web.claim","WEB_EXTERNAL",0,0),
"DESKTOP_UI":CompilerSpec("DESKTOP_UI","wu.compiler.desktop-ui","v0.1","DESKTOP_UI_OBSERVED","desktop.observation","DESKTOP_UI_PROCESS",800,800),
"MEMORY":CompilerSpec("MEMORY","wu.compiler.memory","v0.1","MEMORY_RECORDED","memory.record","MEMORY_EXPERIENCE",0,0),
"KNOWLEDGE":CompilerSpec("KNOWLEDGE","wu.compiler.knowledge","v0.1","DOCUMENT_CLAIMS","knowledge.claim","KNOWLEDGE_DOCUMENT",0,0),
"CONTEXT_CONTINUITY":CompilerSpec("CONTEXT_CONTINUITY","wu.compiler.context-continuity","v0.1","CONTEXT_CONTINUITY_RECORDED","context.continuity","CONTEXT_CONTINUITY",0,0),
"AUTONOMY":CompilerSpec("AUTONOMY","wu.compiler.autonomy","v0.1","SELF_WILL_DECISION_RECORDED","autonomy.decision","AUTONOMY_SELF_WILL",0,0),
"CHAIN_EVENT":CompilerSpec("CHAIN_EVENT","wu.compiler.chain-event","v0.1","CHAIN_EVENT_RECORDED","chain.event","TASK_RUN_LIFECYCLE",1000,1000),
"EXECUTION_INTEGRITY":CompilerSpec("EXECUTION_INTEGRITY","wu.compiler.execution-integrity","v0.1","EXECUTION_INTEGRITY_RECORDED","execution.integrity","EXECUTION_INTEGRITY",1000,1000),
"METRICS":CompilerSpec("METRICS","wu.compiler.metrics","v0.1","METRIC_OBSERVED","metrics.observation","OBSERVABILITY_METRICS",800,800),
"MIGRATION_AUDIT":CompilerSpec("MIGRATION_AUDIT","wu.compiler.migration-audit","v0.1","MIGRATION_AUDIT_RECORDED","migration.audit","MIGRATION_AUDIT",0,0),
"MODEL_OUTPUT":CompilerSpec("MODEL_OUTPUT","wu.compiler.model-output","v0.1","MODEL_PROPOSED","model.proposed","INTERNAL_MODEL_OUTPUT",0,0),
}

class ToolResultCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="TOOL_RESULT": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        rows=[make_direct_known(envelope,self.spec)]
        tool_name=str(payload.get("tool_name") or payload.get("toolName") or "").strip()
        if tool_name:
            rows.append(make_direct_known(
                envelope,self.spec,
                proposition_type="TOOL_IDENTITY",
                predicate="tool.identity",
                subject_ref="tool:"+tool_name,
                object_text=tool_name,
            ))
        evidence=payload.get("write_evidence")
        if payload.get("observed_write_effect") is True and isinstance(evidence,dict) and evidence.get("authoritative") is True:
            changed=tuple(str(x).strip() for x in (evidence.get("changed_files") or ()) if str(x).strip())
            deleted=tuple(str(x).strip() for x in (evidence.get("deleted_files") or ()) if str(x).strip())
            for path in sorted(set(changed)):
                rows.append(make_direct_known(envelope,self.spec,proposition_type="FILE_WRITE_OBSERVED",predicate="filesystem.write_observed",subject_ref=envelope.source_native_id,object_text=path,authority_ceiling_milli=1000,empirical_evidence_weight_milli=1000,authority_domain="FILESYSTEM_ARTIFACT"))
            for path in sorted(set(deleted)):
                rows.append(make_direct_known(envelope,self.spec,proposition_type="FILE_DELETE_OBSERVED",predicate="filesystem.delete_observed",subject_ref=envelope.source_native_id,object_text=path,authority_ceiling_milli=1000,empirical_evidence_weight_milli=1000,authority_domain="FILESYSTEM_ARTIFACT"))
        elif payload.get("write_effect") is True:
            rows.append(make_direct_known(envelope,self.spec,proposition_type="TOOL_WRITE_DECLARED",predicate="tool.write_declared",object_text=payload_text(payload,envelope.payload_sha256),authority_ceiling_milli=0,empirical_evidence_weight_milli=0))
        return tuple(rows)

class FactExecutionCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="FACT_EXECUTION": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        transaction=payload.get("fact_transaction") if isinstance(payload.get("fact_transaction"),dict) else {}
        action=str(transaction.get("action") or payload.get("action") or "").strip()
        rows=[make_direct_known(envelope,self.spec)]
        if action:
            rows.append(make_direct_known(envelope,self.spec,proposition_type="TOOL_IDENTITY",predicate="execution.action_identity",subject_ref="action:"+action,object_text=action))
        return tuple(rows)

class RuntimeEnvironmentCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="RUNTIME_ENVIRONMENT": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        machine=str(payload.get("machine") or "runtime").strip()
        return (
            make_direct_known(envelope,self.spec),
            make_direct_known(envelope,self.spec,proposition_type="RUNTIME_IDENTITY",predicate="runtime.identity",subject_ref="runtime:"+machine,object_text=machine),
        )

class KnowledgeCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="KNOWLEDGE": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        document_id=str(payload.get("document_id") or envelope.source_native_id)
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"),dict) else {}
        name=str(metadata.get("file_name") or document_id)
        return (
            make_direct_known(envelope,self.spec),
            make_direct_known(envelope,self.spec,proposition_type="KNOWLEDGE_DOCUMENT_IDENTITY",predicate="knowledge.document_identity",subject_ref=document_id,object_text=name),
        )

class MemoryCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="MEMORY": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        memory_id=str(payload.get("memory_id") or envelope.source_native_id)
        return (
            make_direct_known(envelope,self.spec),
            make_direct_known(envelope,self.spec,proposition_type="MEMORY_STORE_IDENTITY",predicate="memory.store_identity",subject_ref=memory_id,object_text=str(payload.get("memory_type") or "memory")),
        )

class FilesystemEvidenceCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        if envelope.source_kind!="FILESYSTEM": raise ValueError("compiler source_kind mismatch")
        payload=envelope.payload_inline or {}
        path=str(payload.get("path") or envelope.source_native_id)
        rows=[]
        if isinstance(payload.get("exists"),bool):
            rows.append(make_direct_known(envelope,self.spec,proposition_type="FILE_EXISTS",predicate="filesystem.exists",subject_ref=path,object_text="true" if payload["exists"] else "false"))
        sha=payload.get("sha256")
        if isinstance(sha,str) and len(sha)==64 and all(ch in "0123456789abcdef" for ch in sha):
            rows.append(make_direct_known(envelope,self.spec,proposition_type="FILE_HASH_AT",predicate="filesystem.sha256",subject_ref=path,object_text=sha))
        return tuple(rows) if rows else (make_direct_known(envelope,self.spec),)

class ChainEventCompiler(DeterministicSourceCompiler):
    def __call__(self,envelope:WorldIngressEnvelope):
        payload=envelope.payload_inline or {}
        event=str(payload.get("event") or payload.get("event_kind") or payload.get("kind") or "unknown")
        return (make_direct_known(envelope,self.spec,proposition_type="CHAIN_EVENT_RECORDED",predicate="chain.event",object_text=event),)

def build_p3_compilers()->dict[str,object]:
    compilers={kind:DeterministicSourceCompiler(spec) for kind,spec in SPECS.items()}
    compilers["TOOL_RESULT"]=ToolResultCompiler(SPECS["TOOL_RESULT"])
    compilers["FACT_EXECUTION"]=FactExecutionCompiler(SPECS["FACT_EXECUTION"])
    compilers["RUNTIME_ENVIRONMENT"]=RuntimeEnvironmentCompiler(SPECS["RUNTIME_ENVIRONMENT"])
    compilers["KNOWLEDGE"]=KnowledgeCompiler(SPECS["KNOWLEDGE"])
    compilers["MEMORY"]=MemoryCompiler(SPECS["MEMORY"])
    compilers["FILESYSTEM"]=FilesystemEvidenceCompiler(SPECS["FILESYSTEM"])
    compilers["GIT_CODE"]=GitCodeCompiler(SPECS["GIT_CODE"])
    compilers["CHAIN_EVENT"]=ChainEventCompiler(SPECS["CHAIN_EVENT"])
    return compilers

__all__=["SPECS","build_p3_compilers","ToolResultCompiler","FactExecutionCompiler","RuntimeEnvironmentCompiler","KnowledgeCompiler","MemoryCompiler","FilesystemEvidenceCompiler","GitCodeCompiler","ChainEventCompiler"]