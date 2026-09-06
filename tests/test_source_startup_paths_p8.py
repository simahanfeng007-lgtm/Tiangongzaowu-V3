"""Controlled Windows DOS-resolution denial, not an AppContainer/product PASS."""
import os
from pathlib import Path

import pytest

from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows native startup path contract")
def test_existing_embedded_startup_survives_dos_resolution_denial(tmp_path, monkeypatch):
    for name, relative in (
        ("APPDATA", "appdata"), ("TIANGONG_DOCUMENTS_PATH", "documents"),
        ("TIANGONG_LIFE_DATA_ROOT", "life-data"), ("TIANGONG_LIFE_RUNTIME_ROOT", "life-runtime"),
    ):
        monkeypatch.setenv(name, str(tmp_path / relative))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Runtime exports these existing workspace bindings. Preserve the test
    # process environment even if startup fails after exporting them.
    monkeypatch.setenv("TIANGONG_DESKTOP_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("TIANGONG_WORKSPACE_ROOT", str(workspace))
    settings = GatewayConfig(
        environment="test", deployment_mode="embedded", port=0,
        state_root=tmp_path / "gateway", workspace_root=workspace,
        min_free_bytes=1_048_576, backend_internal_token="p8-local-path-contract-" + "0" * 48,
        release_source_root=ROOT,
        skill_root=ROOT / "app/backend/tiangong-backend/_internal/omni_body_skill",
    )
    original_resolve = Path.resolve

    def denied_dos_lookup(path, strict=False):
        if strict:
            raise PermissionError("controlled AppContainer DOS-volume lookup denial")
        return original_resolve(path, strict=strict)

    # Test-only fault injection: production pathlib and OS ACLs are unchanged.
    with monkeypatch.context() as denied:
        denied.setattr(Path, "resolve", denied_dos_lookup)
        runtime = GatewayRuntime.start(settings)
        try:
            status, ready = runtime.ready_payload()
            assert status == 200 and ready["status"] == "READY", ready
        finally:
            runtime.close()
