from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "embedded_python_integrity.py"


def _module():
    spec = importlib.util.spec_from_file_location("embedded_python_integrity_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recorded(path: Path, relative: str) -> str:
    content = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
    return f"{relative},sha256={digest},{len(content)}\n"


def _installed_demo(tmp_path: Path) -> tuple[Path, Path, Path]:
    site = tmp_path / "Lib" / "site-packages"
    package = site / "demo"
    package.mkdir(parents=True)
    module_file = package / "__init__.py"
    module_file.write_text("value = 1\n", encoding="utf-8")
    dist_info = site / "demo-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        _recorded(module_file, "demo/__init__.py")
        + "demo/__pycache__/__init__.cpython-312.pyc,,\n"
        + _recorded(dist_info / "METADATA", "demo-1.0.dist-info/METADATA")
        + "demo-1.0.dist-info/RECORD,,\n"
        + "demo-1.0.dist-info/direct_url.json,,\n",
        encoding="utf-8",
    )
    lock = tmp_path / "requirements-release.lock"
    lock.write_text("demo==1.0\n", encoding="utf-8")
    return site, lock, module_file


def test_missing_real_module_is_detected_while_optional_provenance_and_bytecode_are_ignored(tmp_path: Path) -> None:
    helper = _module()
    site, lock, module_file = _installed_demo(tmp_path)
    assert not helper.audit_embedded_install(site, lock).problems

    module_file.unlink()
    result = helper.audit_embedded_install(site, lock)
    assert "demo/__init__.py" in result.problems[0]
    assert result.repair_specs == ("demo==1.0",)


def test_same_size_module_replacement_fails_hash_check_and_requests_reinstall(tmp_path: Path) -> None:
    helper = _module()
    site, lock, module_file = _installed_demo(tmp_path)
    assert not helper.audit_embedded_install(site, lock).problems

    module_file.write_text("value = 2\n", encoding="utf-8")
    result = helper.audit_embedded_install(site, lock)
    assert "content hash mismatch" in result.problems[0]
    assert result.repair_specs == ("demo==1.0",)


def test_standard_record_hashes_are_accepted(tmp_path: Path) -> None:
    helper = _module()
    site, lock, module_file = _installed_demo(tmp_path)
    record = site / "demo-1.0.dist-info" / "RECORD"
    original = record.read_text(encoding="utf-8")
    original_row = original.splitlines()[0]
    for algorithm in ("sha256", "sha384", "sha512"):
        digest = base64.urlsafe_b64encode(hashlib.new(algorithm, module_file.read_bytes()).digest()).rstrip(b"=").decode("ascii")
        replacement = f"demo/__init__.py,{algorithm}={digest},{module_file.stat().st_size}"
        record.write_text(original.replace(original_row, replacement), encoding="utf-8")
        assert not helper.audit_embedded_install(site, lock).problems


def test_size_change_fails_before_hashing(tmp_path: Path) -> None:
    helper = _module()
    site, lock, module_file = _installed_demo(tmp_path)
    module_file.write_text("value = 1234\n", encoding="utf-8")
    assert "size mismatch" in helper.audit_embedded_install(site, lock).problems[0]


def test_missing_record_and_missing_pinned_distribution_fail_closed(tmp_path: Path) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    (site / "demo-1.0.dist-info" / "RECORD").unlink()
    result = helper.audit_embedded_install(site, lock)
    assert "RECORD is missing" in result.problems[0]
    assert result.repair_specs == ("demo==1.0",)

    (site / "demo-1.0.dist-info" / "METADATA").unlink()
    result = helper.audit_embedded_install(site, lock)
    assert any("required version 1.0 is not installed" in item for item in result.problems)


def test_record_outside_runtime_is_rejected(tmp_path: Path) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    with (site / "demo-1.0.dist-info" / "RECORD").open("a", encoding="utf-8") as stream:
        stream.write("../../../outside.py,,\n")
    assert helper.audit_embedded_install(site, lock).problems


def test_recorded_scripts_are_checked_but_remain_inside_runtime(tmp_path: Path) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    command = scripts / "demo.exe"
    command.write_bytes(b"exe")
    with (site / "demo-1.0.dist-info" / "RECORD").open("a", encoding="utf-8") as stream:
        stream.write(_recorded(command, "../../Scripts/demo.exe"))
    assert not helper.audit_embedded_install(site, lock).problems
    command.unlink()
    assert "../../Scripts/demo.exe" in helper.audit_embedded_install(site, lock).problems[0]


def test_unpinned_transitive_distribution_is_repaired_at_installed_version(tmp_path: Path) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    helper_dist = site / "helper-2.0.dist-info"
    helper_dist.mkdir()
    (helper_dist / "METADATA").write_text("Metadata-Version: 2.1\nName: helper\nVersion: 2.0\n", encoding="utf-8")
    (helper_dist / "RECORD").write_text("helper/__init__.py,,\nhelper-2.0.dist-info/RECORD,,\n", encoding="utf-8")
    result = helper.audit_embedded_install(site, lock)
    assert result.repair_specs == ("helper==2.0",)
    assert "helper/__init__.py" in result.problems[0]


def test_pinned_version_and_record_size_limits_are_checked(tmp_path: Path, monkeypatch) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    lock.write_text("demo==1.1\n", encoding="utf-8")
    result = helper.audit_embedded_install(site, lock)
    assert any("required version 1.1" in problem for problem in result.problems)
    assert result.repair_specs == ("demo==1.1",)

    lock.write_text("demo==1.0\n", encoding="utf-8")
    monkeypatch.setattr(helper, "MAX_RECORD_BYTES", 1)
    result = helper.audit_embedded_install(site, lock)
    assert "exceeds the audit limit" in result.problems[0]


def test_recorded_hash_is_required_and_total_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    helper = _module()
    site, lock, _ = _installed_demo(tmp_path)
    record = site / "demo-1.0.dist-info" / "RECORD"
    record.write_text(record.read_text(encoding="utf-8").replace("sha256=", "sha1="), encoding="utf-8")
    assert "invalid file size/hash" in helper.audit_embedded_install(site, lock).problems[0]

    site, lock, module_file = _installed_demo(tmp_path / "bounded")
    monkeypatch.setattr(helper, "MAX_TOTAL_HASHED_BYTES", len(module_file.read_bytes()) - 1)
    assert "hashing exceeds the audit limit" in helper.audit_embedded_install(site, lock).problems[0]
