from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_m2_production_publisher_enriches_existing_git_ingress() -> None:
    source = text("app/backend/tiangong-backend/v3/repository_perception.py")
    assert "repository_structure_index().update(observation)" in source
    assert '"repository_observation": observation.model_dump(mode="json")' in source
    assert '"structure_delta": structure_delta.model_dump(mode="json")' in source
    assert "notify_native_post_commit(NativePostCommitEvent(" in source
    assert 'source_kind="GIT_CODE"' in source
    assert "repository.local-git-structure.v0.2" in source


def test_m2_production_publisher_preserves_fail_open_git_fallback() -> None:
    source = text("app/backend/tiangong-backend/v3/repository_perception.py")
    assert 'payload: dict = observation.model_dump(mode="json")' in source
    assert 'producer_ref = "repository.local-git.v0.1"' in source
    assert "except Exception:" in source


def test_m2_git_compiler_accepts_wrapped_repository_payload() -> None:
    source = text("src/world_understanding/source_compilers/git_code.py")
    assert 'payload.get("repository_observation")' in source
    assert 'payload.get("structure_delta")' in source
    assert 'delta.status == "FAILED_OPEN"' in source


def test_m2_uses_existing_world_ingress_not_second_context_or_runtime() -> None:
    source = text("app/backend/tiangong-backend/v3/repository_perception.py")
    forbidden = (
        "repository_runtime",
        "repo_scheduler",
        "repo_worker",
        "repo_agent",
        "repo_learning",
        "RepositoryContextPacket",
    )
    assert not any(token in source for token in forbidden)
