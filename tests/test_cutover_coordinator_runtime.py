from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from total_gateway.cutover_coordinator import ChannelCutoverCoordinator
from total_gateway.store import StoreConflictError


class ChannelCutoverCoordinatorRuntimeTests(unittest.TestCase):
    def test_clean_install_discovers_current_secure_credentials_without_legacy_file(self) -> None:
        coordinator = ChannelCutoverCoordinator(
            runtime=SimpleNamespace(),
            communication_token="c" * 48,
            component_manifest=SimpleNamespace(manifest_sha256="a" * 64),
            delivery_trust_bundle_factory=lambda now_ms: ("trust", now_ms),
        )
        activated: list[tuple[str, str, str]] = []
        authorities: list[tuple[object, object]] = []

        class Client:
            def health(self):
                return {"instance_id": "communication-clean-install"}

            def migrate_legacy_credentials(self):
                return {"migrated": []}

            def credential_status(self):
                return {
                    "credentials": [
                        {
                            "channel": "wechat",
                            "tenant_id": "wechat",
                            "link_account_id": "qr-account",
                            "configured": True,
                        }
                    ]
                }

            def install_delivery_authority(self, trust, components):
                authorities.append((trust, components))

        coordinator._client = Client()  # type: ignore[assignment]
        coordinator._activate = lambda _candidate, item, _now: activated.append(  # type: ignore[method-assign]
            (str(item["channel"]), str(item["tenant_id"]), str(item["link_account_id"]))
        )

        coordinator._bootstrap()

        self.assertEqual(activated, [("wechat", "wechat", "qr-account")])
        self.assertEqual(len(authorities), 1)

    def test_transient_store_conflict_is_recorded_and_retried(self) -> None:
        coordinator = ChannelCutoverCoordinator(
            runtime=SimpleNamespace(),
            communication_token="c" * 48,
            component_manifest=SimpleNamespace(),
            delivery_trust_bundle_factory=lambda _now_ms: None,
        )
        observed_errors: list[str | None] = []
        original_set_error = coordinator._set_error
        attempts = 0

        def record_error(code: str | None) -> None:
            observed_errors.append(code)
            original_set_error(code)

        def renew_due(_now_ms: int) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise StoreConflictError(
                    "prior channel ownership epoch has not expired safely"
                )
            coordinator._closed.set()

        coordinator._set_error = record_error  # type: ignore[method-assign]
        coordinator._renew_due = renew_due  # type: ignore[method-assign]
        coordinator._bootstrap = lambda: None  # type: ignore[method-assign]
        coordinator._closed = threading.Event()

        coordinator.start()
        assert coordinator._thread is not None
        coordinator._thread.join(timeout=3.0)

        self.assertFalse(coordinator._thread.is_alive())
        self.assertEqual(attempts, 2)
        self.assertIn(
            "prior channel ownership epoch has not expired safely",
            observed_errors,
        )


if __name__ == "__main__":
    unittest.main()
