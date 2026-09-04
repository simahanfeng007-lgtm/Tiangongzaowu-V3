from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from contracts import canonical_json_bytes
from total_gateway.policy_evidence import PolicyEvidenceLedger


def test_separate_ledgers_publish_one_content_address_without_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "policy-evidence"
    ledgers = (PolicyEvidenceLedger(root), PolicyEvidenceLedger(root))
    payload = {
        "schema": "tiangong.gateway.policy-evidence-test.v1",
        "decision_id": "policy-decision-concurrent",
        "outcome": "ALLOW",
    }
    original_link = os.link
    publish_barrier = threading.Barrier(2)
    link_outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def synchronized_link(source, destination, *args, **kwargs):
        publish_barrier.wait(timeout=10)
        try:
            original_link(source, destination, *args, **kwargs)
        except FileExistsError:
            with outcomes_lock:
                link_outcomes.append("exists")
            raise
        else:
            with outcomes_lock:
                link_outcomes.append("created")

    monkeypatch.setattr(os, "link", synchronized_link)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = tuple(
            pool.map(
                lambda ledger: ledger.record(
                    "policy-decision",
                    "policy-decision-concurrent",
                    payload,
                ),
                ledgers,
            )
        )

    assert rows[0] == rows[1]
    assert sorted(link_outcomes) == ["created", "exists"]
    path = Path(rows[0]["path"])
    assert path.read_bytes() == canonical_json_bytes(payload) + b"\n"
    assert tuple(path.parent.glob("~*.tmp")) == ()


def test_publish_failure_without_winner_fails_closed_and_cleans_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "policy-evidence"
    ledger = PolicyEvidenceLedger(root)

    def reject_link(*_args, **_kwargs):
        raise PermissionError("publication denied")

    monkeypatch.setattr(os, "link", reject_link)
    with pytest.raises(PermissionError, match="publication denied"):
        ledger.record(
            "policy-decision",
            "policy-decision-denied",
            {"schema": "tiangong.gateway.policy-evidence-test.v1"},
        )

    evidence_directory = root / "d"
    assert tuple(evidence_directory.glob("*.json")) == ()
    assert tuple(evidence_directory.glob("~*.tmp")) == ()
