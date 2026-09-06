"""Publication sealing failures must not become a verifiable release.

The signed fixture is synthetic protocol evidence, not approval of any real
source revision or Windows native acceptance.
"""

import json
from pathlib import Path

import pytest

from total_gateway.tool_source_publication import SourcePublicationError
from tests.test_tool_source_bundle_p8 import source  # noqa: F401
from tests.test_tool_source_publication_p8 import publication  # noqa: F401
from tests.test_tool_source_publication_review_p8 import (  # noqa: F401
    reviewed, publish, verify,
)


def test_reopen_rejects_a_writable_publication_directory(reviewed):
    result = publish(reviewed)
    root = reviewed[-2]
    root.chmod(0o755)
    with pytest.raises(SourcePublicationError, match="directory is not sealed"):
        verify(reviewed, result)


def test_ignored_directory_sealing_cannot_report_publication_success(reviewed, monkeypatch):
    root = reviewed[-2]
    real_chmod = Path.chmod
    attempts = []

    def ignore_seal(path, mode, *args, **kwargs):
        if path == root and mode == 0o555:
            attempts.append(mode)
            return None
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", ignore_seal)
    with pytest.raises(SourcePublicationError, match="directory is not sealed"):
        publish(reviewed)
    assert attempts == [0o555]
    assert root.stat().st_mode & 0o222
    receipt = json.loads((root / "publication.json").read_bytes())
    with pytest.raises(SourcePublicationError, match="directory is not sealed"):
        verify(reviewed, receipt)


def test_failed_sealing_leaves_receipt_unacceptable_on_reopen(reviewed, monkeypatch):
    root = reviewed[-2]
    real_chmod = Path.chmod
    attempts = []

    def fail_seal(path, mode, *args, **kwargs):
        if path == root and mode == 0o555:
            attempts.append(mode)
            raise OSError("injected publication-directory seal failure")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_seal)
    with pytest.raises(OSError, match="injected publication-directory seal failure"):
        publish(reviewed)
    assert attempts == [0o555]
    # The prior code leaves a well-formed receipt after this real call boundary.
    # Receipt bytes alone must not turn that failed operation into a valid release.
    receipt = json.loads((root / "publication.json").read_bytes())
    with pytest.raises(SourcePublicationError, match="directory is not sealed"):
        verify(reviewed, receipt)
