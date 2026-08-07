from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "total_gateway" / "embedded_backend.py"


def _section(start_marker: str, end_marker: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_learning_decision_prompt_requires_generic_first_principles_skills() -> None:
    decision = _section("def _learning_decision", "def _self_iteration_decision")
    assert "GENERIC" in decision
    assert "first" in decision.lower() and "principles" in decision.lower()
    assert "parameterized or discovered dynamically at run time" in decision
    assert "input_schema" in decision and "output_schema" in decision
    assert "acceptance criteria" in decision
    assert "draft-only" in decision


def test_learning_synthesis_prompt_requires_generic_first_principles_skills() -> None:
    synthesis = _section("def _learning_synthesis", "def _share_compose")
    assert "GENERIC" in synthesis
    assert "first" in synthesis.lower() and "principles" in synthesis.lower()
    assert "parameterized or discovered at run time" in synthesis
    assert "input_schema" in synthesis and "output_schema" in synthesis
    assert "acceptance checks" in synthesis
    assert "draft_only" in synthesis
