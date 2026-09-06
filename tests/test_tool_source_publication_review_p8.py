"""Operator review/byte publication contracts with synthetic signed evidence.

These fixtures test acceptance rules. They are not native execution receipts
and never claim the real repository's risk changes have been approved.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from contracts import canonical_json_bytes
from total_gateway.tool_source_publication import (
    SourcePublicationError, prepare_tool_source_publication, publish_tool_source_revision,
    verify_published_tool_source_revision,
)

from tests.test_tool_source_bundle_p8 import source  # noqa: F401
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


@pytest.fixture
def reviewed(publication, tmp_path):
    root, base, head, package, marker = publication
    path, digest = package()
    arguments = dict(bundle_path=path, expected_sha256=digest, base_commit=base,
                     candidate_commit=head, requested_action_ids=("skill.list",))
    proposal = prepare_tool_source_publication(root, **arguments)
    binding = {"candidate_sha256": proposal["source_candidate"]["candidate_sha256"],
               "source_inputs_sha256": proposal["source_inputs_sha256"],
               "capability_manifest_sha256": proposal["capability_manifest_sha256"],
               "may_publish": False, "may_authorize": False, "may_execute": False}
    contracts = {**binding, "schema": "tiangong.source-evidence-contract-observation.v1",
                 "status": "EVIDENCE_CONTRACT_OBSERVED", "cases": [
                     {"case_id": "fixture." + kind, "kind": kind, "action_id": "skill.list",
                      "passed": True, "observation_sha256": hashlib.sha256(kind.encode()).hexdigest()}
                     for kind in ("positive", "negative")]}
    lock = {**binding, "schema": "tiangong.running-source-lock-observation.v1",
            "status": "RUNNING_SOURCE_LOCK_OBSERVED", "old_before_sha256": "0" * 64,
            "old_after_sha256": "0" * 64, "new_run_sha256": proposal["capability_manifest_sha256"],
            "resumption_verified": True, "old_run_continued": True}
    with zipfile.ZipFile(path) as archive:
        evidence = {"build": archive.read("build-report.json"),
                    "evidence_contract": canonical_json_bytes(contracts),
                    "running_manifest_lock": canonical_json_bytes(lock)}
    private = Ed25519PrivateKey.generate()

    def approval(*, mutate=lambda value: None):
        review = {
            "schema": "tiangong.tool-source-publication-review.v1",
            "proposal_sha256": proposal["proposal_sha256"],
            "reviewed_action_ids": proposal["required_review_action_ids"],
            "approved_risk_downgraded_action_ids": list(proposal["manifest_review"]["risk_downgraded_action_ids"]),
            "approved_new_a0_action_ids": list(proposal["manifest_review"]["newly_a0_action_ids"]),
            "accept_generated_manifest": True,
            "evidence_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in evidence.items()},
        }
        mutate(review)
        raw = canonical_json_bytes(review)
        return {"review_bytes": raw,
                "signature": private.sign(b"tiangong.tool-source-publication-review.v1\x00" + raw),
                "trusted_public_key": private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}

    return root, arguments, evidence, approval, tmp_path / "published", marker


def publish(reviewed, **overrides):
    root, arguments, evidence, approval, destination, _ = reviewed
    return publish_tool_source_revision(root, **arguments, publication_root=destination,
                                        evidence=evidence, **{**approval(), **overrides})


def verify(reviewed, result, **overrides):
    root, arguments, _, approval, destination, _ = reviewed
    fields = dict(publication_root=destination,
                  expected_publication_sha256=result["publication_sha256"],
                  expected_bundle_sha256=arguments["expected_sha256"],
                  base_commit=arguments["base_commit"], candidate_commit=arguments["candidate_commit"],
                  requested_action_ids=arguments["requested_action_ids"],
                  trusted_public_key=approval()["trusted_public_key"])
    fields.update(overrides)
    return verify_published_tool_source_revision(root, **fields)


def test_reviewed_version_is_published_separately_without_current_run_or_registry_switch(reviewed):
    result = publish(reviewed)
    root = reviewed[-2]
    assert result["status"] == "SOURCE_REVISION_PUBLISHED"
    assert result["running_manifest_changed"] is False
    assert result["may_authorize"] is result["may_execute"] is False
    assert (root / "publication.json").is_file()
    assert (root / result["source_root"]).is_dir()
    assert not (root.parent / "current").exists()
    assert not reviewed[-1].exists()
    assert (root / "review.json").read_bytes() == reviewed[3]()["review_bytes"]
    assert verify(reviewed, result) == result
    for path in root.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o222 == 0
    with pytest.raises(SourcePublicationError, match="new directory"):
        publish(reviewed)


@pytest.mark.parametrize("relative", ["publication.json", "review.json", "evidence_contract.json"])
def test_reopening_does_not_trust_modified_publication_metadata(reviewed, relative):
    result = publish(reviewed)
    path = reviewed[-2] / relative
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b" ")
    path.chmod(0o444)
    with pytest.raises(SourcePublicationError):
        verify(reviewed, result)


def test_reopening_uses_external_publication_and_reviewer_pins(reviewed):
    result = publish(reviewed)
    with pytest.raises(SourcePublicationError, match="reviewed identity"):
        verify(reviewed, result, expected_publication_sha256="0" * 64)
    with pytest.raises(SourcePublicationError, match="signature"):
        verify(reviewed, result, trusted_public_key=b"x" * 32)


@pytest.mark.parametrize("field", ["signature", "trusted_public_key"])
def test_unknown_signer_or_unsigned_build_cannot_publish(reviewed, field):
    value = b"x" * (64 if field == "signature" else 32)
    with pytest.raises(SourcePublicationError, match="signature"):
        publish(reviewed, **{field: value})
    assert not reviewed[-2].exists()


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(proposal_sha256="0" * 64),
    lambda r: r.update(reviewed_action_ids=[]),
    lambda r: r.update(approved_new_a0_action_ids=["invented"]),
    lambda r: r.update(accept_generated_manifest=False),
    lambda r: r.update(accept_generated_manifest=1),
    lambda r: r.update(extra_authority=True),
])
def test_even_a_valid_signature_must_cover_the_exact_review_scope(reviewed, mutate):
    with pytest.raises(SourcePublicationError, match="exact source and risk scope"):
        publish(reviewed, **reviewed[3](mutate=mutate))
    assert not reviewed[-2].exists()


@pytest.mark.parametrize("name, mutate, message", [
    ("evidence_contract", lambda d: d["cases"].pop(), "coverage is incomplete"),
    ("evidence_contract", lambda d: d["cases"][0].update(passed=False), "case is invalid or failed"),
    ("evidence_contract", lambda d: d["cases"][0].update(kind=[]), "case is invalid or failed"),
    ("evidence_contract", lambda d: d.update(candidate_sha256="f" * 64), "identity or outcome"),
    ("running_manifest_lock", lambda d: d.update(old_after_sha256=d["new_run_sha256"]), "old/new source lock"),
    ("running_manifest_lock", lambda d: d.update(resumption_verified=False), "old/new source lock"),
    ("running_manifest_lock", lambda d: d.update(cleanup_error="failed shutdown"), "identity or outcome"),
])
def test_review_does_not_hide_incomplete_or_contradictory_behavioral_observations(reviewed, name, mutate, message):
    evidence = reviewed[2]
    value = json.loads(evidence[name])
    mutate(value)
    evidence[name] = canonical_json_bytes(value)
    with pytest.raises(SourcePublicationError, match=message):
        publish(reviewed)
    assert not reviewed[-2].exists()


def test_evidence_cannot_change_after_review_was_signed(reviewed):
    approved = reviewed[3]()
    reviewed[2]["evidence_contract"] += b"\n"
    with pytest.raises(SourcePublicationError, match="evidence bytes"):
        publish(reviewed, **approved)
    assert not reviewed[-2].exists()


def test_partial_staging_failure_never_writes_publication_completion_marker(reviewed, monkeypatch):
    import total_gateway.tool_source_publication as module

    def fail(*args, **kwargs):
        raise OSError("injected staging failure")

    monkeypatch.setattr(module, "stage_tool_source_bundle", fail)
    with pytest.raises(OSError, match="staging failure"):
        publish(reviewed)
    assert reviewed[-2].is_dir()
    assert not (reviewed[-2] / "publication.json").exists()
    with pytest.raises(SourcePublicationError, match="new directory"):
        publish(reviewed)


def test_operator_cli_publishes_and_reverifies_the_signed_version(reviewed, tmp_path, capsys):
    root, arguments, evidence, approval, destination, _ = reviewed
    signed = approval()
    paths = {}
    for name, raw in {"key": signed["trusted_public_key"], "review": signed["review_bytes"],
                      "signature": signed["signature"], "contracts": evidence["evidence_contract"],
                      "lock": evidence["running_manifest_lock"]}.items():
        paths[name] = tmp_path / (name + ".input")
        paths[name].write_bytes(raw)
    script = Path(__file__).resolve().parents[1] / "scripts/publish-tool-source.py"
    spec = importlib.util.spec_from_file_location("p8_operator_publication_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = ["--repository", str(root), "--sha256", arguments["expected_sha256"],
              "--base", arguments["base_commit"], "--candidate", arguments["candidate_commit"],
              "--action", "skill.list", "--destination", str(destination),
              "--trusted-review-key", str(paths["key"])]
    command = [*common, "--bundle", str(arguments["bundle_path"]),
               "--review", str(paths["review"]), "--signature", str(paths["signature"]),
               "--evidence-contract", str(paths["contracts"]), "--running-manifest-lock", str(paths["lock"])]
    assert module.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "SOURCE_REVISION_PUBLISHED"
    assert module.main([*common, "--verify-only", "--publication-sha256", result["publication_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert module.main([*common, "--verify-only", "--publication-sha256", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "SOURCE_PUBLICATION_REJECTED"
