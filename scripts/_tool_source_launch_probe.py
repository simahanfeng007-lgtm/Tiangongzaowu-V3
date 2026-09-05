"""Trusted offline worker; its parent must enforce OS containment before launch."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback


REPORT_NAME = "launch-observation.json"


def main() -> int:
    if os.environ.get("TIANGONG_SANDBOX") != "1" or not sys.dont_write_bytecode:
        raise RuntimeError("source launch probe requires its contained -B parent")
    workspace = Path(__file__).resolve().parent
    source = workspace / "r/source"
    output = workspace / REPORT_NAME
    if output.exists():
        raise RuntimeError("source launch observation already exists")
    # All mutable application roots remain inside this one private sandbox.
    os.environ.update({
        "APPDATA": str(workspace / "appdata"),
        "TIANGONG_DOCUMENTS_PATH": str(workspace / "documents"),
        "TIANGONG_LIFE_DATA_ROOT": str(workspace / "life-data"),
        "TIANGONG_LIFE_RUNTIME_ROOT": str(workspace / "life-runtime"),
    })
    sys.path[:0] = [str(source / "src"), str(source / "app/backend/tiangong-backend")]
    phase = "source_consistency"
    report = {"schema": "tiangong.tool-source-launch-observation.v1",
              "may_publish": False, "may_authorize": False, "may_execute": False}
    runtime = None
    try:
        from total_gateway.tool_source_launch import verify_source_revision
        report["source_consistency"] = verify_source_revision(
            source, source_inputs_sha256=sys.argv[1], capability_sha256=sys.argv[2],
        )
        phase = "release_generation"
        from total_gateway.release_manifest import write_release_manifest, RELEASE_MANIFEST_FILENAME
        release = write_release_manifest(workspace / "release", source)
        report["release_manifest_sha256"] = release.release_manifest_sha256
        phase = "gateway_startup"
        from total_gateway.bootstrap import GatewayConfig
        from total_gateway.runtime import GatewayRuntime
        action_workspace = workspace / "action-workspace"
        action_workspace.mkdir()
        config = GatewayConfig(
            environment="test", deployment_mode="embedded", port=0,
            state_root=workspace / "gateway", workspace_root=action_workspace,
            min_free_bytes=1_048_576, backend_internal_token="p8-offline-probe-" + "0" * 48,
            release_source_root=source,
            release_manifest_path=workspace / "release" / RELEASE_MANIFEST_FILENAME,
            skill_root=source / "app/backend/tiangong-backend/_internal/omni_body_skill",
        )
        runtime = GatewayRuntime.start(config)
        report["gateway_health"] = runtime.health_payload()
        phase = "gateway_readiness"
        report["ready_http_status"], report["gateway_readiness"] = runtime.ready_payload()
        if report["ready_http_status"] != 200 or report["gateway_readiness"].get("status") != "READY":
            raise RuntimeError("isolated Gateway did not reach READY")
        phase = "gateway_shutdown"
        runtime.close()
        runtime = None
        # Reobserve after startup/shutdown; successful boot must not have
        # modified the source or mixed cached authorities during assembly.
        phase = "post_shutdown_source_consistency"
        report["post_shutdown_source_consistency"] = verify_source_revision(
            source, source_inputs_sha256=sys.argv[1], capability_sha256=sys.argv[2],
        )
        report["status"] = "ISOLATED_STARTUP_OBSERVED"
        return_code = 0
    except Exception as exc:
        report.update(status="STARTUP_PROBE_FAILED", failed_phase=phase,
                      error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        if phase == "source_consistency":
            diagnostics = []
            for path in (source, source / "pyproject.toml", source / "src",
                         source / "src/total_gateway", source / "src/total_gateway/__init__.py"):
                row = {"path": str(path)}
                for name, operation in (
                    ("resolve_strict", lambda: str(path.resolve(strict=True))),
                    ("resolve_nonstrict", lambda: str(path.resolve(strict=False))),
                    ("stat", lambda: {"mode": path.stat().st_mode, "attributes": getattr(path.stat(), "st_file_attributes", None)}),
                    ("is_symlink", path.is_symlink), ("is_junction", path.is_junction),
                ):
                    try:
                        row[name] = operation()
                    except Exception as error:
                        row[name] = {"error_type": type(error).__name__, "error": str(error)}
                diagnostics.append(row)
            report["source_path_diagnostics"] = diagnostics
        return_code = 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "phase": phase}, ensure_ascii=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
