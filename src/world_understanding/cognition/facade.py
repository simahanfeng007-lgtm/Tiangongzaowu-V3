"""Minimal-intrusion facade and master-valve boundary.

The facade deliberately accepts `enabled` from the owning V3 runtime instead of
reading global configuration itself. With the valve OFF it never constructs the
SQLite store, reads memory/fact journals, starts workers, calls an LLM, or writes
state. This is the sole public attachment point planned for Zongdiaodu.

V0.1 public consolidation is always deterministic-policy authority. Protected C4
operations remain internal until a later integration binds them to V3's existing
typed Runtime authorization rather than trusting a caller-provided string.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Sequence

from contracts.cognition_evidence import CognitionEvidence

from .consolidator import CognitionConsolidator, CognitionProposal, ConsolidationResult
from .evidence import CognitionEvidenceLedger
from .priors import install_default_software_priors
from .retrieval import CognitionRetriever
from .stability import StabilityPolicy
from .store import WorldCognitionStore


class WorldCognitionFacade:
    def __init__(
        self,
        *,
        enabled: bool = False,
        root: str | os.PathLike[str] | None = None,
        policy: StabilityPolicy | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.root = Path(root).expanduser().resolve(strict=False) if root is not None else (
            Path.home() / ".tiangong" / "v3" / "world_cognition"
        )
        self.policy = policy or StabilityPolicy()
        self._store: WorldCognitionStore | None = None
        self._evidence_ledger: CognitionEvidenceLedger | None = None
        self._consolidator: CognitionConsolidator | None = None
        self._retriever: CognitionRetriever | None = None

    @property
    def storage_exists(self) -> bool:
        return self.enabled and (self.root / "cognition.sqlite3").is_file()

    def _components(self) -> tuple[WorldCognitionStore, CognitionEvidenceLedger, CognitionConsolidator, CognitionRetriever]:
        if not self.enabled:
            raise RuntimeError("world cognition is disabled")
        if self._store is None:
            self._store = WorldCognitionStore(self.root)
            self._evidence_ledger = CognitionEvidenceLedger(self._store)
            self._consolidator = CognitionConsolidator(self._store, policy=self.policy)
            self._retriever = CognitionRetriever(self._store, policy=self.policy)
        assert self._evidence_ledger is not None and self._consolidator is not None and self._retriever is not None
        return self._store, self._evidence_ledger, self._consolidator, self._retriever

    def put_evidence(self, evidence: CognitionEvidence) -> bool:
        if not self.enabled:
            return False
        _, ledger, _, _ = self._components()
        return ledger.ingest(evidence)

    def install_software_priors(self, *, life_id: str, now_ms: int | None = None) -> tuple:
        if not self.enabled:
            return ()
        store, _, _, _ = self._components()
        return install_default_software_priors(
            store,
            life_id=life_id,
            created_at_ms=int(time.time() * 1000) if now_ms is None else int(now_ms),
        )

    def consolidate(
        self,
        proposal: CognitionProposal,
        *,
        support_evidence_ids: Iterable[str] = (),
        counterevidence_ids: Iterable[str] = (),
        now_ms: int | None = None,
    ) -> ConsolidationResult | None:
        if not self.enabled:
            return None
        _, _, consolidator, _ = self._components()
        return consolidator.consolidate(
            proposal,
            support_evidence_ids=support_evidence_ids,
            counterevidence_ids=counterevidence_ids,
            now_ms=int(time.time() * 1000) if now_ms is None else int(now_ms),
            decision_authority="deterministic_policy",
        )

    def project_context(
        self,
        *,
        life_id: str,
        domain: str,
        world_scope_hash: str,
        principal_scope_hash: str,
        query: str = "",
        allowed_privacy_scopes: Sequence[str] = ("system",),
        max_items: int = 8,
        max_chars: int = 6000,
        now_ms: int | None = None,
    ) -> str:
        if not self.enabled:
            return ""
        _, _, _, retriever = self._components()
        try:
            return retriever.project_context(
                life_id=life_id,
                domain=domain,
                world_scope_hash=world_scope_hash,
                principal_scope_hash=principal_scope_hash,
                now_ms=int(time.time() * 1000) if now_ms is None else int(now_ms),
                query=query,
                allowed_privacy_scopes=allowed_privacy_scopes,
                max_items=max_items,
                max_chars=max_chars,
            )
        except Exception:
            # Cognition is an optional context source. Its read-path failure must
            # degrade to the legacy V3 context path, never to Runtime failure.
            return ""

    def counts(self) -> dict[str, int]:
        if not self.enabled:
            return {"priors": 0, "evidence": 0, "statements": 0, "revisions": 0, "heads": 0}
        store, _, _, _ = self._components()
        return store.counts()


__all__ = ["WorldCognitionFacade"]
