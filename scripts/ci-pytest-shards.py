"""Run full pytest collection in module-preserving CI shards and verify its union.

Development-only: no runtime imports, test marking, retry, xfail or timeout changes.
Each shard collects BOTH configured test roots, then selects its assigned modules.
A successful aggregate requires every collected item to finish exactly once.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = "tiangong.ci.pytest-shard.v1"


class EvidenceError(ValueError):
    """Missing, inconsistent or incomplete CI evidence must never pass the gate."""


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def partition(nodeids: list[str], count: int) -> list[list[str]]:
    if type(count) is not int or not 1 <= count <= 16:
        raise EvidenceError("Shard count must be between 1 and 16")
    if (not nodeids or any(not isinstance(n, str) or not n for n in nodeids)
            or len(set(nodeids)) != len(nodeids)):
        raise EvidenceError("Empty or duplicate full collection")
    groups: dict[str, list[str]] = defaultdict(list)
    for node in nodeids:
        groups[node.split("::", 1)[0]].append(node)
    if len(groups) < count:
        raise EvidenceError("More shards than collected modules")
    loads = [0] * count
    assigned = {}
    # Greedy balancing by item count; never split a file's fixtures or test order.
    for path in sorted(groups, key=lambda p: (-len(groups[p]), p)):
        index = min(range(count), key=lambda i: (loads[i], i))
        assigned[path] = index
        loads[index] += len(groups[path])
    result: list[list[str]] = [[] for _ in range(count)]
    for node in nodeids:
        result[assigned[node.split("::", 1)[0]]].append(node)
    return result


def git(repo: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *args],
                            capture_output=True, timeout=60, env=env)
    if result.returncode:
        raise EvidenceError("Git identity/clean-source check failed: " + args[0])
    return result.stdout


def identity(repo: Path, expected_head: str) -> dict:
    if re.fullmatch("[0-9a-f]{40}", expected_head) is None:
        raise EvidenceError("Expected an exact commit SHA")
    actual = git(repo, "rev-parse", "HEAD^{commit}").decode().strip()
    if actual != expected_head:
        raise EvidenceError("Checkout differs from trusted workflow commit")
    git(repo, "diff", "--no-ext-diff", "--no-textconv", "--quiet", "HEAD", "--")
    observer = Path(__file__).read_bytes()
    if git(repo, "show", actual + ":scripts/ci-pytest-shards.py") != observer:
        raise EvidenceError("Observer differs from the committed script")
    return {"head": actual, "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
            "observer_sha256": hashlib.sha256(observer).hexdigest()}


def validate_report(report: dict) -> None:
    payload = dict(report)
    checksum = payload.pop("report_sha256", None)
    if checksum != digest(payload) or report.get("schema") != SCHEMA:
        raise EvidenceError("Report schema or checksum mismatch")
    index, count = report.get("shard"), report.get("shards")
    if type(index) is not int or type(count) is not int or not 0 <= index < count:
        raise EvidenceError("Invalid shard identity")
    selected = partition(report["collected"], count)[index]
    if selected != report.get("selected"):
        raise EvidenceError("Selection differs from the complete collection partition")
    if report.get("started") != selected or report.get("finished") != selected:
        raise EvidenceError("Missing, duplicated or out-of-order test execution")
    outcomes = report.get("outcomes", {})
    if set(outcomes) != set(selected):
        raise EvidenceError("Outcome coverage differs from selection")
    for node in selected:
        phases = outcomes[node]
        if set(phases) not in ({"setup", "call", "teardown"}, {"setup", "teardown"}):
            raise EvidenceError("Incomplete test lifecycle")
        if any(len(rows) != 1 for rows in phases.values()):
            raise EvidenceError("Duplicate test lifecycle reports")
        if any(row["outcome"] not in {"passed", "skipped"}
               for rows in phases.values() for row in rows):
            raise EvidenceError("A test or fixture failed")
        if (phases["setup"][0]["outcome"] == "passed") != ("call" in phases):
            raise EvidenceError("Setup outcome and call lifecycle disagree")
    if (type(report.get("exit_code")) is not int or report["exit_code"] != 0
            or report.get("session_finished") is not True or report.get("violations")
            or report.get("collection_errors") or report.get("failed_subtests")):
        raise EvidenceError("Pytest did not complete successfully")


def collect_plugin(index: int, count: int):
    import pytest

    class Ledger:
        def __init__(self):
            self.seen, self.collected, self.selected = [], [], []
            self.started, self.finished = [], []
            self.outcomes, self.collection_errors, self.collection_skips = {}, [], []
            self.violations, self.failed_subtests = [], []
            self.session_finished = False

        def pytest_itemcollected(self, item):
            self.seen.append(item.nodeid)

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_collection_modifyitems(self, config, items):
            result = yield
            self.collected = [item.nodeid for item in items]
            # No other plugin may silently select a subset before this partition.
            if self.collected != self.seen:
                raise pytest.UsageError("Full collected items were filtered or reordered")
            try:
                self.selected = partition(self.collected, count)[index]
            except EvidenceError as exc:
                raise pytest.UsageError(str(exc)) from exc
            selected_set = set(self.selected)
            omitted = [item for item in items if item.nodeid not in selected_set]
            items[:] = [item for item in items if item.nodeid in selected_set]
            config.hook.pytest_deselected(items=omitted)
            return result

        def pytest_collection_finish(self, session):
            if [item.nodeid for item in session.items] != self.selected:
                self.violations.append("Selection changed after partitioning")

        def pytest_collectreport(self, report):
            if report.failed:
                self.collection_errors.append(report.nodeid)
            if report.skipped:
                self.collection_skips.append(report.nodeid)

        def pytest_runtest_logstart(self, nodeid, location):
            self.started.append(nodeid)

        def pytest_runtest_logfinish(self, nodeid, location):
            self.finished.append(nodeid)

        def pytest_runtest_logreport(self, report):
            # Pytest 9 emits subtest reports in addition to the enclosing call.
            # Keep failures independently; do not miscount them as test lifecycles.
            if getattr(report, "context", None) is not None:
                if report.failed:
                    self.failed_subtests.append(report.nodeid)
                return
            phases = self.outcomes.setdefault(report.nodeid, {})
            phases.setdefault(report.when, []).append({"outcome": report.outcome,
                                                       "wasxfail": hasattr(report, "wasxfail")})

        def pytest_sessionfinish(self, session, exitstatus):
            self.session_finished = True

    return Ledger()


def run(repo: Path, expected_head: str, index: int, count: int, output: Path) -> int:
    import pytest

    if type(index) is not int or not 0 <= index < count:
        raise EvidenceError("Invalid shard index")
    if os.environ.get("PYTEST_ADDOPTS"):
        raise EvidenceError("External pytest selection options are not permitted")
    repo = repo.resolve()
    output = output.resolve()
    if output.is_relative_to(repo):
        raise EvidenceError("Evidence output must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    original = identity(repo, expected_head)
    ledger = collect_plugin(index, count)
    old_cwd = Path.cwd()
    try:
        os.chdir(repo)
        code = int(pytest.main(["-q", "-ra", "--junitxml=" + str(output / "pytest.xml")],
                               plugins=[ledger]))
    finally:
        os.chdir(old_cwd)
    try:
        if identity(repo, expected_head) != original:
            ledger.violations.append("Source identity changed during pytest")
    except EvidenceError as exc:
        ledger.violations.append(str(exc))
    report = {"schema": SCHEMA, "identity": original, "shard": index, "shards": count,
              "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
              "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
              "platform": sys.platform, "python": sys.version, "pytest": pytest.__version__,
              "ci_env": os.environ.get("TIANGONG_CI_ENV", ""), "exit_code": code,
              **{name: getattr(ledger, name) for name in (
                  "collected", "selected", "started", "finished", "outcomes", "session_finished",
                  "violations", "collection_errors", "collection_skips", "failed_subtests")}}
    report["report_sha256"] = digest(report)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                                  indent=2) + "\n", encoding="utf-8")
    try:
        validate_report(report)
    except EvidenceError as exc:
        print("Incomplete/failed shard: " + str(exc), file=sys.stderr)
        return code or 1
    return 0


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("Duplicate JSON report key")
        result[key] = value
    return result


def aggregate(reports: list[dict], expected: dict, count: int, run_id: str,
              run_attempt: str, platform: str, ci_env: str) -> dict:
    if len(reports) != count:
        raise EvidenceError("Missing or surplus shard reports")
    slots = set()
    exemplar = reports[0]
    for report in reports:
        validate_report(report)
        if report["identity"] != expected or report["shards"] != count or report["shard"] in slots:
            raise EvidenceError("Mismatched source or duplicated shard")
        for key, value in (("run_id", run_id), ("run_attempt", run_attempt),
                           ("platform", platform), ("ci_env", ci_env)):
            if report.get(key) != value:
                raise EvidenceError("Mismatched execution identity: " + key)
        for key in ("collected", "collection_skips", "python", "pytest"):
            if report[key] != exemplar[key]:
                raise EvidenceError("Shards disagree on collection or runtime: " + key)
        slots.add(report["shard"])
    full = exemplar["collected"]
    executed = [node for r in reports for node in r["finished"]]
    if Counter(executed) != Counter(full) or slots != set(range(count)):
        raise EvidenceError("Full collection does not equal disjoint execution union")
    summaries = Counter()
    for r in reports:
        for phases in r["outcomes"].values():
            terminal = next((rows[0] for rows in phases.values() if rows[0]["outcome"] == "skipped"),
                            phases.get("call", phases["setup"])[0])
            if terminal["wasxfail"]:
                label = "xfailed" if terminal["outcome"] == "skipped" else "xpassed"
            else:
                label = terminal["outcome"]
            summaries[label] += 1
    return {"schema": "tiangong.ci.pytest-shard-union.v1", "identity": expected,
            "run_id": run_id, "run_attempt": run_attempt, "shards": count,
            "collected_count": len(full), "collection_sha256": digest(full),
            "collection_skips": exemplar["collection_skips"], "outcomes": dict(summaries),
            "complete_disjoint_union": True, "report_sha256s": sorted(r["report_sha256"] for r in reports),
            "limitation": "Module order retained within shards; cross-module single-process order is not tested."}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("run", "aggregate"))
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--expected-head", required=True)
    p.add_argument("--shards", type=int, required=True)
    p.add_argument("--shard", type=int)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--inputs", type=Path)
    args = p.parse_args()
    try:
        if args.mode == "run":
            return run(args.repo, args.expected_head, args.shard, args.shards, args.output)
        if args.inputs is None:
            raise EvidenceError("Aggregate inputs required")
        files = sorted(args.inputs.rglob("report.json"))
        reports = [json.loads(f.read_text(encoding="utf-8"), object_pairs_hook=strict_pairs) for f in files]
        merged = aggregate(reports, identity(args.repo, args.expected_head), args.shards,
                           os.environ.get("GITHUB_RUN_ID", "local"),
                           os.environ.get("GITHUB_RUN_ATTEMPT", "local"), "win32", "1")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(merged, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(merged, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print("CI shard gate failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
