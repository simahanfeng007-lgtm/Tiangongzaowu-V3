"""第四批审计修复回归：网关安全五项高危。

1. soul-backup destination/verify 路径校验（任意路径写/存在性 oracle）
2. 已取消的 request generation 不能被 acquire_generation_lease 复活
3. dispatch permit 释放凭据化：无证报错、重复释放幂等、CLAIMED 直达
   完结不盗扣 inflight
4. 自迭代补丁不得触碰安全关键面（信任根/测试/凭据路径）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import derive_effect_identity, derive_run_identity
from total_gateway.desktop_api import (
    _validated_soul_backup_destination,
    _validated_soul_backup_verify_path,
)
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.store import GatewayStateStore, StoreConflictError

REQUEST_ID = "req_" + "3" * 64
RUN_ID = derive_run_identity(REQUEST_ID, 1).run_id
HASH_A = "a" * 64
HASH_B = "b" * 64


def _claim() -> EffectClaim:
    identity = derive_effect_identity(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        run_sequence=1,
        generation=1,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=HASH_A,
    )
    return EffectClaim(
        **identity.model_dump(),
        owner_component_id="tiangong-backend",
        claimed_at_ms=1_000,
        claim_sha256=HASH_B,
    ).with_computed_sha256()


def _result(effect_id: str) -> EffectResult:
    return EffectResult(
        result_id="effect_result_audit4_" + effect_id[4:16],
        effect_id=effect_id,
        status="SUCCEEDED",
        fact_id="fact_execution_audit4",
        result_object_id="result_object_audit4",
        result_object_sha256=HASH_B,
        evidence_sha256=HASH_A,
        observed_at_ms=1_400,
        result_sha256=HASH_B,
    ).with_computed_sha256()


class SoulBackupPathValidationTests(unittest.TestCase):
    def test_destination_must_be_absolute_new_tgsoul_in_existing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = _validated_soul_backup_destination(str(root / "soul-1.tgsoul"))
            self.assertEqual(good.suffix, ".tgsoul")
            for bad in (
                "soul-relative.tgsoul",                                   # 相对路径
                str(root / "soul-1.exe"),                                  # 后缀
                str(root / "missing-parent" / "soul-1.tgsoul"),            # 父目录不存在
            ):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        _validated_soul_backup_destination(bad)
            existing = root / "exists.tgsoul"
            existing.write_bytes(b"x")
            with self.assertRaises(ValueError):                            # 不得覆盖
                _validated_soul_backup_destination(str(existing))

    def test_verify_path_must_stay_inside_backup_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_root = root / "soul-backups"
            backup_root.mkdir()
            ok = _validated_soul_backup_verify_path(
                str(backup_root / "soul-1.tgsoul"), backup_root
            )
            self.assertTrue(ok.is_relative_to(backup_root))
            with self.assertRaises(ValueError):
                _validated_soul_backup_verify_path(str(root / "outside.tgsoul"), backup_root)
            with self.assertRaises(ValueError):
                _validated_soul_backup_verify_path(str(root / ".." / "escape.tgsoul"), backup_root)


class GenerationRevivalGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(
            Path(self.temporary.name) / "gateway.sqlite3", now_ms=900
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _acquire(self, **overrides):
        values = {
            "request_id": REQUEST_ID,
            "run_id": RUN_ID,
            "run_sequence": 1,
            "generation": 1,
            "gateway_epoch": 3,
            "lease_id": "lease_001",
            "owner_instance_id": "gateway_instance_001",
            "issued_at_ms": 1_000,
            "lease_duration_ms": 10_000,
        }
        values.update(overrides)
        return self.store.acquire_generation_lease(**values)

    def test_cancelled_generation_cannot_be_revived_by_next_lease(self) -> None:
        self._acquire()
        cancelled = self.store.cancel_generation(
            REQUEST_ID, reason_code="user_cancel", cancelled_at_ms=1_500
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        # 修复前：一次普通的 generation+1 获取会把 CANCELLED 的 fence 改写
        # SUPERSEDED、请求复活为 ACTIVE 并抹掉 cancel_reason_code。
        with self.assertRaises(StoreConflictError):
            self._acquire(
                generation=2,
                lease_id="lease_002",
                issued_at_ms=2_000,
            )
        view = self.store.get_generation(REQUEST_ID)
        self.assertEqual(view.status, "CANCELLED")
        self.assertEqual(view.cancel_reason_code, "user_cancel")


class DispatchPermitAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(
            Path(self.temporary.name) / "gateway.sqlite3", now_ms=900
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_release_requires_issued_permit_and_repeated_release_is_idempotent(self) -> None:
        claim = _claim()
        self.store.claim_effect(claim)
        with self.assertRaises(StoreConflictError):
            self.store.release_dispatch_permit(
                effect_id=claim.effect_id, attempt=1, now_ms=1_100
            )
        self.store.acquire_dispatch_permit(
            effect_id=claim.effect_id, attempt=1,
            expected_fence_epoch=0, nonce_sha256=HASH_A,
            ticket_id="ticket-1", ticket_sha256=HASH_B, now_ms=1_100,
        )
        self.assertEqual(self.store.action_fence_status()["inflight_count"], 1)
        self.store.complete_effect(_result(claim.effect_id))
        # 成对调用（complete 后显式 release）：幂等返回，不再递减。
        self.store.release_dispatch_permit(
            effect_id=claim.effect_id, attempt=1, now_ms=1_500
        )
        self.assertEqual(self.store.action_fence_status()["inflight_count"], 0)

    def test_completing_without_permit_does_not_drain_someone_elses_inflight(self) -> None:
        holder = _claim()
        self.store.claim_effect(holder)
        self.store.acquire_dispatch_permit(
            effect_id=holder.effect_id, attempt=1,
            expected_fence_epoch=0, nonce_sha256=HASH_A,
            ticket_id="ticket-2", ticket_sha256=HASH_B, now_ms=1_100,
        )
        # 另一个 effect 从 CLAIMED 直接完结（无 permit）。
        other_identity = derive_effect_identity(
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            run_sequence=1,
            generation=1,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=HASH_A,
        )
        other = EffectClaim(
            **other_identity.model_dump(),
            owner_component_id="tiangong-backend",
            claimed_at_ms=1_000,
            claim_sha256=HASH_B,
        ).with_computed_sha256()
        self.store.claim_effect(other)
        # 不经 permit 的执行路径：start（不增计数）→ complete。
        # 修复前：complete 无条件递减，盗扣 holder 的在途计数。
        self.store.mark_effect_started(other.effect_id, started_at_ms=1_200)
        self.store.complete_effect(_result(other.effect_id))
        self.assertEqual(self.store.action_fence_status()["inflight_count"], 1)


class SelfIterationForbiddenSurfaceTests(unittest.TestCase):
    def test_security_critical_targets_are_rejected_before_resolution(self) -> None:
        from total_gateway.embedded_backend import EmbeddedBackendRuntime

        backend = object.__new__(EmbeddedBackendRuntime)
        for target in (
            "tests/test_anything.py",
            "runtime_security/ticket_verification.py",
            "contracts/canonical.py",
            "total_gateway/backend_client.py",
            "total_gateway/store.py",
            "total_gateway/desktop_api.py",
            "credentials/vault.json",
            "config/secrets.yaml",
        ):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    backend._resolve_self_iteration_target(target)


if __name__ == "__main__":
    unittest.main()
