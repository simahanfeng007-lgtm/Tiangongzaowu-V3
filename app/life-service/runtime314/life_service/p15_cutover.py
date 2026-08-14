"""P15 M8: A-F cutover phase verification.

Each phase is a static + structural check over the authoritative source tree,
failing closed if the production wiring drifts toward a dual path or a second
authority.  Phase F must leave no ``old=true/new=true`` dual production mode.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LIFE_SERVICE = ROOT / "src" / "life_service"


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def verify_cutover_phase(phase: str) -> dict[str, object]:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, bool(ok)))

    coordinator = _source("src/life_service/memory_coordinator.py")
    embedded = _source("src/life_service/embedded_runtime.py")
    context = _source("src/life_service/context.py")
    memory_context = _source("src/life_service/memory_context.py")
    store_schema = _source("src/life_service/store_schema.py")
    memory_repository = _source("src/life_service/store_memory_repository.py")
    wu_bridge = _source("src/world_understanding/cognition/memory_candidate.py")

    if phase == "A":
        # Shadow-write derivations while legacy Context behavior is intact.
        check(
            "derivation_shadow_writer",
            "commit_life_event_l1" in coordinator,
        )
        check(
            "legacy_context_builder_preserved",
            "class CausalContextBuilder" in context,
        )
    elif phase == "B":
        # All production memory writes flow through the coordinator.
        check(
            "runtime_delegates_to_coordinator",
            "_memory_coordinator().commit_contract_assertion" in embedded,
        )
        check(
            "no_direct_store_write_in_runtime",
            "store.put_live_memory_assertion" not in embedded,
        )
    elif phase == "C":
        # Context reads can switch to layered lineage-aware selection.
        check(
            "layered_selection_available",
            "select_layered_memories" in memory_context,
        )
        check(
            "lineage_dedupe_available",
            "dedupe_lineage" in memory_context,
        )
    elif phase == "D":
        # Temperament only adapts from eligible core memory.
        check(
            "per_turn_adaptation_retired",
            "adapt_from_completed_turn" not in embedded,
        )
        check(
            "core_memory_adaptation_wired",
            "adapt_temperament_from_core" in embedded,
        )
    elif phase == "E":
        # Memory -> World candidate path is active and bridged.
        # After M3-01..03 the table DDL lives in the schema authority and
        # the operational SQL lives in the Memory repository.
        check(
            "world_candidate_outbox_table",
            '"memory_world_candidate_outbox"' in store_schema
            and "memory_world_candidate_outbox" in memory_repository,
        )
        check(
            "wu_bridge_available",
            "class MemoryWorldCandidateBridge" in wu_bridge,
        )
    elif phase == "F":
        # No temporary dual path remains in production.
        allowed_writers = {
            "store.py",
            "store_memory_repository.py",
            "memory_coordinator.py",
            "memory_migration.py",
            "p15_cutover.py",
        }
        direct_writes: list[str] = []
        for path in sorted(LIFE_SERVICE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if (
                "put_live_memory_assertion" in text
                or "put_memory_derivation" in text
            ) and path.name not in allowed_writers:
                direct_writes.append(path.name)
        check("no_dual_write_path", not direct_writes)
        check(
            "no_dual_temperament_path",
            "adapt_from_completed_turn(" not in embedded,
        )
        check(
            "no_second_memory_runtime",
            "class MemoryCoordinator" in coordinator
            and "class MemoryRuntime" not in coordinator,
        )
    else:
        raise ValueError(f"unknown cutover phase: {phase}")

    return {
        "phase": phase,
        "ok": all(ok for _name, ok in checks),
        "checks": tuple(checks),
    }


def verify_all_phases() -> tuple[dict[str, object], ...]:
    return tuple(
        verify_cutover_phase(phase) for phase in ("A", "B", "C", "D", "E", "F")
    )


__all__ = ["verify_all_phases", "verify_cutover_phase"]
