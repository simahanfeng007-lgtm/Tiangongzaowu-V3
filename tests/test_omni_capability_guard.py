from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import os
import re
import tempfile
import time
import types
import unicodedata
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    OmniCapabilityGrantPayload,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    canonical_json_bytes,
)
from total_gateway.tickets import TicketSigner


ROOT = Path(__file__).resolve().parents[1]
OMNI_ROOT = ROOT / "readable-python-source" / "omni_body_skill"
CAPABILITY_SOURCE = OMNI_ROOT / "tools" / "omni_capability.py"
WRAPPER_SOURCE = OMNI_ROOT / "tools" / "omni_body_v3.py"
RUNTIME_SOURCE = OMNI_ROOT / "tools" / "omni_body_tool.py"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_path_boundary():
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"), filename=str(RUNTIME_SOURCE))
    config = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BodyRuntimeConfig")
    runtime = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BodyRuntime")
    methods = [
        node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef) and node.name in {"_is_inside", "_resolve"}
    ]

    class OmniBodyError(RuntimeError):
        pass

    namespace = {
        "BodyRuntimeConfig": None,
        "List": List,
        "OmniBodyError": OmniBodyError,
        "Optional": Optional,
        "Path": Path,
        "dataclass": dataclass,
        "field": field,
        "os": os,
        "re": re,
        "unicodedata": unicodedata,
    }
    exec(compile(ast.Module(body=[config, *methods], type_ignores=[]), str(RUNTIME_SOURCE), "exec"), namespace)

    class Boundary:
        pass

    Boundary._is_inside = namespace["_is_inside"]
    Boundary._resolve = namespace["_resolve"]
    return namespace["BodyRuntimeConfig"], Boundary, OmniBodyError


class CapabilityFixture:
    def __init__(self, root: Path) -> None:
        self.module = load_module("test_omni_capability_consumer", CAPABILITY_SOURCE)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.other_workspace = root / "other"
        self.other_workspace.mkdir()
        self.nonce_root = root / "nonces"
        self.trust_path = root / "trust.json"
        self.private = Ed25519PrivateKey.generate()
        self.signer = TicketSigner("omni_execution_key", self.private)
        self.now_ms = time.time_ns() // 1_000_000
        raw_public = self.private.public_key().public_bytes_raw()
        descriptor = PublicKeyDescriptor(
            kid="omni_execution_key",
            issuer="tiangong-total-gateway",
            audience="tiangong-backend",
            purpose="execution_ticket",
            public_key_base64url=base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii"),
            public_key_sha256=hashlib.sha256(raw_public).hexdigest(),
            state="ACTIVE",
            not_before_ms=self.now_ms - 10_000,
            not_after_ms=self.now_ms + 3_600_000,
            component_manifest_hash=HASH_D,
        )
        self.trust = TrustBundle(
            bundle_id="trust_omni_consumer",
            revision=1,
            gateway_epoch=3,
            generated_at_ms=self.now_ms,
            required_scopes=(
                TrustScope(
                    issuer=descriptor.issuer,
                    audience=descriptor.audience,
                    purpose=descriptor.purpose,
                ),
            ),
            keys=(descriptor,),
            production_ready=True,
            bundle_sha256=HASH_A,
        ).with_computed_sha256()
        self.trust_path.write_bytes(canonical_json_bytes(self.trust.model_dump(mode="json")))

    def env(self):
        return {
            "TIANGONG_OMNI_TRUST_BUNDLE_PATH": str(self.trust_path),
            "TIANGONG_OMNI_TRUST_BUNDLE_SHA256": self.trust.bundle_sha256,
            "TIANGONG_OMNI_GATEWAY_EPOCH": "3",
            "TIANGONG_OMNI_NONCE_ROOT": str(self.nonce_root),
        }

    def grant(self, nonce: str, *, skill: bool = False, expired: bool = False):
        args = {"command": "echo safe"}
        action = "shell.run"
        target = "result.txt"
        issued = self.now_ms - 70_000 if expired else self.now_ms - 1_000
        expires = self.now_ms - 10_000 if expired else self.now_ms + 30_000
        payload = OmniCapabilityGrantPayload(
            grant_id="grant_" + nonce,
            ticket_id="ticket_omni_guard",
            decision_id="decision_omni_guard",
            decision_sha256=HASH_A,
            impact_sha256=HASH_B,
            action_permission_sha256=HASH_C,
            action_registry_sha256=HASH_D,
            capability_manifest_hash=HASH_C,
            component_manifest_hash=HASH_D,
            action_id=action,
            action_version="omni-registry-v1",
            arguments_sha256=self.module.invocation_arguments_sha256(action, target, args),
            workspace_id="workspace_main",
            workspace_scope_hash=self.module.workspace_scope_hash(str(self.workspace)),
            principal_scope_hash=HASH_A,
            risk_class="A4",
            allowed_side_effects=("local_write", "read"),
            path_policy="workspace_only",
            allow_absolute_paths=True,
            allow_shell=True,
            allow_python=False,
            confirmation_sha256=None,
            skill_id="skill_main" if skill else None,
            skill_version="1.0.0" if skill else None,
            skill_sha256=HASH_C if skill else None,
            skill_activation_sha256=HASH_D if skill else None,
            gateway_epoch=3,
            nonce=nonce,
            issued_at_ms=issued,
            not_before_ms=issued,
            expires_at_ms=expires,
        )
        return self.signer.sign_omni_capability(payload), action, target, args

    def runtime_meta(self, *, skill: bool = False):
        values = {
            "execution_ticket_id": "ticket_omni_guard",
            "principal_scope_hash": HASH_A,
            "workspace_id": "workspace_main",
            "action_version": "omni-registry-v1",
            "decision_sha256": HASH_A,
            "impact_sha256": HASH_B,
            "action_permission_sha256": HASH_C,
            "action_registry_sha256": HASH_D,
            "capability_manifest_hash": HASH_C,
            "component_manifest_hash": HASH_D,
            "confirmation_sha256": None,
        }
        if skill:
            values.update(
                {
                    "skill_id": "skill_main",
                    "skill_version": "1.0.0",
                    "skill_sha256": HASH_C,
                    "skill_activation_sha256": HASH_D,
                }
            )
        return values


class OmniCapabilityConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CapabilityFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, grant, action, target, args, runtime, *, workspace=None):
        with mock.patch.dict(os.environ, self.fixture.env(), clear=False):
            return self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json") if hasattr(grant, "model_dump") else grant,
                action=action,
                target=target,
                args=args,
                workspace=str(workspace or self.fixture.workspace),
                runtime_meta=runtime,
            )

    def test_exact_signed_grant_enables_privilege_once(self) -> None:
        grant, action, target, args = self.fixture.grant("nonce_exact")
        result = self.verify(grant, action, target, args, self.fixture.runtime_meta())
        self.assertEqual(
            (result["allow_shell"], result["allow_python"], result["allow_absolute_paths"], result["confirmed"]),
            (True, False, True, False),
        )
        with self.assertRaisesRegex(self.fixture.module.CapabilityGrantError, "replay"):
            self.verify(grant, action, target, args, self.fixture.runtime_meta())

    def test_gateway_inline_trust_bundle_works_without_file_environment_pin(self) -> None:
        grant, action, target, args = self.fixture.grant("nonce_inline_trust")
        runtime = self.fixture.runtime_meta()
        runtime.update(
            {
                "gateway_epoch": self.fixture.trust.gateway_epoch,
                "trust_bundle_sha256": self.fixture.trust.bundle_sha256,
                "trust_bundle": self.fixture.trust.model_dump(mode="json"),
            }
        )
        state_root = Path(self.temporary.name) / "omni_state"
        with mock.patch.dict(
            os.environ,
            {"TIANGONG_OMNI_BODY_STATE_ROOT": str(state_root)},
            clear=True,
        ):
            result = self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json"),
                action=action,
                target=target,
                args=args,
                workspace=str(self.fixture.workspace),
                runtime_meta=runtime,
            )
        self.assertTrue(result["allow_shell"])
        self.assertTrue((state_root / "capability_nonces").is_dir())

    def test_gateway_inline_trust_bundle_pin_is_fail_closed(self) -> None:
        grant, action, target, args = self.fixture.grant("nonce_inline_tampered")
        runtime = self.fixture.runtime_meta()
        trust = self.fixture.trust.model_dump(mode="json")
        trust["bundle_id"] = "trust_tampered"
        runtime.update(
            {
                "gateway_epoch": self.fixture.trust.gateway_epoch,
                "trust_bundle_sha256": self.fixture.trust.bundle_sha256,
                "trust_bundle": trust,
            }
        )
        with mock.patch.dict(
            os.environ,
            {"TIANGONG_OMNI_BODY_STATE_ROOT": str(Path(self.temporary.name) / "omni_state_bad")},
            clear=True,
        ), self.assertRaisesRegex(self.fixture.module.CapabilityGrantError, "pin or epoch"):
            self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json"),
                action=action,
                target=target,
                args=args,
                workspace=str(self.fixture.workspace),
                runtime_meta=runtime,
            )

    def test_action_args_workspace_principal_skill_epoch_expiry_and_signature_are_bound(self) -> None:
        cases = []
        grant, action, target, args = self.fixture.grant("nonce_action")
        cases.append((grant, "python.run", target, args, self.fixture.workspace, self.fixture.runtime_meta(), "action binding"))
        grant, action, target, args = self.fixture.grant("nonce_args")
        cases.append((grant, action, target, {"command": "evil"}, self.fixture.workspace, self.fixture.runtime_meta(), "argument binding"))
        grant, action, target, args = self.fixture.grant("nonce_workspace")
        cases.append((grant, action, target, args, self.fixture.other_workspace, self.fixture.runtime_meta(), "workspace binding"))
        grant, action, target, args = self.fixture.grant("nonce_principal")
        runtime = self.fixture.runtime_meta()
        runtime["principal_scope_hash"] = HASH_D
        cases.append((grant, action, target, args, self.fixture.workspace, runtime, "principal_scope_hash"))
        grant, action, target, args = self.fixture.grant("nonce_skill", skill=True)
        runtime = self.fixture.runtime_meta(skill=True)
        runtime["skill_id"] = "skill_other"
        cases.append((grant, action, target, args, self.fixture.workspace, runtime, "Skill binding"))
        grant, action, target, args = self.fixture.grant("nonce_expired", expired=True)
        cases.append((grant, action, target, args, self.fixture.workspace, self.fixture.runtime_meta(), "time or authority"))
        grant, action, target, args = self.fixture.grant("nonce_signature")
        forged = grant.model_copy(update={"signature": "A" * 86})
        cases.append((forged, action, target, args, self.fixture.workspace, self.fixture.runtime_meta(), "signature is invalid"))
        for item, action, target, args, workspace, runtime, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(self.fixture.module.CapabilityGrantError, expected):
                self.verify(item, action, target, args, runtime, workspace=workspace)

        grant, action, target, args = self.fixture.grant("nonce_epoch")
        bad_env = dict(self.fixture.env(), TIANGONG_OMNI_GATEWAY_EPOCH="4")
        with mock.patch.dict(os.environ, bad_env, clear=False), self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "pin or epoch"
        ):
            self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json"),
                action=action,
                target=target,
                args=args,
                workspace=str(self.fixture.workspace),
                runtime_meta=self.fixture.runtime_meta(),
            )

    def test_unknown_or_incomplete_signed_fields_fail_closed(self) -> None:
        grant, action, target, args = self.fixture.grant("nonce_fields")
        raw = grant.model_dump(mode="json")
        raw["payload"]["unknown_authority"] = True
        with self.assertRaisesRegex(self.fixture.module.CapabilityGrantError, "fields are incomplete or unknown"):
            self.verify(raw, action, target, args, self.fixture.runtime_meta())


class OmniWrapperAndPathTests(unittest.TestCase):
    def test_defaults_are_deny_and_model_confirmation_or_flags_are_removed(self) -> None:
        Config, _, _ = load_path_boundary()
        defaults = Config()
        self.assertFalse(defaults.allow_absolute_paths)
        self.assertFalse(defaults.allow_shell)
        self.assertFalse(defaults.allow_python)
        self.assertFalse(defaults.require_confirmation_for_a4)

        wrapper = load_module("test_omni_signed_wrapper", WRAPPER_SOURCE)
        captured = {}

        class FakeConfig:
            def __init__(self, **values):
                captured["config"] = values

        class FakeRuntime:
            def __init__(self, config):
                self.config = config

            def run(self, *, action, target, args):
                captured["args"] = dict(args)
                return {"success": False, "needs_confirmation": True}

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            wrapper, "_import_runtime", return_value=(FakeRuntime, FakeConfig, None)
        ), mock.patch.object(
            wrapper, "_verify_capability", return_value={}
        ), mock.patch.dict(
            os.environ,
            {
                "TIANGONG_OMNI_ALLOW_SHELL": "1",
                "TIANGONG_OMNI_ALLOW_PYTHON": "1",
                "TIANGONG_OMNI_ALLOW_ABSOLUTE_PATHS": "1",
            },
            clear=False,
        ):
            blocked = wrapper.run_omni_body(
                {
                    "action": "shell.run",
                    "target": "x",
                    "workspace": temp,
                    "allow_shell": True,
                    "allow_python": True,
                    "allow_absolute_paths": True,
                    "args": {"confirmed": True},
                }
            )
            self.assertIn("[CAPABILITY_REQUIRED]", blocked["cuowu"])
            self.assertNotIn("config", captured)
            wrapper.run_omni_body(
                {
                    "action": "shell.run",
                    "target": "x",
                    "workspace": temp,
                    "allow_shell": True,
                    "allow_python": True,
                    "allow_absolute_paths": True,
                    "args": {"confirmed": True},
                    "__capability_grant": {"signed": "placeholder"},
                }
            )
        self.assertFalse(captured["config"]["allow_shell"])
        self.assertFalse(captured["config"]["allow_python"])
        self.assertFalse(captured["config"]["allow_absolute_paths"])
        self.assertNotIn("confirmed", captured["args"])

    def test_path_traversal_device_unc_symlink_hardlink_and_unicode_attacks_are_rejected(self) -> None:
        Config, Boundary, OmniBodyError = load_path_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            boundary = Boundary()
            boundary.workspace = root.resolve()
            boundary.config = Config(workspace=str(root))
            outside = Path(temp) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            inside = root / "inside.txt"
            inside.write_text("inside", encoding="utf-8")
            self.assertEqual(boundary._resolve(str(inside.resolve()), must_exist=True), inside.resolve())
            attacks = (
                str(outside.resolve()),
                "../outside.txt",
                r"\\server\share\file.txt",
                r"C:\Windows\system.ini",
                r"\\?\C:\Windows\system.ini",
                "e\u0301.txt",
            )
            for attack in attacks:
                with self.subTest(attack=attack), self.assertRaises(OmniBodyError):
                    boundary._resolve(attack)

            original = root / "original.txt"
            original.write_text("data", encoding="utf-8")
            hardlink = root / "hardlink.txt"
            os.link(original, hardlink)
            with self.assertRaisesRegex(OmniBodyError, "hard-linked"):
                boundary._resolve("hardlink.txt", must_exist=True)

            symlink = root / "symlink.txt"
            try:
                symlink.symlink_to(outside)
            except OSError:
                symlink = None
            if symlink is not None:
                with self.assertRaisesRegex(OmniBodyError, "reparse"):
                    boundary._resolve("symlink.txt", must_exist=True)


if __name__ == "__main__":
    unittest.main()
