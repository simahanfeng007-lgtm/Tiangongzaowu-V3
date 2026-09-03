from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


store_path = "src/total_gateway/store.py"
store = read(store_path)
store = replace_once(
    store,
    "CHANNEL_LEASE_CLOCK_SKEW_MS = 5_000\n",
    "CHANNEL_LEASE_CLOCK_SKEW_MS = 5_000\n"
    "_LIMITED_ACTIVATION_BUNDLE_WRITE_TOKEN = object()\n",
    "bundle write token",
)
store = replace_once(
    store,
    '''    @property
    def authority_kind(self) -> str:
        from .composition_activation_registration import (
            EXISTING_GATEWAY_STATE_STORE_AUTHORITY,
        )

        return EXISTING_GATEWAY_STATE_STORE_AUTHORITY

''',
    "",
    "remove direct port authority",
)
store = replace_once(
    store,
    '''    def put_limited_activation_registration(
        self,
        registration,
        *,
        expected_absent: bool,
        recorded_at_ms: int,
    ) -> bool:
        """Persist one P7B.1 eligibility row under existing Store authority."""

        from contracts.verification import VerificationPlan
''',
    '''    def _put_limited_activation_registration_from_bundle(
        self,
        registration,
        *,
        expected_absent: bool,
        recorded_at_ms: int,
        _bundle_write_token: object,
    ) -> bool:
        """Private sink reachable only through the authoritative bundle path."""

        if _bundle_write_token is not _LIMITED_ACTIVATION_BUNDLE_WRITE_TOKEN:
            raise StoreConflictError(
                "limited activation writes require the authoritative bundle path"
            )

        from contracts.verification import VerificationPlan
''',
    "privatize registration insert",
)
store = replace_once(
    store,
    '''        from .composition_activation_registration import (
            LimitedCompositionActivationRegistrar,
        )

        with self._lock, self._write_transaction():
            registry_created = self.put_registry_snapshot(
''',
    '''        from .composition_activation_registration import (
            EXISTING_GATEWAY_STATE_STORE_AUTHORITY,
            LimitedCompositionActivationRegistrar,
            compile_limited_activation_registration,
        )

        with self._lock, self._write_transaction():
            expected_registration = compile_limited_activation_registration(
                proposal,
                plan=plan,
                validation=validation,
                action_registry=action_registry,
                verification_registry=verification_registry,
                verification_bindings=verification_bindings,
                current_world_state_sha256=current_world_state_sha256,
                expected_principal_scope_hash=expected_principal_scope_hash,
                registered_at_ms=recorded_at_ms,
            )

            class _BundleRegistrationPort:
                authority_kind = EXISTING_GATEWAY_STATE_STORE_AUTHORITY

                def __init__(self, owner, expected) -> None:
                    self._owner = owner
                    self._expected = expected

                def get_limited_activation_registration(self, registration_id):
                    if registration_id != self._expected.registration_id:
                        raise StoreConflictError(
                            "limited activation bundle requested another identity"
                        )
                    return self._owner.get_limited_activation_registration(
                        registration_id
                    )

                def put_limited_activation_registration(
                    self,
                    registration,
                    *,
                    expected_absent: bool,
                    recorded_at_ms: int,
                ) -> bool:
                    if registration != self._expected:
                        raise StoreConflictError(
                            "limited activation bundle write differs from rebuilt authority"
                        )
                    return self._owner._put_limited_activation_registration_from_bundle(
                        registration,
                        expected_absent=expected_absent,
                        recorded_at_ms=recorded_at_ms,
                        _bundle_write_token=(
                            _LIMITED_ACTIVATION_BUNDLE_WRITE_TOKEN
                        ),
                    )

            registration_port = _BundleRegistrationPort(
                self, expected_registration
            )
            registry_created = self.put_registry_snapshot(
''',
    "insert guarded bundle port",
)
store = replace_once(
    store,
    "            receipt = LimitedCompositionActivationRegistrar(self).register(\n",
    "            receipt = LimitedCompositionActivationRegistrar(\n"
    "                registration_port\n"
    "            ).register(\n",
    "route registrar through guarded port",
)
write(store_path, store)

# Add a focused regression proving the Store object is no longer the public port.
test_path = "tests/test_composition_activation_store_p7b2.py"
test = read(test_path)
test = replace_once(
    test,
    '''from total_gateway.composition_activation_shadow import (
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
''',
    '''from total_gateway.composition_activation_registration import (
    ExistingGatewayActivationRegistrationPort,
    compile_limited_activation_registration,
)
from total_gateway.composition_activation_shadow import (
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
''',
    "test imports",
)
new_test = r'''

def test_direct_store_registration_port_is_closed_and_private_sink_is_guarded() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            registration = compile_limited_activation_registration(
                fixture["proposal"],
                plan=fixture["plan"],
                validation=fixture["validation"],
                action_registry=fixture["action_registry"],
                verification_registry=fixture["verification_registry"],
                verification_bindings=fixture["verification_bindings"],
                current_world_state_sha256=fixture[
                    "current_world_state_sha256"
                ],
                expected_principal_scope_hash=fixture[
                    "expected_principal_scope_hash"
                ],
                registered_at_ms=1_600,
            )
            assert not isinstance(
                store, ExistingGatewayActivationRegistrationPort
            )
            assert not hasattr(store, "put_limited_activation_registration")
            with pytest.raises(
                StoreConflictError,
                match="authoritative bundle path",
            ):
                store._put_limited_activation_registration_from_bundle(
                    registration,
                    expected_absent=True,
                    recorded_at_ms=1_600,
                    _bundle_write_token=object(),
                )
            assert store._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0] == 0

'''
store_test_anchor = "\ndef test_p7b2_has_no_second_store_or_execution_authority() -> None:\n"
test = replace_once(
    test,
    store_test_anchor,
    new_test + store_test_anchor,
    "insert direct write guard test",
)
write(test_path, test)

# Record the stronger architecture boundary.
doc_path = "docs/capability-composition/P7B2_GATEWAY_STORE_REGISTRATION.md"
doc = read(doc_path)
doc = replace_once(
    doc,
    '''No in-memory production dictionary participates in the decision.

## Restart and expiry
''',
    '''No in-memory production dictionary participates in the decision.

The `GatewayStateStore` object itself is deliberately **not** the public P7B.1
registration port. The bundle method first rebuilds the expected registration
from P7A/P7B.1 authorities, creates a transaction-local private port bound to
that exact object, and presents that port to the registrar. The underlying
Store insert is private and requires a module-local unforgeable identity token.
Calling the Store directly therefore cannot bypass the authoritative rebuild.

## Restart and expiry
''',
    "document write guard",
)
write(doc_path, doc)

# Refresh the explicit P19 authority freeze after changing store.py.
freeze_path = ROOT / "docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json"
freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
freeze["authority_surface_sha256"][store_path] = hashlib.sha256(
    (ROOT / store_path).read_bytes()
).hexdigest()
freeze_path.write_text(
    json.dumps(freeze, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8",
    newline="\n",
)

print(json.dumps({"ok": True, "hardened": "P7B.2-store-write-boundary"}))
