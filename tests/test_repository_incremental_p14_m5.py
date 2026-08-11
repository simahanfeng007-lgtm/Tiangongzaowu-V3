from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BACKEND_PARENT = ROOT / "app" / "backend" / "tiangong-backend"
for path in (SRC, BACKEND_PARENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.world_understanding.repository import (
    RepositoryIdentity,
    RepositoryObservation,
    RepositoryProviderCapabilities,
    RepositoryRevision,
    RepositoryWorkingTreeState,
)
from v3 import repository_perception as perception


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "p14@example.invalid")
    _git(root, "config", "user.name", "P14 Test")
    _git(root, "checkout", "-b", "main")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def test_m5_clean_head_advance_emits_commit_modify_delta(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "alpha.py"
    target.write_text("value = 1\n", encoding="utf-8")
    first_head = _commit(tmp_path, "first")

    provider = perception.LocalGitRepositoryProvider()
    identity = provider.discover(str(tmp_path))
    assert identity is not None
    before = provider.observe(identity)
    assert before.revision.head_commit == first_head
    assert before.changes == ()

    target.write_text("value = 2\n", encoding="utf-8")
    second_head = _commit(tmp_path, "second")
    after = provider.observe_delta(identity, before.revision)

    assert after.revision.head_commit == second_head
    assert len(after.changes) == 1
    change = after.changes[0]
    assert change.change_kind == "MODIFY"
    assert change.old_path == "alpha.py"
    assert change.new_path == "alpha.py"
    assert change.old_blob_sha
    assert change.new_blob_sha
    assert change.old_blob_sha != change.new_blob_sha


def test_m5_branch_switch_never_replays_cross_branch_commit_diff(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "alpha.py"
    target.write_text("value = 'main'\n", encoding="utf-8")
    _commit(tmp_path, "main baseline")

    provider = perception.LocalGitRepositoryProvider()
    identity = provider.discover(str(tmp_path))
    assert identity is not None
    main_observation = provider.observe(identity)
    assert main_observation.revision.branch == "main"

    _git(tmp_path, "checkout", "-b", "alternate")
    target.write_text("value = 'alternate'\n", encoding="utf-8")
    _commit(tmp_path, "alternate change")

    alternate = provider.observe_delta(identity, main_observation.revision)
    assert alternate.revision.branch == "alternate"
    assert alternate.revision.head_commit != main_observation.revision.head_commit
    assert alternate.working_tree_state.dirty is False
    assert alternate.changes == ()


class _ProtocolProvider:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.delta_calls: list[RepositoryRevision] = []

    def capabilities(self) -> RepositoryProviderCapabilities:
        return RepositoryProviderCapabilities()

    def discover(self, workspace_root: str) -> RepositoryIdentity | None:
        return RepositoryIdentity(
            provider_kind="test",
            repository_id="repo.test",
            repository_root_ref=str(self.root),
            worktree_id="worktree.test",
            worktree_root_ref=str(self.root),
            remote_identity_hash=None,
            default_branch_hint="main",
        )

    def observe(self, identity: RepositoryIdentity) -> RepositoryObservation:
        return self._observation(identity, "b" * 40)

    def observe_delta(
        self,
        identity: RepositoryIdentity,
        previous_revision: RepositoryRevision,
    ) -> RepositoryObservation:
        self.delta_calls.append(previous_revision)
        return self._observation(identity, "c" * 40)

    @staticmethod
    def _observation(identity: RepositoryIdentity, head: str) -> RepositoryObservation:
        return RepositoryObservation.build(
            identity=identity,
            revision=RepositoryRevision(
                branch="main",
                head_commit=head,
                parent_commit=None,
                detached_head=False,
                observed_at_ms=2000,
            ),
            working_tree_state=RepositoryWorkingTreeState.build(),
            changes=(),
            files=(),
            provider_version="test-v1",
        )


def test_m5_active_observer_depends_on_repository_provider_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _ProtocolProvider(tmp_path)
    previous = RepositoryRevision(
        branch="main",
        head_commit="a" * 40,
        parent_commit=None,
        detached_head=False,
        observed_at_ms=1000,
    )
    monkeypatch.setattr(perception, "duqu_workspace_root", lambda: tmp_path)

    observation = perception.observe_active_repository(
        provider,
        previous_revision=previous,
    )

    assert observation is not None
    assert observation.revision.head_commit == "c" * 40
    assert provider.delta_calls == [previous]


def test_m5_source_has_no_second_repository_revision_cache() -> None:
    perception_source = (BACKEND_PARENT / "v3" / "repository_perception.py").read_text(encoding="utf-8")
    production_source = (SRC / "world_understanding" / "production.py").read_text(encoding="utf-8")
    assert "RepositoryRevisionCache" not in perception_source
    assert "_repository_revision" not in perception_source
    assert "live_repository_frame" in production_source
    assert "frame.branch == branch" in production_source
