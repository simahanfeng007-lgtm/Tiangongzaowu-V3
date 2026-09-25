"""Production L5 consolidation of bounded, native execution observations.

The model does not vote on stability. Copies share task/input lineage; L4
hypotheses, recalled memories and autonomous probes cannot supply a quorum.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
import sys

from contracts.canonical import canonical_sha256
from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.cognition_statement import CognitionValue
from contracts.world_understanding._base import WorldRecordRef
from .consolidator import CognitionProposal
from .facade import WorldCognitionFacade
from .l5 import to_l5_view


@lru_cache(maxsize=1)
def execution_condition():
    """Actual installed executor bytes, interpreter and dictionary; no model fields."""
    from capability_dictionary import load_dictionary
    from omni_body_skill.tools import omni_body_tool, sandbox_runtime, portable_text
    return canonical_sha256({"dictionary": load_dictionary().sha256,
        "python": sys.version, "platform": sys.platform,
        "executor": [hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                     for module in (omni_body_tool, sandbox_runtime, portable_text)]})


def observation_binding(invocation, result, raw_result=None):
    """Called only after native tool dispatch, not accepted from result payloads."""
    from capability_dictionary import load_dictionary
    if not isinstance(invocation, dict) or invocation.get("action") not in load_dictionary().tools:
        return None
    action = invocation["action"]
    target = str(invocation.get("target") or "")
    binding = {"schema": "tiangong.native-observation-binding.v1", "action": action,
        "target": target[:4096], "input_sha256": canonical_sha256(invocation),
        "condition_sha256": execution_condition(),
        "action_sha256": canonical_sha256(load_dictionary().tools[action]),
        "result_sha256": canonical_sha256(result)}
    if action in {"file.hash", "file.read"} and result.get("ok") is True and isinstance(raw_result, dict):
        # Read native result wrappers only. File contents are never parsed.
        pending = [raw_result]
        for row in pending:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            sha = row.get("sha256") or evidence.get("sha256")
            path = row.get("path") or evidence.get("path")
            if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) and isinstance(path, str):
                binding.update(file_sha256=sha, file_path=path)
                break
            if len(pending) < 16:
                pending.extend(row[key] for key in ("result", "data", "output", "actual") if isinstance(row.get(key), dict))
    return binding


class RuntimeCognitionConsolidation:
    def __init__(self, facade: WorldCognitionFacade):
        self.facade = facade

    def observe(self, envelope):
        payload = envelope.payload_inline or {}
        binding = payload.get("observation_binding")
        if (envelope.source_kind != "TOOL_RESULT" or envelope.producer_ref != "v3.tool_result_contract"
                or payload.get("schema") != "tiangong.v3.tool_result.v1" or type(payload.get("ok")) is not bool
                or payload.get("source_inquiry_id") or not envelope.run_id or not envelope.request_id
                or not isinstance(binding, dict) or binding.get("schema") != "tiangong.native-observation-binding.v1"
                or binding.get("condition_sha256") != execution_condition()):
            return None
        from capability_dictionary import load_dictionary
        action = binding.get("action")
        if action not in load_dictionary().tools or binding.get("action_sha256") != canonical_sha256(load_dictionary().tools[action]):
            return None
        scope = envelope.scope_hint
        at = envelope.source_time.recorded_at_ms
        source = CognitionSourceRef(source_kind="fact_execution", object_id=envelope.envelope_id,
                                    object_revision=1, sha256=envelope.dedup_key)
        # Either a shared request or a shared input collapses into ONE group.
        roots = tuple(sorted({canonical_sha256({"task": envelope.request_id}),
                              canonical_sha256({"input": binding.get("file_sha256") or binding["input_sha256"]})}))
        material = dict(life_id=scope.life_id, domain="software", world_scope_hash=scope.world_scope_hash,
            principal_scope_hash=scope.principal_scope_hash, privacy_scope=scope.privacy_scope,
            source_ref=source, evidence_class="execution_verified", source_credibility_milli=900,
            authority_ceiling_milli=900, provenance_integrity_milli=1000, observation_mode="positive",
            observation=json.dumps({"action": action, "ok": payload["ok"], "result_sha256": binding["result_sha256"]}),
            coverage_milli=1000, search_scope_hash=None, independence_group_hash=canonical_sha256(roots),
            lineage_root_hashes=roots, derived_from_evidence_ids=(), ancestor_cognition_ids=(),
            content_object_id=envelope.envelope_id, content_sha256=envelope.payload_sha256,
            extractor_kind="direct_tool", observed_at_ms=at, valid_from_ms=at,
            valid_until_ms=at + 7 * 24 * 3600_000, volatility_class="long")
        evidence = CognitionEvidence(evidence_id=derive_cognition_evidence_id(**material),
            evidence_sha256="0"*64, **material).with_computed_evidence_sha256()
        self.facade.put_evidence(evidence)
        proposal = CognitionProposal(life_id=scope.life_id, domain="software", world_scope_hash=scope.world_scope_hash,
            principal_scope_hash=scope.principal_scope_hash, privacy_scope=scope.privacy_scope,
            claim_kind="capability_fact", subject_ref="action:" + action,
            predicate="runtime.independent_execution_observations",
            value=CognitionValue(kind="string", string_value=f"{action}: recent independent executions succeeded under the bound host; content quality and future success are unproven"),
            condition_object_id="runtime.execution-condition", condition_sha256=execution_condition())
        return self.facade.consolidate(proposal,
            support_evidence_ids=(evidence.evidence_id,) if payload["ok"] else (),
            counterevidence_ids=() if payload["ok"] else (evidence.evidence_id,), now_ms=at)

    def eligible(self, scope, now_ms):
        return tuple(row.statement for row in self.facade.retrieve(
            life_id=scope.life_id, domain="software", world_scope_hash=scope.world_scope_hash,
            principal_scope_hash=scope.principal_scope_hash, allowed_privacy_scopes=(scope.privacy_scope,),
            now_ms=now_ms, max_items=256) if row.statement.condition_sha256 == execution_condition())

    def materialize(self, data, previous, envelope):
        self.observe(envelope)
        return replace(data, stable_cognition=tuple(to_l5_view(statement, scope=data.frame.scope)
            for statement in self.eligible(data.frame.scope, data.materialized_at_ms)),
            replace_previous_cognition=True)

    def context_candidates(self, query, snapshot):
        from world_understanding.context_output.enrichment import ContextProjectionCandidate
        from world_understanding.context_output.runtime_facts import _data
        if snapshot.state_ref != query.basis_world_state_ref or snapshot.state.scope != query.scope:
            return ()
        refs = {r.sort_key() for r in (() if snapshot.cognition_heads is None else snapshot.cognition_heads.refs)}
        candidates = []
        for statement in self.eligible(query.scope, query.created_at_ms):
            ref = WorldRecordRef(record_type="world_cognition", record_id=statement.cognition_id,
                revision=statement.revision, sha256=statement.statement_sha256)
            if ref.sort_key() in refs:
                candidates.append(ContextProjectionCandidate(ref, "stable_cognition", _data({
                    "claim": statement.value.string_value, "level": statement.stability_level,
                    "context_only": True, "condition_sha256": statement.condition_sha256}), 900, 750, 900))
        return tuple(candidates)
