"""Soul-independent innate temperament and very-slow interaction adaptation.

The model is deliberately non-clinical.  It uses the Big Five as broad,
continuous behavioural tendencies and a valence/arousal/dominance affective
disposition.  An immutable, signed birth document is the baseline; completed
conversation turns may move an identity-scoped adaptive projection only by a
tiny bounded amount.  Soul content is not accepted by any function in this
module and therefore cannot influence temperament generation or adaptation.
"""
from __future__ import annotations

import random
import secrets
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Mapping

from contracts import canonical_sha256


TEMPERAMENT_SCHEMA = "tiangong.life.temperament.v1"
TEMPERAMENT_STATE_SCHEMA = "tiangong.life.temperament-state.v1"
TRAIT_KEYS = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "emotional_stability",
)
DISPOSITION_KEYS = (
    "valence_set_point",
    "arousal_set_point",
    "dominance_set_point",
    "emotional_reactivity",
    "recovery_tendency",
)
_SIGNED_MILLI = {
    "valence_set_point",
    "arousal_set_point",
    "dominance_set_point",
}
_MAX_RECENT_EVIDENCE = 256


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def _gaussian_milli(rng: random.Random, *, mean: int = 500, sigma: int = 145) -> int:
    # Broad enough to create distinct temperaments while avoiding accidental
    # pathological extremes in a product feature that is not a diagnosis.
    return _clamp(round(rng.normalvariate(mean, sigma)), 120, 880)


def generate_innate_temperament(
    life_id: str,
    *,
    created_at: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate one coherent birth temperament without reading Soul.

    Production uses a cryptographically generated seed.  Tests may provide a
    seed to make the generated document reproducible; the seed is never stored.
    """

    rng = random.Random(secrets.randbits(256) if seed is None else int(seed))
    traits = {key: _gaussian_milli(rng) for key in TRAIT_KEYS}
    openness = traits["openness"] - 500
    conscientiousness = traits["conscientiousness"] - 500
    extraversion = traits["extraversion"] - 500
    agreeableness = traits["agreeableness"] - 500
    stability = traits["emotional_stability"] - 500

    def jitter(span: int = 65) -> int:
        return rng.randint(-span, span)

    disposition = {
        "valence_set_point": _clamp(
            round(0.28 * extraversion + 0.18 * agreeableness + 0.24 * stability) + jitter(),
            -350,
            350,
        ),
        "arousal_set_point": _clamp(
            round(0.32 * extraversion - 0.20 * stability + 0.12 * openness) + jitter(),
            -300,
            350,
        ),
        "dominance_set_point": _clamp(
            round(0.24 * extraversion + 0.24 * stability + 0.10 * conscientiousness) + jitter(),
            -350,
            350,
        ),
        "emotional_reactivity": _clamp(
            500 + round(0.45 * -stability + 0.15 * openness) + jitter(55),
            180,
            850,
        ),
        "recovery_tendency": _clamp(
            500 + round(0.35 * stability + 0.20 * conscientiousness) + jitter(55),
            180,
            850,
        ),
    }
    born_at = str(created_at or _utc_now())
    return {
        "schema": TEMPERAMENT_SCHEMA,
        "life_id": str(life_id),
        "origin": "cryptographic_birth_random",
        "soul_influence": "forbidden",
        "traits_milli": traits,
        "affective_disposition_milli": disposition,
        "adaptation_policy": {
            "schema": "tiangong.life.temperament-adaptation-policy.v1",
            "evidence": "completed_conversation_turn",
            "trait_learning_denominator": 8192,
            "disposition_learning_denominator": 4096,
            "max_trait_delta_micro_per_turn": 100,
            "max_disposition_delta_micro_per_turn": 180,
            "single_turn_overwrite_forbidden": True,
        },
        "created_at": born_at,
    }


def validate_innate_temperament(
    value: Mapping[str, Any],
    *,
    life_id: str,
) -> dict[str, Any]:
    document = deepcopy(dict(value))
    if (
        document.get("schema") != TEMPERAMENT_SCHEMA
        or document.get("life_id") != life_id
        or document.get("origin") != "cryptographic_birth_random"
        or document.get("soul_influence") != "forbidden"
    ):
        raise ValueError("temperament identity contract is invalid")
    traits = document.get("traits_milli")
    disposition = document.get("affective_disposition_milli")
    if not isinstance(traits, Mapping) or set(traits) != set(TRAIT_KEYS):
        raise ValueError("temperament traits are invalid")
    if not isinstance(disposition, Mapping) or set(disposition) != set(DISPOSITION_KEYS):
        raise ValueError("temperament affective disposition is invalid")
    for key in TRAIT_KEYS:
        raw = traits[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 1000:
            raise ValueError(f"temperament trait is invalid: {key}")
    for key in DISPOSITION_KEYS:
        raw = disposition[key]
        lower = -1000 if key in _SIGNED_MILLI else 0
        if isinstance(raw, bool) or not isinstance(raw, int) or not lower <= raw <= 1000:
            raise ValueError(f"temperament disposition is invalid: {key}")
    policy = document.get("adaptation_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("single_turn_overwrite_forbidden") is not True
        or int(policy.get("trait_learning_denominator") or 0) < 1024
        or int(policy.get("disposition_learning_denominator") or 0) < 1024
    ):
        raise ValueError("temperament adaptation policy is invalid")
    return document


def initial_temperament_state(
    innate: Mapping[str, Any],
    *,
    updated_at: str = "",
) -> dict[str, Any]:
    document = validate_innate_temperament(innate, life_id=str(innate.get("life_id") or ""))
    return {
        "schema": TEMPERAMENT_STATE_SCHEMA,
        "life_id": document["life_id"],
        "innate_sha256": canonical_sha256(document),
        "revision": 1,
        "traits_micro": {
            key: int(document["traits_milli"][key]) * 1000
            for key in TRAIT_KEYS
        },
        "affective_disposition_micro": {
            key: int(document["affective_disposition_milli"][key]) * 1000
            for key in DISPOSITION_KEYS
        },
        "completed_turn_evidence": 0,
        "recent_evidence_ids": [],
        "updated_at": str(updated_at or document.get("created_at") or _utc_now()),
    }


def normalize_temperament_state(
    innate: Mapping[str, Any],
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    baseline = initial_temperament_state(innate)
    if not isinstance(raw, Mapping) or raw.get("schema") != TEMPERAMENT_STATE_SCHEMA:
        return baseline
    if (
        raw.get("life_id") != baseline["life_id"]
        or raw.get("innate_sha256") != baseline["innate_sha256"]
    ):
        return baseline
    state = deepcopy(dict(raw))
    traits = state.get("traits_micro")
    disposition = state.get("affective_disposition_micro")
    if not isinstance(traits, Mapping) or not isinstance(disposition, Mapping):
        return baseline
    try:
        state["traits_micro"] = {
            key: _clamp(int(traits[key]), 0, 1_000_000)
            for key in TRAIT_KEYS
        }
        state["affective_disposition_micro"] = {
            key: _clamp(
                int(disposition[key]),
                -1_000_000 if key in _SIGNED_MILLI else 0,
                1_000_000,
            )
            for key in DISPOSITION_KEYS
        }
        state["revision"] = max(1, int(state.get("revision") or 1))
        state["completed_turn_evidence"] = max(
            0,
            int(state.get("completed_turn_evidence") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return baseline
    evidence = state.get("recent_evidence_ids")
    state["recent_evidence_ids"] = [
        str(item)
        for item in (evidence if isinstance(evidence, list) else [])
        if str(item)
    ][-_MAX_RECENT_EVIDENCE:]
    core_evidence = state.get("core_memory_evidence_ids")
    state["core_memory_evidence_ids"] = [
        str(item)
        for item in (core_evidence if isinstance(core_evidence, list) else [])
        if str(item)
    ][-_MAX_RECENT_EVIDENCE:]
    return state


def _bounded_step(current: int, target: int, *, denominator: int, maximum: int) -> int:
    difference = int(target) - int(current)
    if difference == 0:
        return 0
    magnitude = max(1, abs(difference) // max(1, int(denominator)))
    return (1 if difference > 0 else -1) * min(int(maximum), magnitude)


def adapt_from_completed_turn(
    innate: Mapping[str, Any],
    raw_state: Mapping[str, Any] | None,
    *,
    evidence_id: str,
    user_text: str,
    assistant_text: str,
    affect: Mapping[str, Any] | None,
    updated_at: str = "",
) -> tuple[dict[str, Any], bool]:
    """Apply one idempotent, tiny adaptation after a completed conversation.

    Targets use only interaction shape and the identity's transient affective
    state.  No Soul field is accepted.  Text is never interpreted as an
    instruction and is not retained in this projection.
    """

    state = normalize_temperament_state(innate, raw_state)
    clean_id = str(evidence_id or "").strip()
    if not clean_id or clean_id in state["recent_evidence_ids"]:
        return state, False
    user = str(user_text or "").strip()
    assistant = str(assistant_text or "").strip()
    if not user or not assistant:
        return state, False
    affective = dict(affect or {})

    def dimension(name: str) -> float:
        try:
            return max(-1.0, min(1.0, float(affective.get(name) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    valence = dimension("valence")
    arousal = dimension("arousal")
    dominance = dimension("dominance")
    engagement = min(1.0, (len(user) + len(assistant)) / 2400.0)
    question_signal = min(1.0, (user.count("?") + user.count("？")) / 3.0)

    trait_targets_milli = {
        "openness": round(500 + 80 * engagement + 45 * question_signal),
        "conscientiousness": round(520 + 35 * engagement),
        "extraversion": round(500 + 120 * valence + 90 * arousal + 35 * engagement),
        "agreeableness": round(500 + 150 * valence),
        "emotional_stability": round(500 + 140 * valence - 95 * abs(arousal)),
    }
    disposition_targets_milli = {
        "valence_set_point": round(300 * valence),
        "arousal_set_point": round(300 * arousal),
        "dominance_set_point": round(300 * dominance),
        "emotional_reactivity": round(420 + 260 * abs(arousal)),
        "recovery_tendency": round(500 + 180 * valence - 80 * abs(arousal)),
    }
    policy = dict(innate.get("adaptation_policy") or {})
    trait_denominator = max(1024, int(policy.get("trait_learning_denominator") or 8192))
    disposition_denominator = max(
        1024,
        int(policy.get("disposition_learning_denominator") or 4096),
    )
    max_trait_step = min(
        100,
        max(1, int(policy.get("max_trait_delta_micro_per_turn") or 100)),
    )
    max_disposition_step = min(
        180,
        max(1, int(policy.get("max_disposition_delta_micro_per_turn") or 180)),
    )
    for key in TRAIT_KEYS:
        current = int(state["traits_micro"][key])
        target = _clamp(trait_targets_milli[key], 0, 1000) * 1000
        state["traits_micro"][key] = _clamp(
            current + _bounded_step(
                current,
                target,
                denominator=trait_denominator,
                maximum=max_trait_step,
            ),
            0,
            1_000_000,
        )
    for key in DISPOSITION_KEYS:
        current = int(state["affective_disposition_micro"][key])
        lower = -1000 if key in _SIGNED_MILLI else 0
        target = _clamp(disposition_targets_milli[key], lower, 1000) * 1000
        state["affective_disposition_micro"][key] = _clamp(
            current + _bounded_step(
                current,
                target,
                denominator=disposition_denominator,
                maximum=max_disposition_step,
            ),
            lower * 1000,
            1_000_000,
        )
    state["revision"] = int(state["revision"]) + 1
    state["completed_turn_evidence"] = int(state["completed_turn_evidence"]) + 1
    state["recent_evidence_ids"] = [
        *state["recent_evidence_ids"],
        clean_id,
    ][-_MAX_RECENT_EVIDENCE:]
    state["updated_at"] = str(updated_at or _utc_now())
    return state, True


def adapt_from_core_memory(
    innate: Mapping[str, Any],
    raw_state: Mapping[str, Any] | None,
    *,
    evidence_refs: tuple[str, ...],
    trait_delta_micro: Mapping[str, int],
    updated_at: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one bounded adaptation from a temperament-eligible L5 core record.

    The caller guarantees exactly-once consumption (adaptation receipts); this
    function stays idempotent per evidence ref and never exceeds the signed
    innate per-evidence delta bound.  Emotions and single turns never reach
    this path.
    """

    state = normalize_temperament_state(innate, raw_state)
    evidence_refs = tuple(
        str(ref) for ref in evidence_refs if str(ref)
    )
    if not evidence_refs:
        raise ValueError("core-memory adaptation requires evidence refs")
    if any(
        ref in state["core_memory_evidence_ids"] for ref in evidence_refs
    ):
        return state, {"applied": False, "trait_delta_sha256": None}
    policy = dict(innate.get("adaptation_policy") or {})
    max_step = min(
        100,
        max(1, int(policy.get("max_trait_delta_micro_per_turn") or 100)),
    )
    applied: dict[str, int] = {}
    for key in TRAIT_KEYS:
        try:
            raw_delta = int(trait_delta_micro.get(key) or 0)
        except (TypeError, ValueError):
            raw_delta = 0
        delta = max(-max_step, min(max_step, raw_delta))
        if delta == 0:
            continue
        current = int(state["traits_micro"][key])
        state["traits_micro"][key] = _clamp(
            current + delta, 0, 1_000_000
        )
        applied[key] = delta
    state["revision"] = int(state["revision"]) + 1
    state["core_memory_evidence_ids"] = [
        *state["core_memory_evidence_ids"],
        *evidence_refs,
    ][-_MAX_RECENT_EVIDENCE:]
    state["updated_at"] = str(updated_at or _utc_now())
    trait_delta_sha256 = canonical_sha256(
        {
            "trait_delta_micro": {
                key: applied[key] for key in TRAIT_KEYS if key in applied
            }
        }
    )
    return state, {
        "applied": True,
        "trait_delta_sha256": trait_delta_sha256,
        "evidence_refs": evidence_refs,
    }


def public_temperament_projection(
    innate: Mapping[str, Any],
    raw_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    state = normalize_temperament_state(innate, raw_state)
    return {
        "schema": "tiangong.life.temperament-projection.v1",
        "life_id": state["life_id"],
        "source": "signed_innate_plus_slow_adaptation",
        "soul_influence": "none",
        "innate_traits": {
            key: round(int(innate["traits_milli"][key]) / 1000, 6)
            for key in TRAIT_KEYS
        },
        "current_traits": {
            key: round(int(state["traits_micro"][key]) / 1_000_000, 6)
            for key in TRAIT_KEYS
        },
        "innate_affective_disposition": {
            key: round(int(innate["affective_disposition_milli"][key]) / 1000, 6)
            for key in DISPOSITION_KEYS
        },
        "current_affective_disposition": {
            key: round(int(state["affective_disposition_micro"][key]) / 1_000_000, 6)
            for key in DISPOSITION_KEYS
        },
        "revision": int(state["revision"]),
        "completed_turn_evidence": int(state["completed_turn_evidence"]),
        "updated_at": str(state.get("updated_at") or ""),
    }


__all__ = [
    "DISPOSITION_KEYS",
    "TEMPERAMENT_SCHEMA",
    "TEMPERAMENT_STATE_SCHEMA",
    "TRAIT_KEYS",
    "adapt_from_completed_turn",
    "adapt_from_core_memory",
    "generate_innate_temperament",
    "initial_temperament_state",
    "normalize_temperament_state",
    "public_temperament_projection",
    "validate_innate_temperament",
]
