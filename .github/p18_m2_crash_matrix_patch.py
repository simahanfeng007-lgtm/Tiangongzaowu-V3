from pathlib import Path

path = Path('src/total_gateway/regenerative_provider.py')
text = path.read_text(encoding='utf-8')
start = text.index('    def _recover(self, payload: Mapping[str, Any]) -> dict[str, Any]:\n')
end = text.index('    def _verify_completion(self, payload: Mapping[str, Any]) -> dict[str, Any]:\n', start)
replacement = r'''    def _recover(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        now_ms = _integer(payload.get("now_ms"), label="now_ms")

        def execution_events(effect_id: str) -> list[Any]:
            return [
                event for event in self._store.list_execution_events(
                    identity.request_id, run_id=identity.run_id, generation=identity.generation
                ) if event.effect_id == effect_id
            ]

        def result_for(effect_id: str, status: str, reason: str) -> EffectResult:
            evidence = canonical_sha256({
                "domain": "tiangong.gateway.crash-window-effect-recovery.v1",
                "effect_id": effect_id,
                "status": status,
                "reason": reason,
            })
            return EffectResult(
                result_id="rlt_" + canonical_sha256({
                    "effect_id": effect_id, "status": status, "recovery": reason
                }),
                effect_id=effect_id,
                status=status,
                fact_id="fact_" + canonical_sha256({"effect_id": effect_id, "evidence": evidence}),
                result_object_id=None,
                result_object_sha256=None,
                evidence_sha256=evidence,
                error_code=None if status == "SUCCEEDED" else reason,
                observed_at_ms=now_ms,
                model_generated=False,
                result_sha256="0" * 64,
            ).with_computed_sha256()

        # Repair cross-table crash windows from the canonical physical Effect
        # ledger back into the append-only execution ledger.  This does not
        # guess whether a STARTED action applied: it deliberately marks it
        # AMBIGUOUS.  A CLAIMED-only effect is proven not dispatched.
        for record in self._store.list_effects_for_request(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        ):
            effect_id = record.claim.effect_id
            events = execution_events(effect_id)
            terminal = next((
                event for event in reversed(events)
                if event.event_type in {"step.committed", "step.failed", "step.ambiguous"}
            ), None)
            if terminal is not None:
                continue
            dispatched = next((
                event for event in reversed(events) if event.event_type == "step.dispatched"
            ), None)
            prepared = next((
                event for event in reversed(events) if event.event_type == "step.prepared"
            ), None)
            source = dispatched or prepared

            if record.state == "CLAIMED":
                # No STARTED fence exists, therefore this physical attempt was
                # durably prepared but never dispatched.  Finalize the stale
                # attempt as non-applied so it cannot poison Completion Proof.
                self._store.complete_effect(result_for(
                    effect_id, "FAILED_FINAL", "process_restart_before_dispatch"
                ))
                if source and source.logical_effect_id and source.attempt_id and source.step_id:
                    self._store.append_execution_event(
                        event_key=f"step.failed:{source.step_id}:{source.attempt_id}:restart-before-dispatch",
                        request_id=identity.request_id, run_id=identity.run_id,
                        generation=identity.generation, epoch_index=source.epoch_index,
                        event_type="step.failed", created_at_ms=now_ms,
                        payload={
                            "effect_state": "FAILED_FINAL",
                            "reason": "process_restart_before_dispatch",
                            "proven_not_applied": True,
                        },
                        logical_effect_id=source.logical_effect_id,
                        attempt_id=source.attempt_id,
                        step_id=source.step_id,
                        effect_id=effect_id,
                        causal_parent_event_id=source.event_id,
                    )
                continue

            if record.state == "SIDE_EFFECT_STARTED":
                if source is None or not source.logical_effect_id or not source.attempt_id or not source.step_id:
                    raise StoreCorruptionError(
                        "started effect has no prepared/dispatch event for crash recovery"
                    )
                if dispatched is None:
                    dispatched, _ = self._store.append_execution_event(
                        event_key=f"step.dispatched:{source.step_id}:{source.attempt_id}:recovered",
                        request_id=identity.request_id, run_id=identity.run_id,
                        generation=identity.generation, epoch_index=source.epoch_index,
                        event_type="step.dispatched", created_at_ms=now_ms,
                        payload={
                            "effect_state": "SIDE_EFFECT_STARTED",
                            "dispatch_boundary": "reconstructed_from_effect_started_fence",
                        },
                        logical_effect_id=source.logical_effect_id,
                        attempt_id=source.attempt_id,
                        step_id=source.step_id,
                        effect_id=effect_id,
                        causal_parent_event_id=source.event_id,
                    )
                    source = dispatched
                result = result_for(effect_id, "AMBIGUOUS", "process_restart_after_dispatch")
                self._store.complete_effect(result)
                self._store.append_execution_event(
                    event_key=f"step.ambiguous:{source.step_id}:{source.attempt_id}:restart",
                    request_id=identity.request_id, run_id=identity.run_id,
                    generation=identity.generation, epoch_index=source.epoch_index,
                    event_type="step.ambiguous", created_at_ms=now_ms,
                    payload={
                        "effect_state": "AMBIGUOUS",
                        "reason": "process_restart_after_dispatch",
                        "result_sha256": result.result_sha256,
                    },
                    logical_effect_id=source.logical_effect_id,
                    attempt_id=source.attempt_id,
                    step_id=source.step_id,
                    effect_id=effect_id,
                    causal_parent_event_id=source.event_id,
                )
                continue

            if record.state in {"SUCCEEDED", "AMBIGUOUS", "FAILED_FINAL"}:
                if source is None or not source.logical_effect_id or not source.attempt_id or not source.step_id:
                    raise StoreCorruptionError(
                        "terminal effect has no prepared/dispatch event for execution-ledger healing"
                    )
                event_type = {
                    "SUCCEEDED": "step.committed",
                    "AMBIGUOUS": "step.ambiguous",
                    "FAILED_FINAL": "step.failed",
                }[record.state]
                self._store.append_execution_event(
                    event_key=f"{event_type}:{source.step_id}:{source.attempt_id}:recovered",
                    request_id=identity.request_id, run_id=identity.run_id,
                    generation=identity.generation, epoch_index=source.epoch_index,
                    event_type=event_type, created_at_ms=now_ms,
                    payload={
                        "effect_state": record.state,
                        "reason": "healed_from_canonical_effect_ledger",
                        "recovered_terminal_event": True,
                    },
                    logical_effect_id=source.logical_effect_id,
                    attempt_id=source.attempt_id,
                    step_id=source.step_id,
                    effect_id=effect_id,
                    causal_parent_event_id=source.event_id,
                )

        recovered = self._store.recover_regenerative_execution(
            identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            recovered_at_ms=now_ms,
        )
        if not recovered.get("recoverable"):
            return {"recoverable": False, "reason": recovered.get("reason")}
        frontier: ExecutionFrontier = recovered["frontier"]
        event, _ = self._store.append_execution_event(
            event_key=f"run.resumed:{frontier.frontier_version}:{frontier.global_step}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=frontier.epoch_index,
            event_type="run.resumed",
            created_at_ms=now_ms,
            payload={
                "checkpoint_id": recovered["checkpoint"].checkpoint_id,
                "used_previous_checkpoint": bool(recovered["used_previous_checkpoint"]),
                "frontier_hash": frontier.frontier_hash,
                "pending_effect_ids": list(recovered["pending_effect_ids"]),
                "ambiguous_effect_ids": list(recovered["ambiguous_effect_ids"]),
            },
        )
        return {
            "recoverable": True,
            "checkpoint": recovered["checkpoint"].model_dump(mode="json"),
            "frontier": frontier.model_dump(mode="json"),
            "pending_effect_ids": list(recovered["pending_effect_ids"]),
            "ambiguous_effect_ids": list(recovered["ambiguous_effect_ids"]),
            "used_previous_checkpoint": bool(recovered["used_previous_checkpoint"]),
            "ledger_seq": event.ledger_seq,
        }

'''
path.write_text(text[:start] + replacement + text[end:], encoding='utf-8', newline='\n')
