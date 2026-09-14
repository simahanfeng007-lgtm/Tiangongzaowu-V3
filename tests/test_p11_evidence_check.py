from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy

import pytest

from contracts import canonical_sha256
from tests.test_capability_formal_shadow_p11 import (
    _cases, _faults, _live_cases_and_bindings,
)
from world_understanding.capability_composition import build_p11_formal_shadow_report


CHECK = runpy.run_path(str(
    Path(__file__).resolve().parents[1] / "scripts" / "check-p11-evidence.py"
))
HEAD = "1" * 40


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _identity(document):
    return {
        "head": HEAD,
        **{key: document[key] for key in (
            "evidence_mode", "formal_gate_passed", "cutover_gate_passed",
            "cutover_blockers", "task_count", "model_observation_count",
            "fault_case_count", "report_sha256",
        )},
    }


@pytest.fixture(scope="module")
def recorded():
    report = build_p11_formal_shadow_report(
        _cases(), _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    return {**report.payload(), "report_sha256": report.report_sha256}


@pytest.fixture
def files(tmp_path, recorded):
    report, identity = tmp_path / "report.json", tmp_path / "identity.json"
    _write(report, recorded)
    _write(identity, _identity(recorded))
    return report, identity


def test_audit_recomputes_recorded_bytes_without_authorizing_p12(files):
    report, identity = files
    result = CHECK["check_evidence"](report, identity, HEAD)
    assert result["evaluation_matches"]
    assert result["formal_gate_passed"]
    assert not result["p12_authorized"]
    assert result["scope"] == "STRUCTURE_AND_CONTENT_HASHES_ONLY"
    assert result["report_file_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_cli_preserves_production_blockers_and_input_files(files, tmp_path, capsys):
    report, identity = files
    before = report.read_bytes(), identity.read_bytes()
    output = tmp_path / "audit.json"
    args = ["--report", str(report), "--identity", str(identity),
            "--expected-head", HEAD, "--output", str(output)]
    assert CHECK["main"](args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "INTEGRITY_VERIFIED"
    assert CHECK["main"]([*args, "--require-production"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert not result["p12_authorized"]
    assert "p11.production_shadow_trace.required" in result["cutover_blockers"]
    assert json.loads(output.read_text()) == result
    assert (report.read_bytes(), identity.read_bytes()) == before


def test_cli_rejects_source_identity_mismatch(files, capsys):
    report, identity = files
    assert CHECK["main"]([
        "--report", str(report), "--identity", str(identity),
        "--expected-head", "2" * 40,
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "p11.audit.identity_mismatch"


def test_rehashed_summary_cannot_replace_recomputed_metrics(files):
    report, identity = files
    document = json.loads(report.read_text())
    document["median_dynamic_context_tokens"] += 1
    document["report_sha256"] = canonical_sha256({
        key: value for key, value in document.items() if key != "report_sha256"
    })
    _write(report, document)
    _write(identity, _identity(document))
    with pytest.raises(CHECK["EvidenceCheckError"], match="recomputed_report_mismatch"):
        CHECK["check_evidence"](report, identity, HEAD)


@pytest.mark.parametrize("mutation", [
    "duplicate_key", "bool_count", "unknown_field", "false_containment",
])
def test_ambiguous_or_wrongly_typed_json_fails_closed(files, mutation):
    report, identity = files
    document = json.loads(report.read_text())
    if mutation == "duplicate_key":
        text = report.read_text()
        report.write_text('{"task_count": 999, ' + text[1:])
    elif mutation == "bool_count":
        document["cases"][0]["execution"]["static_execution_count"] = True
        _write(report, document)
    elif mutation == "false_containment":
        document["faults"][0]["contained"] = False
        _write(report, document)
    else:
        document["approved_for_p12"] = True
        _write(report, document)
    with pytest.raises(CHECK["EvidenceCheckError"]):
        CHECK["check_evidence"](report, identity, HEAD)


def test_serialized_live_binding_sidecar_is_required_and_checked(tmp_path):
    # Synthetic contract data: these bytes make no live-provider claim.
    cases, bindings = _live_cases_and_bindings()
    value = build_p11_formal_shadow_report(
        cases, _faults(), evidence_mode="LIVE_PROVIDER_REPLAY",
        live_replay_bindings=bindings,
    )
    document = {**value.payload(), "report_sha256": value.report_sha256}
    report, identity, sidecar = (
        tmp_path / name for name in ("report.json", "identity.json", "bindings.json")
    )
    _write(report, document)
    _write(identity, _identity(document))
    _write(sidecar, [{**item.payload(), "binding_sha256": item.binding_sha256}
                     for item in bindings])
    with pytest.raises(ValueError, match="binding_evidence_missing"):
        CHECK["check_evidence"](report, identity, HEAD)
    result = CHECK["check_evidence"](report, identity, HEAD, sidecar)
    assert result["evaluation_matches"]
    assert result["bindings_file_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert not result["p12_authorized"]


def test_cli_refuses_to_overwrite_input_evidence(files, capsys):
    report, identity = files
    before = report.read_bytes()
    assert CHECK["main"]([
        "--report", str(report), "--identity", str(identity),
        "--expected-head", HEAD, "--output", str(report),
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "p11.audit.output_overwrites_input"
    assert report.read_bytes() == before
