"""Build an immutable Tool Source candidate using the existing OS sandbox.

No candidate code is imported into this parent process. The parent uses its
installed/trusted worker, not a script selected by the candidate. This command
does not publish Source, mutate a registry, reload a running manifest, or
approve a permission change. Linux has no OS-contained backend here and fails
closed before candidate execution; Windows compatibility fallback is disabled.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app/backend/tiangong-backend")]

from omni_body_skill.tools.sandbox_runtime import SandboxLimits, SandboxRunner  # noqa: E402
from contracts import canonical_json_bytes  # noqa: E402
from source_authority import validator as source_validator  # noqa: E402
from world_understanding.tool_capability_world.source_candidate import (  # noqa: E402
    inspect_tool_source_candidate,
    materialize_tool_source_candidate,
    read_tool_source_manifests,
)
from total_gateway.tool_manifest_evolution import review_manifest_evolution  # noqa: E402
from world_understanding.tool_capability_world.source_inputs import compile_tool_source_inputs  # noqa: E402


WORKER_NAME = ".tiangong-source-build-worker.py"
ARTIFACT_NAME = ".tiangong-candidate-compiled-manifest.json"
AUTHORITY_FILES = {
    "compiler": "app/backend/tiangong-backend/v3/fact_kernel/__init__.py",
    "actions": "src/omni_body_skill/tools/omni_body_tool.py",
    "schemas": "src/omni_body_skill/tool_contracts.py",
}


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("build artifact contains duplicate JSON keys")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("build artifact contains a non-finite value")


def build_candidate(repository: Path, *, base: str, head: str, action_ids: tuple[str, ...]):
    if os.name != "nt":
        raise RuntimeError("source_build_os_containment_unavailable: Windows AppContainer is required")
    candidate = inspect_tool_source_candidate(repository, base_commit=base, candidate_commit=head,
                                              requested_action_ids=action_ids)
    published, committed_candidate = read_tool_source_manifests(repository, candidate)
    worker = (ROOT / "scripts/_tool_source_build_worker.py").read_bytes()
    with materialize_tool_source_candidate(repository, candidate) as snapshot:
        # Trusted parser/topology code examines candidate bytes as DATA in the
        # parent; a candidate's own validator cannot award this evidence.
        parsed = 0
        for path in sorted(snapshot.rglob("*.py")):
            ast.parse(path.read_bytes(), filename=path.relative_to(snapshot).as_posix())
            parsed += 1
        failures = source_validator.validate_source_authority(
            source_validator.load_config(snapshot / "source-ownership.json"), repo_root=snapshot,
        )
        if failures:
            raise ValueError("source build snapshot failed trusted source topology validation")
        source_inputs = compile_tool_source_inputs(snapshot)
        expected_bindings = {
            name: {"path": relative, "sha256": hashlib.sha256((snapshot / relative).read_bytes()).hexdigest()}
            for name, relative in AUTHORITY_FILES.items()
        }
        for name in (WORKER_NAME, ARTIFACT_NAME):
            if (snapshot / name).exists():
                raise ValueError("candidate collides with a reserved build artifact")
        with (snapshot / WORKER_NAME).open("xb") as target:
            target.write(worker)
        with tempfile.TemporaryDirectory(prefix="tg-build-state-") as state:
            runner = SandboxRunner(snapshot, Path(state) / "state", Path(state) / "trash",
                                   SandboxLimits(timeout_seconds=120, max_changed_bytes=32 * 1024 * 1024))
            outcome = runner.run([sys.executable, "-X", "utf8", "-I", "-B", str(snapshot / WORKER_NAME)],
                                 require_os_containment=True)
        if not outcome["ok"]:
            return {
                "source_candidate": asdict(candidate), "build_process": outcome,
                "status": "BUILD_FAILED", "may_publish": False,
                "may_authorize": False, "may_execute": False,
                "worker_sha256": hashlib.sha256(worker).hexdigest(),
            }
        if outcome["containment"] != "windows-appcontainer" or outcome["network"] != "denied":
            raise RuntimeError("source build containment evidence is invalid")
        if {name.replace("\\", "/") for name in outcome["changed_files"]} != {ARTIFACT_NAME} or outcome["deleted_files"]:
            raise ValueError("source build modified immutable inputs or produced undeclared files")
        path = snapshot / ARTIFACT_NAME
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
            raise ValueError("source build artifact is missing or unsafe")
        raw = path.read_bytes()
        artifact = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_pairs,
                              parse_constant=_invalid_constant)
        if not isinstance(artifact, dict) or artifact.get("schema") != "tiangong.tool-source-build-artifact.v1":
            raise ValueError("source build artifact schema is invalid")
        if (
            artifact.get("compiler") != "v3.fact_kernel.compile_manifest"
            or artifact.get("authority_bindings") != expected_bindings
            or not isinstance(artifact.get("gateway_manifest"), dict)
            or canonical_json_bytes(artifact.get("source_inputs")) != canonical_json_bytes(asdict(source_inputs))
        ):
            raise ValueError("source build compiler bindings differ from committed bytes")
        if artifact["gateway_manifest"].get("source_inputs_sha256") != source_inputs.source_inputs_sha256:
            raise ValueError("source build Manifest revision differs from committed inputs")
        review = review_manifest_evolution(published, artifact["gateway_manifest"],
                                           requested_action_ids=candidate.requested_action_ids)
        # A successful build is still not a reviewed publication or execution
        # grant. Keep the actual process result and full collateral diff.
        return {
            "schema": "tiangong.tool-source-isolated-build-report.v1",
            "status": "ISOLATED_BUILD_OBSERVED",
            "source_candidate": asdict(candidate),
            "worker_sha256": hashlib.sha256(worker).hexdigest(),
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "build_process": outcome,
            "trusted_static_checks": {
                "python_ast_files": parsed,
                "source_topology_valid": True,
                "validator_source_sha256": hashlib.sha256(Path(source_validator.__file__).read_bytes()).hexdigest(),
            },
            "build_artifact": artifact,
            "manifest_review": asdict(review),
            "committed_manifest_matches_build": committed_candidate == artifact["gateway_manifest"],
            "evidence_contract_tests_verified": False,
            "review_approval_verified": False,
            "running_manifest_lock_verified": False,
            "may_publish": False,
            "may_authorize": False,
            "may_execute": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--action", required=True, action="append")
    parser.add_argument("--report", type=Path, required=True, help="new report file; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.report.exists() or args.report.is_symlink():
        parser.error("report must be a new file")
    try:
        report = build_candidate(args.repository.absolute(), base=args.base, head=args.candidate,
                                 action_ids=tuple(sorted(args.action)))
    except (ValueError, OSError, RuntimeError) as exc:
        report = {"status": "BUILD_REJECTED", "error": str(exc), "may_publish": False,
                  "may_authorize": False, "may_execute": False,
                  "base_commit": args.base, "candidate_commit": args.candidate}
    with args.report.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": report["status"], "report": str(args.report.absolute()), "may_publish": False},
                     ensure_ascii=True))
    return 0 if report["status"] == "ISOLATED_BUILD_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
