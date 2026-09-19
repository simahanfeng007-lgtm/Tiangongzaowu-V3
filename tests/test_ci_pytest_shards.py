"""CI-only partition contracts; no product acceptance is inferred from fixtures."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci-pytest-shards.py"
spec = importlib.util.spec_from_file_location("ci_shard_contract", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NODES = ["cases/test_a.py::test_first", "cases/test_a.py::test_second", "cases/test_b.py::test_last"]
IDENTITY = {"head": "a" * 40, "tree": "b" * 40, "observer_sha256": "c" * 64}


def seal(report):
    report.pop("report_sha256", None)
    report["report_sha256"] = m.digest(report)
    return report


def reports():
    result = []
    for index, selected in enumerate(m.partition(NODES, 2)):
        result.append(seal({
            "schema": m.SCHEMA, "identity": IDENTITY, "shard": index, "shards": 2,
            "run_id": "run", "run_attempt": "1", "platform": "win32", "ci_env": "1",
            "python": "fixture", "pytest": "fixture", "exit_code": 0, "session_finished": True,
            "collected": NODES.copy(), "selected": selected.copy(), "started": selected.copy(),
            "finished": selected.copy(), "collection_skips": [], "collection_errors": [],
            "violations": [], "failed_subtests": [],
            "outcomes": {node: {phase: [{"outcome": "passed", "wasxfail": False}]
                                 for phase in ("setup", "call", "teardown")} for node in selected},
        }))
    return result


def merge(rows):
    return m.aggregate(rows, IDENTITY, 2, "run", "1", "win32", "1")


class ShardUnionContracts(unittest.TestCase):
    def test_deterministic_partition_preserves_modules_and_order(self):
        self.assertEqual(m.partition(NODES, 2), [NODES[:2], NODES[2:]])
        self.assertEqual(m.partition(NODES, 2), m.partition(NODES, 2))

    def test_balancing_never_loses_or_duplicates_items(self):
        nodes = [f"t{i}.py::test_{j}" for i in range(20) for j in range(i + 1)]
        shards = m.partition(nodes, 8)
        self.assertEqual(sorted(sum(shards, [])), sorted(nodes))
        for module in {n.split("::")[0] for n in nodes}:
            self.assertEqual(sum(any(n.startswith(module + "::") for n in s) for s in shards), 1)

    def test_empty_duplicate_and_invalid_partition_rejected(self):
        for nodes, count in (([], 1), (["a", "a"], 1), (NODES, 0), (NODES, True), (NODES, 17), (NODES, 4)):
            with self.subTest(nodes=nodes, count=count), self.assertRaises(m.EvidenceError):
                m.partition(nodes, count)

    def test_complete_union_is_required_and_counted(self):
        value = merge(reports())
        self.assertEqual(value["collected_count"], 3)
        self.assertEqual(value["outcomes"], {"passed": 3})
        self.assertTrue(value["complete_disjoint_union"])

    def test_missing_surplus_and_duplicate_shard_rejected(self):
        rows = reports()
        for bad in (rows[:1], rows + rows[:1], [rows[0], rows[0]]):
            with self.subTest(length=len(bad)), self.assertRaises(m.EvidenceError): merge(bad)

    def test_corrupted_report_checksum_rejected(self):
        rows = reports(); rows[0]["run_attempt"] = "2"
        with self.assertRaisesRegex(m.EvidenceError, "checksum"): merge(rows)

    def test_source_workflow_attempt_platform_and_environment_mismatch_rejected(self):
        for key, value in (("identity", {}), ("run_id", "old"), ("run_attempt", "2"),
                           ("platform", "linux"), ("ci_env", "0"), ("python", "other"),
                           ("pytest", "other"), ("shard", True)):
            rows = reports(); rows[0][key] = value; seal(rows[0])
            with self.subTest(key=key), self.assertRaises(m.EvidenceError): merge(rows)

    def test_incomplete_collection_in_one_shard_rejected(self):
        rows = reports(); rows[0]["collected"] = NODES[:1]; seal(rows[0])
        with self.assertRaises(m.EvidenceError): merge(rows)

    def test_silently_removed_selection_rejected(self):
        rows = reports(); rows[0]["selected"].pop(); seal(rows[0])
        with self.assertRaises(m.EvidenceError): merge(rows)

    def test_missing_duplicate_and_reordered_execution_rejected(self):
        for key in ("started", "finished"):
            for value in (NODES[:1], NODES[:2] + NODES[:1], list(reversed(NODES[:2]))):
                rows = reports(); rows[0][key] = value; seal(rows[0])
                with self.subTest(key=key, value=value), self.assertRaises(m.EvidenceError): merge(rows)

    def test_missing_outcome_and_lifecycle_rejected(self):
        for phase in (None, "setup", "call", "teardown"):
            rows = reports()
            if phase is None: del rows[0]["outcomes"][NODES[0]]
            else: del rows[0]["outcomes"][NODES[0]][phase]
            seal(rows[0])
            with self.subTest(phase=phase), self.assertRaises(m.EvidenceError): merge(rows)

    def test_duplicate_phase_and_failed_teardown_rejected(self):
        rows = reports()
        rows[0]["outcomes"][NODES[0]]["call"] *= 2
        seal(rows[0])
        with self.assertRaises(m.EvidenceError): merge(rows)
        rows = reports(); rows[0]["outcomes"][NODES[0]]["teardown"][0]["outcome"] = "failed"
        seal(rows[0])
        with self.assertRaises(m.EvidenceError): merge(rows)

    def test_failed_collection_subtest_interruption_or_bad_exit_rejected(self):
        for key, value in (("exit_code", 1), ("exit_code", False), ("session_finished", False),
                           ("collection_errors", ["module"]), ("failed_subtests", [NODES[0]]),
                           ("violations", ["source changed"])):
            rows = reports(); rows[0][key] = value; seal(rows[0])
            with self.subTest(key=key), self.assertRaises(m.EvidenceError): merge(rows)

    def test_skips_and_xfails_are_not_counted_as_passes(self):
        rows = reports()
        item = rows[0]["outcomes"][NODES[0]]
        del item["call"]; item["setup"][0]["outcome"] = "skipped"
        other = rows[0]["outcomes"][NODES[1]]["call"][0]
        other.update(outcome="skipped", wasxfail=True)
        seal(rows[0])
        self.assertEqual(merge(rows)["outcomes"], {"skipped": 1, "xfailed": 1, "passed": 1})

    def test_teardown_skip_is_disclosed_not_a_pass_or_new_failure(self):
        rows = reports()
        rows[0]["outcomes"][NODES[0]]["teardown"][0]["outcome"] = "skipped"
        seal(rows[0])
        self.assertEqual(merge(rows)["outcomes"], {"skipped": 1, "passed": 2})

    def test_non_strict_xpass_is_disclosed_without_changing_pytest_semantics(self):
        rows = reports()
        rows[0]["outcomes"][NODES[0]]["call"][0]["wasxfail"] = True
        seal(rows[0])
        self.assertEqual(merge(rows)["outcomes"], {"xpassed": 1, "passed": 2})

    def test_collection_skips_disagreement_rejected(self):
        rows = reports(); rows[0]["collection_skips"] = ["optional.py"]; seal(rows[0])
        with self.assertRaises(m.EvidenceError): merge(rows)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(m.EvidenceError):
            json.loads('{"shard":0,"shard":1}', object_pairs_hook=m.strict_pairs)


class ShardNativePytestFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.repo = self.root / "repo"; self.repo.mkdir()
        self.put("scripts/ci-pytest-shards.py", SCRIPT.read_bytes())
        self.put("pytest.ini", "[pytest]\ntestpaths=cases other\n")
        self.put("cases/test_a.py", "import pytest\ndef test_a(): assert True\n@pytest.mark.skip(reason='fixture')\ndef test_skip(): pass\n")
        self.put("cases/test_b.py", "def test_b(): assert True\n")
        self.put("other/test_c.py", "def test_c(): assert True\n")
        self.git("init", "-q"); self.git("config", "user.name", "CI fixture")
        self.git("config", "user.email", "ci@example.invalid"); self.git("config", "core.autocrlf", "false")
        self.commit()

    def put(self, path, data):
        p = self.repo / path; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=env, stderr=subprocess.STDOUT)

    def commit(self):
        self.git("add", "-A"); self.git("commit", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()

    def execute(self, tag, part=0, count=1, extra_env=None):
        out = self.root / tag
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTEST_"))}
        env.update(extra_env or {})
        command = [sys.executable, str(self.repo / "scripts/ci-pytest-shards.py"), "run", "--repo", str(self.repo),
                   "--expected-head", self.head, "--shards", str(count), "--shard", str(part), "--output", str(out)]
        result = subprocess.run(command, capture_output=True, env=env, timeout=45)
        report = json.loads((out / "report.json").read_bytes()) if (out / "report.json").exists() else None
        return result, report

    def test_actual_pytest_collects_all_roots_and_covers_each_item_once(self):
        rows = []
        for i in range(2):
            result, report = self.execute("part" + str(i), i, 2)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            m.validate_report(report); rows.append(report)
        sample = rows[0]
        merged = m.aggregate(rows, sample["identity"], 2, sample["run_id"], sample["run_attempt"],
                             sys.platform, sample["ci_env"])
        self.assertEqual(merged["outcomes"], {"passed": 3, "skipped": 1})
        self.assertEqual(merged["collected_count"], 4)
        self.assertTrue(any(n.startswith("other/") for n in rows[0]["collected"]))

    def test_actual_assertion_failure_is_not_accepted(self):
        self.put("cases/test_b.py", "def test_b(): assert False\n"); self.commit()
        result, report = self.execute("failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["exit_code"], 1)
        with self.assertRaises(m.EvidenceError): m.validate_report(report)

    def test_actual_collection_error_is_not_accepted(self):
        self.put("cases/test_b.py", "def syntax_error(\n"); self.commit()
        result, report = self.execute("collect_error")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(report["collection_errors"])

    def test_other_plugin_cannot_silently_drop_tests(self):
        self.put("cases/conftest.py", "def pytest_collection_modifyitems(items):\n    items.pop()\n")
        self.commit()
        result, report = self.execute("filtered")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"filtered or reordered", result.stdout + result.stderr)

    def test_source_mutation_during_test_is_not_accepted(self):
        self.put("cases/test_b.py", "from pathlib import Path\ndef test_b():\n    Path('pytest.ini').write_text('changed')\n")
        self.commit(); result, report = self.execute("dirty")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(report["violations"])

    def test_actual_subtest_reports_are_not_duplicate_lifecycles(self):
        self.put("cases/test_b.py", "import unittest\nclass Subtests(unittest.TestCase):\n    def test_subs(self):\n        for i in range(3):\n            with self.subTest(i=i): self.assertLess(i, 4)\n")
        self.commit(); result, report = self.execute("subtests")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        m.validate_report(report)

    def test_actual_failed_subtest_cannot_hide_behind_enclosing_pass(self):
        self.put("cases/test_b.py", "import unittest\nclass Subtests(unittest.TestCase):\n    def test_subs(self):\n        with self.subTest(i=1): self.assertEqual(1, 2)\n")
        self.commit(); result, report = self.execute("subtest_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(report["failed_subtests"])

    def test_actual_setup_skip_and_teardown_error(self):
        self.put("cases/test_b.py", "import pytest\n@pytest.fixture\ndef nope(): pytest.skip('fixture')\ndef test_b(nope): pass\n")
        self.commit(); result, report = self.execute("setup_skip")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        m.validate_report(report)
        self.put("cases/test_b.py", "import pytest\n@pytest.fixture\ndef broken():\n    yield\n    raise ValueError('teardown')\ndef test_b(broken): pass\n")
        self.commit(); result, report = self.execute("teardown_error")
        self.assertNotEqual(result.returncode, 0)
        with self.assertRaises(m.EvidenceError): m.validate_report(report)

    def test_actual_existing_xfail_stays_separate_from_pass(self):
        self.put("cases/test_b.py", "import pytest\n@pytest.mark.xfail(strict=True, reason='fixture')\ndef test_b(): assert False\n")
        self.commit(); result, report = self.execute("xfail")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        m.validate_report(report)
        row = report["outcomes"]["cases/test_b.py::test_b"]["call"][0]
        self.assertEqual(row, {"outcome": "skipped", "wasxfail": True})

    def test_external_pytest_subset_option_is_rejected(self):
        result, report = self.execute("options", extra_env={"PYTEST_ADDOPTS": "-k test_a"})
        self.assertNotEqual(result.returncode, 0); self.assertIsNone(report)

    def test_uncommitted_source_and_wrong_head_rejected(self):
        self.put("cases/test_b.py", "changed=True\n")
        result, report = self.execute("uncommitted")
        self.assertNotEqual(result.returncode, 0); self.assertIsNone(report)
        self.head = "f" * 40
        result, report = self.execute("wrong_head")
        self.assertNotEqual(result.returncode, 0); self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
