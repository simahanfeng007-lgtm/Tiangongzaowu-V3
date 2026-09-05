"""Observe a real source-pinned Gateway boot in OS isolation, never publish it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app/backend/tiangong-backend")]

from omni_body_skill.tools.sandbox_runtime import SandboxLimits, SandboxRunner  # noqa: E402
from total_gateway.tool_source_bundle import stage_tool_source_bundle, verify_staged_tool_source_bundle  # noqa: E402
from total_gateway.tool_source_candidate import _strict_pairs, _invalid_constant  # noqa: E402


def _valid_observation(observation, staged) -> bool:
    if not isinstance(observation, dict):
        return False
    # Retained failure evidence wins over an inconsistent success label.
    # Test key presence, not truthiness: even empty failure fields contradict
    # a successful v1 startup observation and must not become acceptance.
    if any(key in observation for key in (
        "cleanup_error", "error", "error_type", "failed_phase", "traceback",
    )):
        return False
    if not (
        observation.get("schema") == "tiangong.tool-source-launch-observation.v1"
        and observation.get("status") == "ISOLATED_STARTUP_OBSERVED"
        and type(observation.get("ready_http_status")) is int and observation["ready_http_status"] == 200
        and isinstance(observation.get("gateway_readiness"), dict)
        and observation["gateway_readiness"].get("status") == "READY"
        and isinstance(observation.get("gateway_health"), dict)
        and observation["gateway_health"].get("status") == "ALIVE"
        and all(observation.get(key) is False for key in ("may_publish", "may_authorize", "may_execute"))
    ):
        return False
    digest = observation.get("release_manifest_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return False
    for field in ("source_consistency", "post_shutdown_source_consistency"):
        proof = observation.get(field)
        if not isinstance(proof, dict) or not (
            proof.get("status") == "SOURCE_CONSISTENCY_OBSERVED"
            and proof.get("source_inputs_sha256") == staged["source_inputs_sha256"]
            and proof.get("capability_manifest_sha256") == staged["capability_manifest_sha256"]
            and type(proof.get("observed_file_count")) is int and proof["observed_file_count"] > 0
            and all(proof.get(key) is False for key in ("may_publish", "may_authorize", "may_execute"))
        ):
            return False
    return True


def probe(bundle: Path, *, expected_sha256: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("source_launch_probe_os_containment_unavailable")
    report = {"schema": "tiangong.tool-source-isolated-startup-report.v1",
              "bundle_sha256": expected_sha256,
              "may_publish": False, "may_authorize": False, "may_execute": False}
    try:
        return _probe(bundle, expected_sha256=expected_sha256, report=report)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        # Preserve process/child observations if later verification or cleanup
        # fails; never replace already-collected evidence with a bare error.
        report.update(status="STARTUP_PROBE_FAILED", error_type=type(exc).__name__, error=str(exc))
        return report


def _probe(bundle: Path, *, expected_sha256: str, report: dict) -> dict:
    worker = (ROOT / "scripts/_tool_source_launch_probe.py").read_bytes()
    report["worker_sha256"] = hashlib.sha256(worker).hexdigest()
    with tempfile.TemporaryDirectory(prefix="tg-lp-") as temporary:
        # Resolve only the parent-owned, already-created temporary directory.
        # Windows TEMP may use a short-name alias. Pass its observed physical
        # name to the unchanged strict stager; never relax candidate path checks.
        private = Path(temporary).resolve(strict=True)
        workspace = private / "w"
        workspace.mkdir()
        # Preserve Gateway's path-length validation in the deep AppContainer
        # namespace; use a compact private staging name, not a relaxed limit.
        revision = workspace / "r"
        staged = stage_tool_source_bundle(bundle, expected_sha256=expected_sha256, staging_root=revision)
        report["staged_source"] = staged
        with (workspace / "probe.py").open("xb") as output:
            output.write(worker)
        runner = SandboxRunner(workspace, private / "s", private / "trash",
                               SandboxLimits(timeout_seconds=120, max_changed_bytes=64 * 1024 * 1024))
        result = runner.run([sys.executable, "-I", "-B", "-X", "utf8", str(workspace / "probe.py"),
                             staged["source_inputs_sha256"], staged["capability_manifest_sha256"]],
                            require_os_containment=True)
        report["probe_process"] = result
        observation = workspace / "launch-observation.json"
        if observation.is_file() and not observation.is_symlink() and 0 < observation.stat().st_size <= 2 * 1024 * 1024:
            raw = observation.read_bytes()
            report["observation_sha256"] = hashlib.sha256(raw).hexdigest()
            report["observation"] = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
        report["post_probe_source"] = verify_staged_tool_source_bundle(
            bundle, expected_sha256=expected_sha256, staging_root=revision,
        )
        observation = report.get("observation")
        report["status"] = "ISOLATED_STARTUP_OBSERVED" if (
            result["ok"] is True and result["containment"] == "windows-appcontainer"
            and result["network"] == "denied"
            and _valid_observation(observation, staged)
        ) else "STARTUP_PROBE_FAILED"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error("report must be a new file")
    try:
        report = probe(args.bundle.absolute(), expected_sha256=args.sha256)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        report = {"status": "STARTUP_PROBE_REJECTED", "error_type": type(exc).__name__, "error": str(exc),
                  "bundle_sha256": args.sha256, "may_publish": False, "may_authorize": False, "may_execute": False}
    with args.report.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.report.absolute()), "may_publish": False}))
    return 0 if report["status"] == "ISOLATED_STARTUP_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
