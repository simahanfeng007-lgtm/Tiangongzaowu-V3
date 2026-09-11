"""P10 machine experience -> the existing P5 policy and MemoryCoordinator.

ID-only production entry. No model verdict, source pin, parent list or aggregate
is accepted from a request. World owns current sources; Gateway owns execution;
Life's signed journal owns the event; the existing Memory transaction owns CAS.
"""
from __future__ import annotations

import json

from contracts import canonical_json_bytes, canonical_sha256
from life_service.memory_coordinator import MemoryCoordinator, l1_derivation_id
from life_service.store import LifeShadowStoreError
from world_understanding.capability_composition.capability_experience_api import (
    apply_capability_experience_observation, build_capability_experience_memory_intent,
    evaluate_capability_experience_admission, exact_source_hashes,
)
from world_understanding.capability_composition.capability_experience_attribution import (
    AttributionTraceV1, completion_evidence_from_decision, evaluate_attribution_integrity,
)
from world_understanding.capability_composition.capability_experience_memory import build_memory_coordinator_disposition
from world_understanding.capability_composition.capability_experience_policy import CapabilityExperienceAggregateStateV1, CapabilityExperienceObservationV1
from .method_source_run_binding import MethodRunSourceResolver
from .method_source_publication import MethodPublicationResolver


class ExperienceWritebackError(ValueError):
    pass


def _reject(code):
    raise ExperienceWritebackError("learning_experience." + code)


def _live_parent(store, derivation, *, life_id, principal_ref, privacy_scope, now_ms):
    if derivation is None or not derivation.has_valid_derivation_sha256():
        _reject("parent_missing_or_corrupt")
    assertion = store.get_memory_assertion(derivation.memory_id, derivation.memory_revision)
    if (assertion is None or not assertion.has_valid_assertion_sha256()
            or store.get_latest_memory_assertion(derivation.memory_id) != assertion
            or assertion.lifecycle_status != "active"
            or not store.is_derivation_active(derivation.derivation_id)
            or derivation.memory_assertion_sha256 != assertion.assertion_sha256
            or (derivation.life_id, derivation.principal_ref, derivation.privacy_scope)
                != (life_id, principal_ref, privacy_scope)
            or (assertion.expires_at_ms is not None and assertion.expires_at_ms <= now_ms)):
        _reject("parent_inactive_or_cross_scope")
    raw = store.read_protected_payload(assertion.protected_payload_id)
    return assertion, raw


def commit_observation_via_memory(coordinator: MemoryCoordinator, observation: CapabilityExperienceObservationV1,
                                  *, source_event_sha256: str, evidence_sha256: str,
                                  current_sources: tuple, now_ms: int) -> dict:
    """Internal trusted-caller seam. The production entry below supplies all inputs.

    L1 observation is durable before L3. An interrupted L3 attempt leaves only
    factual L1 evidence, not a success counter. L3/head/payload/outbox commit in
    the original Store transaction. Three bounded CAS retries never overwrite
    a winner; after exhaustion the original worker callback can retry.
    """
    if type(coordinator) is not MemoryCoordinator or type(observation) is not CapabilityExperienceObservationV1:
        _reject("typed_memory_and_observation_required")
    observation = CapabilityExperienceObservationV1.model_validate_json(observation.model_dump_json())
    if (not observation.has_valid_sha256() or type(now_ms) is not int or now_ms < observation.observed_at_ms
            or any(type(s) is not str or len(s) != 64 or any(c not in "0123456789abcdef" for c in s)
                   for s in (source_event_sha256, evidence_sha256))):
        _reject("observation_identity_or_clock")
    if exact_source_hashes(current_sources) != exact_source_hashes((*observation.plan.method_source_refs, *observation.plan.action_source_refs)):
        _reject("source_revalidation_required")
    attribution = evaluate_attribution_integrity(observation.plan, observation.trace,
        expected_principal_scope_hash=observation.principal_scope_hash,
        expected_privacy_scope_hash=observation.privacy_scope_hash, checked_at_ms=observation.attribution.checked_at_ms)
    if attribution != observation.attribution:
        _reject("attribution_not_reconstructed")
    admission = evaluate_capability_experience_admission(observation,
        expected_principal_scope_hash=observation.principal_scope_hash,
        expected_privacy_scope_hash=observation.privacy_scope_hash, decided_at_ms=observation.observed_at_ms)
    if not (admission.positive_allowed or admission.negative_allowed):
        return {"status": "EXPERIENCE_NOT_ADMITTED", "memory_write_performed": False,
                "admission": admission.decision, "reason_codes": list(admission.reason_codes)}
    seed, _ = apply_capability_experience_observation(None, observation, admission)
    event_id = "lev_" + source_event_sha256
    memory_id = "mem_" + canonical_sha256({"domain": "tiangong.machine-experience-parent.v1", "life_id": observation.life_id, "event_id": event_id})
    parent_plaintext = canonical_json_bytes({"schema": "tiangong.machine-capability-observation.v1",
        "source_event_sha256": source_event_sha256, "evidence_sha256": evidence_sha256,
        "observation": observation.model_dump(mode="json"), "context_section": "DATA"})
    coordinator.commit_contract_assertion(plaintext=parent_plaintext, memory_id=memory_id,
        life_id=observation.life_id, principal_ref=observation.principal_ref,
        assertion_kind="observation", epistemic_status="observed", lifecycle_status="active",
        privacy_scope=observation.privacy_scope, retention_class="ACTIVE_WORKING",
        source_event_ids=(event_id,), valid_from_ms=observation.observed_at_ms,
        created_at_ms=observation.observed_at_ms)
    store = coordinator.store
    parent = store.get_memory_derivation(l1_derivation_id(life_id=observation.life_id, source_event_id=event_id))
    scope = dict(life_id=observation.life_id, principal_ref=observation.principal_ref,
                 privacy_scope=observation.privacy_scope, now_ms=now_ms)
    _, stored_parent_bytes = _live_parent(store, parent, **scope)
    if stored_parent_bytes != parent_plaintext or parent.layer != "L1_STREAM" or parent.lineage_root_event_ids != (event_id,):
        _reject("observation_parent_rebound")
    seed_intent, _ = build_capability_experience_memory_intent(seed,
        parent_derivation_ids=(parent.derivation_id,), created_at_ms=now_ms)
    for attempt in range(3):
        prior_head = store.get_active_memory_head(life_id=observation.life_id,
            principal_ref=observation.principal_ref, claim_key=seed_intent.claim_key, layer="L3_EXPERIENCE")
        prior = None
        parents = (parent,)
        if prior_head is not None:
            assertion, raw = _live_parent(store, prior_head, **scope)
            try:
                payload = json.loads(raw)
                prior = CapabilityExperienceAggregateStateV1.model_validate_json(canonical_json_bytes(payload["experience_state"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise ExperienceWritebackError("learning_experience.prior_payload_invalid") from exc
            if (canonical_json_bytes(payload) != raw or not prior.has_valid_sha256()
                    or payload.get("schema") != "tiangong.capability-experience-memory-payload.v1"
                    or prior_head.semantic_domain != "CAPABILITY_KNOWLEDGE"
                    or any(payload.get(k) is not False for k in ("instruction_authority", "world_authority", "may_authorize", "may_execute"))
                    or (prior.life_id, prior.principal_ref, prior.principal_scope_hash, prior.privacy_scope, prior.privacy_scope_hash)
                       != (observation.life_id, observation.principal_ref, observation.principal_scope_hash, observation.privacy_scope, observation.privacy_scope_hash)):
                _reject("prior_scope_or_hash_invalid")
            # Inspect all ancestors, including this observation on a duplicate.
            pending, seen = [prior_head], set()
            while pending:
                ancestor = pending.pop()
                if ancestor is None or len(seen) >= 4096:
                    _reject("parent_lineage_invalid")
                if ancestor.derivation_id in seen:
                    continue
                seen.add(ancestor.derivation_id)
                _live_parent(store, ancestor, **scope)
                pending.extend(store.get_memory_derivation(p.parent_derivation_id) for p in ancestor.parent_memory_refs)
            if observation.observation_id in prior.observation_ids:
                if event_id not in prior_head.lineage_root_event_ids or parent.derivation_id not in seen:
                    _reject("duplicate_observation_lineage_mismatch")
                return _receipt(prior, prior_head, observation_id=observation.observation_id, created=False, admission=admission.decision)
            if observation.observed_at_ms < prior.last_observed_at_ms:
                _reject("observation_time_regressed")
            parents = tuple(sorted((parent, prior_head), key=lambda p: p.derivation_id))
        state, negative = apply_capability_experience_observation(prior, observation, admission)
        intent, plaintext = build_capability_experience_memory_intent(state,
            parent_derivation_ids=tuple(p.derivation_id for p in parents), negative_evidence=negative, created_at_ms=now_ms)
        disposition = build_memory_coordinator_disposition(state, intent, parents)
        try:
            _, derivation, created = coordinator._materialize_promotion(disposition=disposition, parents=parents,
                principal_ref=observation.principal_ref, privacy_scope=observation.privacy_scope, plaintext=plaintext,
                created_at_ms=disposition.created_at_ms, policy_version=intent.policy_version,
                head_guard=(intent.claim_key, "L3_EXPERIENCE", None if prior_head is None else prior_head.derivation_sha256))
        except (LifeShadowStoreError, RuntimeError) as exc:
            if str(exc) != "memory promotion head changed":
                raise
            if attempt == 2:
                _reject("memory_head_contention_retry_required")
            continue
        if derivation.layer != "L3_EXPERIENCE" or derivation.world_candidate_eligible:
            _reject("memory_materialization_domain_mismatch")
        return _receipt(state, derivation, observation_id=observation.observation_id, created=created, admission=admission.decision)
    _reject("memory_head_contention_retry_required")


def _receipt(state, derivation, *, observation_id, created, admission):
    return {"status": "CAPABILITY_EXPERIENCE_COMMITTED", "observation_id": observation_id, "memory_id": derivation.memory_id,
        "derivation_id": derivation.derivation_id, "state_sha256": state.state_sha256,
        "admission": admission, "success_count": state.experience.success_count,
        "failure_count": state.experience.failure_count, "lifecycle": state.experience.lifecycle,
        "memory_write_performed": created, "l3_write_performed": created,
        "parent_observation_preserved": True, "duplicate": not created,
        "context_section": "DATA", "may_authorize": False, "may_execute": False}


def _current_sources(runtime, executable, *, life_id, principal_scope_hash):
    """Recheck the SAME P6 World frame, not caller-owned source hashes.

    Caller holds the canonical WorldStore retention lock until the Memory CAS
    finishes. Tool descriptor hashes bind the compiler-derived source refs;
    registry/Manifest and per-source dependencies must be current and non-stale.
    Native Methods additionally reopen the existing signed P9 archive.
    """
    binding = runtime.store._method_source_resolver
    if type(binding) is not MethodRunSourceResolver or binding.gateway is not runtime.store:
        _reject("world_source_authority_required")
    store = binding.world.store
    old = store.get(executable.legacy_plan.world_state_ref)
    if old is None or old.state.state_sha256 != executable.world_state_sha256:
        _reject("bound_world_unavailable")
    scope = old.state.scope
    if (scope.life_id != life_id or scope.principal_scope_hash != principal_scope_hash
            or {b.key:b.value for b in scope.scope_bindings}.get("workspace_id") != executable.workspace.workspace_id):
        _reject("world_scope_mismatch")
    current = store.current(life_id=scope.life_id, world_scope_hash=scope.world_scope_hash,
        principal_scope_hash=scope.principal_scope_hash, frame_id=old.frame_id)
    if current is None or current.state.scope != scope or not current.state.has_valid_hash():
        _reject("current_world_unavailable")
    store._validate_snapshot(current)
    stale = {(r.record_type, r.record_id) for r in current.state.stale_refs}
    coordinator = runtime.orchestration._composition_steps
    if (not coordinator._registry.has_valid_sha256() or not coordinator._manifest.has_valid_sha256()
            or coordinator._registry.registry_sha256 != executable.action_registry_sha256
            or coordinator._registry.source_manifest_sha256 != executable.capability_manifest_sha256):
        _reject("runtime_registry_changed")
    refs = (*executable.legacy_plan.method_source_refs, *executable.legacy_plan.action_source_refs)
    for source in refs:
        is_tool = source.source_kind == "TOOL_ACTION"
        kind, key = ("ToolCapability", "action_id") if is_tool else ("SkillMethod", "method_id")
        matches = []
        for entity in current.entities:
            if entity.entity_type != kind:
                continue
            attrs = {a.key: a.value.string_value for a in entity.attributes}
            if attrs.get(key) == source.semantic_id:
                matches.append((entity, attrs))
        if len(matches) != 1:
            _reject("current_source_missing_or_ambiguous")
        entity, attrs = matches[0]
        if (not entity.has_valid_hash() or entity.lifecycle != "ACTIVE" or entity.epistemic_state != "CURRENT"
                or ("world_entity", entity.entity_id) in stale
                or attrs.get("descriptor_sha256") != source.descriptor_sha256
                or attrs.get("action_version" if is_tool else "version") != source.version):
            _reject("source_revalidation_required")
        entity_ref = next((r for r in current.entity_heads.refs if r.record_id == entity.entity_id), None)
        dependency = None if entity_ref is None else current.dependencies.binding_for(entity_ref)
        if dependency is None or "source:" + source.source_sha256 not in dependency.source_keys:
            _reject("source_dependency_missing")
        if is_tool:
            if (attrs.get("availability") != "AVAILABLE"
                    or attrs.get("source_manifest_sha256") != executable.capability_manifest_sha256
                    or source.manifest_sha256 != executable.capability_manifest_sha256
                    or "action-registry:" + executable.action_registry_sha256 not in dependency.source_keys):
                _reject("source_registry_mismatch")
        elif attrs.get("source_sha256") != source.source_sha256:
            _reject("method_source_changed")
    natives = tuple(s for s in executable.legacy_plan.method_source_refs if any(p.endswith(".json") for p in s.source_files))
    if natives:
        resolver = binding.world._method_revision_resolver
        if type(resolver) is not MethodPublicationResolver:
            _reject("method_archive_authority_required")
        methods = resolver.load(current)
        available = {p.method_id: p.source_ref for p in methods.primitives}
        if any(available.get(s.semantic_id) != s for s in natives):
            _reject("method_archive_source_changed")
    return tuple(refs)


def _machine_observation(runtime, executable, evidence, *, life_id):
    """Observe only the closed registered P7 chain. Unobserved paths defer.

    `unknown_*` are about this machine ledger, not omniscient claims about a
    hostile host. Positive eligibility is limited to its existing A0 read/verify
    contract: external write/shell/python paths cannot obtain an invented PASS.
    All stored content is structured references; no reply/prompt is promoted.
    """
    gateway = runtime.store
    plan = executable.legacy_plan
    coordinator = runtime.orchestration._composition_steps
    if evidence["blockers"] != sorted(["P5_PROVENANCE_AND_MEMORY_ADMISSION_REQUIRED",
            "CURRENT_SOURCE_REVALIDATION_REQUIRED", "MEMORY_PARENT_LINEAGE_AND_CAS_REQUIRED"]):
        _reject("sealed_completion_and_p19_required")
    readiness = gateway.get_latest_verification_readiness(request_id=plan.request_id,
        run_id=plan.run_id, generation=plan.generation, require_authoritative=True)
    decisions = [r.decision for r in gateway.list_completion_decisions(plan.request_id,
        run_id=plan.run_id, generation=plan.generation) if r.decision.decision_sha256 == evidence["completion_sha256"]]
    if len(decisions) != 1 or readiness is None or not readiness.has_valid_identity():
        _reject("terminal_machine_binding_changed")
    decision = decisions[0]
    if (not readiness.verification_ready or readiness.required_entry_count < 1
            or readiness.satisfied_entry_count != readiness.required_entry_count
            or decision.verification_readiness_sha256 != readiness.readiness_sha256
            or executable.verification_plan_sha256 != readiness.verification_plan_sha256):
        _reject("p19_not_exact_and_complete")
    for record_id in readiness.supporting_verification_record_ids:
        record = gateway.get_verification_record(record_id)
        if (record is None or not record.has_valid_identity() or record.model_generated
                or record.status != "PASS"
                or (record.request_id, record.run_id, record.generation) != (plan.request_id, plan.run_id, plan.generation)):
            _reject("verification_record_untrusted")
    if not readiness.supporting_verification_record_ids:
        _reject("verification_record_missing")
    final = coordinator.finalize_plan(executable)
    expected_effects = {final.parent_effect_id, *final.lineage_effect_ids}
    executions = [e for e in gateway.list_effects_for_request(plan.request_id, run_id=plan.run_id, generation=plan.generation)
                  if e.claim.effect_kind == "execution"]
    if {e.claim.effect_id for e in executions} != expected_effects or any(e.state != "SUCCEEDED" for e in executions):
        _reject("execution_chain_not_closed")
    permissions = {p.action_id:p for p in coordinator._registry.permissions}
    for step in executable.step_bindings:
        permission = permissions.get(step.action_id)
        if (permission is None or not permission.has_valid_sha256() or permission.effective_risk != "A0"
                or permission.allow_shell or permission.allow_python or permission.effect not in {"read", "verify"}
                or not set(permission.allowed_side_effects).issubset({"none", "read"})):
            _reject("unobserved_side_effect_provenance")
    records = gateway.list_composition_authorizations_for_plan(executable.executable_plan_id, current_only=False)
    if {r.request.prebound_effect_id for r in records} != set(final.lineage_effect_ids):
        _reject("authorization_lineage_incomplete")
    for record in records:
        if record.request.principal_scope_hash != plan.principal_scope_hash:
            _reject("authorization_principal_changed")
    # Exact compilation/context hashes and Fact bytes were checked by the
    # original registration and finalizer. No caller-supplied provenance flags.
    at = max(evidence["completed_at_ms"], readiness.evaluated_at_ms, executable.sealed_at_ms)
    trace = AttributionTraceV1(request_id=plan.request_id, run_id=plan.run_id, generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash, privacy_scope_hash=canonical_sha256("private"),
        composition_plan_sha256=plan.plan_sha256, completion=completion_evidence_from_decision(decision),
        active_verification_plan_sha256=readiness.verification_plan_sha256,
        verification_record_refs=tuple(sorted(readiness.supporting_verification_record_ids)),
        terminal_effect_ids=tuple(sorted(expected_effects)), terminal_fact_ids=tuple(sorted(evidence["fact_refs"])),
        terminal_fact_hashes=tuple(sorted(evidence["fact_refs"].values())),
        observed_method_source_refs=tuple(sorted(plan.method_source_refs, key=lambda s:(s.source_kind,s.semantic_id,s.version,s.source_sha256,s.descriptor_sha256))),
        observed_action_source_refs=tuple(sorted(plan.action_source_refs, key=lambda s:(s.source_kind,s.semantic_id,s.version,s.source_sha256,s.descriptor_sha256))),
        has_acceptance_obligations=True, active_verification_plan_complete=True,
        effect_fact_lineage_complete=True, source_refs_complete=True, source_revisions_continuous=True,
        request_scope_continuous=True, human_takeover=False, alternate_execution_chain=False,
        unknown_external_overwrite=False, unknown_side_effects=False, unresolved_reconciliation=False,
        secret_or_credential_present=False, prompt_injection_present=bool(plan.information_flow_findings),
        context_identity_truncated=False, collected_at_ms=at, trace_sha256="0"*64).with_computed_sha256()
    attribution = evaluate_attribution_integrity(plan, trace, expected_principal_scope_hash=plan.principal_scope_hash,
        expected_privacy_scope_hash=canonical_sha256("private"), checked_at_ms=at)
    return CapabilityExperienceObservationV1(
        observation_id="capobs_" + canonical_sha256({"domain":"tiangong.machine-capability-observation.v1",
            "request_id":plan.request_id,"run_id":plan.run_id,"generation":plan.generation,
            "executable_plan_sha256":executable.executable_plan_sha256}),
        life_id=life_id, principal_ref=life_id, principal_scope_hash=plan.principal_scope_hash,
        privacy_scope="private", privacy_scope_hash=canonical_sha256("private"),
        goal_class="goal:"+plan.goal_fingerprint, environment_class=plan.environment_class,
        scene_fingerprint=canonical_sha256({"world_state_sha256":plan.world_state_sha256, "goal":plan.goal_fingerprint}),
        context_fingerprint_sha256=plan.context_fingerprint_sha256, composition_topology_sha256=plan.dependency_graph_sha256,
        plan=plan, trace=trace, attribution=attribution, outcome="SUCCESS",
        # Coverage of the required machine predicates, NOT a model quality score.
        quality_milli=1000*readiness.satisfied_entry_count//readiness.required_entry_count,
        observed_at_ms=at, observation_sha256="0"*64).with_computed_sha256()


def commit_terminal_experience(runtime, execution: dict) -> dict:
    """Called by the already installed worker callback after execution/audit.

    Lock order Life -> Gateway -> WorldStore. No WorldRuntime lock or callback
    is acquired while these are held. Gateway SQLite excludes new evidence on
    another connection; WorldStore excludes source cutovers until Memory CAS.
    No recovery thread or new ledger is created. Explicit callback retry closes
    an interrupted L1/L3/audit sequence without re-dispatching any tool.
    """
    from .learning_output_binding import collect_composition_learning_evidence
    life = runtime.life_service
    with life._lock, runtime.store._lock, runtime.store._write_transaction():
        life_id = execution["life_id"]
        if life._active()["life_id"] != life_id:
            _reject("active_life_changed")
        identity = life._world_identity_provider(life_id) if callable(life._world_identity_provider) else None
        executable_record = runtime.store.get_executable_composition_plan_for_request(execution["request_id"],
            run_id=execution["run_id"], generation=execution["generation"])
        if executable_record is None:
            _reject("registered_plan_missing")
        executable = executable_record.executable_plan
        if (not isinstance(identity, dict) or identity.get("life_id") != life_id
                or identity.get("principal_scope_hash") != executable.principal_scope_hash
                or identity.get("workspace_id") != executable.workspace.workspace_id
                or execution["status"] != "completed"):
            _reject("configured_identity_or_terminal_mismatch")
        evidence = collect_composition_learning_evidence(runtime.store, runtime.orchestration._composition_steps,
            request_id=execution["request_id"], run_id=execution["run_id"], generation=execution["generation"])
        event = life.record_learning_execution_evidence(life_id, evidence)
        binding = runtime.store._method_source_resolver
        if type(binding) is not MethodRunSourceResolver:
            _reject("world_source_authority_required")
        with binding.world.store.retention_transaction():
            sources = _current_sources(runtime, executable, life_id=life_id, principal_scope_hash=executable.principal_scope_hash)
            observation = _machine_observation(runtime, executable, evidence, life_id=life_id)
            import time
            result = commit_observation_via_memory(life._memory_coordinator(), observation,
                source_event_sha256=event["event_sha256"], evidence_sha256=evidence["evidence_sha256"],
                current_sources=sources, now_ms=max(time.time_ns()//1_000_000, observation.observed_at_ms))
        # A journal receipt is secondary. Failure cannot roll back committed
        # Memory; retry resolves the same aggregate/observation before auditing.
        if result["status"] != "CAPABILITY_EXPERIENCE_COMMITTED":
            return result
        receipt = {k:v for k,v in result.items() if k not in {"memory_write_performed", "l3_write_performed", "duplicate"}}
        audit_key = "learning.experience:" + observation.observation_id
        try:
            journal = life.system.journal.event_by_idempotency_key(life_id, audit_key)
            if journal is not None:
                # A later observation may have advanced this same aggregate.
                # Preserve the ORIGINAL audited head for the old observation;
                # do not rewrite that receipt with today's aggregate counts.
                payload = journal["payload"]
                if (journal["event_type"] != "learning.experience_committed"
                        or payload["machine_event_id"] != event["event_id"]
                        or payload["receipt"]["observation_id"] != observation.observation_id):
                    _reject("experience_audit_identity_rebound")
            else:
                journal = life.system.journal.append(life_id, "learning.experience_committed",
                    {"machine_event_id":event["event_id"], "receipt":receipt}, actor="gateway_learning",
                    idempotency_key=audit_key)
            result["audit_event_id"] = journal["event_id"]
            result["audit_pending"] = False
        except Exception:
            result["audit_pending"] = True
        return result
