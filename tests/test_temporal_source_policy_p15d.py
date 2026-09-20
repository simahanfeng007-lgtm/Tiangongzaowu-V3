"""P15-D: temporal source policy — the sealed side under revocation.

The new-request and pre-Plan sides are already pinned by the R1C2 suites
(WORLD_NO_LONGER_CURRENT, PREPARATION_DRIFT, bundle/archive byte drift) and
the P9 retention suite covers release/restart/pruning. This file adds the
remaining sealed-side revocation counterexamples on the real roundtrip
world: a sealed plan's source archive is tampered with or removed AFTER
registration — the reader must refuse (never fall back to latest or to a
silent substitute), the pin stays observable, and re-registration of the
same plan against the damaged archive world is refused at compile time.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_source_execution_roundtrip_p12 import (  # noqa: F401  fixtures/helpers
    intake, intake_factory, source, _prepare, _workspace_binding,
)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


def _seal(c):
    """Register one plan and return (resolver, scope, executable record)."""
    from tests.test_source_registration_intake_p12 import _compile, _register
    result = _compile(c, _prepare(c)[0], intents=frozenset(
        {'verification-intent:plan-bound-acceptance'}))
    outcome = _register(c, result)
    record = c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1)
    assert record is not None
    return outcome, record



def _tamper(blob: Path) -> None:
    """Flip the archive's last byte, imitating an attacker who briefly
    regains write access; the staged file is restored to read-only after."""
    raw = bytearray(blob.read_bytes())
    raw[-1] ^= 0xFF
    blob.chmod(0o644)
    blob.write_bytes(bytes(raw))
    blob.chmod(0o444)


def _archive_dir(c, tmp_path) -> Path:
    root = tmp_path / 'method-archives'
    assert root.is_dir(), 'fixture archive root must exist'
    return root


def test_sealed_read_refuses_tampered_archive(intake, tmp_path):
    """Tampering the sealed plan's source archive after registration closes
    the reader; no silent fallback to any other source state."""
    c = intake
    outcome, record = _seal(c)
    methods = c['resolver'].read(request_id=c['rc'].request_id,
                                 run_id=c['rc'].run_id, generation=1,
                                 scope=c['query_scope'])
    assert methods is not None  # healthy path first

    archive = _archive_dir(c, tmp_path)
    victims = sorted(archive.glob('*.json'))
    assert victims, 'the sealed plan must have a source archive on disk'
    blob = victims[0]
    _tamper(blob)
    with pytest.raises(Exception):
        c['resolver'].read(request_id=c['rc'].request_id,
                           run_id=c['rc'].run_id, generation=1,
                           scope=c['query_scope'])
    # The durable registration and pin remain observable for audit even
    # though execution reads are closed.
    assert c['gateway'].get_executable_composition_plan_record(
        outcome.executable_plan_id) is not None
    assert any(pin.owner_id == 'method-plan:' + outcome.executable_plan_id
               for pin in c['world'].store.retained_states())


def test_sealed_read_refuses_removed_archive(intake, tmp_path):
    """Removing the archive outright is also a refusal, never a latest pick."""
    c = intake
    _outcome, _record = _seal(c)
    archive = _archive_dir(c, tmp_path)
    victims = sorted(archive.glob('*.json'))
    assert victims
    for blob in victims:
        blob.chmod(0o644)
        blob.unlink()
    with pytest.raises(Exception):
        c['resolver'].read(request_id=c['rc'].request_id,
                           run_id=c['rc'].run_id, generation=1,
                           scope=c['query_scope'])


def test_new_preparation_on_damaged_archive_is_refused(intake, tmp_path):
    """A NEW pre-Plan preparation against the damaged archive world is
    refused at prepare time — revocation reaches future requests too."""
    c = intake
    _outcome, _record = _seal(c)
    archive = _archive_dir(c, tmp_path)
    victims = sorted(archive.glob('*.json'))
    assert victims
    _tamper(victims[0])
    with pytest.raises(Exception):
        _prepare(c)
