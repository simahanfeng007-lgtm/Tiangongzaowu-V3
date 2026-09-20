#!/usr/bin/env python3
"""Recompute P11 evidence from exported bytes, without granting phase exit.

Checks structure, content hashes, source identity and evaluator results. It
cannot authenticate a provider, prove a production trace or replace review.
No provider, Gateway, Runtime or business store is opened by this command.
"""

from __future__ import annotations

import argparse
from dataclasses import fields, is_dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys
from types import UnionType
from typing import Literal, get_args, get_origin, get_type_hints

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import canonical_json_bytes
from total_gateway.p11_live_replay_bridge import P11LiveReplayBindingV1
from world_understanding.capability_composition.formal_shadow import (
    P11FaultCaseV1,
    P11FormalShadowReportV1,
    build_p11_formal_shadow_report,
)


class EvidenceCheckError(ValueError):
    pass


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceCheckError("p11.audit.duplicate_json_key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise EvidenceCheckError("p11.audit.nonfinite_json")


def _read(path: Path):
    with path.open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise EvidenceCheckError("p11.audit.file_too_large")
    value = json.loads(
        data.decode("utf-8"), object_pairs_hook=_object,
        parse_constant=_invalid_constant,
    )
    return value, hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=None)
def _hints(cls):
    return get_type_hints(cls)


def _decode(expected, value):
    """Decode only the closed dataclass field types in the P11 contracts."""
    origin, args = get_origin(expected), get_args(expected)
    if origin is UnionType:
        for choice in args:
            try:
                return _decode(choice, value)
            except EvidenceCheckError:
                pass
        raise EvidenceCheckError("p11.audit.field_type_invalid")
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            raise EvidenceCheckError("p11.audit.literal_invalid")
        return value
    if origin is tuple:
        if type(value) is not list:
            raise EvidenceCheckError("p11.audit.array_required")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        if len(value) != len(args):
            raise EvidenceCheckError("p11.audit.tuple_size_invalid")
        return tuple(_decode(kind, item) for kind, item in zip(args, value))
    if expected in (str, int, bool, type(None)):
        if type(value) is not expected:
            raise EvidenceCheckError("p11.audit.field_type_invalid")
        return value
    if is_dataclass(expected):
        names = {field.name for field in fields(expected)}
        derived = {"active_path_preserved", "current_authority_preserved", "contained"} \
            if expected is P11FaultCaseV1 else set()
        if type(value) is not dict or set(value) != names | derived:
            raise EvidenceCheckError("p11.audit.fields_invalid")
        hints = _hints(expected)
        decoded = expected(**{
            name: _decode(hints[name], value[name]) for name in names
        })
        if any(
            type(value[name]) is not bool or value[name] != getattr(decoded, name)
            for name in derived
        ):
            raise EvidenceCheckError("p11.audit.derived_field_mismatch")
        return decoded
    raise EvidenceCheckError("p11.audit.unsupported_contract_type")


def check_evidence(
    report_path: Path,
    identity_path: Path,
    expected_head: str,
    bindings_path: Path | None = None,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
        raise EvidenceCheckError("p11.audit.expected_head_invalid")
    document, report_file_sha = _read(report_path)
    identity, identity_file_sha = _read(identity_path)
    report = _decode(P11FormalShadowReportV1, document)
    if not report.has_valid_sha256():
        raise EvidenceCheckError("p11.audit.report_hash_invalid")

    expected_identity = {
        "head": expected_head,
        "evidence_mode": report.evidence_mode,
        "formal_gate_passed": report.formal_gate_passed,
        "cutover_gate_passed": report.cutover_gate_passed,
        "cutover_blockers": list(report.cutover_blockers),
        "task_count": report.task_count,
        "model_observation_count": report.model_observation_count,
        "fault_case_count": report.fault_case_count,
        "report_sha256": report.report_sha256,
    }
    if type(identity) is not dict or any(
        key not in identity
        or canonical_json_bytes(identity[key]) != canonical_json_bytes(value)
        for key, value in expected_identity.items()
    ):
        raise EvidenceCheckError("p11.audit.identity_mismatch")

    bindings = ()
    bindings_file_sha = None
    if bindings_path is not None:
        raw_bindings, bindings_file_sha = _read(bindings_path)
        bindings = _decode(tuple[P11LiveReplayBindingV1, ...], raw_bindings)
    rebuilt = build_p11_formal_shadow_report(
        report.cases, report.faults, evidence_mode=report.evidence_mode,
        live_replay_bindings=bindings,
    )
    rebuilt_document = {**rebuilt.payload(), "report_sha256": rebuilt.report_sha256}
    if canonical_json_bytes(document) != canonical_json_bytes(rebuilt_document):
        raise EvidenceCheckError("p11.audit.recomputed_report_mismatch")

    return {
        "schema": "tiangong.p11-evidence-audit.v1",
        "scope": "STRUCTURE_AND_CONTENT_HASHES_ONLY",
        "source_head": expected_head,
        "report_file_sha256": report_file_sha,
        "identity_file_sha256": identity_file_sha,
        "bindings_file_sha256": bindings_file_sha,
        "report_sha256": rebuilt.report_sha256,
        "evidence_mode": rebuilt.evidence_mode,
        "task_count": rebuilt.task_count,
        "model_observation_count": rebuilt.model_observation_count,
        "fault_case_count": rebuilt.fault_case_count,
        "evaluation_matches": True,
        "formal_gate_passed": rebuilt.formal_gate_passed,
        "cutover_gate_passed": False,
        "cutover_blockers": list(rebuilt.cutover_blockers),
        "p12_authorized": False,
        "independent_review_required": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-production", action="store_true",
        help="Return 2 while any cutover blocker remains, including independent review.",
    )
    args = parser.parse_args(argv)
    try:
        inputs = {p.resolve() for p in (args.report, args.identity, args.bindings) if p}
        if args.output is not None and args.output.resolve() in inputs:
            raise EvidenceCheckError("p11.audit.output_overwrites_input")
        result = check_evidence(
            args.report, args.identity, args.expected_head, args.bindings
        )
        result["production_required"] = args.require_production
        result["status"] = (
            "BLOCKED" if not result["formal_gate_passed"]
            or (args.require_production and result["cutover_blockers"])
            else "INTEGRITY_VERIFIED"
        )
        code = 2 if result["status"] == "BLOCKED" else 0
    except (OSError, ValueError, TypeError, RecursionError, KeyError) as exc:
        # Do not echo raw evidence, credentials or arbitrary external error text.
        error = str(exc) if isinstance(exc, EvidenceCheckError) else getattr(
            exc, "code", "p11.audit.invalid_input"
        )
        result = {"status": "INVALID", "error": error, "p12_authorized": False}
        code = 1
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None and code != 1:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
