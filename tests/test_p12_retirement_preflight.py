"""Synthetic Git fixtures for the non-authorizing P12 R0 development audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-p12-static-skill-retirement.py"
spec = importlib.util.spec_from_file_location("p12_retirement_preflight", SCRIPT)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class RetirementPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "P12 Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "core.autocrlf", "false")
        self.put("src/omni_body_skill/runtime.py", "raise RuntimeError('must not be imported')\n")
        self.put("src/total_gateway/skill_selection.py", 'QUERY = "skill.route"\n')
        self.put("app/context.py", 'skill_context = "TIANGONG_ENABLE_LEARNED_SKILL_CONTEXT"\n')
        self.put("src/omni_body_skill/registry/skill_router_index.json", '{"skills": []}\n')
        self.put("src/omni_body_skill/deliverable_skills/demo.md", "fixture only\n")
        self.put("src/learning.py", 'FROZEN = "life.learning.legacy_publication_frozen"\n')
        self.baseline = self.commit("baseline")
        self.put("scripts/audit-p12-static-skill-retirement.py", SCRIPT.read_bytes())
        self.head = self.commit("observer")

    def run_git(self, *args, data=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(self.repo), *args], input=data,
                                capture_output=True, check=True, env=env)
        return result.stdout

    def put(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    def commit(self, message):
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", message)
        return self.run_git("rev-parse", "HEAD").decode().strip()

    def inspect(self):
        return audit_module.audit(self.repo, self.head, self.baseline)

    def test_five_surfaces_are_detected_without_importing_product(self):
        report = self.inspect()
        self.assertEqual(set(report["findings"]), set(audit_module.PATTERNS))
        self.assertTrue(all(report["findings"].values()))
        self.assertTrue(report["r0_scope_passed"])
        self.assertEqual(report["evidence_mode"], "STATIC_GIT_INVENTORY")

    def test_never_authorizes_phase_exit_or_retirement(self):
        report = self.inspect()
        for key in ("p11_exit_proven", "p12_authorized", "retirement_authorized",
                    "production_zero_usage_proven", "production_behavior_changed_by_audit"):
            self.assertIs(report[key], False)

    def test_report_is_deterministic_and_digest_recomputable(self):
        report = self.inspect()
        self.assertEqual(report, self.inspect())
        digest = report.pop("report_sha256")
        raw = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_bad_sha_forms_are_rejected(self):
        for value in ("HEAD", self.head[:7], self.head.upper(), "-x", "f" * 39, "z" * 40):
            with self.subTest(value=value), self.assertRaises(audit_module.AuditError):
                audit_module.audit(self.repo, value, self.baseline)

    def test_wrong_current_head_is_rejected(self):
        with self.assertRaisesRegex(audit_module.AuditError, "HEAD differs"):
            audit_module.audit(self.repo, self.baseline, self.baseline)

    def test_missing_baseline_is_rejected(self):
        with self.assertRaises(audit_module.AuditError):
            audit_module.audit(self.repo, self.head, "0" * 40)

    def test_dirty_tracked_source_is_rejected(self):
        self.put("src/learning.py", "changed = True\n")
        with self.assertRaises(audit_module.AuditError):
            self.inspect()

    def test_staged_source_change_is_rejected(self):
        self.put("src/learning.py", "changed = True\n")
        self.run_git("add", "-A")
        with self.assertRaises(audit_module.AuditError):
            self.inspect()

    def test_observer_bytes_must_match_frozen_commit(self):
        self.put("scripts/audit-p12-static-skill-retirement.py", "print('fake observer')\n")
        self.head = self.commit("fake observer")
        with self.assertRaisesRegex(audit_module.AuditError, "Observer bytes"):
            self.inspect()

    def test_observer_cannot_be_omitted(self):
        (self.repo / "scripts/audit-p12-static-skill-retirement.py").unlink()
        self.head = self.commit("remove observer")
        with self.assertRaisesRegex(audit_module.AuditError, "Observer bytes"):
            self.inspect()

    def test_removing_required_component_is_an_error_not_zero_usage(self):
        (self.repo / "src/total_gateway/skill_selection.py").unlink()
        self.head = self.commit("missing gateway")
        with self.assertRaisesRegex(audit_module.AuditError, "preserved component"):
            self.inspect()

    def test_product_mutation_is_outside_r0_scope(self):
        self.put("src/learning.py", "enabled = True\n")
        self.head = self.commit("product mutation")
        report = self.inspect()
        self.assertFalse(report["r0_scope_passed"])
        self.assertIn({"path": "src/learning.py", "status": "M"}, report["prohibited_changes"])

    def test_product_deletion_is_outside_r0_scope(self):
        (self.repo / "src/learning.py").unlink()
        self.head = self.commit("product deletion")
        report = self.inspect()
        self.assertFalse(report["r0_scope_passed"])
        self.assertIn({"path": "src/learning.py", "status": "D"}, report["prohibited_changes"])

    def test_added_runtime_or_policy_cannot_hide_in_preflight(self):
        self.put("src/second_runtime.py", "pass\n")
        self.put("policy.json", "{}\n")
        self.head = self.commit("outside scope")
        paths = {row["path"] for row in self.inspect()["prohibited_changes"]}
        self.assertEqual(paths, {"src/second_runtime.py", "policy.json"})

    def test_binary_non_utf8_and_large_files_are_disclosed(self):
        self.put("src/native.pyc", b"\0\xff")
        self.put("src/bad.py", b"\xff")
        self.put("src/large.txt", b"x" * 100_001)
        self.head = self.commit("opaque inputs")
        with patch.object(audit_module, "MAX_FILE", 100_000):
            rows = self.inspect()["unscanned_inputs"]
        self.assertEqual({row["reason"] for row in rows},
                         {"non_text_extension", "not_utf8", "file_size_limit"})

    def test_git_symlinks_are_not_followed(self):
        oid = self.run_git("hash-object", "-w", "--stdin", data=b"/do/not/read/external-secret").decode().strip()
        self.run_git("update-index", "--add", "--cacheinfo", "120000", oid, "src/link.py")
        self.run_git("commit", "-qm", "symlink")
        # Native checkout needs no Windows symlink privilege when core.symlinks=false.
        self.run_git("config", "core.symlinks", "false")
        self.run_git("checkout-index", "-f", "--", "src/link.py")
        self.head = self.run_git("rev-parse", "HEAD").decode().strip()
        report = self.inspect()
        self.assertTrue(any(row["path"] == "src/link.py" for row in report["unscanned_inputs"]))
        self.assertNotIn("external-secret", json.dumps(report))

    def test_untracked_files_are_not_claimed_as_snapshot_inputs(self):
        self.put("src/untracked.py", "never_scan_this_secret = 'skill.read'\n")
        report = self.inspect()
        self.assertFalse(any(row["path"] == "src/untracked.py" for row in report["scanned_inputs"]))

    def test_no_matches_never_proves_production_zero_use(self):
        with patch.object(audit_module, "PATTERNS", {"empty": "impossible_pattern_xyz123"}):
            report = self.inspect()
        self.assertEqual(report["findings"]["empty"], [])
        self.assertFalse(report["production_zero_usage_proven"])
        self.assertFalse(report["retirement_authorized"])

    def test_findings_contain_tokens_not_source_lines(self):
        self.put("src/private.py", 'skill_context = "DO_NOT_ECHO_SECRET"\n')
        self.head = self.commit("private fixture")
        self.assertNotIn("DO_NOT_ECHO_SECRET", json.dumps(self.inspect()))

    def test_match_truncation_is_explicit(self):
        self.put("src/repeated.py", "# skill.read\n" * 110)
        self.head = self.commit("many matches")
        rows = self.inspect()["findings"]["static_query"]
        row = next(row for row in rows if row["path"] == "src/repeated.py")
        self.assertEqual(row["match_count"], 110)
        self.assertEqual(len(row["matches"]), 100)
        self.assertTrue(row["matches_truncated"])

    def test_audit_does_not_change_files_or_git_head(self):
        before = {p.relative_to(self.repo).as_posix(): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.repo).parts}
        self.inspect()
        after = {p.relative_to(self.repo).as_posix(): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.repo).parts}
        self.assertEqual(before, after)
        self.assertEqual(self.head, self.run_git("rev-parse", "HEAD").decode().strip())

    def test_native_blob_tampering_and_truncation_are_rejected(self):
        content = b"abc"
        oid = hashlib.sha1(b"blob 3\0" + content).hexdigest()
        good = f"{oid} blob 3\n".encode() + content + b"\n"
        rows = [{"oid": oid, "size": 3}]
        with patch.object(audit_module, "git", return_value=good):
            self.assertEqual(audit_module.read_blobs(self.repo, rows), {oid: content})
        for bad in (b"", good[:-1], good.replace(b"abc", b"abd"), good + b"x",
                    good.replace(b"blob 3", b"tree 3")):
            with self.subTest(payload=bad), patch.object(audit_module, "git", return_value=bad):
                with self.assertRaises(audit_module.AuditError):
                    audit_module.read_blobs(self.repo, rows)

    def test_input_budget_is_bounded(self):
        with patch.object(audit_module, "MAX_TOTAL", 1), self.assertRaises(audit_module.AuditError):
            self.inspect()

    def test_cli_exit_codes_distinguish_scope_from_invalid_input(self):
        command = [os.sys.executable, str(SCRIPT), "--repo", str(self.repo),
                   "--expected-head", self.head, "--baseline-head", self.baseline]
        ok = subprocess.run(command, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertFalse(json.loads(ok.stdout)["retirement_authorized"])
        self.put("src/new.py", "pass\n")
        self.head = self.commit("out of scope")
        command[5] = self.head
        blocked = subprocess.run(command, capture_output=True)
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertFalse(json.loads(blocked.stdout)["r0_scope_passed"])
        command[5] = self.baseline
        invalid = subprocess.run(command, capture_output=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertFalse(json.loads(invalid.stderr)["retirement_authorized"])


if __name__ == "__main__":
    unittest.main()
