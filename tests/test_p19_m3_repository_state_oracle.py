"""P19-R2 M3 RepositoryStateOracle tests — review completion gate.

Uses the REAL read-only git provider (LocalGitRepositoryProvider) against
temporary git repositories; observations are lineage-bound through the
v24 store; the source-authority check reuses the REAL
scripts/check-source-authority.py against this repository itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contracts.verification import AcceptancePredicate
from total_gateway.outcome_oracles.repository_state import RepositoryStateOracle
from total_gateway.store import GatewayStateStore
from total_gateway.verification_registry import VerifierRegistry
from total_gateway.verification_recording import VerificationRecorder

REPO_ROOT = Path(__file__).resolve().parents[1]

# v3 provider import (tests already set these paths via conftest/pytest ini)
for _path in ("src", "app/backend/tiangong-backend"):
    _candidate = str(REPO_ROOT / _path)
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from v3.repository_perception import LocalGitRepositoryProvider  # noqa: E402

from tests.test_p19_m3_write_evidence_v2 import HASH_B  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


class RepositoryOracleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = GatewayStateStore.open(
            self.root / "gateway.sqlite3", now_ms=900
        )
        self._seed_request()
        self.snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        self.oracle = RepositoryStateOracle(
            snapshot=self.snapshot,
            store=self.store,
            authority_validator=lambda repo_root: [],
        )
        self.provider = LocalGitRepositoryProvider()
        self._repo = self.root / "project"
        self._repo.mkdir()
        _git(self._repo, "init", "-q")
        _git(self._repo, "config", "user.email", "test@example.com")
        _git(self._repo, "config", "user.name", "Test")
        (self._repo / "README.md").write_text("base\n", encoding="utf-8")
        _git(self._repo, "add", ".")
        _git(self._repo, "commit", "-q", "-m", "base")
        self._obs_counter = 0

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _seed_request(self) -> None:
        from contracts import (
            InboundEnvelope,
            InboundScope,
            derive_inbound_scope_keys,
            derive_run_identity,
        )

        scope = InboundScope(
            channel="desktop",
            tenant_id="tenant_m3r",
            link_account_id="desktop_m3r",
            conversation_ref="conversation_m3r",
            channel_message_ref="message_m3r",
            sender_ref="sender_m3r",
        )
        keys = derive_inbound_scope_keys(scope)
        envelope = InboundEnvelope(
            inbound_id="inbound_m3r",
            channel=scope.channel,
            tenant_id=scope.tenant_id,
            link_account_id=scope.link_account_id,
            conversation_ref=scope.conversation_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            principal_scope_hash=keys.principal_scope_hash,
            message_scope_hash=keys.message_scope_hash,
            channel_message_ref=scope.channel_message_ref,
            sender_ref=scope.sender_ref,
            received_at_ms=1_000,
            idempotency_key="7" * 64,
            channel_metadata_hash=HASH_B,
            text="modify the repository",
        )
        registration = self.store.register_request(
            envelope, ingress_sha256=HASH_B, created_at_ms=1_100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            gateway_epoch=1,
            lease_id="lease_m3r",
            owner_instance_id="gateway_m3r",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )

    def _observe_and_store(self, *, effect_ordinal: int, delta_from=None) -> str:
        """Capture a real provider observation and bind it into the store.

        ``delta_from``: a prior provider observation object — when given,
        the capture uses ``observe_delta`` so committed changes between
        the two revisions land in the observation's authoritative
        ``changes`` (plain ``observe`` only reports the working-tree
        overlay).
        """
        self._obs_counter += 1
        identity = self.provider.discover(str(self._repo))
        assert identity is not None
        if delta_from is not None:
            observation = self.provider.observe_delta(
                identity, delta_from.revision
            )
        else:
            observation = self.provider.observe(identity)
        self._last_observation = observation
        payload = observation.model_dump(mode="json")
        created = self.store.put_repository_observation(
            observation_sha256=observation.observation_sha256,
            observation_payload=payload,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            effect_id=f"eff_m3r_{effect_ordinal}_{self._obs_counter}",
            repository_id=identity.repository_id,
            head_commit=observation.revision.head_commit,
            observed_at_ms=20_000 + self._obs_counter,
            recorded_at_ms=21_000 + self._obs_counter,
        )
        self.assertTrue(created)
        return observation.observation_sha256

    def _evaluate(self, pre_sha, post_sha, kind, **params):
        predicate = AcceptancePredicate.create(
            predicate_type=kind, subject_kind="repository", params=params or None
        )
        return self.oracle.evaluate(
            pre_observation_sha256=pre_sha,
            post_observation_sha256=post_sha,
            predicate=predicate,
            evaluated_at_ms=30_000,
        )


class DeltaPredicateTests(RepositoryOracleTestBase):
    def test_required_paths_changed_pass_and_fail(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        (self._repo / "src" ).mkdir(exist_ok=True)
        (self._repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/main.py"]
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)
        missing = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/other.py"]
        )
        self.assertEqual(missing.status, "FAIL")

    def test_untracked_and_rename_and_delete_count_as_changes(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        # rename committed first (a staged rename keeps the old index
        # path in the working-tree observation until committed)
        _git(self._repo, "mv", "README.md", "INTRO.md")
        _git(self._repo, "commit", "-q", "-m", "rename")
        # untracked addition (stays untracked)
        (self._repo / "notes.txt").write_text("note\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        record = self._evaluate(
            pre, post,
            "repository.required_paths_changed",
            paths=["notes.txt", "INTRO.md"],
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)
        deleted_still_delta = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["README.md"]
        )
        self.assertEqual(deleted_still_delta.status, "PASS")  # removal is a change

    def test_forbidden_paths_unchanged(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        (self._repo / "locked.cfg").write_text("v=2\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        untouched = self._evaluate(
            pre, post, "repository.forbidden_paths_unchanged", paths=["other.txt"]
        )
        self.assertEqual(untouched.status, "PASS")
        touched = self._evaluate(
            pre, post, "repository.forbidden_paths_unchanged", paths=["locked.cfg"]
        )
        self.assertEqual(touched.status, "FAIL")

    def test_no_change_means_required_paths_fail(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        # observations are content-addressed: an empty commit advances the
        # revision (distinct identity) without changing any path
        _git(self._repo, "commit", "-q", "--allow-empty", "-m", "noop")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/main.py"]
        )
        self.assertEqual(record.status, "FAIL")


class AuthorityTests(RepositoryOracleTestBase):
    def test_unknown_observation_is_error_never_pass(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        bogus = "a" * 64
        record = self._evaluate(
            pre, bogus, "repository.required_paths_changed", paths=["x"]
        )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("authority:observation_not_found", record.reason_codes)
        # A WU committed-frame snapshot has no path into this oracle at
        # all: the only entry is the lineage-bound store table above.
        wu_style_sha = "b" * 64
        wu_record = self._evaluate(
            pre, wu_style_sha, "repository.required_paths_changed", paths=["x"]
        )
        self.assertEqual(wu_record.status, "ERROR")

    def test_lineage_and_repository_binding_enforced(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        (self._repo / "x.txt").write_text("x\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        # rebind the post observation to a different lineage via raw SQL
        # -> oracle must not treat it as authority for this request
        import sqlite3

        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute(
                "UPDATE repository_observation SET request_id = ?"
                " WHERE observation_sha256 = ?",
                ("req_" + "e" * 64, post),
            )
            connection.commit()
        finally:
            connection.close()
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["x.txt"]
        )
        self.assertEqual(record.status, "ERROR")

    def test_generated_mirror_direct_edit_detected(self) -> None:
        # declare a generated mirror target in the temp repo's ownership
        ownership = {
            "schema": "tiangong.source-ownership.v2",
            "authority_policy": {"editable_roots": ["src"]},
            "mappings": [
                {
                    "id": "m1",
                    "source": "src",
                    "source_role": "authoritative",
                    "targets": ["mirror/generated.py"],
                }
            ],
        }
        (self._repo / "source-ownership.json").write_text(
            json.dumps(ownership), encoding="utf-8"
        )
        pre = self._observe_and_store(effect_ordinal=0)
        mirror = self._repo / "mirror" / "generated.py"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text("# direct edit\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        clean = self._evaluate(
            pre, post, "repository.no_generated_mirror_direct_edit"
        )
        # ownership targets come from source-ownership.json at repo root
        self.assertEqual(clean.status, "FAIL")
        self.assertIn("repository.generated_mirror_direct_edit", clean.reason_codes)

    def test_no_mirror_edit_passes(self) -> None:
        # ownership authority must EXIST before the predicate can judge:
        # declare a mirror target we do not touch.
        ownership = {
            "schema": "tiangong.source-ownership.v2",
            "authority_policy": {"editable_roots": ["src"]},
            "mappings": [
                {
                    "id": "m1",
                    "source": "src",
                    "source_role": "authoritative",
                    "targets": ["mirror/generated.py"],
                }
            ],
        }
        (self._repo / "source-ownership.json").write_text(
            json.dumps(ownership), encoding="utf-8"
        )
        pre = self._observe_and_store(effect_ordinal=0)
        (self._repo / "src").mkdir(exist_ok=True)
        (self._repo / "src" / "ok.py").write_text("ok\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        record = self._evaluate(pre, post, "repository.no_generated_mirror_direct_edit")
        self.assertEqual(record.status, "PASS", record.reason_codes)

    def test_source_authority_with_real_validator_against_this_repo(self) -> None:
        # Default validator = the REAL check-source-authority.py. Against
        # this repository it must PASS; against a broken config FAIL.
        real_oracle = RepositoryStateOracle(
            snapshot=self.snapshot, store=self.store
        )
        pre = self._observe_and_store(effect_ordinal=0)
        # Temp repo lacks the script -> default validator raises -> the
        # oracle reports ERROR (authority check failed), never PASS:
        record = real_oracle.evaluate(
            pre_observation_sha256=pre,
            post_observation_sha256=pre,
            predicate=AcceptancePredicate.create(
                predicate_type="repository.source_authority_valid",
                subject_kind="repository",
            ),
            evaluated_at_ms=30_000,
        )
        self.assertEqual(record.status, "ERROR")

        # Injected validator drives deterministic PASS/FAIL:
        passing = RepositoryStateOracle(
            snapshot=self.snapshot, store=self.store,
            authority_validator=lambda root: [],
        )
        failing = RepositoryStateOracle(
            snapshot=self.snapshot, store=self.store,
            authority_validator=lambda root: ["source_authority.failed"],
        )
        ok = passing.evaluate(
            pre_observation_sha256=pre,
            post_observation_sha256=pre,
            predicate=AcceptancePredicate.create(
                predicate_type="repository.source_authority_valid",
                subject_kind="repository",
            ),
            evaluated_at_ms=30_000,
        )
        self.assertEqual(ok.status, "PASS")
        bad = failing.evaluate(
            pre_observation_sha256=pre,
            post_observation_sha256=pre,
            predicate=AcceptancePredicate.create(
                predicate_type="repository.source_authority_valid",
                subject_kind="repository",
            ),
            evaluated_at_ms=30_000,
        )
        self.assertEqual(bad.status, "FAIL")

    def test_real_repo_source_authority_validator_passes(self) -> None:
        # The REAL validator against THIS repository (integration truth).
        from total_gateway.outcome_oracles.repository_state import (
            _default_authority_validator,
        )

        errors = _default_authority_validator(str(REPO_ROOT))
        self.assertEqual(errors, [])


class PersistenceTests(RepositoryOracleTestBase):
    def test_observation_idempotent_and_rebind_conflict(self) -> None:
        identity = self.provider.discover(str(self._repo))
        assert identity is not None
        observation = self.provider.observe(identity)
        payload = observation.model_dump(mode="json")
        args = dict(
            observation_sha256=observation.observation_sha256,
            observation_payload=payload,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            effect_id="eff_m3r_x",
            repository_id=identity.repository_id,
            head_commit=observation.revision.head_commit,
            observed_at_ms=20_000,
            recorded_at_ms=21_000,
        )
        self.assertTrue(self.store.put_repository_observation(**args))
        self.assertFalse(self.store.put_repository_observation(**args))
        from total_gateway.store import StoreConflictError

        with self.assertRaises(StoreConflictError):
            self.store.put_repository_observation(
                **{**args, "effect_id": "eff_m3r_OTHER"}
            )

    def test_full_chain_record_only_zero_state(self) -> None:
        pre = self._observe_and_store(effect_ordinal=0)
        (self._repo / "src").mkdir(exist_ok=True)
        (self._repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        post = self._observe_and_store(effect_ordinal=1, delta_from=self._last_observation)
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/main.py"]
        )
        self.assertEqual(record.status, "PASS")
        self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=1_500)
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        outcome = recorder.record(record, recorded_at_ms=31_000)
        self.assertTrue(outcome.created_by_this_call)

        import sqlite3

        connection = sqlite3.connect(self.store.path)
        try:
            decisions = connection.execute(
                "SELECT COUNT(*) FROM completion_decisions"
            ).fetchone()[0]
            aggregates = connection.execute(
                "SELECT COUNT(*) FROM aggregate_state"
            ).fetchone()[0]
            outbox_rows = connection.execute(
                "SELECT COUNT(*) FROM outbox"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual((decisions, aggregates, outbox_rows), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
