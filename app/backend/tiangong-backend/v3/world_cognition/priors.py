"""Built-in cognitive priors.

Priors are explicit, versioned interpretive constraints. They carry zero
empirical evidence weight and therefore cannot certify a software-world fact.
"""

from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.cognition_prior import CognitionPrior, derive_cognition_prior_id

from .store import WorldCognitionStore


PRIOR_POLICY_REF = "policy.world_cognition.priors.v1"
PRIOR_POLICY = {
    "schema": "tiangong.world_cognition.priors.v1",
    "principles": (
        "continuity",
        "evidence_first",
        "reality_over_memory",
        "stability",
        "revisability",
        "provenance",
        "anti_hallucination",
    ),
}
PRIOR_POLICY_SHA256 = canonical_sha256(PRIOR_POLICY)

_SOFTWARE_PRIORS = (
    (
        "continuity",
        "continuity",
        "Prefer interpretations that preserve causal and architectural continuity unless stronger current evidence shows a real discontinuity.",
        800,
    ),
    (
        "evidence_first",
        "epistemic",
        "Direct, provenance-preserving observation outranks unsupported inference when describing the current software world.",
        1000,
    ),
    (
        "reality_over_memory",
        "epistemic",
        "For mutable software facts, current verified observation outranks historical memory when the two conflict.",
        1000,
    ),
    (
        "stability",
        "consolidation",
        "A mature cognition must not be rewritten by one weak or correlated observation; independent evidence is required for promotion.",
        900,
    ),
    (
        "revisability",
        "revision",
        "Non-protected cognition remains revisable when sufficiently strong contradictory evidence is observed.",
        900,
    ),
    (
        "provenance",
        "epistemic",
        "Every persistent cognition must remain traceable to evidence lineage whose authority cannot increase through consolidation.",
        1000,
    ),
    (
        "anti_hallucination",
        "epistemic",
        "Model-generated inference or reflection may propose cognition but cannot by itself promote a cognition into stable reality.",
        1000,
    ),
)


def default_software_priors(*, life_id: str, created_at_ms: int) -> tuple[CognitionPrior, ...]:
    priors: list[CognitionPrior] = []
    for key, kind, principle, weight in _SOFTWARE_PRIORS:
        prior_id = derive_cognition_prior_id(life_id=life_id, domain="software", prior_key=key)
        prior = CognitionPrior(
            prior_id=prior_id,
            life_id=life_id,
            domain="software",
            prior_key=key,
            prior_kind=kind,
            principle=principle,
            interpretive_weight_milli=weight,
            source_policy_ref=PRIOR_POLICY_REF,
            source_policy_sha256=PRIOR_POLICY_SHA256,
            revision=1,
            status="active",
            created_at_ms=created_at_ms,
            prior_sha256="0" * 64,
        ).with_computed_prior_sha256()
        priors.append(prior)
    return tuple(priors)


def install_default_software_priors(
    store: WorldCognitionStore,
    *,
    life_id: str,
    created_at_ms: int,
) -> tuple[CognitionPrior, ...]:
    priors = default_software_priors(life_id=life_id, created_at_ms=created_at_ms)
    for prior in priors:
        store.put_prior(prior)
    return priors


__all__ = [
    "PRIOR_POLICY",
    "PRIOR_POLICY_REF",
    "PRIOR_POLICY_SHA256",
    "default_software_priors",
    "install_default_software_priors",
]
