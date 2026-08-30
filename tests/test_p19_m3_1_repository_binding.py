"""P19-R2 M3.1 RepositoryStateOracle tests — binding closure gate.

Covers review §11 items 7-14: content/lineage separation, observation
contract re-validation, metadata-vs-payload consistency, multi-binding
reuse, subject-effect window, mirror boundary matching, no execution of
checked-repo Python, post-capture drift, and full-chain RECORD-only
persistence.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contracts.verification import AcceptancePredicate
from total_gateway.outcome_oracles.repository_state import (
    OracleInvocationError,
    RepositoryStateOracle,
    _path_hits_target,
)
from total_gateway.store import GatewayStateStore, StoreConflictError
from total_gateway.verification_registry import VerifierRegistry
from total_gateway.verification_recording import VerificationRecorder

REPO_ROOT = Path(__file__).resolve().parents[1]

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
        # pinned resampler by default: no drift (drift tested explicitly)
        self.oracle = RepositoryStateOracle(
            snapshot=self.snapshot,
            store=self.store,
            authority_validator=lambda repo_root: [],
            resampler=lambda root: self._pinned_post_sha,
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
        self._pinned_post_sha = "0" * 64  # updated on each POST capture
        self._last_observation = None

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
            tenant_id="tenant_m31r",
            link_account_id="desktop_m31r",
            conversation_ref="conversation_m31r",
            channel_message_ref="message_m31r",
            sender_ref="sender_m31r",
        )
        keys = derive_inbound_scope_keys(scope)
        envelope = InboundEnvelope(
            inbound_id="inbound_m31r",
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
            lease_id="lease_m31r",
            owner_instance_id="gateway_m31r",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )

    def _create_effect(self, ordinal: int = 0) -> str:
        from contracts import derive_effect_identity
        from total_gateway.effects import EffectClaim
        identity = derive_effect_identity(
            request_id=self.request_id, run_id=self.run_id,
            run_sequence=1, generation=2, effect_kind="execution",
            ordinal=ordinal, intent_sha256="6" * 64,
        )
        claim = EffectClaim(
            effect_id=identity.effect_id, request_id=self.request_id,
            run_id=self.run_id, run_sequence=1, generation=2,
            effect_kind="execution", ordinal=ordinal,
            intent_sha256="6" * 64, owner_component_id="tiangong-backend",
            claimed_at_ms=20_000, claim_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.claim_effect(claim)
        return identity.effect_id

    def _capture_observation(self, *, delta_from=None):
        identity = self.provider.discover(str(self._repo))
        assert identity is not None
        if delta_from is not None:
            observation = self.provider.observe_delta(
                identity, delta_from.revision
            )
        else:
            observation = self.provider.observe(identity)
        self._last_observation = observation
        return observation

    def _store_content(self, observation) -> str:
        """Persist the observation CONTENT (contract-validated)."""
        payload = observation.model_dump(mode="json")
        created = self.store.put_repository_observation(
            observation_sha256=observation.observation_sha256,
            observation_payload=payload,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            effect_id=self._create_effect(0),
            repository_id=observation.identity.repository_id,
            head_commit=observation.revision.head_commit,
            observed_at_ms=observation.observed_at_ms,
            recorded_at_ms=observation.observed_at_ms + 1,
        )
        self.assertTrue(created)
        return observation.observation_sha256

    def _bind(
        self,
        observation,
        *,
        role: str,
        subject_effect_id: str,
        observed_at_ms: int | None = None,
    ) -> str:
        return self.store.put_repository_observation_binding(
            observation_sha256=observation.observation_sha256,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            subject_effect_id=subject_effect_id,
            observation_role=role,
            observed_at_ms=(
                observed_at_ms
                if observed_at_ms is not None
                else observation.observed_at_ms
            ),
            recorded_at_ms=observation.observed_at_ms + 2,
        )

    def _window(self, *, subject: str | None = None, delta: bool = True):
        """Capture PRE + POST (with a committed change) and bind both."""
        if subject is None:
            subject = self._create_effect(100)
            self._window_subject = subject
        pre_obs = self._capture_observation()
        self._store_content(pre_obs)
        pre_binding = self._bind(pre_obs, role="PRE", subject_effect_id=subject)
        if delta:
            (self._repo / "src").mkdir(exist_ok=True)
            (self._repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
            _git(self._repo, "add", ".")
            _git(self._repo, "commit", "-q", "-m", "change")
        post_obs = self._capture_observation(
            delta_from=pre_obs if delta else None
        )
        self._store_content(post_obs)
        self._pinned_post_sha = post_obs.observation_sha256
        post_binding = self._bind(post_obs, role="POST", subject_effect_id=subject)
        return pre_binding, post_binding

    def _evaluate(self, pre_binding, post_binding, kind, *, subject: str | None = None, **params):
        if subject is None:
            subject = self._window_subject
        predicate = AcceptancePredicate.create(
            predicate_type=kind, subject_kind="repository", params=params or None
        )
        return self.oracle.evaluate(
            subject_effect_id=subject,
            pre_binding_id=pre_binding,
            post_binding_id=post_binding,
            predicate=predicate,
            evaluated_at_ms=30_000,
        )


class ContentTrustTests(RepositoryOracleTestBase):
    """§11 items 7-8: payload tamper + metadata mismatch rejected."""

    def test_item7_tampered_payload_with_claimed_id_rejected(self) -> None:
        observation = self._capture_observation()
        payload = observation.model_dump(mode="json")
        payload["revision"]["head_commit"] = "f" * 40  # tamper content
        with self.assertRaises(ValueError):
            self.store.put_repository_observation(
                observation_sha256=observation.observation_sha256,
                observation_payload=payload,
                request_id=self.request_id,
                run_id=self.run_id,
                generation=2,
                effect_id="eff_x",
                repository_id=observation.identity.repository_id,
                head_commit="f" * 40,
                observed_at_ms=observation.observed_at_ms,
                recorded_at_ms=1,
            )

    def test_item8_external_metadata_mismatch_rejected(self) -> None:
        observation = self._capture_observation()
        payload = observation.model_dump(mode="json")
        with self.assertRaises(ValueError):
            self.store.put_repository_observation(
                observation_sha256=observation.observation_sha256,
                observation_payload=payload,
                request_id=self.request_id,
                run_id=self.run_id,
                generation=2,
                effect_id="eff_x",
                repository_id="somebody_else",  # metadata != payload identity
                head_commit=observation.revision.head_commit,
                observed_at_ms=observation.observed_at_ms,
                recorded_at_ms=1,
            )
        with self.assertRaises(ValueError):
            self.store.put_repository_observation(
                observation_sha256=observation.observation_sha256,
                observation_payload=payload,
                request_id=self.request_id,
                run_id=self.run_id,
                generation=2,
                effect_id="eff_x",
                repository_id=observation.identity.repository_id,
                head_commit="0" * 40,  # wrong head
                observed_at_ms=observation.observed_at_ms,
                recorded_at_ms=1,
            )
        with self.assertRaises(ValueError):
            self.store.put_repository_observation(
                observation_sha256=observation.observation_sha256,
                observation_payload=payload,
                request_id=self.request_id,
                run_id=self.run_id,
                generation=2,
                effect_id="eff_x",
                repository_id=observation.identity.repository_id,
                head_commit=observation.revision.head_commit,
                observed_at_ms=observation.observed_at_ms + 999,
                recorded_at_ms=1,
            )

    def test_item9_same_observation_multiple_bindings_allowed(self) -> None:
        observation = self._capture_observation()
        self._store_content(observation)
        first = self._bind(observation, role="PRE", subject_effect_id=self._create_effect(10))
        second = self._bind(observation, role="PRE", subject_effect_id=self._create_effect(11))
        post = self._bind(observation, role="POST", subject_effect_id=self._create_effect(10))
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, post)
        # idempotent rebind of the identical binding
        self.assertEqual(
            first,
            self._bind(observation, role="PRE", subject_effect_id=self._create_effect(10)),
        )
        # same binding_id with different content is impossible (derived),
        # but a conflicting stored row is rejected:
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute(
                "UPDATE repository_observation_binding SET subject_effect_id = ?"
                " WHERE binding_id = ?",
                (self._create_effect(60), first),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreConflictError):
            self._bind(observation, role="PRE", subject_effect_id=self._create_effect(10))


class WindowTests(RepositoryOracleTestBase):
    """§5 / §11 item 10: subject-effect window enforcement."""

    def test_required_paths_changed_pass_and_fail(self) -> None:
        pre, post = self._window()
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/main.py"]
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)
        missing = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/other.py"]
        )
        self.assertEqual(missing.status, "FAIL")

    def test_item10_pre_post_different_subject_effects_is_error(self) -> None:
        subject = self._create_effect(100)
        pre_obs = self._capture_observation()
        self._store_content(pre_obs)
        pre_binding = self._bind(pre_obs, role="PRE", subject_effect_id=subject)
        (self._repo / "src").mkdir(exist_ok=True)
        (self._repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        _git(self._repo, "add", ".")
        _git(self._repo, "commit", "-q", "-m", "change")
        post_obs = self._capture_observation(delta_from=pre_obs)
        self._store_content(post_obs)
        self._pinned_post_sha = post_obs.observation_sha256
        # POST bound to ANOTHER effect: another step's changes must not
        # satisfy this effect's predicate
        post_binding = self._bind(post_obs, role="POST", subject_effect_id=self._create_effect(50))
        record = self._evaluate(
            pre_binding, post_binding,
            "repository.required_paths_changed", paths=["src/main.py"],
        )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("authority:post_subject_mismatch", record.reason_codes)

    def test_unknown_binding_raises_without_fake_record(self) -> None:
        pre, post = self._window()
        with self.assertRaises(OracleInvocationError):
            self.oracle.evaluate(
                subject_effect_id=self._create_effect(100),
                pre_binding_id="rob_" + "9" * 60,
                post_binding_id=post,
                predicate=AcceptancePredicate.create(
                    predicate_type="repository.source_authority_valid",
                    subject_kind="repository",
                ),
                evaluated_at_ms=30_000,
            )


class MirrorBoundaryTests(RepositoryOracleTestBase):
    """§6 / §11 items 11-12: production-shaped boundary matching."""

    def _ownership(self, targets: list[str]) -> None:
        ownership = {
            "schema": "tiangong.source-ownership.v2",
            "authority_policy": {"editable_roots": ["src"]},
            "mappings": [
                {
                    "id": "m1",
                    "source": "src",
                    "source_role": "authoritative",
                    "targets": targets,
                }
            ],
        }
        (self._repo / "source-ownership.json").write_text(
            json.dumps(ownership), encoding="utf-8"
        )

    def test_item11_directory_target_hit(self) -> None:
        self._ownership(["app/life-service/runtime314/contracts"])
        pre_obs = self._capture_observation()
        self._store_content(pre_obs)
        effect = self._create_effect(20)
        self._window_subject = effect
        pre = self._bind(pre_obs, role="PRE", subject_effect_id=effect)
        mirror = self._repo / "app/life-service/runtime314/contracts"
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "verification.py").write_text("# direct edit\n", encoding="utf-8")
        _git(self._repo, "add", ".")
        _git(self._repo, "commit", "-q", "-m", "mirror edit")
        post_obs = self._capture_observation(delta_from=pre_obs)
        self._store_content(post_obs)
        self._pinned_post_sha = post_obs.observation_sha256
        post = self._bind(post_obs, role="POST", subject_effect_id=effect)
        record = self._evaluate(
            pre, post, "repository.no_generated_mirror_direct_edit",
            subject="eff_m",
        )
        self.assertEqual(record.status, "FAIL")
        self.assertIn("repository.generated_mirror_direct_edit", record.reason_codes)

    def test_item12_similar_prefix_no_false_hit(self) -> None:
        self._ownership(["foo/bar"])
        pre_obs = self._capture_observation()
        self._store_content(pre_obs)
        effect = self._create_effect(20)
        self._window_subject = effect
        pre = self._bind(pre_obs, role="PRE", subject_effect_id=effect)
        decoy = self._repo / "foo/barista"
        decoy.mkdir(parents=True, exist_ok=True)
        (decoy / "x.py").write_text("x = 1\n", encoding="utf-8")
        _git(self._repo, "add", ".")
        _git(self._repo, "commit", "-q", "-m", "decoy")
        post_obs = self._capture_observation(delta_from=pre_obs)
        self._store_content(post_obs)
        self._pinned_post_sha = post_obs.observation_sha256
        post = self._bind(post_obs, role="POST", subject_effect_id=effect)
        record = self._evaluate(
            pre, post, "repository.no_generated_mirror_direct_edit",
            subject="eff_m",
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)

    def test_path_hits_target_unit_semantics(self) -> None:
        self.assertTrue(_path_hits_target("foo/bar", "foo/bar"))
        self.assertTrue(_path_hits_target("foo/bar/x.py", "foo/bar"))
        self.assertTrue(_path_hits_target("foo/bar/x.py", "foo/bar/"))
        self.assertFalse(_path_hits_target("foo/barista/x.py", "foo/bar"))
        self.assertFalse(_path_hits_target("foo/barista", "foo/bar"))


class AuthorityExecutionTests(RepositoryOracleTestBase):
    """§7 / §11 items 13-14: no checked-repo Python, drift gate."""

    def _authority_setup(self):
        self._ownership = None
        ownership = {
            "schema": "tiangong.source-ownership.v2",
            "authority_policy": {"editable_roots": ["src"]},
            "mappings": [
                {
                    "id": "m1",
                    "source": "src",
                    "source_role": "authoritative",
                    "targets": [],
                }
            ],
        }
        (self._repo / "source-ownership.json").write_text(
            json.dumps(ownership), encoding="utf-8"
        )

    def test_item13_malicious_check_script_never_executed(self) -> None:
        self._authority_setup()
        # plant a malicious "validator" inside the checked repo
        malicious = self._repo / "scripts"
        malicious.mkdir(exist_ok=True)
        marker = self.root / "pwned.marker"
        (malicious / "check-source-authority.py").write_text(
            f"from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        pre, post = self._window()
        oracle = RepositoryStateOracle(
            snapshot=self.snapshot,
            store=self.store,
            authority_validator=None,  # default = trusted src module
            resampler=lambda root: self._pinned_post_sha,
        )
        record = oracle.evaluate(
            subject_effect_id=self._create_effect(100),
            pre_binding_id=pre,
            post_binding_id=post,
            predicate=AcceptancePredicate.create(
                predicate_type="repository.source_authority_valid",
                subject_kind="repository",
            ),
            evaluated_at_ms=30_000,
        )
        # the verdict itself may legitimately be PASS (the minimal config
        # validates); the review gate is that the malicious script NEVER ran
        self.assertFalse(marker.exists())

    def test_item14_post_capture_drift_is_error_never_pass(self) -> None:
        self._authority_setup()
        pre, post = self._window()
        # repository changes AFTER the post capture
        (self._repo / "drift.txt").write_text("drift\n", encoding="utf-8")
        oracle = RepositoryStateOracle(
            snapshot=self.snapshot,
            store=self.store,
            authority_validator=lambda root: [],
            resampler=None,  # REAL provider resample
        )
        record = oracle.evaluate(
            subject_effect_id=self._create_effect(100),
            pre_binding_id=pre,
            post_binding_id=post,
            predicate=AcceptancePredicate.create(
                predicate_type="repository.source_authority_valid",
                subject_kind="repository",
            ),
            evaluated_at_ms=30_000,
        )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("authority:post_state_drifted", record.reason_codes)

    def test_real_repo_source_authority_with_pinned_state(self) -> None:
        # integration truth: the trusted validator against THIS repository
        from source_authority.validator import load_config, validate_source_authority

        config = load_config(REPO_ROOT / "source-ownership.json")
        errors = validate_source_authority(
            config, repo_root=REPO_ROOT, require_sources=False
        )
        self.assertEqual(errors, [])


class PersistenceTests(RepositoryOracleTestBase):
    def test_full_chain_record_only_zero_state(self) -> None:
        pre, post = self._window()
        record = self._evaluate(
            pre, post, "repository.required_paths_changed", paths=["src/main.py"]
        )
        self.assertEqual(record.status, "PASS")
        self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=1_500)
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        outcome = recorder.record(record, recorded_at_ms=31_000)
        self.assertTrue(outcome.created_by_this_call)

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
