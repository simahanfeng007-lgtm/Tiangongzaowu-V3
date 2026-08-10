from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _read_result(path: str, content: str) -> dict:
    return {
        "schema": "tiangong.v3.omni_body.v1",
        "ok": True,
        "zhuangtai": "wancheng",
        "action": "file.read",
        "target": path,
        "result": {
            "success": True,
            "action": "file.read",
            "path": path,
            "content": content,
        },
    }


def _failed_read_result(path: str) -> dict:
    return {
        "schema": "tiangong.v3.omni_body.v1",
        "ok": False,
        "zhuangtai": "cuowu",
        "action": "file.read",
        "target": path,
        "error": f"[FileNotFoundError] {path}",
        "result": {
            "success": False,
            "action": "file.read",
            "path": path,
            "exists": False,
        },
    }


def test_parallel_read_quality_keeps_each_real_result_and_source_text() -> None:
    scheduler = importlib.import_module("v3.zongdiaodu")
    prompt = (
        r"Use file tools to read both e2e-targets\alpha.txt and "
        r"e2e-targets\beta.txt. Return the exact content of each. "
        "Do not write or modify anything."
    )
    alpha = r"C:\workspace\e2e-targets\alpha.txt"
    beta = r"C:\workspace\e2e-targets\beta.txt"

    observations = [
        scheduler._simple_chain_quality_gate_payload(
            "req_parallel", prompt, "omni_body",
            {"action": "file.read", "target": alpha, "args": {}},
            _read_result(alpha, "alpha line 1\nalpha line 2\n"), 1,
        ),
        scheduler._simple_chain_quality_gate_payload(
            "req_parallel", prompt, "omni_body",
            {"action": "file.read", "target": beta, "args": {}},
            _read_result(beta, "beta line 1\nbeta line 2\n"), 1,
        ),
    ]

    assert all(item["ok"] is True for item in observations)
    assert observations[0]["codex_evidence"]["actual"]["paths"]
    assert observations[1]["codex_evidence"]["actual"]["paths"]
    assert observations[0]["source_text_map"]["entries"][0]["text"].startswith("alpha line")
    assert observations[1]["source_text_map"]["entries"][0]["text"].startswith("beta line")
    assert scheduler._simple_chain_read_coverage_issues(prompt, observations) == []
    assert scheduler._simple_chain_missing_deliverable_paths(prompt, observations, []) == []


def test_failed_parallel_read_stays_failed_and_blocks_read_coverage() -> None:
    scheduler = importlib.import_module("v3.zongdiaodu")
    prompt = (
        r"Use file tools to read both e2e-targets\alpha.txt and "
        r"e2e-targets\missing.txt. Return the exact content of each. "
        "Do not write or modify anything. Do not claim completion if either read fails."
    )
    alpha = r"C:\workspace\e2e-targets\alpha.txt"
    missing = r"C:\workspace\e2e-targets\missing.txt"
    success = scheduler._simple_chain_quality_gate_payload(
        "req_missing", prompt, "omni_body",
        {"action": "file.read", "target": alpha, "args": {}},
        _read_result(alpha, "alpha content\n"), 1,
    )
    failure = scheduler._simple_chain_quality_gate_payload(
        "req_missing", prompt, "omni_body",
        {"action": "file.read", "target": missing, "args": {}},
        _failed_read_result(missing), 1,
    )

    assert success["ok"] is True
    assert failure["ok"] is False
    assert failure["tool_status"] == "failed"
    assert failure["failures"]
    issues = scheduler._simple_chain_read_coverage_issues(prompt, [success, failure])
    assert issues == ["requested read coverage is incomplete: missing 1 of 2 target paths"]
    assert scheduler._simple_chain_missing_deliverable_paths(prompt, [success, failure], []) == []
    assert scheduler._simple_chain_verbatim_read_reply(prompt, [success, failure]) == ""


def test_exact_parallel_read_can_close_from_complete_source_evidence() -> None:
    scheduler = importlib.import_module("v3.zongdiaodu")
    prompt = (
        r"Read both e2e-targets\alpha.txt and e2e-targets\beta.txt. "
        "Return the exact content of each. Do not write or modify anything."
    )
    alpha = r"C:\workspace\e2e-targets\alpha.txt"
    beta = r"C:\workspace\e2e-targets\beta.txt"
    observations = [
        scheduler._simple_chain_quality_gate_payload(
            "req_grounded", prompt, "omni_body",
            {"action": "file.read", "target": alpha, "args": {}},
            _read_result(alpha, "alpha exact\n"), 1,
        ),
        scheduler._simple_chain_quality_gate_payload(
            "req_grounded", prompt, "omni_body",
            {"action": "file.read", "target": beta, "args": {}},
            _read_result(beta, "beta exact\n"), 1,
        ),
    ]

    reply = scheduler._simple_chain_verbatim_read_reply(prompt, observations)
    assert alpha in reply
    assert beta in reply
    assert "alpha exact\n" in reply
    assert "beta exact\n" in reply
    assert reply.count("---BEGIN EXACT CONTENT---") == 2


def test_read_only_missing_target_has_no_platform_write_surface() -> None:
    scheduler = importlib.import_module("v3.zongdiaodu")
    assert not hasattr(scheduler, "_simple_chain_fallback_write_deliverable")
    assert not hasattr(scheduler, "_simple_chain_try_fallback_delivery")
