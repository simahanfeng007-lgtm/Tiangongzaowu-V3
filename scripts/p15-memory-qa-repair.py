from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def rewrite(relative: str, transform) -> bool:
    path = ROOT / relative
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if after == before:
        return False
    path.write_text(after, encoding="utf-8", newline="\n")
    return True


def patch_coordinator(text: str) -> str:
    # Real chat explicit-memory attachment must preserve the detector's expiry.
    start = text.index("    def attach_explicit_l4(")
    end = text.index(
        "    # ------------------------------------------------------------------\n"
        "    # Learning -> Memory closure",
        start,
    )
    block = text[start:end]
    if "deadline = expiry_deadline_ms(detection.expiry_kind, created_at_ms)" not in block:
        anchor = (
            "        source_events = assertion.source_event_ids or (source_event_id,)\n"
            "        domain = _semantic_domain_for_assertion_kind(assertion.assertion_kind)\n"
        )
        replacement = (
            "        source_events = assertion.source_event_ids or (source_event_id,)\n"
            "        deadline = expiry_deadline_ms(detection.expiry_kind, created_at_ms)\n"
            "        domain = _semantic_domain_for_assertion_kind(assertion.assertion_kind)\n"
        )
        if anchor not in block:
            raise RuntimeError("attach_explicit_l4 source anchor changed")
        block = block.replace(anchor, replacement, 1)
        expiry_anchor = "            expires_at_ms=None,"
        if expiry_anchor not in block:
            raise RuntimeError("attach_explicit_l4 expiry anchor changed")
        block = block.replace(expiry_anchor, "            expires_at_ms=deadline,", 1)
        text = text[:start] + block + text[end:]

    adapter = '''    def commit_contract_assertion(
        self,
        *,
        plaintext: bytes,
        memory_id: str,
        life_id: str,
        principal_ref: str,
        assertion_kind: str,
        epistemic_status: str,
        lifecycle_status: str,
        privacy_scope: str,
        retention_class: str,
        source_event_ids: tuple[str, ...],
        causal_utility_milli: int = 0,
        user_importance_milli: int = 0,
        verification_strength_milli: int = 0,
        future_dependency_milli: int = 0,
        valid_from_ms: int,
        created_at_ms: int,
        search_terms: tuple[str, ...] = (),
        expires_at_ms: int | None = None,
    ) -> tuple[MemoryAssertionV3, int, bool]:
        """Adapter for legacy projection writes with correction closure.

        Only a newly-active assertion gets an L1 derivation. Lifecycle-only
        revisions never mint a second L1 from the same journal event, which
        prevents a correction event from aliasing the replacement memory's L1.
        Corrected/superseded revisions also resume derivation invalidation on
        every retry so journal reconciliation heals a crash window.
        """

        source_event_id = source_event_ids[0] if source_event_ids else None
        derivation = None
        if source_event_id is not None and lifecycle_status == "active":
            derivation = MemoryDerivationV1(
                derivation_id=l1_derivation_id(
                    life_id=life_id,
                    source_event_id=source_event_id,
                ),
                life_id=life_id,
                memory_id=memory_id,
                memory_revision=1,
                memory_assertion_sha256="0" * 64,
                layer="L1_STREAM",
                semantic_domain=_semantic_domain_for_assertion_kind(
                    assertion_kind
                ),
                origin="LIFE_EVENT",
                principal_ref=principal_ref,
                workspace_ref=None,
                privacy_scope=privacy_scope,
                claim_key="l1:" + source_event_id,
                parent_memory_refs=(),
                source_event_ids=(source_event_id,),
                lineage_root_event_ids=(source_event_id,),
                external_evidence_refs=(),
                promotion_policy_version=L1_POLICY_VERSION,
                promotion_reason_codes=(),
                valid_from_ms=valid_from_ms,
                expires_at_ms=expires_at_ms,
                context_eligible=True,
                learning_eligible=False,
                temperament_eligible=False,
                self_cognition_eligible=False,
                world_candidate_eligible=False,
                created_at_ms=created_at_ms,
                derivation_sha256="0" * 64,
            ).with_computed_derivation_sha256()
        assertion, change_seq, created = self._store.put_live_memory_assertion(
            plaintext,
            memory_id=memory_id,
            life_id=life_id,
            assertion_kind=assertion_kind,
            epistemic_status=epistemic_status,
            lifecycle_status=lifecycle_status,
            privacy_scope=privacy_scope,
            retention_class=retention_class,
            source_event_ids=source_event_ids,
            causal_utility_milli=causal_utility_milli,
            user_importance_milli=user_importance_milli,
            verification_strength_milli=verification_strength_milli,
            future_dependency_milli=future_dependency_milli,
            valid_from_ms=valid_from_ms,
            created_at_ms=created_at_ms,
            search_terms=search_terms,
            expires_at_ms=expires_at_ms,
            derivation=derivation,
        )
        if source_event_id is not None and lifecycle_status == "active":
            self._ensure_l1_derivation(
                assertion=assertion,
                source_event_id=source_event_id,
                principal_ref=principal_ref,
            )
        if lifecycle_status in {"corrected", "superseded"}:
            for target in self._store.list_derivations_for_memory(memory_id):
                if not self._store.is_derivation_active(target.derivation_id):
                    continue
                invalidate_cascade(
                    self._store,
                    derivation_id=target.derivation_id,
                    reason=lifecycle_status,
                    invalidated_at_ms=created_at_ms,
                    source_trigger_ref=source_event_id,
                )
        return assertion, change_seq, created

'''
    if "Adapter for legacy projection writes with correction closure." not in text:
        pattern = re.compile(
            r"(?ms)^    def commit_contract_assertion\(.*?"
            r"^        return assertion, change_seq, created\n\n"
            r"(?=    def _ensure_l1_derivation)"
        )
        text, count = pattern.subn(adapter, text, count=1)
        if count != 1:
            raise RuntimeError("commit_contract_assertion replacement failed")

    correction = '''    def correct_claim(
        self,
        *,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        target_derivation_id: str,
        user_message_event_id: str,
        plaintext: bytes,
        created_at_ms: int,
        policy_version: str = L4_POLICY_VERSION,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, tuple, bool]:
        """Correct one active claim with crash-recoverable invalidation.

        The deterministic replacement is committed before the old DAG is
        invalidated. If the process dies after replacement commit, retry finds
        that replacement and resumes the cascade. The replacement derivation
        itself is preserved from the correction cascade; privacy erasure never
        receives that preservation exemption.
        """

        target = self._store.get_memory_derivation(target_derivation_id)
        if target is None:
            raise MemoryCoordinatorError("correction target is missing")
        memory_id = "mem_" + canonical_sha256(
            {
                "domain": "tiangong.life.correction-memory.v1",
                "target_derivation_id": target_derivation_id,
                "user_message_event_id": user_message_event_id,
                "policy_version": policy_version,
            }
        )
        derivation_id = "mdr_" + canonical_sha256(
            {
                "domain": "tiangong.life.correction-derivation.v1",
                "target_derivation_id": target_derivation_id,
                "user_message_event_id": user_message_event_id,
                "policy_version": policy_version,
            }
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("correction assertion is missing")
            invalidations = ()
            if self._store.is_derivation_active(target_derivation_id):
                invalidations = invalidate_cascade(
                    self._store,
                    derivation_id=target_derivation_id,
                    reason="corrected",
                    invalidated_at_ms=created_at_ms,
                    source_trigger_ref=user_message_event_id,
                    preserve_derivation_ids=(existing.derivation_id,),
                )
            return assertion, existing, invalidations, False
        if not self._store.is_derivation_active(target_derivation_id):
            raise MemoryCoordinatorError("correction target is already inactive")
        domain = target.semantic_domain
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=life_id,
            memory_id=memory_id,
            memory_revision=1,
            memory_assertion_sha256="0" * 64,
            layer=target.layer,
            semantic_domain=domain,
            origin="USER_EXPLICIT",
            principal_ref=principal_ref,
            workspace_ref=None,
            privacy_scope=privacy_scope,
            claim_key=target.claim_key,
            parent_memory_refs=(_parent_ref(target),),
            source_event_ids=(user_message_event_id,),
            lineage_root_event_ids=target.lineage_root_event_ids,
            external_evidence_refs=(),
            promotion_policy_version=policy_version,
            promotion_reason_codes=("corrected",),
            valid_from_ms=created_at_ms,
            expires_at_ms=None,
            context_eligible=target.context_eligible,
            learning_eligible=target.learning_eligible,
            temperament_eligible=target.temperament_eligible,
            self_cognition_eligible=target.self_cognition_eligible,
            world_candidate_eligible=target.world_candidate_eligible,
            created_at_ms=created_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        assertion, _seq, created = self._store.put_live_memory_assertion(
            plaintext,
            memory_id=memory_id,
            life_id=life_id,
            assertion_kind=_assertion_kind_for_domain(domain),
            epistemic_status="user_asserted",
            lifecycle_status="active",
            privacy_scope=privacy_scope,
            retention_class="LONG_TERM_MEMORY",
            source_event_ids=(user_message_event_id,),
            verification_strength_milli=750,
            valid_from_ms=created_at_ms,
            created_at_ms=created_at_ms,
            derivation=derivation,
            activate_head=True,
        )
        stored = self._store.get_memory_derivation(derivation_id)
        if stored is None:
            raise MemoryCoordinatorError("correction derivation commit failed")
        invalidations = invalidate_cascade(
            self._store,
            derivation_id=target_derivation_id,
            reason="corrected",
            invalidated_at_ms=created_at_ms,
            source_trigger_ref=user_message_event_id,
            preserve_derivation_ids=(stored.derivation_id,),
        )
        return assertion, stored, invalidations, created

'''
    if "Correct one active claim with crash-recoverable invalidation." not in text:
        pattern = re.compile(
            r"(?ms)^    def correct_claim\(.*?"
            r"^        return assertion, stored, invalidations, created\n\n"
            r"(?=    # ------------------------------------------------------------------\n"
            r"    # Temperament / Self Cognition)"
        )
        text, count = pattern.subn(correction, text, count=1)
        if count != 1:
            raise RuntimeError("correct_claim replacement failed")
    return text


def patch_invalidation(text: str) -> str:
    replacement = '''def invalidate_cascade(
    store: LifeShadowStore,
    *,
    derivation_id: str,
    reason: str = "corrected",
    invalidated_at_ms: int,
    source_trigger_ref: str | None = None,
    preserve_derivation_ids: tuple[str, ...] = (),
) -> tuple[MemoryInvalidationRecord, ...]:
    """Invalidate a derivation DAG and resume safely after partial crashes.

    Already-invalidated nodes remain traversal anchors rather than forcing an
    early no-op. Active descendants that lost all surviving independent parent
    support are therefore still invalidated on retry. Correction replacements
    may be preserved explicitly; privacy erasure deliberately ignores that
    exemption and always traverses the full privacy lineage.
    """

    if reason not in {
        "corrected",
        "superseded",
        "stale",
        "privacy_erasure",
        "invalidated",
    }:
        raise ValueError("invalidation reason is invalid")
    root = store.get_memory_derivation(derivation_id)
    if root is None:
        raise ValueError("invalidation target derivation does not exist")
    preserved = (
        set()
        if reason == "privacy_erasure"
        else set(preserve_derivation_ids)
    )
    invalidated: dict[str, MemoryDerivationV1] = {}
    queue: list[MemoryDerivationV1] = [root]
    while queue:
        current = queue.pop(0)
        if current.derivation_id in invalidated:
            continue
        invalidated[current.derivation_id] = current
        for child in store.list_derivation_children(current.derivation_id):
            if child.derivation_id in invalidated:
                continue
            if child.derivation_id in preserved:
                continue
            if reason != "privacy_erasure" and _still_supported(
                store, child, invalidated_ids=invalidated
            ):
                continue
            queue.append(child)

    records: list[MemoryInvalidationRecord] = []
    invalidated_ids = set(invalidated)
    for current_id, derivation in invalidated.items():
        if current_id in preserved or not store.is_derivation_active(current_id):
            continue
        current_reason = (
            reason if current_id == root.derivation_id else "stale"
        )
        descendants = tuple(
            sorted(
                child_id
                for child_id in invalidated_ids
                if child_id != current_id
            )
        )
        record = MemoryInvalidationRecord(
            invalidation_id=_invalidation_id(
                life_id=derivation.life_id,
                derivation_id=derivation.derivation_id,
                invalidated_at_ms=invalidated_at_ms,
                reason=current_reason,
                source_trigger_ref=source_trigger_ref,
            ),
            life_id=derivation.life_id,
            principal_ref=derivation.principal_ref,
            derivation_id=derivation.derivation_id,
            memory_id=derivation.memory_id,
            memory_revision=derivation.memory_revision,
            assertion_sha256=derivation.memory_assertion_sha256,
            reason=current_reason,
            source_trigger_ref=source_trigger_ref,
            invalidated_at_ms=invalidated_at_ms,
            descendant_derivation_ids=descendants,
            invalidation_sha256="0" * 64,
        ).with_computed_invalidation_sha256()
        store.put_memory_invalidation(record)
        store.clear_active_head(
            life_id=derivation.life_id,
            principal_ref=derivation.principal_ref,
            claim_key=derivation.claim_key,
            layer=derivation.layer,
            derivation_id=derivation.derivation_id,
        )
        records.append(record)
    return tuple(records)
'''
    if "preserve_derivation_ids: tuple[str, ...] = ()" in text:
        return text
    pattern = re.compile(
        r"(?ms)^def invalidate_cascade\(.*?^    return tuple\(records\)\n"
    )
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("invalidate_cascade replacement failed")
    return text


def patch_gateway(text: str) -> str:
    if "from datetime import datetime, timezone\n" not in text:
        anchor = "from dataclasses import dataclass\n"
        if anchor not in text:
            raise RuntimeError("gateway import anchor changed")
        text = text.replace(
            anchor,
            anchor + "from datetime import datetime, timezone\n",
            1,
        )
    replacement = '''def _gateway_p15_memory_recall(runtime: object, user_text: object) -> str:
    """Return bounded, non-expired long-term memory for chat context (P15)."""

    try:
        from life_service.explicit_memory import (
            detect_explicit_intent,
            expiry_deadline_ms,
        )

        text = str(user_text or "").strip()
        life_service = getattr(runtime, "life_service", None)
        if life_service is None:
            return ""
        active = (
            life_service._active()
            if hasattr(life_service, "_active")
            else {}
        )
        life_id = str(active.get("life_id") or "")
        if not life_id:
            return ""
        memory_markers = (
            "名字", "我叫", "我是谁", "称呼", "记住", "记得", "之前", "上次",
            "偏好", "习惯", "忘了", "长期", "以后",
        )
        lines: list[str] = []
        seen: set[str] = set()
        now_ms = time.time_ns() // 1_000_000

        def created_at_ms(row: Mapping[str, object]) -> int | None:
            raw = str(row.get("created_at") or "").strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1000)
            except (TypeError, ValueError, OverflowError):
                return None

        def recallable(row: Mapping[str, object], snippet: str) -> bool:
            try:
                detection = detect_explicit_intent(snippet)
            except ValueError:
                return True
            if not detection.triggered or detection.expiry_kind is None:
                return True
            created_ms = created_at_ms(row)
            if created_ms is None:
                return False
            deadline = expiry_deadline_ms(detection.expiry_kind, created_ms)
            return deadline is None or now_ms < deadline

        def collect(query: str, limit: int = 10) -> None:
            status, payload, _ = life_service.request(
                "POST",
                "/api/v1/v3/life/memory/search",
                {"life_id": life_id, "query": query, "limit": limit},
            )
            if status >= 400 or not isinstance(payload, Mapping) or payload.get("ok") is not True:
                return
            for row in (payload.get("results") or [])[:limit]:
                if not isinstance(row, Mapping):
                    continue
                content = row.get("content")
                if isinstance(content, str):
                    snippet = content
                elif isinstance(content, Mapping):
                    snippet = str(content.get("text") or content.get("content") or "")
                else:
                    snippet = ""
                snippet = str(snippet).strip()
                if (
                    snippet
                    and snippet not in seen
                    and recallable(row, snippet)
                ):
                    seen.add(snippet)
                    lines.append(snippet[:1200])

        collect(text)
        if not lines and any(marker in text for marker in memory_markers):
            collect("")
        return "\n".join(lines[:10])
    except Exception:
        return ""

'''
    if "Return bounded, non-expired long-term memory for chat context (P15)." in text:
        return text
    pattern = re.compile(
        r"(?ms)^def _gateway_p15_memory_recall\(.*?"
        r"^    except Exception:\n        return \"\"\n\n"
        r"(?=def _gateway_body_state_query)"
    )
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("gateway recall replacement failed")
    return text


def main() -> int:
    changed = []
    for relative, transform in (
        ("src/life_service/memory_coordinator.py", patch_coordinator),
        ("src/life_service/memory_invalidation.py", patch_invalidation),
        ("src/total_gateway/runtime.py", patch_gateway),
    ):
        if rewrite(relative, transform):
            changed.append(relative)
    print({"ok": True, "changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
