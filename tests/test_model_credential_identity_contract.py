from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V3_DIR = ROOT / "app" / "backend" / "tiangong-backend" / "v3"
PACKAGE_NAME = "tiangong_backend_v3_credential_contract_test"


def _load_peizhi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in tuple(sys.modules):
        if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + "."):
            sys.modules.pop(name, None)
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(V3_DIR)]
    sys.modules[PACKAGE_NAME] = package
    module = importlib.import_module(f"{PACKAGE_NAME}.peizhi")
    monkeypatch.setattr(module, "API_PEIZHI_LUJING", tmp_path / "api_keys.json")
    return module


ALL_MODEL_ENV_KEYS = (
    "TIANGONG_DEEPSEEK_API_KEY", "TIANGONG_DEEPSEEK_V4_API_KEY", "DEEPSEEK_API_KEY",
    "TIANGONG_OPENAI_API_KEY", "TIANGONG_GPT_5_6_API_KEY", "OPENAI_API_KEY",
    "TIANGONG_ZHIPU_API_KEY", "TIANGONG_GLM_5_2_API_KEY", "ZAI_API_KEY",
    "ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "TIANGONG_MINIMAX_API_KEY",
    "TIANGONG_MINIMAX_M3_API_KEY", "MINIMAX_API_KEY", "TIANGONG_MIMO_API_KEY",
    "MIMO_API_KEY", "TIANGONG_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
    "TIANGONG_GOOGLE_API_KEY", "GOOGLE_API_KEY",
)


def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_MODEL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("identity", "base_url", "env_name"),
    (
        ("deepseek", "https://api.deepseek.com/v1", "TIANGONG_DEEPSEEK_API_KEY"),
        ("openai", "https://api.openai.com/v1", "TIANGONG_OPENAI_API_KEY"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4", "TIANGONG_ZHIPU_API_KEY"),
        ("minimax", "https://api.minimaxi.com/v1", "TIANGONG_MINIMAX_API_KEY"),
        ("mimo", "https://api.xiaomimimo.com/v1", "TIANGONG_MIMO_API_KEY"),
        ("anthropic", "https://api.anthropic.com", "TIANGONG_ANTHROPIC_API_KEY"),
        ("google", "https://generativelanguage.googleapis.com/v1beta", "TIANGONG_GOOGLE_API_KEY"),
    ),
)
def test_official_provider_identity_env_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, identity: str, base_url: str, env_name: str
) -> None:
    peizhi = _load_peizhi(monkeypatch, tmp_path)
    _clear_model_env(monkeypatch)
    monkeypatch.setenv(env_name, f"identity-key:{identity}")
    assert peizhi.duqu_endpoint_api_miyao(identity, base_url) == f"identity-key:{identity}"
    assert peizhi.provider_identity_env_names(identity) == (env_name,)


@pytest.mark.parametrize(
    ("family", "identity_env"),
    (
        ("deepseek_v4", "TIANGONG_DEEPSEEK_API_KEY"),
        ("gpt_5_6", "TIANGONG_OPENAI_API_KEY"),
        ("glm_5_2", "TIANGONG_ZHIPU_API_KEY"),
        ("minimax_m3", "TIANGONG_MINIMAX_API_KEY"),
    ),
)
def test_legacy_family_callers_can_read_new_identity_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, family: str, identity_env: str
) -> None:
    peizhi = _load_peizhi(monkeypatch, tmp_path)
    _clear_model_env(monkeypatch)
    monkeypatch.setenv(identity_env, "new-identity-key")
    assert peizhi.duqu_api_miyao(family) == "new-identity-key"


def test_identity_slot_precedes_legacy_family_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    peizhi = _load_peizhi(monkeypatch, tmp_path)
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("TIANGONG_DEEPSEEK_API_KEY", "identity-key")
    monkeypatch.setenv("TIANGONG_DEEPSEEK_V4_API_KEY", "legacy-family-key")
    assert peizhi.duqu_endpoint_api_miyao("deepseek", "https://api.deepseek.com/v1") == "identity-key"


def test_legacy_family_slot_still_reads_when_identity_slot_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    peizhi = _load_peizhi(monkeypatch, tmp_path)
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("TIANGONG_DEEPSEEK_V4_API_KEY", "legacy-family-key")
    assert peizhi.duqu_endpoint_api_miyao("deepseek", "https://api.deepseek.com/v1") == "legacy-family-key"


def test_custom_endpoint_never_inherits_official_provider_identity_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    peizhi = _load_peizhi(monkeypatch, tmp_path)
    _clear_model_env(monkeypatch)
    custom_url = "https://models.example.com/v1"
    monkeypatch.setenv("TIANGONG_DEEPSEEK_API_KEY", "must-not-leak")
    assert peizhi.duqu_endpoint_api_miyao("deepseek", custom_url) is None
    scope = peizhi.custom_scope_id(custom_url)
    endpoint_env = f"TIANGONG_{scope.upper().replace('-', '_')}_API_KEY"
    monkeypatch.setenv(endpoint_env, "endpoint-only-key")
    assert peizhi.duqu_endpoint_api_miyao("deepseek", custom_url) == "endpoint-only-key"


def test_electron_writer_and_python_reader_share_identity_namespace() -> None:
    electron = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
    assert 'function providerApiKeyEnvName(provider)' in electron
    assert 'return `TIANGONG_${String(provider || "").toUpperCase().replace(/-/g, "_")}_API_KEY`;' in electron
