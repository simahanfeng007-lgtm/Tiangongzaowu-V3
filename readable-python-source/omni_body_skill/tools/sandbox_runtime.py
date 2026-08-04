"""Per-execution sandbox for untrusted Python and shell actions.

The sandbox has no global scheduler and therefore does not throttle healthy
long-running work. Each invocation receives a private workspace copy, a
secret-free environment, process-tree lifetime controls, bounded output, and a
brokered atomic merge back into the real workspace. On Windows the process is
created inside an AppContainer with no capabilities (therefore no network) and
is attached to a kill-on-close Job Object. Other platforms retain the same
workspace broker and resource limits for deterministic tests/development.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

from .portable_text import decode_portable_bytes, subprocess_environment

class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    max_workspace_bytes: int = 2 * 1024 * 1024 * 1024
    max_changed_bytes: int = 512 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024
    max_memory_bytes: int = 2 * 1024 * 1024 * 1024
    max_processes: int = 32


_SECRET_MARKERS = (
    "API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "COOKIE",
    "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY", "SESSION_KEY",
)
_ENV_ALLOW = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
    "LANG", "LC_ALL", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}
_SKIP_NAMES = {
    ".omni_audit",
    ".omni_backups",
    ".omni_trash",
    ".omni_workspace.lock",
    ".tiangong_sandboxes",
}
_STATUS_DLL_INIT_FAILED = 0xC0000142
WINDOWS_UTF8_SHELL_MARKER = "__tiangong_windows_utf8_cmd_v1__"
WINDOWS_POWERSHELL_SHELL_MARKER = "__tiangong_windows_powershell_v1__"


def _prepare_windows_utf8_shell_command(
    command: Sequence[str] | str,
    *,
    cwd: Path | None = None,
) -> list[str] | str:
    """Wrap marked cmd text after workspace-path rewriting.

    Encoding the inner command only at this stage preserves the sandbox's
    real-workspace-to-private-workspace rewrite. The outer argv is ASCII-safe,
    and PowerShell sets UTF-8 before starting cmd, so redirection does not
    inherit the host's legacy console code page.
    """
    if isinstance(command, str) or len(command) != 2:
        return command if isinstance(command, str) else list(command)
    marker = str(command[0])
    if marker not in {
        WINDOWS_UTF8_SHELL_MARKER,
        WINDOWS_POWERSHELL_SHELL_MARKER,
    }:
        return list(command)
    command_text = str(command[1])
    cwd_payload = (
        base64.b64encode(str(cwd).encode("utf-16-le")).decode("ascii")
        if cwd is not None
        else ""
    )
    powershell = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )
    if not powershell:
        raise SandboxError("windows_utf8_shell_requires_powershell")
    if marker == WINDOWS_POWERSHELL_SHELL_MARKER:
        command_payload = base64.b64encode(
            command_text.encode("utf-16-le")
        ).decode("ascii")
        script = (
            "$utf8=[System.Text.UTF8Encoding]::new($false);"
            "[Console]::InputEncoding=$utf8;"
            "[Console]::OutputEncoding=$utf8;"
            "$OutputEncoding=$utf8;"
            "$ProgressPreference='SilentlyContinue';"
            f"$cwd=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{cwd_payload}'));"
            f"$command=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{command_payload}'));"
            "try{"
            "[Environment]::CurrentDirectory=$cwd;"
            "$null=New-PSDrive -Name TiangongWorkspace -PSProvider FileSystem -Root $cwd -Scope Global -ErrorAction Stop;"
            "Set-Location -LiteralPath 'TiangongWorkspace:\\' -ErrorAction Stop;"
            "}catch{Write-Error $_;exit 125};"
            "$global:LASTEXITCODE=$null;"
            "& ([ScriptBlock]::Create($command));"
            "$success=$?;"
            "$code=$LASTEXITCODE;"
            "if($null -eq $code){$code=if($success){0}else{1}};"
            "exit $code"
        )
        encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_script,
        ]
    command_payload = base64.b64encode(command_text.encode("utf-16-le")).decode("ascii")
    script = (
        "$utf8=[System.Text.UTF8Encoding]::new($false);"
        "[Console]::InputEncoding=$utf8;"
        "[Console]::OutputEncoding=$utf8;"
        "$OutputEncoding=$utf8;"
        f"$command=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{command_payload}'));"
        + (
            f"$cwd=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{cwd_payload}'));"
            "$command='cd /d \"'+$cwd+'\" && '+$command;"
            if cwd_payload
            else ""
        )
        +
        "$comspec=$env:COMSPEC;"
        "if(-not $comspec){$comspec=Join-Path $env:SystemRoot 'System32\\cmd.exe'};"
        "& $comspec /d /s /c $command;"
        "$code=$LASTEXITCODE;"
        "if($null -eq $code){$code=0};"
        "exit $code"
    )
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded_script,
    ]


def _is_secret_name(name: str) -> bool:
    upper = str(name).upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def sanitized_environment(base: Mapping[str, str] | None, temp_dir: Path) -> dict[str, str]:
    source = dict(base or os.environ)
    env: dict[str, str] = {}
    for key, value in source.items():
        if key.upper() in _ENV_ALLOW and not _is_secret_name(key):
            env[str(key)] = str(value)
    env.update({
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        # CreateProcess with an AppContainer security capability requires this
        # variable to exist.  Bind it to the private invocation directory,
        # never to the user's real profile.
        "LOCALAPPDATA": str(temp_dir),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "TIANGONG_SANDBOX": "1",
        "TIANGONG_SANDBOX_NETWORK": "denied",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    })
    return env


def _safe_rel(root: Path, path: Path) -> Path:
    try:
        rel = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise SandboxError("sandbox_cwd_outside_workspace") from exc
    if any(part in {"..", ""} for part in rel.parts):
        raise SandboxError("sandbox_relative_path_invalid")
    return rel


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attrs = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attrs & reparse)
    except FileNotFoundError:
        return False


def _tree_size(root: Path, limit: int) -> int:
    total = 0
    for path in root.rglob("*"):
        if any(part in _SKIP_NAMES for part in path.relative_to(root).parts):
            continue
        if _is_link_or_reparse(path):
            raise SandboxError(f"sandbox_link_forbidden:{path.relative_to(root)}")
        if path.is_file():
            total += path.stat().st_size
            if total > limit:
                raise SandboxError("sandbox_workspace_size_limit")
    return total


def _copy_workspace(source: Path, destination: Path, limit: int) -> None:
    _tree_size(source, limit)
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in _SKIP_NAMES for part in rel.parts):
            continue
        if _is_link_or_reparse(path):
            raise SandboxError(f"sandbox_link_forbidden:{rel}")
        target = destination / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    rows: dict[str, tuple[int, str]] = {}
    for path in root.rglob("*"):
        rel_path = path.relative_to(root)
        if any(part in _SKIP_NAMES for part in rel_path.parts):
            continue
        if path.is_file() and not _is_link_or_reparse(path):
            rel = rel_path.as_posix()
            rows[rel] = (path.stat().st_size, _file_digest(path))
    return rows


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".sandbox", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _merge_changes(
    sandbox_workspace: Path,
    real_workspace: Path,
    before: Mapping[str, tuple[int, str]],
    *,
    max_changed_bytes: int,
    trash_root: Path,
) -> dict[str, Any]:
    after = _snapshot(sandbox_workspace)
    changed = sorted(key for key, value in after.items() if before.get(key) != value)
    deleted = sorted(set(before).difference(after))
    changed_bytes = sum(after[key][0] for key in changed)
    if changed_bytes > max_changed_bytes:
        raise SandboxError("sandbox_changed_output_size_limit")
    for rel in changed:
        source = sandbox_workspace / Path(rel)
        destination = real_workspace / Path(rel)
        if _is_link_or_reparse(source):
            raise SandboxError(f"sandbox_output_link_forbidden:{rel}")
        _atomic_copy(source, destination)
    timestamp = str(int(time.time() * 1000))
    for rel in deleted:
        destination = real_workspace / Path(rel)
        if not destination.exists() or _is_link_or_reparse(destination):
            continue
        trash = trash_root / timestamp / Path(rel)
        trash.parent.mkdir(parents=True, exist_ok=True)
        os.replace(destination, trash)
    return {"changed_files": changed, "deleted_files": deleted, "changed_bytes": changed_bytes}


def _rewrite_workspace_paths(command: Sequence[str] | str, real: Path, sandbox: Path) -> list[str] | str:
    real_text = str(real.resolve(strict=False))
    sandbox_text = str(sandbox.resolve(strict=False))
    flags = re.IGNORECASE if os.name == "nt" else 0
    embedded_root = re.compile(
        re.escape(real_text) + r"(?=$|[\\/\"'\s])",
        flags,
    )
    def rewrite_item(item: object) -> str:
        value = str(item)
        exact_path_rewritten = False
        try:
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                rel = candidate.resolve(strict=False).relative_to(real.resolve(strict=False))
                value = str(sandbox / rel)
                exact_path_rewritten = True
        except (OSError, ValueError):
            pass
        if not exact_path_rewritten:
            # Shell commands are passed as one argv item (for example
            # ``cmd.exe /c 'cd C:\\workspace\\project && ...'``).  Rewriting
            # only argv items that are paths leaves those embedded paths
            # pointing at the real workspace and bypasses the private copy.
            value = embedded_root.sub(lambda _match: sandbox_text, value)
        return value

    if isinstance(command, str):
        return rewrite_item(command)
    return [rewrite_item(item) for item in command]


def _posix_preexec(limits: SandboxLimits):
    def apply() -> None:
        import resource
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        memory = max(128 * 1024 * 1024, int(limits.max_memory_bytes))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        except (ValueError, OSError):
            pass
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_changed_bytes, limits.max_changed_bytes))
    return apply


def _run_portable(
    command: Sequence[str] | str, cwd: Path, env: Mapping[str, str], limits: SandboxLimits,
) -> tuple[int, bytes, bytes, str]:
    process = subprocess.Popen(
        command if isinstance(command, str) else list(command), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        start_new_session=False,
        preexec_fn=_posix_preexec(limits) if os.name != "nt" else None,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = process.communicate(timeout=limits.timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
        stdout, stderr = process.communicate()
        raise SandboxError("sandbox_timeout")
    return process.returncode, stdout, stderr, "portable-resource-sandbox"


# Windows AppContainer launcher is isolated here so importing on other platforms
# never evaluates Windows-only structures.
def _run_windows_appcontainer(
    command: Sequence[str] | str, cwd: Path, env: Mapping[str, str], limits: SandboxLimits, sandbox_root: Path,
) -> tuple[int, bytes, bytes, str]:
    if os.name != "nt":
        return _run_portable(command, cwd, env, limits)
    compat = os.environ.get("TIANGONG_SANDBOX_COMPAT", "0").strip().lower() in {"1", "true", "yes", "on"}
    try:
        from .windows_appcontainer import run_appcontainer
        result = run_appcontainer(command, cwd=cwd, env=env, limits=limits, sandbox_root=sandbox_root)
    except Exception as exc:
        # Fail closed by default. Compatibility mode is explicit and still
        # retains workspace-copy, secret-free environment and process-tree kill.
        if not compat:
            raise SandboxError(f"windows_appcontainer_unavailable:{type(exc).__name__}:{exc}") from exc
        code, stdout, stderr, _ = _run_portable(command, cwd, env, limits)
        return code, stdout, stderr, "compat-workspace-job-sandbox"
    if result[0] == _STATUS_DLL_INIT_FAILED:
        # The AppContainer token was created, but Windows terminated the child
        # during loader initialization; user code never ran.  Treat this as an
        # unavailable containment backend, not as a command failure.
        if not compat:
            raise SandboxError("windows_appcontainer_unavailable:STATUS_DLL_INIT_FAILED")
        code, stdout, stderr, _ = _run_portable(command, cwd, env, limits)
        return code, stdout, stderr, "compat-workspace-job-sandbox"
    return result


class SandboxRunner:
    def __init__(self, workspace: Path, state_root: Path, trash_root: Path, limits: SandboxLimits | None = None):
        self.workspace = workspace.expanduser().resolve()
        self.state_root = state_root.expanduser().resolve()
        self.trash_root = trash_root.expanduser().resolve()
        self.limits = limits or SandboxLimits()
        self.state_root.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        op_id: str = "",
    ) -> dict[str, Any]:
        if not command:
            raise SandboxError("sandbox_command_empty")
        raw_run_id = str(op_id or f"run_{time.time_ns()}")
        # Deep Windows workspaces can exceed MAX_PATH before the command even
        # starts when the full operation id is used as another directory
        # component. A content-addressed short name preserves uniqueness and
        # audit correlation without consuming roughly 100 path characters.
        run_id = "r_" + hashlib.sha256(raw_run_id.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
        run_root = self.state_root / (run_id or f"run_{time.time_ns()}")
        sandbox_workspace = run_root / "workspace"
        temp_dir = run_root / "temp"
        if os.name == "nt":
            try:
                from .windows_appcontainer import appcontainer_storage_root

                container_storage = appcontainer_storage_root()
                container_runs = container_storage / "TiangongToolSandboxRuns"
                container_runs.mkdir(parents=True, exist_ok=True)
                run_root = container_runs / (run_id or f"run_{time.time_ns()}")
                # Windows rewrites a supplied LOCALAPPDATA base to
                #   <base>/Packages/<profile>/AC
                # and TEMP to that directory's Temp child for an AppContainer
                # process. Put the brokered workspace inside that effective
                # TEMP. A sibling of it may carry the same DACL yet remain
                # unreachable through the AppContainer namespace.
                temp_dir = run_root / "environment"
                effective_temp = (
                    temp_dir
                    / "Packages"
                    / container_storage.parent.name
                    / "AC"
                    / "Temp"
                )
                sandbox_workspace = effective_temp / "workspace"
            except Exception as exc:
                compat = os.environ.get("TIANGONG_SANDBOX_COMPAT", "0").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                if not compat:
                    raise SandboxError(
                        f"windows_appcontainer_storage_unavailable:{type(exc).__name__}:{exc}"
                    ) from exc
        if run_root.exists():
            shutil.rmtree(run_root, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        _copy_workspace(self.workspace, sandbox_workspace, self.limits.max_workspace_bytes)
        before = _snapshot(sandbox_workspace)
        real_cwd = (cwd or self.workspace).expanduser().resolve(strict=False)
        sandbox_cwd = sandbox_workspace / _safe_rel(self.workspace, real_cwd)
        sandbox_cwd.mkdir(parents=True, exist_ok=True)
        rewritten = _rewrite_workspace_paths(command, self.workspace, sandbox_workspace)
        if os.name == "nt":
            rewritten = _prepare_windows_utf8_shell_command(rewritten, cwd=sandbox_cwd)
        limits = SandboxLimits(
            timeout_seconds=max(1, int(timeout_seconds or self.limits.timeout_seconds)),
            max_workspace_bytes=self.limits.max_workspace_bytes,
            max_changed_bytes=self.limits.max_changed_bytes,
            max_output_bytes=self.limits.max_output_bytes,
            max_memory_bytes=self.limits.max_memory_bytes,
            max_processes=self.limits.max_processes,
        )
        env = subprocess_environment(sanitized_environment(os.environ, temp_dir))
        started = time.monotonic()
        try:
            if os.name == "nt":
                code, stdout, stderr, containment = _run_windows_appcontainer(
                    rewritten, sandbox_cwd, env, limits, run_root,
                )
            else:
                code, stdout, stderr, containment = _run_portable(rewritten, sandbox_cwd, env, limits)
            if len(stdout) > limits.max_output_bytes or len(stderr) > limits.max_output_bytes:
                raise SandboxError("sandbox_process_output_limit")
            merge = _merge_changes(
                sandbox_workspace, self.workspace, before,
                max_changed_bytes=limits.max_changed_bytes, trash_root=self.trash_root,
            )
            decoded_stdout = decode_portable_bytes(
                stdout, source="sandbox stdout", allow_legacy_windows=True
            )
            decoded_stderr = decode_portable_bytes(
                stderr, source="sandbox stderr", allow_legacy_windows=True
            )
            return {
                "returncode": int(code),
                "stdout": decoded_stdout.text[-8000:],
                "stderr": decoded_stderr.text[-8000:],
                "stdout_encoding": decoded_stdout.encoding,
                "stderr_encoding": decoded_stderr.encoding,
                "legacy_output_encoding": bool(
                    decoded_stdout.legacy_fallback or decoded_stderr.legacy_fallback
                ),
                "ok": int(code) == 0,
                "containment": containment,
                "network": "denied" if containment == "windows-appcontainer" else "not_os_enforced",
                "sandbox_root": str(run_root),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                **merge,
            }
        finally:
            if os.environ.get("TIANGONG_KEEP_SANDBOX", "0").strip().lower() not in {"1", "true", "yes", "on"}:
                shutil.rmtree(run_root, ignore_errors=True)
