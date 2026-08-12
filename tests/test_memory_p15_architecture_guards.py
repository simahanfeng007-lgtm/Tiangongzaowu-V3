"""P15 architecture guards (M0): no second runtime/store/writer/thread/listener.

These are static source scans that fail closed whenever the tree drifts
toward a forbidden second authority: a second LifeShadowStore, a second
WorldStateStore production root, a memory-owned runtime/thread/listener, a
direct store bypass in memory modules, or a raised MEMORY compiler authority.
"""

from __future__ import annotations

import re
from pathlib import Path

from life_service import store as life_store_module


ROOT = Path(__file__).resolve().parents[1]
LIFE_SERVICE = ROOT / "src" / "life_service"
CONTRACTS = ROOT / "src" / "contracts"
WORLD_UNDERSTANDING = ROOT / "src" / "world_understanding"

MEMORY_SOURCE_FILES = (
    LIFE_SERVICE / "memory_classification.py",
    LIFE_SERVICE / "memory_lifecycle.py",
    LIFE_SERVICE / "memory_migration.py",
    CONTRACTS / "memory_layers.py",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_single_life_shadow_store_authority() -> None:
    definitions = [
        path
        for path in sorted(LIFE_SERVICE.glob("*.py"))
        if "class LifeShadowStore" in _source(path)
    ]
    assert definitions == [LIFE_SERVICE / "store.py"]


def test_single_world_state_production_authority() -> None:
    world_store = _source(WORLD_UNDERSTANDING / "world_state" / "store.py")
    assert world_store.count("class WorldStateStore") == 1
    production = _source(WORLD_UNDERSTANDING / "production.py")
    assert production.count("class ProductionWorldUnderstandingRuntime") == 1


def test_memory_modules_do_not_own_runtime_resources() -> None:
    forbidden = (
        "threading.Thread",
        "import threading",
        "socketserver",
        "http.server",
        "BaseHTTPRequestHandler",
        "subprocess",
        "os.system",
        "multiprocessing",
        "sched.scheduler",
    )
    for path in MEMORY_SOURCE_FILES:
        text = _source(path)
        for token in forbidden:
            assert token not in text, f"{path.name} must not own {token}"


def test_memory_modules_do_not_open_their_own_store() -> None:
    for path in MEMORY_SOURCE_FILES:
        text = _source(path)
        assert "sqlite3.connect" not in text
        assert "LifeShadowStore.open" not in text
        assert "create_engine" not in text


def test_memory_modules_do_not_touch_world_authority() -> None:
    for path in MEMORY_SOURCE_FILES:
        text = _source(path)
        assert "world_understanding" not in text
        assert "WorldStateStore" not in text
        assert "MemoryCompiler" not in text


def test_no_new_runtime_scheduler_gateway_listener_classes_in_memory_modules() -> None:
    for path in MEMORY_SOURCE_FILES:
        text = _source(path)
        for token in ("Runtime", "Scheduler", "Gateway", "Listener"):
            assert not re.search(
                rf"class\s+\w*{token}\b", text
            ), f"{path.name} declares a forbidden {token} class"


def test_future_memory_authority_modules_would_also_fail_closed() -> None:
    for name in (
        "memory_coordinator.py",
        "memory_promotion.py",
        "explicit_memory.py",
        "memory_invalidation.py",
    ):
        path = LIFE_SERVICE / name
        if not path.exists():
            continue
        text = _source(path)
        assert "threading.Thread" not in text
        assert "sqlite3.connect" not in text
        assert "http.server" not in text
        assert "world_understanding" not in text
        assert "LifeShadowStore.open" not in text


def test_shadow_schema_is_v14_with_derivation_tables() -> None:
    assert life_store_module.SHADOW_STORE_SCHEMA_VERSION == 14
    assert {
        "memory_derivations",
        "memory_derivation_parents",
        "memory_active_heads",
        "memory_consumer_offsets",
    } <= life_store_module._EXPECTED_TABLES


def test_memory_compiler_direct_known_authority_remains_zero() -> None:
    text = _source(WORLD_UNDERSTANDING / "source_compilers" / "p3.py")
    matches = re.findall(
        r'CompilerSpec\("MEMORY"[^)]*?,\s*(\d+)\s*,\s*(\d+)\s*\)', text
    )
    assert matches, "MEMORY compiler spec was not found"
    assert all(
        authority == "0" and weight == "0"
        for authority, weight in matches
    )


def test_derivation_schema_version_is_frozen() -> None:
    text = _source(CONTRACTS / "memory_layers.py")
    assert (
        'MEMORY_DERIVATION_SCHEMA_VERSION = "tiangong.life.memory-derivation.v1"'
        in text
    )
