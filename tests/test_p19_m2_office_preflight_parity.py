"""The retired Office prose preflight has no content gate or authority."""
import inspect
import pytest
from v3.simple_chain import content_preflight, kernel


def test_kernel_reexport_is_same_object():
    assert kernel._office_content_gaps is content_preflight._office_content_gaps


@pytest.mark.parametrize("name,content", [("shell.docx", b"title"), ("empty.xlsx", b""), ("rows.xlsx", b"placeholder"), ("report.docx", b"full text")])
def test_content_and_prose_do_not_create_office_requirements(tmp_path, name, content):
    path = tmp_path / name
    path.write_bytes(content)
    assert content_preflight._office_content_gaps("包含姓名分数两列、至少5行、各3条要点", [{"path": str(path)}]) == []
    assert content_preflight._office_content_gaps("任意内容", None) == []


def test_preflight_never_creates_verification_authority():
    source = inspect.getsource(content_preflight._office_content_gaps)
    assert "VerificationRecord" not in source
    assert "verification_plan" not in source
