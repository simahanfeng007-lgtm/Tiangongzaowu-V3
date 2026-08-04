import json
import tempfile
import unittest
from pathlib import Path

from total_gateway.bootstrap import (
    EpochStateError,
    GatewayConfig,
    GatewayConfigurationError,
    InstanceEpochLease,
    SingleInstanceError,
    probe_disk_health,
)


class GatewayConfigurationTests(unittest.TestCase):
    def test_production_is_loopback_port_7184_and_paths_are_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token = "shadow-token-" + "a" * 40
            communication_token = "communication-token-" + "b" * 40
            skill_root = Path(temporary) / "skills"
            skill_root.mkdir()
            config = GatewayConfig.from_environment(
                {
                    "TIANGONG_GATEWAY_STATE_ROOT": str(Path(temporary) / "state"),
                    "TIANGONG_GATEWAY_SHADOW_TOKEN": token,
                    "TIANGONG_GATEWAY_COMMUNICATION_TOKEN": communication_token,
                    "TIANGONG_GATEWAY_SKILL_ROOT": str(skill_root),
                    "TIANGONG_GATEWAY_URL": "http://127.0.0.1:7184",
                }
            )
            self.assertEqual(config.bind_host, "127.0.0.1")
            self.assertEqual(config.port, 7184)
            self.assertTrue(config.state_root.is_absolute())
            self.assertEqual(config.shadow_api_token, token)
            self.assertEqual(config.communication_api_token, communication_token)
            self.assertEqual(config.skill_root, skill_root.resolve())
            self.assertNotIn(token, repr(config))
            self.assertNotIn(communication_token, repr(config))

    def test_rejects_unknown_secret_like_variable_bad_integer_or_relative_root(self) -> None:
        cases = (
            {"TIANGONG_GATEWAY_API_KEY": "secret"},
            {"TIANGONG_GATEWAY_PORT": "+7184"},
            {"TIANGONG_GATEWAY_STATE_ROOT": "relative/state"},
            {"TIANGONG_GATEWAY_PORT": "7185"},
            {"TIANGONG_GATEWAY_SHADOW_TOKEN": "too-short"},
            {"TIANGONG_GATEWAY_COMMUNICATION_TOKEN": "too-short"},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(GatewayConfigurationError):
                GatewayConfig.from_environment(values)


class InstanceEpochTests(unittest.TestCase):
    def test_second_instance_is_rejected_and_restart_advances_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            first = InstanceEpochLease.acquire(root, "gateway-instance-001", now_ms=1_000)
            self.assertEqual(first.gateway_epoch, 1)
            with self.assertRaises(SingleInstanceError):
                InstanceEpochLease.acquire(root, "gateway-instance-002", now_ms=2_000)
            first.release()
            second = InstanceEpochLease.acquire(root, "gateway-instance-002", now_ms=3_000)
            self.assertEqual(second.gateway_epoch, 2)
            record = json.loads((root / "gateway.epoch.json").read_text(encoding="utf-8"))
            self.assertEqual(record["previous_instance_id"], "gateway-instance-001")
            second.release()

    def test_missing_or_corrupt_epoch_in_initialized_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            first = InstanceEpochLease.acquire(root, "gateway-instance-001", now_ms=1_000)
            first.release()
            (root / "gateway.epoch.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(EpochStateError):
                InstanceEpochLease.acquire(root, "gateway-instance-002", now_ms=2_000)

            (root / "gateway.epoch.json").unlink()
            with self.assertRaises(EpochStateError):
                InstanceEpochLease.acquire(root, "gateway-instance-003", now_ms=3_000)


class DiskHealthTests(unittest.TestCase):
    def test_write_fsync_readback_probe_leaves_no_probe_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = probe_disk_health(
                root,
                min_free_bytes=1_048_576,
                probe_bytes=128,
                now_ms=1_000,
            )
            self.assertTrue(evidence.healthy)
            self.assertEqual(evidence.reason_code, "disk.ok")
            self.assertFalse(any(path.name.startswith(".disk-probe-") for path in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
