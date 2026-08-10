from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "network_capabilities.py"
spec = importlib.util.spec_from_file_location("tiangong_network_capabilities", MODULE_PATH)
network = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(network)


class _Response:
    def __init__(self, payload: bytes, declared: int | None = None):
        self.payload = payload
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def read(self, amount: int) -> bytes:
        return self.payload[:amount]


class NetworkCapabilityTests(unittest.TestCase):
    def test_bounded_download_never_returns_silent_prefix(self):
        self.assertEqual(network.read_bounded_http_body(_Response(b"12345", 5), 5), b"12345")
        with self.assertRaisesRegex(network.NetworkCapabilityError, "too_large"):
            network.read_bounded_http_body(_Response(b"123456", 6), 5)
        with self.assertRaisesRegex(network.NetworkCapabilityError, "too_large"):
            network.read_bounded_http_body(_Response(b"123456", None), 5)

    def test_github_url_policy(self):
        self.assertEqual(
            network.canonical_public_github_repo_url("https://github.com/OpenAI/example.git"),
            "https://github.com/OpenAI/example.git",
        )
        rejected = [
            "http://github.com/a/b.git",
            "https://gitlab.com/a/b.git",
            "git@github.com:a/b.git",
            "https://token@github.com/a/b.git",
            "https://github.com/a/b.git?x=1",
            "https://github.com/a/b/tree/main",
            "file:///tmp/repo",
        ]
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(network.NetworkCapabilityError):
                    network.canonical_public_github_repo_url(value)

    def test_clone_uses_fixed_non_shell_argv_and_secret_free_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = root / "repo"
            calls = []

            def fake_run(command, **kwargs):
                calls.append((list(command), dict(kwargs)))
                if "clone" in command:
                    dest.mkdir()
                    (dest / ".git").mkdir()
                    return mock.Mock(returncode=0, stdout=b"clone ok", stderr=b"")
                return mock.Mock(returncode=0, stdout=(b"a" * 40) + b"\n", stderr=b"")

            env = {"PATH": os.environ.get("PATH", ""), "GITHUB_TOKEN": "must-not-leak", "HTTPS_PROXY": "http://proxy.invalid:8080"}
            with mock.patch.object(network.shutil, "which", return_value="git"), mock.patch.object(network.subprocess, "run", side_effect=fake_run):
                result = network.clone_public_github_repo(
                    "https://github.com/OpenAI/example",
                    dest,
                    timeout_seconds=30,
                    environment=env,
                )
            self.assertTrue(result["success"])
            clone_cmd, clone_kwargs = calls[0]
            self.assertEqual(clone_kwargs["shell"], False)
            self.assertIn("https://github.com/OpenAI/example.git", clone_cmd)
            self.assertIn("credential.helper=", clone_cmd)
            self.assertNotIn("GITHUB_TOKEN", clone_kwargs["env"])
            self.assertEqual(clone_kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(clone_kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(clone_kwargs["env"]["GIT_LFS_SKIP_SMUDGE"], "1")

    def test_failed_clone_removes_only_new_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sentinel = root / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            dest = root / "repo"

            def fake_run(command, **kwargs):
                dest.mkdir(exist_ok=True)
                (dest / "partial").write_text("partial", encoding="utf-8")
                return mock.Mock(returncode=128, stdout=b"", stderr=b"network error")

            with mock.patch.object(network.shutil, "which", return_value="git"), mock.patch.object(network.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(network.NetworkCapabilityError, "git_clone_failed"):
                    network.clone_public_github_repo("https://github.com/OpenAI/example", dest, timeout_seconds=30)
            self.assertFalse(dest.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_runtime_contract_and_manifest_keep_git_clone_typed_not_shell(self):
        contracts_path = ROOT / "readable-python-source" / "omni_body_skill" / "tool_contracts.py"
        omni_path = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "omni_body_tool.py"
        manifest_path = ROOT / "readable-python-source" / "omni_body_skill" / "registry" / "capability_manifest.generated.json"
        contracts = contracts_path.read_text(encoding="utf-8")
        omni = omni_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn('"git.clone": {', contracts)
        self.assertIn('def _action_git_clone(', omni)
        row = manifest["capabilities"]["git.clone"]
        self.assertTrue(row["executable"])
        self.assertEqual(row["risk"], "A2")
        self.assertEqual(row["effect"], "create")
        self.assertEqual(row["handler"], "_action_git_clone")

    def test_generic_sandbox_network_remains_denied(self):
        sandbox = (ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "sandbox_runtime.py").read_text(encoding="utf-8")
        self.assertIn('"TIANGONG_SANDBOX_NETWORK": "denied"', sandbox)


if __name__ == "__main__":
    unittest.main()
