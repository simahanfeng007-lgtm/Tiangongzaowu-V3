import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def call(config, *extra):
    return subprocess.run([sys.executable, str(ROOT/"scripts/configure-mcp.py"), "owner",
        "--config", str(config), "--application", "google.docs", "--location", "test-host",
        "--workspace", "workspace-a", *extra], capture_output=True, text=True)


def test_cli_registers_environment_without_claiming_connection_and_preserves_existing_entries(tmp_path):
    path=tmp_path/"config.json"
    path.write_text(json.dumps({"servers":{"keep":{"command":"installed-command"}}}))
    first=call(path,"--url","https://example.invalid/mcp","--header-env","Authorization=OWNER_TOKEN")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["connection_state"] == "configured_not_verified"
    content=path.read_bytes()
    assert json.loads(content)["servers"]["keep"] == {"command":"installed-command"}
    assert call(path,"--url","https://different.invalid/mcp").returncode != 0
    assert path.read_bytes() == content


def test_cli_rejects_bad_environment_and_incompatible_credential_fields_without_writes(tmp_path):
    path=tmp_path/"config.json"
    assert call(path,"--url","http://remote.invalid/mcp").returncode != 0
    assert call(path,"--command","server","--issuer","https://issuer.invalid","--client-id","client").returncode != 0
    assert call(path,"--url","https://example.invalid/mcp","--header-env","Authorization=Bearer token-value").returncode != 0
    assert not path.exists()
