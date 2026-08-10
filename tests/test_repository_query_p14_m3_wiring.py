from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_m3_production_query_uses_existing_world_understanding_runtime() -> None:
    source = _text("app/backend/tiangong-backend/v3/world_understanding_production.py")
    assert "def production_repository_graph_query(" in source
    assert "production_world_understanding_runtime().query_repository_graph(query)" in source
    assert "RepositoryQueryRuntime" not in source
    assert "RepositoryGraphRuntime" not in source


def test_m3_runtime_queries_committed_live_stream_only() -> None:
    source = _text("src/world_understanding/production.py")
    assert "def query_repository_graph(" in source
    assert "live = self._streams.get(query.frame_id)" in source
    assert 'raise ValueError("REPOSITORY_QUERY_FRAME_NOT_LIVE")' in source
    assert "execute_repository_graph_query(live.graph, query)" in source


def test_m3_query_path_has_no_git_filesystem_or_gateway_imports() -> None:
    path = ROOT / "src" / "world_understanding" / "software_world" / "query.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"os", "pathlib", "subprocess", "socket", "requests", "httpx", "git", "total_gateway"}
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    assert not roots.intersection(forbidden)


def test_m3_query_contract_is_control_only() -> None:
    source = _text("src/contracts/world_understanding/repository_query.py")
    assert "context_only: Literal[True] = True" in source
    assert "may_authorize: Literal[False] = False" in source
    assert "may_execute: Literal[False] = False" in source
    assert "empirical_evidence_weight_milli: Literal[0] = 0" in source
