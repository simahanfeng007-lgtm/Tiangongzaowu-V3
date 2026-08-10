from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_m4_production_reuses_existing_context_handler_and_runtime() -> None:
    source = _text("app/backend/tiangong-backend/v3/world_understanding_production.py")
    assert "WorldContextRequestHandler(" in source
    assert "projection_enricher=enrich_context" in source
    assert "runtime.repository_context_candidates(query, snapshot)" in source
    assert "RepositoryContextRuntime" not in source
    assert "RepositoryContextPacket" not in source


def test_m4_public_world_context_packet_contract_is_not_forked() -> None:
    source = _text("src/contracts/world_understanding/context_packet.py")
    assert "class WorldContextPacket" in source
    assert "RepositoryContextPacket" not in source
    assert "projection_authority: Literal[\"context_only\"]" in source
    assert "may_execute: Literal[False]" in source


def test_m4_repository_enrichment_has_no_io_gateway_model_or_scheduler_imports() -> None:
    path = ROOT / "src" / "world_understanding" / "context_output" / "repository.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "os", "pathlib", "subprocess", "socket", "requests", "httpx", "urllib", "git",
        "total_gateway", "life_service", "runtime_security", "threading", "asyncio",
    }
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    assert not roots.intersection(forbidden)


def test_m4_does_not_read_repository_structure_cache_for_context() -> None:
    source = _text("src/world_understanding/context_output/repository.py")
    assert "RepositoryStructureIndex" not in source
    assert "repository_structure_index" not in source
    assert "RepositorySourceSpan" not in source
    assert "execute_repository_graph_query" in source


def test_m4_handler_enrichment_is_optional_fail_open_only() -> None:
    source = _text("src/world_understanding/context_output/handler.py")
    assert "projection_enricher" in source
    assert "except Exception:" in source
    assert "enrichment = ()" in source
    assert "self.output_port.emit(query, result.packet)" in source


def test_m4_runtime_requires_exact_snapshot_frame_revision() -> None:
    source = _text("src/world_understanding/production.py")
    assert "live.graph.frame_id != frame_ref.record_id" in source
    assert "live.graph.frame_revision_hash != frame_ref.sha256" in source
    assert "return build_repository_context_candidates(live.graph, query)" in source
