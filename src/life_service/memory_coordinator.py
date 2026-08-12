"""P15 M2/M3 single memory-write coordinator.

Every production memory write (assert, turn, correct, promotion, migration)
must enter through this coordinator.  It owns:

- deterministic LifeEvent -> L1 STREAM derivation (atomic, idempotent);
- L4 EXPLICIT creation bound to a real user_message span;
- promotion materialization (L1->L2, L2->L3, L3/L4->L5) with the integer
  evidence math from :mod:`memory_promotion` and promotion-key idempotency;
- correction cascades through :mod:`memory_invalidation`.

The coordinator never opens its own connection: ``LifeShadowStore`` remains
the single persistence authority and every write below delegates to it.
"""

from __future__ import annotations

from typing import Callable, Mapping

from contracts import (
    LifeEventEnvelope,
    MemoryAssertionV3,
    MemoryDerivationV1,
    MemoryParentRef,
    canonical_json_bytes,
    canonical_sha256,
)
from contracts.world_understanding.memory_candidate import (
    MemoryWorldCandidate,
    derive_memory_lineage_root_hash,
    derive_memory_world_candidate_id,
)

from . import memory_promotion
from . import life_learning_memory
from . import temperament as temperament_module
from .explicit_memory import detect_explicit_intent, expiry_deadline_ms
from .memory_invalidation import invalidate_cascade
from .memory_context import is_injection_marked
from .legacy_layer_migration import (
    LEGACY_MIGRATION_POLICY,
    build_legacy_derivation,
    legacy_layer_for_assertion,
)
from .store import LifeShadowStore, LifeShadowStoreError


L1_POLICY_VERSION = "p15-l1-v1"
L2_POLICY_VERSION = "p15-l2-v1"
L3_POLICY_VERSION = "p15-l3-v1"
L4_POLICY_VERSION = "p15-l4-v1"
L5_POLICY_VERSION = "p15-l5-v1"

_SELF_COGNITION_DOMAINS = frozenset(
    {"SELF_IDENTITY", "CAPABILITY_SELF", "LONG_TERM_GOAL", "OPERATING_RULE"}
)
_DOMAIN_BY_ASSERTION_KIND = {
    "observation": "SYSTEM",
    "user_preference": "USER_PREFERENCE",
    "hard_constraint": "OPERATING_RULE",
    "goal": "LONG_TERM_GOAL",
    "relationship": "RELATIONSHIP",
    "skill": "CAPABILITY_SELF",
    "causal_summary": "OTHER",
    "legacy": "OTHER",
}
_ASSERTION_KIND_BY_DOMAIN = {
    "SELF_IDENTITY": "observation",
    "SELF_BEHAVIOR_PATTERN": "observation",
    "USER_PROFILE": "observation",
    "USER_PREFERENCE": "user_preference",
    "RELATIONSHIP": "relationship",
    "OPERATING_RULE": "hard_constraint",
    "LONG_TERM_GOAL": "goal",
    "TASK": "observation",
    "CAPABILITY_SELF": "skill",
    "CAPABILITY_KNOWLEDGE": "observation",
    "WORLD": "observation",
    "REPOSITORY": "observation",
    "SYSTEM": "observation",
    "OTHER": "observation",
}


class MemoryCoordinatorError(RuntimeError):
    pass


def l1_memory_id(
    *, life_id: str, source_event_id: str, policy_version: str = L1_POLICY_VERSION
) -> str:
    return "mem_" + canonical_sha256(
        {
            "domain": "tiangong.life.l1-memory.v1",
            "life_id": life_id,
            "source_event_id": source_event_id,
            "policy_version": policy_version,
        }
    )


def l1_derivation_id(
    *, life_id: str, source_event_id: str, policy_version: str = L1_POLICY_VERSION
) -> str:
    return "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.l1-derivation.v1",
            "life_id": life_id,
            "source_event_id": source_event_id,
            "policy_version": policy_version,
        }
    )


def l4_memory_id(
    *,
    life_id: str,
    user_message_event_id: str,
    claim_key: str,
    policy_version: str = L4_POLICY_VERSION,
) -> str:
    return "mem_" + canonical_sha256(
        {
            "domain": "tiangong.life.l4-memory.v1",
            "life_id": life_id,
            "user_message_event_id": user_message_event_id,
            "claim_key": claim_key,
            "policy_version": policy_version,
        }
    )


def l4_derivation_id(
    *,
    life_id: str,
    user_message_event_id: str,
    claim_key: str,
    policy_version: str = L4_POLICY_VERSION,
) -> str:
    return "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.l4-derivation.v1",
            "life_id": life_id,
            "user_message_event_id": user_message_event_id,
            "claim_key": claim_key,
            "policy_version": policy_version,
        }
    )


def promotion_memory_id(
    *, promotion_key: str, target_layer: str, policy_version: str
) -> str:
    return "mem_" + canonical_sha256(
        {
            "domain": "tiangong.life.promotion-memory.v1",
            "promotion_key": promotion_key,
            "target_layer": target_layer,
            "policy_version": policy_version,
        }
    )


def promotion_derivation_id(
    *, promotion_key: str, target_layer: str, policy_version: str
) -> str:
    return "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.promotion-derivation.v1",
            "promotion_key": promotion_key,
            "target_layer": target_layer,
            "policy_version": policy_version,
        }
    )


def _semantic_domain_for_assertion_kind(assertion_kind: str) -> str:
    return _DOMAIN_BY_ASSERTION_KIND.get(
        assertion_kind, "OTHER"
    )


def _assertion_kind_for_domain(semantic_domain: str) -> str:
    return _ASSERTION_KIND_BY_DOMAIN.get(semantic_domain, "observation")


def _parent_ref(parent: MemoryDerivationV1) -> MemoryParentRef:
    return MemoryParentRef(
        parent_derivation_id=parent.derivation_id,
        memory_id=parent.memory_id,
        memory_revision=parent.memory_revision,
        assertion_sha256=parent.memory_assertion_sha256,
        parent_ref_sha256="0" * 64,
    ).with_computed_parent_ref_sha256()


class MemoryCoordinator:
    """Single write authority over one LifeShadowStore."""

    def __init__(self, store: LifeShadowStore) -> None:
        self._store = store
        self._open_learning: dict[str, set[str]] = {}
        self._zero_gain: dict[tuple[str, str], int] = {}
        self._last_zero_gain_at: dict[tuple[str, str], int] = {}

    @property
    def store(self) -> LifeShadowStore:
        return self._store

    # ------------------------------------------------------------------
    # LifeEvent -> L1
    # ------------------------------------------------------------------

    def commit_life_event_l1(
        self,
        event: LifeEventEnvelope,
        *,
        event_payload: bytes | None = None,
        policy_version: str = L1_POLICY_VERSION,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, bool]:
        """Persist one LifeEvent as an L1 stream record atomically.

        Deterministic memory/derivation ids make retries idempotent; the
        assertion, protected payload, outbox row and derivation commit in a
        single store transaction.
        """

        if not isinstance(event, LifeEventEnvelope):
            raise MemoryCoordinatorError(
                "LifeEvent->L1 requires a LifeEventEnvelope"
            )
        derivation_id = l1_derivation_id(
            life_id=event.life_id,
            source_event_id=event.event_id,
            policy_version=policy_version,
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("L1 assertion is missing")
            return assertion, existing, False
        memory_id = l1_memory_id(
            life_id=event.life_id,
            source_event_id=event.event_id,
            policy_version=policy_version,
        )
        plaintext = (
            event_payload
            if event_payload is not None
            else canonical_json_bytes(
                {
                    "schema": "tiangong.life.l1-record.v1",
                    "event_id": event.event_id,
                    "event_kind": event.event_kind,
                    "source_kind": event.source_kind,
                    "occurred_at_ms": event.occurred_at_ms,
                }
            )
        )
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=event.life_id,
            memory_id=memory_id,
            memory_revision=1,
            memory_assertion_sha256="0" * 64,
            layer="L1_STREAM",
            semantic_domain="SYSTEM",
            origin="LIFE_EVENT",
            principal_ref=event.principal_ref,
            workspace_ref=None,
            privacy_scope=event.privacy_scope,
            claim_key="l1:" + event.event_id,
            parent_memory_refs=(),
            source_event_ids=(event.event_id,),
            lineage_root_event_ids=(event.event_id,),
            external_evidence_refs=(),
            promotion_policy_version=policy_version,
            promotion_reason_codes=(),
            valid_from_ms=event.observed_at_ms,
            expires_at_ms=None,
            context_eligible=True,
            learning_eligible=False,
            temperament_eligible=False,
            self_cognition_eligible=False,
            world_candidate_eligible=False,
            created_at_ms=event.observed_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        _assertion, _seq, created = self._store.put_live_memory_assertion(
            plaintext,
            memory_id=memory_id,
            life_id=event.life_id,
            assertion_kind="observation",
            epistemic_status="observed",
            lifecycle_status="active",
            privacy_scope=event.privacy_scope,
            retention_class="ACTIVE_WORKING",
            source_event_ids=(event.event_id,),
            verification_strength_milli=event.source_credibility_milli,
            valid_from_ms=event.observed_at_ms,
            created_at_ms=event.observed_at_ms,
            derivation=derivation,
        )
        stored = self._store.get_memory_derivation(derivation_id)
        if stored is None:
            raise MemoryCoordinatorError("L1 derivation commit failed")
        return _assertion, stored, created

    # ------------------------------------------------------------------
    # L4 explicit
    # ------------------------------------------------------------------

    def commit_user_explicit(
        self,
        *,
        l1_parent_derivation_id: str,
        user_message_event_id: str,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        user_text: str,
        plaintext: bytes,
        created_at_ms: int,
        claim_key: str | None = None,
        semantic_domain: str | None = None,
        policy_version: str = L4_POLICY_VERSION,
        expires_at_ms: int | None = None,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, object, bool]:
        """Create L4 EXPLICIT bound to a real user_message event.

        Explicit authority comes from the provided user span; the detector
        only classifies it.  The L4 assertion is always ``user_asserted``
        (I14: persistence authority, never external truth).
        """

        detection = detect_explicit_intent(user_text)
        if not detection.triggered:
            raise MemoryCoordinatorError(
                "user span carries no explicit persistence intent"
            )
        parent = self._store.get_memory_derivation(l1_parent_derivation_id)
        if parent is None or parent.layer != "L1_STREAM":
            raise MemoryCoordinatorError("L4 requires an existing L1 parent")
        claim = claim_key or ("explicit:" + user_message_event_id)
        domain = semantic_domain or _semantic_domain_for_assertion_kind(
            "user_preference"
        )
        memory_id = l4_memory_id(
            life_id=life_id,
            user_message_event_id=user_message_event_id,
            claim_key=claim,
            policy_version=policy_version,
        )
        derivation_id = l4_derivation_id(
            life_id=life_id,
            user_message_event_id=user_message_event_id,
            claim_key=claim,
            policy_version=policy_version,
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("L4 assertion is missing")
            return assertion, existing, detection, False
        deadline = (
            expires_at_ms
            if expires_at_ms is not None
            else expiry_deadline_ms(detection.expiry_kind, created_at_ms)
        )
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=life_id,
            memory_id=memory_id,
            memory_revision=1,
            memory_assertion_sha256="0" * 64,
            layer="L4_EXPLICIT",
            semantic_domain=domain,
            origin="USER_EXPLICIT",
            principal_ref=principal_ref,
            workspace_ref=None,
            privacy_scope=privacy_scope,
            claim_key=claim,
            parent_memory_refs=(_parent_ref(parent),),
            source_event_ids=(user_message_event_id,),
            lineage_root_event_ids=parent.lineage_root_event_ids,
            external_evidence_refs=(),
            promotion_policy_version=policy_version,
            promotion_reason_codes=detection.reason_codes,
            valid_from_ms=created_at_ms,
            expires_at_ms=deadline,
            context_eligible=True,
            learning_eligible=False,
            temperament_eligible=False,
            self_cognition_eligible=False,
            world_candidate_eligible=(domain == "WORLD"),
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
            expires_at_ms=deadline,
            derivation=derivation,
            activate_head=True,
        )
        stored = self._store.get_memory_derivation(derivation_id)
        if stored is None:
            raise MemoryCoordinatorError("L4 derivation commit failed")
        return assertion, stored, detection, created

    def attach_explicit_l4(
        self,
        *,
        life_id: str,
        memory_id: str,
        user_text: str,
        created_at_ms: int,
        principal_ref: str,
        policy_version: str = L4_POLICY_VERSION,
    ) -> MemoryDerivationV1 | None:
        """Attach an L4 EXPLICIT derivation to an already-committed assertion.

        The assertion (and its L1 stream record) were written through
        ``/memory/assert`` or the contract adapter.  When the real user span
        carries explicit persistence intent, this adds the L4 derivation bound
        to that span, with ``user_asserted`` authority and no truth upgrade.
        Idempotent: re-attaching the same span is a no-op.
        """

        detection = detect_explicit_intent(user_text)
        if not detection.triggered:
            return None
        assertion = self._store.get_latest_memory_assertion(memory_id)
        if assertion is None or assertion.life_id != life_id:
            raise MemoryCoordinatorError(
                "explicit L4 memory assertion is missing"
            )
        l1 = None
        for derivation in self._store.list_derivations_for_memory(memory_id):
            if derivation.layer == "L1_STREAM":
                l1 = derivation
                break
        if l1 is None:
            raise MemoryCoordinatorError(
                "explicit L4 requires an L1 stream record"
            )
        source_event_id = (
            assertion.source_event_ids[0]
            if assertion.source_event_ids
            else "lev_"
            + canonical_sha256(
                {
                    "domain": "tiangong.life.memory-source-anchor.v1",
                    "memory_id": memory_id,
                }
            )
        )
        claim = "explicit:" + source_event_id
        derivation_id = l4_derivation_id(
            life_id=life_id,
            user_message_event_id=source_event_id,
            claim_key=claim,
            policy_version=policy_version,
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            return existing
        source_events = assertion.source_event_ids or (source_event_id,)
        domain = _semantic_domain_for_assertion_kind(assertion.assertion_kind)
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=life_id,
            memory_id=assertion.memory_id,
            memory_revision=assertion.revision,
            memory_assertion_sha256=assertion.assertion_sha256,
            layer="L4_EXPLICIT",
            semantic_domain=domain,
            origin="USER_EXPLICIT",
            principal_ref=principal_ref,
            workspace_ref=None,
            privacy_scope=assertion.privacy_scope,
            claim_key=claim,
            parent_memory_refs=(_parent_ref(l1),),
            source_event_ids=source_events,
            lineage_root_event_ids=l1.lineage_root_event_ids,
            external_evidence_refs=(),
            promotion_policy_version=policy_version,
            promotion_reason_codes=detection.reason_codes,
            valid_from_ms=assertion.valid_from_ms,
            expires_at_ms=None,
            context_eligible=True,
            learning_eligible=False,
            temperament_eligible=False,
            self_cognition_eligible=False,
            world_candidate_eligible=(domain == "WORLD"),
            created_at_ms=created_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        self._store.put_memory_derivation(derivation, activate_head=True)
        return derivation

    # ------------------------------------------------------------------
    # Learning -> Memory closure (M4)
    # ------------------------------------------------------------------

    def can_open_learning(
        self, *, life_id: str, subject: str, now_ms: int
    ) -> tuple[bool, int]:
        open_subjects = self._open_learning.setdefault(life_id, set())
        if len(open_subjects) >= life_learning_memory.MAX_OPEN_LEARNING:
            return False, 0
        key = (life_id, subject)
        zero_gain = self._zero_gain.get(key, 0)
        backoff = life_learning_memory.zero_gain_backoff_ms(zero_gain)
        last = self._last_zero_gain_at.get(key, 0)
        if backoff and now_ms < last + backoff:
            return False, last + backoff - now_ms
        return True, 0

    def open_learning(
        self, *, life_id: str, subject: str, now_ms: int
    ) -> bool:
        allowed, _retry = self.can_open_learning(
            life_id=life_id, subject=subject, now_ms=now_ms
        )
        if not allowed:
            return False
        self._open_learning.setdefault(life_id, set()).add(subject)
        return True

    def close_learning(self, *, life_id: str, subject: str) -> None:
        self._open_learning.get(life_id, set()).discard(subject)

    def record_zero_gain(
        self, *, life_id: str, subject: str, now_ms: int
    ) -> int:
        key = (life_id, subject)
        count = self._zero_gain.get(key, 0) + 1
        self._zero_gain[key] = count
        self._last_zero_gain_at[key] = now_ms
        self.close_learning(life_id=life_id, subject=subject)
        return count

    def reset_zero_gain(self, *, life_id: str, subject: str) -> None:
        self._zero_gain.pop((life_id, subject), None)
        self._last_zero_gain_at.pop((life_id, subject), None)

    def commit_learning_result(
        self,
        *,
        learning_event: LifeEventEnvelope,
        learning_id: str,
        subject: str,
        result_sha256: str,
        source_l3_derivation_ids: tuple[str, ...],
        refined_plaintext: bytes,
        created_at_ms: int,
        policy_version: str = life_learning_memory.LEARNING_REFINED_POLICY,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, tuple, bool]:
        """Close one Learning Result into L1 audit + refined L3 experience.

        The result becomes a LifeEvent (L1 audit) and a new L3 refined
        experience that inherits every parent evidence root.  It never writes
        L5 or Temperament and never adds a new independence group by itself
        (the refined record shares its parents' lineage roots).
        """

        if not source_l3_derivation_ids:
            raise MemoryCoordinatorError(
                "learning result requires active L3 refs"
            )
        if (
            len(source_l3_derivation_ids)
            > life_learning_memory.MAX_LEARNING_L3_REFS
        ):
            raise MemoryCoordinatorError("learning L3 refs exceed the bound")
        ids = life_learning_memory.derive_learning_result_ids(
            life_id=learning_event.life_id,
            learning_id=learning_id,
            result_sha256=result_sha256,
        )
        existing = self._store.get_memory_derivation(
            ids["refined_derivation_id"]
        )
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError(
                    "learning refined assertion is missing"
                )
            return assertion, existing, (), False
        _l1_assertion, l1_derivation, _l1_created = self.commit_life_event_l1(
            learning_event,
            event_payload=canonical_json_bytes(
                {
                    "schema": "tiangong.life.learning-result.v1",
                    "learning_id": learning_id,
                    "subject": subject,
                    "result_sha256": result_sha256,
                }
            ),
        )
        source_l3s: list[MemoryDerivationV1] = []
        for derivation_id in source_l3_derivation_ids:
            derivation = self._store.get_memory_derivation(derivation_id)
            if derivation is None or derivation.layer != "L3_EXPERIENCE":
                raise MemoryCoordinatorError(
                    "learning source must be an active L3"
                )
            if not self._store.is_derivation_active(derivation_id):
                raise MemoryCoordinatorError("learning source L3 is inactive")
            source_l3s.append(derivation)
        parents = tuple(
            sorted(
                (*source_l3s, l1_derivation),
                key=lambda item: item.derivation_id,
            )
        )
        roots = tuple(
            sorted(
                {
                    root
                    for parent in parents
                    for root in parent.lineage_root_event_ids
                }
            )
        )
        source_events = tuple(
            sorted(
                {
                    event_id
                    for parent in parents
                    for event_id in parent.source_event_ids
                }
            )
        )
        domain = source_l3s[0].semantic_domain
        derivation = MemoryDerivationV1(
            derivation_id=ids["refined_derivation_id"],
            life_id=learning_event.life_id,
            memory_id=ids["refined_memory_id"],
            memory_revision=1,
            memory_assertion_sha256="0" * 64,
            layer="L3_EXPERIENCE",
            semantic_domain=domain,
            origin="LEARNING_RESULT",
            principal_ref=learning_event.principal_ref,
            workspace_ref=None,
            privacy_scope=learning_event.privacy_scope,
            claim_key="learned:" + learning_id,
            parent_memory_refs=tuple(_parent_ref(parent) for parent in parents),
            source_event_ids=source_events,
            lineage_root_event_ids=roots,
            external_evidence_refs=(),
            promotion_policy_version=policy_version,
            promotion_reason_codes=("learning_refined",),
            valid_from_ms=created_at_ms,
            expires_at_ms=None,
            context_eligible=True,
            learning_eligible=True,
            temperament_eligible=False,
            self_cognition_eligible=False,
            world_candidate_eligible=(domain == "WORLD"),
            created_at_ms=created_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        assertion, _seq, created = self._store.put_live_memory_assertion(
            refined_plaintext,
            memory_id=ids["refined_memory_id"],
            life_id=learning_event.life_id,
            assertion_kind=_assertion_kind_for_domain(domain),
            epistemic_status="user_asserted",
            lifecycle_status="active",
            privacy_scope=learning_event.privacy_scope,
            retention_class="CHECKPOINT",
            source_event_ids=source_events,
            verification_strength_milli=750,
            valid_from_ms=created_at_ms,
            created_at_ms=created_at_ms,
            derivation=derivation,
            activate_head=True,
        )
        stored = self._store.get_memory_derivation(
            ids["refined_derivation_id"]
        )
        if stored is None:
            raise MemoryCoordinatorError(
                "learning refined derivation commit failed"
            )
        self.reset_zero_gain(life_id=learning_event.life_id, subject=subject)
        self.close_learning(life_id=learning_event.life_id, subject=subject)
        return (
            assertion,
            stored,
            (_l1_assertion, l1_derivation),
            created,
        )

    # ------------------------------------------------------------------
    # Runtime contract-record adapter (keeps API compatibility)
    # ------------------------------------------------------------------

    def commit_contract_assertion(
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
        """Adapter for legacy projection writes: assertion + L1 derivation."""

        source_event_id = source_event_ids[0] if source_event_ids else None
        derivation = None
        if source_event_id is not None:
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
                expires_at_ms=None,
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
        if source_event_id is not None:
            self._ensure_l1_derivation(
                assertion=assertion,
                source_event_id=source_event_id,
                principal_ref=principal_ref,
            )
        return assertion, change_seq, created

    def _ensure_l1_derivation(
        self,
        *,
        assertion: MemoryAssertionV3,
        source_event_id: str,
        principal_ref: str,
    ) -> MemoryDerivationV1 | None:
        derivation_id = l1_derivation_id(
            life_id=assertion.life_id,
            source_event_id=source_event_id,
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            return existing
        slot = self._store.find_derivation(
            memory_id=assertion.memory_id,
            memory_revision=assertion.revision,
            layer="L1_STREAM",
        )
        if slot is not None:
            return slot
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=assertion.life_id,
            memory_id=assertion.memory_id,
            memory_revision=assertion.revision,
            memory_assertion_sha256=assertion.assertion_sha256,
            layer="L1_STREAM",
            semantic_domain=_semantic_domain_for_assertion_kind(
                assertion.assertion_kind
            ),
            origin="LIFE_EVENT",
            principal_ref=principal_ref,
            workspace_ref=None,
            privacy_scope=assertion.privacy_scope,
            claim_key="l1:" + source_event_id,
            parent_memory_refs=(),
            source_event_ids=(source_event_id,),
            lineage_root_event_ids=(source_event_id,),
            external_evidence_refs=(),
            promotion_policy_version=L1_POLICY_VERSION,
            promotion_reason_codes=(),
            valid_from_ms=assertion.valid_from_ms,
            expires_at_ms=None,
            context_eligible=True,
            learning_eligible=False,
            temperament_eligible=False,
            self_cognition_eligible=False,
            world_candidate_eligible=False,
            created_at_ms=assertion.created_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        self._store.put_memory_derivation(derivation)
        return derivation

    # ------------------------------------------------------------------
    # Promotion materialization
    # ------------------------------------------------------------------

    def promote_l1_to_l2(
        self,
        *,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        l1_derivation_ids: tuple[str, ...],
        claim_key: str,
        semantic_domain: str,
        plaintext: bytes,
        created_at_ms: int,
        policy_version: str = L2_POLICY_VERSION,
        episode_boundary: bool = True,
        causal_utility_milli: int = 0,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, bool] | None:
        l1s = tuple(
            self._store.get_memory_derivation(derivation_id)
            for derivation_id in l1_derivation_ids
        )
        if any(item is None for item in l1s):
            raise MemoryCoordinatorError("L2 promotion references a missing L1")
        typed = tuple(item for item in l1s if item is not None)
        if any(item.layer != "L1_STREAM" for item in typed):
            raise MemoryCoordinatorError(
                "L1->L2 promotion requires L1 parents only"
            )
        disposition = memory_promotion.evaluate_l2(
            l1_derivations=typed,
            life_id=life_id,
            principal_ref=principal_ref,
            claim_key=claim_key,
            semantic_domain=semantic_domain,
            policy_version=policy_version,
            valid_from_ms=created_at_ms,
            created_at_ms=created_at_ms,
            episode_boundary=episode_boundary,
        )
        if not disposition.allowed:
            return None
        return self._materialize_promotion(
            disposition=disposition,
            parents=typed,
            principal_ref=principal_ref,
            privacy_scope=privacy_scope,
            plaintext=plaintext,
            created_at_ms=created_at_ms,
            policy_version=policy_version,
            assertion_causal_utility_milli=causal_utility_milli,
        )

    def promote_l2_to_l3(
        self,
        *,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        l2_derivation_ids: tuple[str, ...],
        claim_key: str,
        semantic_domain: str,
        plaintext: bytes,
        created_at_ms: int,
        support_weights: Mapping[str, int],
        counter_weights: Mapping[str, int],
        causal_utility_milli: Mapping[str, int],
        recurrence_count: int,
        policy_version: str = L3_POLICY_VERSION,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, bool] | None:
        l2s = tuple(
            self._store.get_memory_derivation(derivation_id)
            for derivation_id in l2_derivation_ids
        )
        if any(item is None for item in l2s):
            raise MemoryCoordinatorError("L3 promotion references a missing L2")
        typed = tuple(item for item in l2s if item is not None)
        if any(item.layer != "L2_DIARY" for item in typed):
            raise MemoryCoordinatorError(
                "L2->L3 promotion requires L2 parents only"
            )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=typed,
            support_weights=support_weights,
            counter_weights=counter_weights,
            causal_utility_milli=causal_utility_milli,
            recurrence_count=recurrence_count,
            life_id=life_id,
            principal_ref=principal_ref,
            claim_key=claim_key,
            semantic_domain=semantic_domain,
            policy_version=policy_version,
            valid_from_ms=created_at_ms,
            created_at_ms=created_at_ms,
        )
        if not disposition.allowed:
            return None
        return self._materialize_promotion(
            disposition=disposition,
            parents=typed,
            principal_ref=principal_ref,
            privacy_scope=privacy_scope,
            plaintext=plaintext,
            created_at_ms=created_at_ms,
            policy_version=policy_version,
        )

    def promote_to_l5(
        self,
        *,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        candidate_derivation_ids: tuple[str, ...],
        claim_key: str,
        semantic_domain: str,
        plaintext: bytes,
        created_at_ms: int,
        support_weights: Mapping[str, int],
        counter_weights: Mapping[str, int],
        recurrence_count: int,
        policy_version: str = L5_POLICY_VERSION,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, bool] | None:
        candidates = tuple(
            self._store.get_memory_derivation(derivation_id)
            for derivation_id in candidate_derivation_ids
        )
        if any(item is None for item in candidates):
            raise MemoryCoordinatorError("L5 promotion references a missing candidate")
        typed = tuple(item for item in candidates if item is not None)
        if any(
            item.layer not in {"L3_EXPERIENCE", "L4_EXPLICIT"}
            for item in typed
        ):
            raise MemoryCoordinatorError(
                "L3/L4->L5 promotion requires L3 or L4 candidates only"
            )
        disposition = memory_promotion.evaluate_l5(
            candidates=typed,
            support_weights=support_weights,
            counter_weights=counter_weights,
            recurrence_count=recurrence_count,
            life_id=life_id,
            principal_ref=principal_ref,
            claim_key=claim_key,
            semantic_domain=semantic_domain,
            policy_version=policy_version,
            valid_from_ms=created_at_ms,
            created_at_ms=created_at_ms,
        )
        if not disposition.allowed:
            return None
        return self._materialize_promotion(
            disposition=disposition,
            parents=typed,
            principal_ref=principal_ref,
            privacy_scope=privacy_scope,
            plaintext=plaintext,
            created_at_ms=created_at_ms,
            policy_version=policy_version,
        )

    def _materialize_promotion(
        self,
        *,
        disposition,
        parents: tuple[MemoryDerivationV1, ...],
        principal_ref: str,
        privacy_scope: str,
        plaintext: bytes,
        created_at_ms: int,
        policy_version: str,
        assertion_causal_utility_milli: int = 0,
    ) -> tuple[MemoryAssertionV3, MemoryDerivationV1, bool]:
        parents = tuple(
            sorted(parents, key=lambda item: item.derivation_id)
        )
        memory_id = promotion_memory_id(
            promotion_key=disposition.promotion_key,
            target_layer=disposition.target_layer,
            policy_version=policy_version,
        )
        derivation_id = promotion_derivation_id(
            promotion_key=disposition.promotion_key,
            target_layer=disposition.target_layer,
            policy_version=policy_version,
        )
        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("promotion assertion is missing")
            return assertion, existing, False
        parent_refs = tuple(_parent_ref(parent) for parent in parents)
        source_events = tuple(
            sorted(
                {
                    event_id
                    for parent in parents
                    for event_id in parent.source_event_ids
                }
            )
        )
        parent_assertions = tuple(
            self._store.get_memory_assertion(
                parent.memory_id, parent.memory_revision
            )
            for parent in parents
        )
        if any(item is None for item in parent_assertions):
            raise MemoryCoordinatorError("promotion parent assertion is missing")
        verified = all(
            item.epistemic_status in {"verified", "observed"}
            for item in parent_assertions
            if item is not None
        )
        domain = disposition.semantic_domain
        derivation = MemoryDerivationV1(
            derivation_id=derivation_id,
            life_id=disposition.life_id,
            memory_id=memory_id,
            memory_revision=1,
            memory_assertion_sha256="0" * 64,
            layer=disposition.target_layer,
            semantic_domain=domain,
            origin="PROMOTION",
            principal_ref=principal_ref,
            workspace_ref=None,
            privacy_scope=privacy_scope,
            claim_key=disposition.claim_key,
            parent_memory_refs=parent_refs,
            source_event_ids=source_events,
            lineage_root_event_ids=disposition.lineage_root_event_ids,
            external_evidence_refs=(),
            promotion_policy_version=disposition.policy_version,
            promotion_reason_codes=disposition.reason_codes,
            valid_from_ms=disposition.valid_from_ms,
            expires_at_ms=None,
            context_eligible=True,
            learning_eligible=disposition.target_layer
            in {"L3_EXPERIENCE", "L5_CORE"},
            temperament_eligible=(
                disposition.target_layer == "L5_CORE"
                and domain == "SELF_BEHAVIOR_PATTERN"
            ),
            self_cognition_eligible=(
                disposition.target_layer == "L5_CORE"
                and domain in _SELF_COGNITION_DOMAINS
                and not (
                    domain == "SELF_IDENTITY"
                    and any(
                        parent.origin == "USER_EXPLICIT"
                        for parent in parents
                    )
                )
            ),
            world_candidate_eligible=(
                disposition.target_layer
                in {"L3_EXPERIENCE", "L4_EXPLICIT", "L5_CORE"}
                and domain == "WORLD"
            ),
            created_at_ms=disposition.created_at_ms,
            derivation_sha256="0" * 64,
        ).with_computed_derivation_sha256()
        assertion, _seq, created = self._store.put_live_memory_assertion(
            plaintext,
            memory_id=memory_id,
            life_id=disposition.life_id,
            assertion_kind=_assertion_kind_for_domain(domain),
            epistemic_status="verified" if verified else "user_asserted",
            lifecycle_status="active",
            privacy_scope=privacy_scope,
            retention_class="LONG_TERM_MEMORY"
            if disposition.target_layer == "L5_CORE"
            else "CHECKPOINT",
            source_event_ids=source_events,
            causal_utility_milli=(
                max(700, assertion_causal_utility_milli)
                if disposition.target_layer == "L3_EXPERIENCE"
                else assertion_causal_utility_milli
            ),
            verification_strength_milli=disposition.support_milli,
            valid_from_ms=disposition.valid_from_ms,
            created_at_ms=disposition.created_at_ms,
            derivation=derivation,
            activate_head=True,
        )
        stored = self._store.get_memory_derivation(derivation_id)
        if stored is None:
            raise MemoryCoordinatorError("promotion derivation commit failed")
        return assertion, stored, created

    # ------------------------------------------------------------------
    # Correction / invalidation closure
    # ------------------------------------------------------------------

    def correct_claim(
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
        """Correct one active claim head and cascade-invalidate descendants."""

        target = self._store.get_memory_derivation(target_derivation_id)
        if target is None:
            raise MemoryCoordinatorError("correction target is missing")
        if not self._store.is_derivation_active(target_derivation_id):
            raise MemoryCoordinatorError("correction target is already inactive")
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
            return assertion, existing, (), False
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
        invalidations = invalidate_cascade(
            self._store,
            derivation_id=target_derivation_id,
            reason="corrected",
            invalidated_at_ms=created_at_ms,
            source_trigger_ref=user_message_event_id,
        )
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
        return assertion, stored, invalidations, created

    # ------------------------------------------------------------------
    # Temperament / Self Cognition (M6)
    # ------------------------------------------------------------------

    def adapt_temperament_from_core(
        self,
        *,
        life_id: str,
        innate: Mapping[str, object],
        current_temperament: Mapping[str, object] | None,
        now_ms: int,
        trait_delta_provider: Callable[
            [MemoryDerivationV1], Mapping[str, int]
        ]
        | None = None,
    ) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
        """Exactly-once temperament adaptation from eligible active L5 core.

        Only ``temperament_eligible`` active L5 derivations participate; each
        is consumed at most once through a durable adaptation receipt.  Plain
        turns and emotions never enter this path.
        """

        if (
            isinstance(now_ms, bool)
            or not isinstance(now_ms, int)
            or now_ms < 0
        ):
            raise MemoryCoordinatorError("temperament timestamp is invalid")
        eligible = tuple(
            derivation
            for derivation in self._store.list_memory_derivations(
                life_id=life_id, active_only=True, limit=4096
            )
            if derivation.layer == "L5_CORE"
            and derivation.temperament_eligible
        )
        state: dict[str, object] = dict(current_temperament or {})
        receipts: list[dict[str, object]] = []
        for derivation in sorted(
            eligible, key=lambda item: item.derivation_id
        ):
            if self._store.has_temperament_receipt(
                life_id, derivation.derivation_id
            ):
                continue
            deltas = (
                trait_delta_provider(derivation)
                if trait_delta_provider is not None
                else {
                    key: 2 for key in temperament_module.TRAIT_KEYS
                }
            )
            state, outcome = temperament_module.adapt_from_core_memory(
                innate,
                state,
                evidence_refs=(derivation.derivation_id,),
                trait_delta_micro=deltas,
                updated_at=str(now_ms),
            )
            delta_sha256 = str(outcome.get("trait_delta_sha256") or "")
            self._store.put_temperament_receipt(
                life_id=life_id,
                derivation_id=derivation.derivation_id,
                trait_delta_sha256=delta_sha256,
                adapted_at_ms=now_ms,
                receipt_payload={
                    "evidence_refs": outcome.get("evidence_refs") or (),
                    "derivation_id": derivation.derivation_id,
                },
            )
            receipts.append(
                {
                    "derivation_id": derivation.derivation_id,
                    "trait_delta_sha256": delta_sha256,
                    "adapted_at_ms": now_ms,
                }
            )
        return state, tuple(receipts)

    # ------------------------------------------------------------------
    # Memory -> World candidate projection (M7)
    # ------------------------------------------------------------------

    def project_memory_world_candidates(
        self,
        *,
        life_id: str,
        now_ms: int,
        policy_version: str = "p15-world-candidate-v1",
        world_scope_hash: str | None = None,
        limit: int = 64,
    ) -> tuple[int, int, tuple[MemoryWorldCandidate, ...]]:
        """Project eligible mature WORLD memories into the durable outbox.

        Only active, non-secret, non-expired, non-injection-marked L3/L4/L5
        WORLD-domain memories become candidates.  Memory never creates a
        WorldPatch here; this is candidate evidence intake only.
        """

        if not 1 <= limit <= 4096:
            raise MemoryCoordinatorError(
                "world candidate projection limit is invalid"
            )
        scope_hash = world_scope_hash or canonical_sha256(
            {
                "domain": "tiangong.world.memory-scope.v1",
                "life_id": life_id,
            }
        )
        candidates: list[MemoryWorldCandidate] = []
        created = 0
        skipped = 0
        for derivation in self._store.list_memory_derivations(
            life_id=life_id, active_only=True, limit=4096
        ):
            if not derivation.world_candidate_eligible:
                skipped += 1
                continue
            if derivation.layer not in {
                "L3_EXPERIENCE",
                "L4_EXPLICIT",
                "L5_CORE",
            }:
                skipped += 1
                continue
            if derivation.semantic_domain != "WORLD":
                skipped += 1
                continue
            if str(derivation.privacy_scope).casefold() == "secret":
                skipped += 1
                continue
            if (
                derivation.expires_at_ms is not None
                and derivation.expires_at_ms < now_ms
            ):
                skipped += 1
                continue
            assertion = self._store.get_memory_assertion(
                derivation.memory_id, derivation.memory_revision
            )
            if assertion is None or assertion.protected_payload_id is None:
                skipped += 1
                continue
            if assertion.epistemic_status in {
                "model_inference",
                "reflection",
                "prospective",
            }:
                skipped += 1
                continue
            try:
                summary = self._store.read_protected_payload(
                    assertion.protected_payload_id
                ).decode("utf-8", errors="strict")
            except Exception:
                skipped += 1
                continue
            if is_injection_marked(summary):
                skipped += 1
                continue
            candidate_id = derive_memory_world_candidate_id(
                life_id=life_id,
                derivation_id=derivation.derivation_id,
                policy_version=policy_version,
            )
            confidence = max(
                0,
                min(
                    1000,
                    int(assertion.verification_strength_milli),
                ),
            )
            epistemic = str(assertion.epistemic_status)
            if epistemic not in {"observed", "user_asserted", "verified"}:
                epistemic = "user_asserted"
            if epistemic == "user_asserted":
                confidence = min(750, confidence)
            candidate = MemoryWorldCandidate(
                candidate_id=candidate_id,
                life_id=life_id,
                world_scope_hash=scope_hash,
                principal_scope_hash=canonical_sha256(
                    {
                        "domain": "tiangong.world.memory-principal.v1",
                        "principal_ref": derivation.principal_ref,
                    }
                ),
                source_memory_id=derivation.memory_id,
                source_memory_revision=derivation.memory_revision,
                source_assertion_sha256=derivation.memory_assertion_sha256,
                source_derivation_id=derivation.derivation_id,
                source_layer=derivation.layer,
                claim_key=derivation.claim_key,
                semantic_payload=summary[:20_000],
                evidence_refs=(),
                lineage_root_hashes=tuple(
                    sorted(
                        derive_memory_lineage_root_hash(event_id)
                        for event_id in derivation.lineage_root_event_ids
                    )
                ),
                epistemic_status=epistemic,
                confidence_milli=confidence,
                volatility_class=(
                    "short"
                    if derivation.expires_at_ms is not None
                    else "medium"
                ),
                valid_from_ms=assertion.valid_from_ms,
                valid_until_ms=derivation.expires_at_ms,
                privacy_scope="private",
                candidate_sha256="0" * 64,
            ).with_computed_candidate_sha256()
            try:
                enqueued = self._store.put_world_candidate_outbox(
                    candidate,
                    derivation_id=derivation.derivation_id,
                    enqueued_at_ms=now_ms,
                )
            except LifeShadowStoreError:
                skipped += 1
                continue
            if enqueued:
                created += 1
                candidates.append(candidate)
            else:
                skipped += 1
            if len(candidates) >= limit:
                break
        return created, skipped, tuple(candidates)

    # ------------------------------------------------------------------
    # Legacy migration (M8)
    # ------------------------------------------------------------------

    def migrate_legacy_memories(
        self,
        *,
        life_id: str,
        now_ms: int,
        policy_version: str = LEGACY_MIGRATION_POLICY,
        limit: int = 512,
    ) -> dict[str, object]:
        """Attach conservative legacy-layer derivations to old assertions.

        The migration is additive, idempotent and never upgrades legacy rows
        to L5.  Assertions that already carry any derivation are skipped.
        """

        if not 1 <= limit <= 4096:
            raise MemoryCoordinatorError(
                "legacy migration limit is invalid"
            )
        counts = {
            "L1_STREAM": 0,
            "L2_DIARY": 0,
            "L3_EXPERIENCE": 0,
            "L4_EXPLICIT": 0,
        }
        skipped = 0
        migrated_ids: list[str] = []
        assertions = self._store.list_latest_memory_assertions(
            life_id, recallable_only=False
        )
        for assertion in assertions[:limit]:
            if assertion.lifecycle_status == "deleted":
                skipped += 1
                continue
            if self._store.has_derivation_for_assertion(
                assertion.memory_id, assertion.revision
            ):
                skipped += 1
                continue
            layer = legacy_layer_for_assertion(assertion)
            derivation = build_legacy_derivation(
                assertion,
                layer=layer,
                created_at_ms=now_ms,
                policy_version=policy_version,
            )
            try:
                created = self._store.put_memory_derivation(derivation)
            except LifeShadowStoreError:
                skipped += 1
                continue
            if created:
                counts[layer] += 1
                migrated_ids.append(derivation.derivation_id)
            else:
                skipped += 1
        return {
            "life_id": life_id,
            "policy_version": policy_version,
            "migrated_by_layer": counts,
            "migrated_count": sum(counts.values()),
            "skipped_count": skipped,
            "migration_ids": tuple(migrated_ids),
        }

    # ------------------------------------------------------------------
    # Incremental promotion consumer (M8 / plan section 15)
    # ------------------------------------------------------------------

    def run_promotion_cycle(
        self,
        *,
        life_id: str,
        now_ms: int,
        limit: int = 32,
        consumer_id: str = "p15-promotion",
    ) -> dict[str, object]:
        """Consume the memory change watermark and promote active heads.

        Only active L2/L3/L4 heads are evaluated; promotion keys keep the
        cycle idempotent, and the consumer offset advances only after the
        pass succeeds.  This is the incremental consumer the plan requires
        (never a full-table scan per query).
        """

        if not 1 <= limit <= 4096:
            raise MemoryCoordinatorError(
                "promotion cycle limit is invalid"
            )
        last = self._store.get_memory_consumer_offset(consumer_id, life_id)
        head = self._store.memory_change_head(life_id)
        if head <= last:
            return {
                "consumed": 0,
                "promotions": (),
                "last_watermark": last,
                "head": head,
            }

        def weight(assertion: MemoryAssertionV3) -> int:
            return min(
                1000,
                {
                    "verified": 1000,
                    "observed": 1000,
                    "user_asserted": 750,
                }.get(assertion.epistemic_status, 0),
            )

        promotions: list[tuple[str, str]] = []
        for derivation in self._store.list_memory_derivations(
            life_id=life_id, layer="L2_DIARY", active_only=True, limit=limit
        ):
            assertion = self._store.get_memory_assertion(
                derivation.memory_id, derivation.memory_revision
            )
            if assertion is None:
                continue
            result = self.promote_l2_to_l3(
                life_id=life_id,
                principal_ref=derivation.principal_ref,
                privacy_scope=derivation.privacy_scope,
                l2_derivation_ids=(derivation.derivation_id,),
                claim_key=derivation.claim_key,
                semantic_domain=derivation.semantic_domain,
                plaintext=canonical_json_bytes(
                    {
                        "schema": "tiangong.life.promotion-cycle.v1",
                        "claim_key": derivation.claim_key,
                        "source": derivation.memory_assertion_sha256,
                    }
                ),
                created_at_ms=now_ms,
                support_weights={derivation.derivation_id: weight(assertion)},
                counter_weights={},
                causal_utility_milli={
                    derivation.derivation_id: assertion.causal_utility_milli
                },
                recurrence_count=assertion.recurrence_count,
            )
            if result is not None and result[2]:
                promotions.append(("L3", result[1].derivation_id))

        candidates = tuple(
            derivation
            for derivation in self._store.list_memory_derivations(
                life_id=life_id, active_only=True, limit=4096
            )
            if derivation.layer in {"L3_EXPERIENCE", "L4_EXPLICIT"}
        )
        by_claim: dict[str, list[MemoryDerivationV1]] = {}
        for derivation in candidates:
            by_claim.setdefault(derivation.claim_key, []).append(derivation)
        for claim_key in sorted(by_claim):
            members = tuple(by_claim[claim_key])
            if len(members) > limit:
                members = members[:limit]
            assertions = tuple(
                self._store.get_memory_assertion(
                    item.memory_id, item.memory_revision
                )
                for item in members
            )
            if any(item is None for item in assertions):
                continue
            weights = {
                item.derivation_id: weight(assertion)
                for item, assertion in zip(members, assertions, strict=True)
                if assertion is not None
            }
            result = self.promote_to_l5(
                life_id=life_id,
                principal_ref=members[0].principal_ref,
                privacy_scope=members[0].privacy_scope,
                candidate_derivation_ids=tuple(
                    item.derivation_id for item in members
                ),
                claim_key=claim_key,
                semantic_domain=members[0].semantic_domain,
                plaintext=canonical_json_bytes(
                    {
                        "schema": "tiangong.life.promotion-cycle-l5.v1",
                        "claim_key": claim_key,
                    }
                ),
                created_at_ms=now_ms,
                support_weights=weights,
                counter_weights={},
                recurrence_count=1,
            )
            if result is not None and result[2]:
                promotions.append(("L5", result[1].derivation_id))

        self._store.advance_memory_consumer_offset(
            consumer_id, life_id, head, updated_at_ms=now_ms
        )
        return {
            "consumed": head - last,
            "promotions": tuple(promotions),
            "last_watermark": head,
            "head": head,
        }

    # ------------------------------------------------------------------
    # Privacy deletion cascade (M8 / I17)
    # ------------------------------------------------------------------

    def delete_memory_with_privacy_cascade(
        self,
        *,
        life_id: str,
        memory_id: str,
        deleted_at_ms: int,
    ) -> int:
        """Tombstone one memory and invalidate every derivation descendant.

        Safe to call after the assertion is already tombstoned (idempotent):
        only the cascade runs in that case.  Returns the number of
        invalidation records written.
        """

        latest = self._store.get_latest_memory_assertion(memory_id)
        if latest is None or latest.life_id != life_id:
            raise MemoryCoordinatorError(
                "memory deletion target is missing"
            )
        if latest.lifecycle_status != "deleted":
            self._store.delete_memory(
                memory_id,
                expected_revision=latest.revision,
                deleted_at_ms=deleted_at_ms,
            )
        invalidations = 0
        for derivation in self._store.list_derivations_for_memory(memory_id):
            if not self._store.is_derivation_active(
                derivation.derivation_id
            ):
                continue
            records = invalidate_cascade(
                self._store,
                derivation_id=derivation.derivation_id,
                reason="privacy_erasure",
                invalidated_at_ms=deleted_at_ms,
                source_trigger_ref="privacy_delete:" + memory_id,
            )
            invalidations += len(records)
        return invalidations


__all__ = [
    "L1_POLICY_VERSION",
    "L2_POLICY_VERSION",
    "L3_POLICY_VERSION",
    "L4_POLICY_VERSION",
    "L5_POLICY_VERSION",
    "MemoryCoordinator",
    "MemoryCoordinatorError",
    "l1_derivation_id",
    "l1_memory_id",
    "l4_derivation_id",
    "l4_memory_id",
    "promotion_derivation_id",
    "promotion_memory_id",
]
