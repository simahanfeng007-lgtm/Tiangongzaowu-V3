from __future__ import annotations

from pathlib import Path

import pytest

from omni_body_skill.tools.sandbox_runtime import _copy_workspace, _merge_changes, _snapshot


@pytest.mark.parametrize("relative", [
    ".TiAnGoNg_EmErGeNcY_AuDiT/secret.txt",
    ".OMNI_WORKSPACE.LOCK",
    "project/.OMNI_AUDIT/secret.txt",
    "project/.TIANGONG_SANDBOXES/secret.txt",
])
def test_runtime_files_are_not_exposed_or_committed_under_case_variants(tmp_path, relative):
    host, private, trash = (tmp_path / name for name in ("host", "private", "trash"))
    host.mkdir()
    protected = host / relative
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("host-runtime-value", encoding="utf-8")
    (host / "artifact.txt").write_text("before", encoding="utf-8")
    _copy_workspace(host, private, 4096)
    assert not (private / relative).exists()
    before = _snapshot(private)
    candidate = private / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("sandbox replacement", encoding="utf-8")
    (private / "artifact.txt").write_text("after", encoding="utf-8")
    result = _merge_changes(private, host, before, max_changed_bytes=4096,
                            trash_root=trash, allow_deletions=False)
    assert result["changed_files"] == ["artifact.txt"]
    assert protected.read_text(encoding="utf-8") == "host-runtime-value"
    assert (host / "artifact.txt").read_text(encoding="utf-8") == "after"
