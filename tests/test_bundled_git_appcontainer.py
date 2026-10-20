"""Only an anchored installed Git launcher receives its complete bundle RX root."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/omni_body_skill/tools/windows_appcontainer.py"
CORE_FILES = (
    "cmd/git.exe", "mingw64/bin/git.exe", "mingw64/libexec/git-core/git.exe", "usr/bin/sh.exe",
    "mingw64/bin/libiconv-2.dll", "mingw64/bin/libintl-8.dll", "mingw64/bin/libpcre2-8-0.dll",
    "mingw64/bin/zlib1.dll", "LICENSE.txt",
)


def _helpers(system_root=None):
    # Run the actual pure selection/validation functions on every platform;
    # importing the full launcher would load Windows DLLs during collection.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
        "_check_budget", "_unlinked_bundle_path", "_runtime_root_for_executable",
        "_bundled_resource_context", "_validate_bundled_python_runtime", "_validate_bundled_resource_context",
        "_system_shell_runtime_roots",
    }]
    def windows_directory(buffer, size):
        assert system_root is not None, "ordinary direct tool must not request a system shell capability"
        buffer.value = str(system_root)
        return len(buffer.value)
    namespace = {"Path": Path, "os": os, "sys": sys, "time": time, "stat": stat,
                 "json": json, "hashlib": hashlib, "re": re, "ctypes": ctypes, "Mapping": dict,
                 "kernel32": SimpleNamespace(GetWindowsDirectoryW=windows_directory)}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


@pytest.fixture
def bundle(tmp_path):
    resources = tmp_path / "installed/resources"
    git = resources / "git"
    gateway = resources / "total-gateway/tiangong-total-gateway.exe"
    python = resources / "python/python.exe"
    for item in (resources / "app.asar", gateway, python):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(b"installed runtime fixture")
    (python.parent / "Lib/site-packages").mkdir(parents=True)
    for name in ("python3.dll", "python312.dll", "python312.zip", "python312._pth"):
        (python.parent / name).write_bytes(b"embedded Python fixture")
    (python.parent / "runtime-manifest.json").write_text(
        json.dumps({"schema": "tiangong.embedded-python.v1"}), encoding="utf-8",
    )
    for name in ("bin", "cmd", "etc", "mingw64/libexec/git-core", "usr/bin"):
        (git / name).mkdir(parents=True, exist_ok=True)
    records = []
    for name in CORE_FILES:
        item = git / name
        item.parent.mkdir(parents=True, exist_ok=True)
        payload = ("public bundle fixture: " + name).encode()
        item.write_bytes(payload)
        records.append({"path": name, "sizeBytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {"schema": "tiangong.isolated-tools.git.v1", "toolId": "git",
                "payloadRoots": ["bin", "cmd", "etc", "mingw64", "usr", "LICENSE.txt"],
                "fileCount": len(records), "sizeBytes": sum(row["sizeBytes"] for row in records), "files": records}
    (git / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return SimpleNamespace(resources=resources, git=git, gateway=gateway, python=python,
                           command=git / "cmd/git.exe", manifest=manifest)


def _anchor(monkeypatch, bundle, kind="gateway"):
    monkeypatch.setattr(sys, "executable", str(bundle.gateway if kind == "gateway" else bundle.python))
    monkeypatch.setattr(sys, "frozen", kind == "gateway", raising=False)


def _select(helpers, command, **kwargs):
    return helpers["_runtime_root_for_executable"](command, deadline=time.monotonic() + 10, **kwargs)


@pytest.mark.parametrize("anchor", ["gateway", "python"])
def test_installed_git_uses_root_covering_launcher_sibling_libraries(bundle, monkeypatch, anchor):
    _anchor(monkeypatch, bundle, anchor)
    monkeypatch.setenv("TIANGONG_BUNDLED_GIT_ROOT", str(bundle.resources.parent / "host-git"))
    runtime = _select(_helpers(), bundle.command)
    assert runtime == bundle.git
    assert runtime != bundle.command.parent
    assert (bundle.git / "mingw64/bin/git.exe").is_relative_to(runtime)
    assert (bundle.git / "usr/bin/sh.exe").is_relative_to(runtime)
    assert runtime != bundle.resources


@pytest.mark.parametrize("command", ["python/python.exe", "python/Lib/site-packages/playwright/driver/node.exe", "git/bin/git.exe"])
def test_other_private_tools_keep_executable_parent(bundle, monkeypatch, command):
    _anchor(monkeypatch, bundle)
    item = bundle.resources / command
    item.parent.mkdir(parents=True, exist_ok=True)
    if not item.exists():
        item.write_bytes(b"ordinary executable")
    assert _select(_helpers(), item) == item.parent.resolve()


@pytest.mark.parametrize("reason", ["not-frozen", "fake-parent", "host-git", "source-python"])
def test_filename_or_environment_alone_never_expands_an_unrelated_git(bundle, monkeypatch, tmp_path, reason):
    _anchor(monkeypatch, bundle)
    command = bundle.command
    if reason == "not-frozen":
        monkeypatch.setattr(sys, "frozen", False)
    elif reason == "fake-parent":
        renamed = bundle.resources / "gateway/tiangong-total-gateway.exe"
        renamed.parent.mkdir()
        renamed.write_bytes(b"fake gateway name")
        monkeypatch.setattr(sys, "executable", str(renamed))
    elif reason == "source-python":
        source = tmp_path / "source/python.exe"
        source.parent.mkdir()
        source.write_bytes(b"source Python")
        monkeypatch.setattr(sys, "executable", str(source))
        monkeypatch.setattr(sys, "frozen", False)
    else:
        command = tmp_path / "host-git/cmd/git.exe"
        command.parent.mkdir(parents=True)
        command.write_bytes(b"unrelated Git")
    monkeypatch.setenv("TIANGONG_BUNDLED_GIT_ROOT", str(bundle.git))
    assert _select(_helpers(), command) == command.parent.resolve()


@pytest.mark.parametrize("missing", [*CORE_FILES, "bundle-manifest.json", "etc", "../app.asar"])
def test_incomplete_matched_git_bundle_fails_closed(bundle, monkeypatch, missing):
    _anchor(monkeypatch, bundle)
    item = bundle.git / missing
    item.rmdir() if item.is_dir() else item.unlink()
    with pytest.raises(OSError, match="trusted_bundled_git_runtime_invalid"):
        _select(_helpers(), bundle.command)


@pytest.mark.parametrize("fault", ["schema", "duplicate", "escaped", "core-sha", "size", "record-type"])
def test_invalid_bundle_manifest_cannot_authorize_a_root(bundle, monkeypatch, fault):
    _anchor(monkeypatch, bundle)
    manifest = bundle.manifest
    if fault == "schema":
        manifest["schema"] = "caller-created"
    elif fault == "duplicate":
        manifest["files"][1] = manifest["files"][0]
    elif fault == "escaped":
        manifest["files"][0]["path"] = "../cmd/git.exe"
    elif fault == "core-sha":
        manifest["files"][0]["sha256"] = "0" * 64
    elif fault == "record-type":
        manifest["files"][0] = "untrusted record"
    else:
        manifest["sizeBytes"] += 1
    (bundle.git / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(OSError, match="trusted_bundled_git_runtime_invalid"):
        _select(_helpers(), bundle.command)


@pytest.mark.parametrize("missing", ["python312._pth", "runtime-manifest.json"])
def test_resource_python_requires_a_complete_private_anchor(bundle, monkeypatch, missing):
    _anchor(monkeypatch, bundle, "python")
    (bundle.python.parent / missing).unlink()
    with pytest.raises(OSError, match="trusted_bundled_git_runtime_invalid"):
        _select(_helpers(), bundle.command)


@pytest.mark.parametrize("redirect", ["git", "git/mingw64", "total-gateway", "git/cmd"])
def test_redirected_bundle_directories_cannot_expand_runtime_rx(bundle, monkeypatch, tmp_path, redirect):
    _anchor(monkeypatch, bundle)
    item = bundle.resources / redirect
    outside = tmp_path / ("outside-" + redirect.replace("/", "-"))
    item.rename(outside)
    if os.name == "nt":
        cmd = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
        created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J", str(item), str(outside)], capture_output=True)
        assert created.returncode == 0, created.stderr
    else:
        item.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(OSError, match="trusted_bundled_git_runtime_invalid"):
            _select(_helpers(), bundle.command)
    finally:
        item.rmdir() if os.name == "nt" else item.unlink()
    assert outside.is_dir()


def test_bundle_validation_obeys_existing_cancellation_and_deadline(bundle, monkeypatch):
    _anchor(monkeypatch, bundle)
    helpers = _helpers()
    with pytest.raises(RuntimeError, match="sandbox_cancelled"):
        _select(helpers, bundle.command, cancel_check=lambda: True)
    with pytest.raises(TimeoutError, match="sandbox_timeout"):
        helpers["_runtime_root_for_executable"](bundle.command, deadline=time.monotonic() - 1)


@pytest.fixture
def system_shell(tmp_path):
    windows = tmp_path / "actual-Windows"
    cmd = windows / "System32/cmd.exe"
    powershell = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
    for item in (cmd, powershell):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(b"native Windows shell fixture")
    return SimpleNamespace(root=windows, cmd=cmd, powershell=powershell)


def _shell_roots(bundle, system_shell, executable=None, path=None, **kwargs):
    if path is None:
        path = os.pathsep.join(map(str, (bundle.python.parent, bundle.command.parent, system_shell.cmd.parent)))
    return _helpers(system_shell.root)["_system_shell_runtime_roots"](
        executable or system_shell.powershell, {"PATH": path}, deadline=time.monotonic() + 10, **kwargs,
    )


@pytest.mark.parametrize("shell", ["cmd", "powershell"])
@pytest.mark.parametrize("anchor", ["gateway", "python"])
def test_native_system_shell_gets_only_explicit_private_roots(bundle, system_shell, monkeypatch, shell, anchor):
    _anchor(monkeypatch, bundle, anchor)
    roots = _shell_roots(bundle, system_shell, getattr(system_shell, shell))
    assert roots == [bundle.python.parent, bundle.git]
    assert bundle.resources not in roots
    assert system_shell.root not in roots


@pytest.mark.parametrize("reason", ["not-frozen", "no-private-path", "host-git", "fake-path", "fake-shell", "spoofed-SystemRoot"])
def test_system_shell_never_trusts_caller_paths_or_unrelated_tools(bundle, system_shell, monkeypatch, tmp_path, reason):
    _anchor(monkeypatch, bundle)
    path = str(bundle.command.parent)
    executable = system_shell.powershell
    if reason == "not-frozen":
        monkeypatch.setattr(sys, "frozen", False)
    elif reason == "no-private-path":
        path = str(system_shell.cmd.parent)
    elif reason == "host-git":
        path = str(tmp_path / "host-git/cmd")
    elif reason == "fake-path":
        path = str(bundle.resources / "git-other/cmd")
    else:
        executable = tmp_path / "caller/System32/WindowsPowerShell/v1.0/powershell.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"caller shell with same name")
        if reason == "spoofed-SystemRoot":
            monkeypatch.setenv("SystemRoot", str(tmp_path / "caller"))
    monkeypatch.setenv("TIANGONG_BUNDLED_GIT_ROOT", str(bundle.git))
    assert _shell_roots(bundle, system_shell, executable, path) == []


@pytest.mark.parametrize("tool", ["python", "git", "node", "ffmpeg"])
def test_shell_requires_only_matched_private_runtime_and_does_not_require_unrelated_git(bundle, system_shell, monkeypatch, tool):
    _anchor(monkeypatch, bundle)
    if tool == "git":
        (bundle.python.parent / "runtime-manifest.json").unlink()
        path = bundle.command.parent
        expected = [bundle.git]
    else:
        (bundle.git / "bundle-manifest.json").unlink()
        path = {"python": bundle.python.parent,
                "node": bundle.python.parent / "Lib/site-packages/playwright/driver",
                "ffmpeg": bundle.python.parent / "Lib/site-packages/imageio_ffmpeg/binaries"}[tool]
        path.mkdir(parents=True, exist_ok=True)
        expected = [bundle.python.parent]
    assert _shell_roots(bundle, system_shell, path=str(path)) == expected


@pytest.mark.parametrize("broken", ["git/bundle-manifest.json", "python/runtime-manifest.json", "python/python312.dll"])
def test_shell_rejects_broken_matched_private_bundle(bundle, system_shell, monkeypatch, broken):
    _anchor(monkeypatch, bundle)
    (bundle.resources / broken).unlink()
    with pytest.raises(OSError, match="trusted_bundled_shell_runtime_invalid"):
        _shell_roots(bundle, system_shell)


def test_system_shell_validation_remains_cancellable(bundle, system_shell, monkeypatch):
    _anchor(monkeypatch, bundle)
    with pytest.raises(RuntimeError, match="sandbox_cancelled"):
        _shell_roots(bundle, system_shell, cancel_check=lambda: True)


@pytest.mark.skipif(os.name != "nt", reason="Windows ctypes launcher wiring")
@pytest.mark.parametrize("anchor", ["gateway", "python"])
@pytest.mark.parametrize("command_kind", ["direct", "shell"])
def test_normal_launcher_provisions_selected_roots_and_attaches_only_named_reader_sids(bundle, system_shell, monkeypatch, anchor, command_kind):
    from omni_body_skill.tools import windows_appcontainer as native
    _anchor(monkeypatch, bundle, anchor)
    sandbox = bundle.resources.parent / "private-sandbox"
    sandbox.mkdir()
    roots, attached = [], []

    class ObservedToken(RuntimeError):
        pass

    @contextmanager
    def reader(root):
        roots.append(root)
        yield native.LPVOID(199 + len(roots))

    def initialize(attributes, count, flags, size):
        if attributes is None:
            size._obj.value = 64
        return True

    def observe_token(attributes, flags, kind, value, size, previous, returned):
        assert kind == native.PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES
        caps = ctypes.cast(value, ctypes.POINTER(native.SECURITY_CAPABILITIES)).contents
        attached.extend((caps.Capabilities[index].Sid, caps.Capabilities[index].Attributes)
                        for index in range(caps.CapabilityCount))
        raise ObservedToken("stop before any native process is created")

    monkeypatch.setattr(native, "_appcontainer_sid", lambda moniker: native.LPVOID(100))
    monkeypatch.setattr(native, "_sid_string", lambda sid: "private-test-profile")
    monkeypatch.setattr(native, "_storage_root_for_sid", lambda sid: sandbox)
    grant = Mock()
    ensure = Mock()
    monkeypatch.setattr(native, "_grant", grant)
    monkeypatch.setattr(native, "_runtime_read_capability", reader)
    monkeypatch.setattr(native, "_ensure_runtime_access", ensure)
    monkeypatch.setattr(native, "_open_inheritable_file", lambda path: None)
    monkeypatch.setattr(native, "_open_inheritable_null", lambda: None)
    monkeypatch.setattr(native.kernel32, "InitializeProcThreadAttributeList", initialize)
    def windows_directory(buffer, size):
        buffer.value = str(system_shell.root)
        return len(buffer.value)
    monkeypatch.setattr(native.kernel32, "GetWindowsDirectoryW", windows_directory)
    monkeypatch.setenv("SystemRoot", str(system_shell.root))
    monkeypatch.setattr(native.kernel32, "UpdateProcThreadAttribute", observe_token)
    monkeypatch.setattr(native.kernel32, "DeleteProcThreadAttributeList", Mock())
    monkeypatch.setattr(native.advapi32, "FreeSid", Mock())
    command = [str(bundle.command), "--version"] if command_kind == "direct" else [str(system_shell.powershell), "-EncodedCommand", "fixture"]
    env = {"PATH": os.pathsep.join(map(str, (bundle.python.parent, bundle.command.parent)))}
    with pytest.raises(ObservedToken):
        native.run_appcontainer(command, cwd=sandbox, env=env, sandbox_root=sandbox,
                                limits=SimpleNamespace(timeout_seconds=10))
    expected = [bundle.git] if command_kind == "direct" else [bundle.python.parent, bundle.git]
    assert roots == expected
    assert [call.args[0] for call in ensure.call_args_list] == expected
    assert attached == [(200 + index, 4) for index in range(len(expected))]
    assert len(attached) == len(expected), "no network capability or duplicate reader SID may be attached"
    assert grant.call_args.args[:3] == (sandbox, "private-test-profile", "(OI)(CI)M")
