"""Per-execution sandbox for untrusted Python and shell actions.

The sandbox has no global scheduler and therefore does not throttle healthy
long-running work. Each invocation receives a private workspace copy, a
secret-free environment, process-tree lifetime controls, bounded output, and a
brokered atomic merge back into the real workspace. On Windows the process is
created inside an AppContainer with no capabilities (therefore no network) and
is attached to a kill-on-close Job Object. Linux strict execution uses bubblewrap
with private process/network namespaces and only system runtimes plus the copied
workspace mounted. Missing OS containment fails closed; the portable supervisor
alone is retained only for callers explicitly allowing development execution.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from typing import Any, Iterable, Mapping, Sequence

from .portable_text import decode_portable_bytes, subprocess_environment

class SandboxError(RuntimeError):
    pass


def _windows_long_path(path: Path) -> Path:
    """Use the Windows extended namespace without resolving links or junctions.

    CopyFile2 and cleanup must handle the private sandbox's deep source tree
    even when the host has LongPathsEnabled=0. No machine setting is changed.
    """
    if os.name != "nt":
        return path
    if not path.is_absolute():
        raise SandboxError("sandbox_long_path_requires_absolute_path")
    value = str(path)
    if value.startswith("\\\\?\\"):
        return path
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


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
    ".tiangong_emergency_audit",
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
            # Load the two system modules before hermetic auto-discovery can stall.
            "$tgSavedAutoload=$PSModuleAutoLoadingPreference;$PSModuleAutoLoadingPreference='None';try{Import-Module ($PSHOME+'\\Modules\\Microsoft.PowerShell.Management\\Microsoft.PowerShell.Management.psd1') -ErrorAction Stop;Import-Module ($PSHOME+'\\Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop;}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 125}finally{$PSModuleAutoLoadingPreference=$tgSavedAutoload};"
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
            "try{"
            "Import-Module ($PSHOME+'\\Modules\\Microsoft.PowerShell.Management\\Microsoft.PowerShell.Management.psd1') -ErrorAction Stop;"
            "[Environment]::CurrentDirectory=$cwd;"
            "$null=New-PSDrive -Name TiangongWorkspace -PSProvider FileSystem -Root $cwd -Scope Global -ErrorAction Stop;"
            "Set-Location -LiteralPath 'TiangongWorkspace:\\' -ErrorAction Stop;"
            "}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 125};"
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
        if any(part.casefold() in _SKIP_NAMES for part in path.relative_to(root).parts):
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
        if any(part.casefold() in _SKIP_NAMES for part in rel.parts):
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
        if any(part.casefold() in _SKIP_NAMES for part in rel_path.parts):
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
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, destination)
        from .workspace_commit import sync_directory
        sync_directory(destination.parent)
    finally:
        temp.unlink(missing_ok=True)


def _merge_changes(
    sandbox_workspace: Path,
    real_workspace: Path,
    before: Mapping[str, tuple[int, str]],
    *,
    max_changed_bytes: int,
    trash_root: Path,
    allow_deletions: bool = True,
    transaction_root: Path | None = None,
    operation: str = "", input_digest: str = "", receipt: dict | None = None,
    cancel_check=None,
) -> dict[str, Any]:
    _tree_size(sandbox_workspace, max_changed_bytes + sum(v[0] for v in before.values()))
    after = _snapshot(sandbox_workspace)
    changed = sorted(key for key, value in after.items() if before.get(key) != value)
    deleted = sorted(set(before).difference(after))
    if deleted and not allow_deletions:
        raise SandboxError("sandbox_deletion_forbidden")
    changed_bytes = sum(after[key][0] for key in changed)
    if changed_bytes > max_changed_bytes:
        raise SandboxError("sandbox_changed_output_size_limit")
    # Never overwrite a host edit made while the private process was running.
    # Validate all destinations before committing any of the private outputs.
    for rel in (*changed, *deleted):
        destination = real_workspace / Path(rel)
        for parent in (destination, *destination.parents):
            if _is_link_or_reparse(parent):
                raise SandboxError(f"sandbox_destination_link_forbidden:{rel}")
            if parent == real_workspace:
                break
        observed = ((destination.stat().st_size, _file_digest(destination))
                    if destination.is_file() else None)
        if observed != before.get(rel) or (destination.exists() and not destination.is_file()):
            raise SandboxError(f"sandbox_destination_changed:{rel}")
    from .workspace_commit import commit
    return commit(source=sandbox_workspace, workspace=real_workspace,
                  before=dict(before), after=after, changed=changed, deleted=deleted,
                  root=transaction_root or (trash_root / "transactions"),
                  operation=operation or uuid.uuid4().hex, input_digest=input_digest,
                  receipt={**(receipt or {}), "changed_files": changed, "deleted_files": deleted,
                           "changed_bytes": changed_bytes}, cancel_check=cancel_check)


def _rewrite_workspace_paths(command: Sequence[str] | str, real: Path, sandbox: Path) -> list[str] | str:
    real_resolved = real.resolve(strict=False)
    sandbox_text = str(sandbox.expanduser().absolute())
    # A Windows 8.3 spelling can survive inside shell text after Path.resolve
    # expands it. Keep the caller spelling and the canonical spelling; both
    # refer to the same frozen workspace, not to additional allowed roots.
    spellings = {str(real.expanduser().absolute()), str(real_resolved)}
    if os.name == "nt":
        spellings.update(value.replace("\\", "/") for value in tuple(spellings))
    flags = re.IGNORECASE if os.name == "nt" else 0
    # One substitution pass prevents a private target that contains a source
    # spelling from being rewritten again by a later alias replacement.
    alternatives = "|".join(re.escape(value) for value in sorted(spellings, key=lambda value: (-len(value), value)))
    embedded_root = re.compile(
        "(?:" + alternatives + r")(?=$|[\\/\"'\s])",
        flags,
    )
    def rewrite_item(item: object) -> str:
        value = str(item)
        exact_path_rewritten = False
        try:
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                rel = candidate.resolve(strict=False).relative_to(real_resolved)
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


def _prepare_windows_cmd_initial_directory(
    command: Sequence[str] | str,
    *,
    cwd: Path | str,
    workspace: Path | str,
    comspec: Path | str,
) -> Sequence[str] | str:
    """Translate only an initial literal CMD cd into the bound private tree.

    CMD's absolute-name canonicalization can query inaccessible ancestors even
    when the target directory is accessible to AppContainer. For the FIRST cd,
    the native process cwd is known: relpath(target, cwd) selects the same private
    directory without that ancestor walk. Later commands, flags, expansion,
    redirection and exit behavior are not interpreted or rewritten here.

    This is not a general shell parser. Ambiguous forms and non-CMD executables
    are left unchanged, as are targets outside the existing brokered workspace.
    The caller invokes this only after copying/link checks and path rebinding,
    immediately before launching with that exact private cwd.
    """
    import ntpath

    if not isinstance(command, str) or any(ch in command for ch in '\r\n\0'):
        return command
    launch = re.fullmatch(
        r'(?P<exe>"[^"\r\n]+"|[^"\s]+)(?P<gap>[ \t]+)'
        r'(?P<flags>(?:/[dDsS][ \t]+){0,2}/[cC][ \t]+)(?P<body>.+)',
        command,
    )
    if launch is None:
        return command
    executable = launch['exe'].strip('"')
    def norm(value: Path | str) -> str:
        return ntpath.normcase(ntpath.normpath(str(value)))

    if (re.match(r"^[A-Za-z]:[\\/]", str(comspec)) is None
            or norm(executable) != norm(comspec)):
        return command
    switches = launch['flags'].lower().split()
    # Without /d, registry AutoRun commands can change cwd before the first cd.
    if '/d' not in switches or len(switches) != len(set(switches)):
        return command
    body = launch['body']
    wrapped = body.startswith('"') and body.endswith('"')
    inner = body[1:-1] if wrapped else body
    initial = re.match(
        r'(?P<prefix>[ \t]*@?(?:cd|chdir)[ \t]+(?:/d[ \t]+)?)'
        r'"(?P<target>[^"\r\n]+)"(?P<suffix>.*)\Z', inner, re.IGNORECASE,
    )
    if initial is None:
        return command
    suffix = initial['suffix'].lstrip(' \t')
    if suffix and not (suffix.startswith('&&') or suffix.startswith('||')
                       or suffix.startswith('&')):
        return command
    target = initial['target']
    if any(ch in target for ch in '%!^&|<>*?'):
        return command
    values = (str(cwd), str(workspace), target)
    if any(re.match(r'^[A-Za-z]:[\\/]', value) is None for value in values):
        return command
    try:
        private_root = norm(workspace)
        if any(ntpath.commonpath((private_root, norm(value))) != private_root
               for value in (cwd, target)):
            return command
        relative = ntpath.relpath(target, str(cwd))
    except ValueError:
        return command
    rewritten = initial['prefix'] + '"' + relative + '"' + initial['suffix']
    if wrapped:
        rewritten = '"' + rewritten + '"'
    return command[:launch.start('body')] + rewritten


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
    *, cancel_check=None,
) -> tuple[int, bytes, bytes, str]:
    if cancel_check is not None and cancel_check():
        raise SandboxError("sandbox_cancelled")
    # File-backed capture avoids communicate() allocating unbounded RAM.
    with tempfile.TemporaryFile(dir=env.get("TEMP")) as out, tempfile.TemporaryFile(dir=env.get("TEMP")) as err:
        process = subprocess.Popen(
            command if isinstance(command, str) else list(command), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, shell=False,
            preexec_fn=_posix_preexec(limits) if os.name != "nt" else None,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + max(1, limits.timeout_seconds)
        try:
            while process.poll() is None:
                if cancel_check is not None and cancel_check():
                    raise SandboxError("sandbox_cancelled")
                if time.monotonic() >= deadline:
                    raise SandboxError("sandbox_timeout")
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limits.max_output_bytes:
                    raise SandboxError("sandbox_process_output_limit")
                time.sleep(0.025)
        finally:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            else:
                import signal
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(limits.max_output_bytes + 1), err.read(limits.max_output_bytes + 1)
        if len(stdout) + len(stderr) > limits.max_output_bytes:
            raise SandboxError("sandbox_process_output_limit")
        return process.returncode, stdout, stderr, "portable-resource-sandbox"


# Windows AppContainer launcher is isolated here so importing on other platforms
# never evaluates Windows-only structures.
def _run_windows_appcontainer(
    command: Sequence[str] | str, cwd: Path, env: Mapping[str, str], limits: SandboxLimits, sandbox_root: Path,
    *, require_os_containment: bool = False,
    moniker: str = "TiangongV3.ToolSandbox", cancel_check=None,
) -> tuple[int, bytes, bytes, str]:
    if os.name != "nt":
        if require_os_containment:
            raise SandboxError("sandbox_os_containment_unavailable")
        return _run_portable(command, cwd, env, limits, cancel_check=cancel_check)
    compat = not require_os_containment and os.environ.get("TIANGONG_SANDBOX_COMPAT", "0").strip().lower() in {"1", "true", "yes", "on"}
    try:
        from .windows_appcontainer import run_appcontainer
        result = run_appcontainer(command, cwd=cwd, env=env, limits=limits, sandbox_root=sandbox_root,
                                  moniker=moniker, cancel_check=cancel_check)
    except Exception as exc:
        # Fail closed by default. Compatibility mode is explicit and still
        # retains workspace-copy, secret-free environment and process-tree kill.
        if getattr(exc, "execution_started", False):
            raise SandboxError(str(exc)) from exc
        if not compat:
            raise SandboxError(f"windows_appcontainer_unavailable:{type(exc).__name__}:{exc}") from exc
        code, stdout, stderr, _ = _run_portable(command, cwd, env, limits, cancel_check=cancel_check)
        return code, stdout, stderr, "compat-workspace-job-sandbox"
    if result[0] == _STATUS_DLL_INIT_FAILED:
        # The AppContainer token was created, but Windows terminated the child
        # during loader initialization; user code never ran.  Treat this as an
        # unavailable containment backend, not as a command failure.
        if not compat:
            raise SandboxError("windows_appcontainer_unavailable:STATUS_DLL_INIT_FAILED")
        code, stdout, stderr, _ = _run_portable(command, cwd, env, limits, cancel_check=cancel_check)
        return code, stdout, stderr, "compat-workspace-job-sandbox"
    return result


class SandboxRunner:
    def __init__(self, workspace: Path, state_root: Path, trash_root: Path, limits: SandboxLimits | None = None):
        self._workspace_input = workspace.expanduser().absolute()
        self.workspace = self._workspace_input.resolve()
        self.state_root = state_root.expanduser().resolve()
        self.trash_root = trash_root.expanduser().resolve()
        self.limits = limits or SandboxLimits()
        self.state_root.mkdir(parents=True, exist_ok=True)

    def run(self, command, **kwargs):
        from .omni_body_tool import _workspace_lock_for, _workspace_mutation_guard
        with _workspace_mutation_guard(self.workspace, _workspace_lock_for(self.workspace)):
            return self._run_locked(command, **kwargs)

    def _run_locked(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_seconds: int | None = None,
        op_id: str = "",
        require_os_containment: bool = False,
        cancel_check=None,
        allow_deletions: bool = True,
        expected_workspace_files: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        # Source Candidate builds must never execute through a portable or
        # explicit compatibility fallback. Check BEFORE preparation/launch,
        # not after untrusted code has already run without OS containment.
        if type(require_os_containment) is not bool:
            raise SandboxError("sandbox_containment_requirement_invalid")
        if type(allow_deletions) is not bool:
            raise SandboxError("sandbox_commit_policy_invalid")
        if require_os_containment and os.name != "nt":
            from .linux_sandbox import bubblewrap_executable
            bubblewrap_executable()
        if cancel_check is not None and (not callable(cancel_check) or cancel_check()):
            raise SandboxError("sandbox_cancelled")
        if not command:
            raise SandboxError("sandbox_command_empty")
        # Retaining an input alias must not let a changed symlink/junction
        # redirect an existing runner to another workspace.
        if self._workspace_input.resolve(strict=False) != self.workspace:
            raise SandboxError("sandbox_workspace_identity_changed")
        raw_run_id = str(op_id or f"run_{time.time_ns()}")
        # Deep Windows workspaces can exceed MAX_PATH before the command even
        # starts when the full operation id is used as another directory
        # component. A content-addressed short name preserves uniqueness and
        # audit correlation without consuming roughly 100 path characters.
        run_id = "r_" + hashlib.sha256(raw_run_id.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
        run_root = self.state_root / (run_id or f"run_{time.time_ns()}")
        from .workspace_commit import recover, replay
        transactions = self.state_root / "commits"
        recover(transactions, self.workspace)
        input_digest = hashlib.sha256(json.dumps({"command": command,
            "cwd": str(cwd or self.workspace), "allow_deletions": allow_deletions,
            "require_os_containment": require_os_containment,
            "expected_workspace_files": dict(expected_workspace_files or {})},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        recovered = replay(transactions, self.workspace, raw_run_id, input_digest)
        if recovered is not None:
            return recovered
        sandbox_workspace = run_root / "workspace"
        temp_dir = run_root / "temp"
        moniker = "TG3.Run." + uuid.uuid4().hex[:20]
        cleanup_profile = None
        if os.name == "nt":
            try:
                from .windows_appcontainer import appcontainer_storage_root, delete_appcontainer_profile

                container_storage = appcontainer_storage_root(moniker)
                cleanup_profile = lambda: delete_appcontainer_profile(moniker)
                container_runs = container_storage / "runs"
                container_runs.mkdir(parents=True, exist_ok=True)
                run_root = container_runs / (run_id or f"run_{time.time_ns()}")
                # Windows rewrites a supplied LOCALAPPDATA base to
                #   <base>/Packages/<profile>/AC
                # and TEMP to that directory's Temp child for an AppContainer
                # process. Put the brokered workspace inside that effective
                # TEMP. A sibling of it may carry the same DACL yet remain
                # unreachable through the AppContainer namespace.
                temp_dir = run_root / "e"
                effective_temp = (
                    temp_dir
                    / "Packages"
                    / container_storage.parent.name
                    / "AC"
                    / "Temp"
                )
                sandbox_workspace = effective_temp / "workspace"
            except Exception as exc:
                compat = not require_os_containment and os.environ.get("TIANGONG_SANDBOX_COMPAT", "0").strip().lower() in {
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
            shutil.rmtree(_windows_long_path(run_root), ignore_errors=True)
        shell_executable = (str(command[0]) if not isinstance(command, str) and command else "")
        native_shell = shell_executable in {WINDOWS_UTF8_SHELL_MARKER, WINDOWS_POWERSHELL_SHELL_MARKER} or Path(shell_executable).name.casefold() in {"cmd.exe", "powershell.exe", "pwsh.exe"}
        if require_os_containment and not native_shell:
            # Keep every path handed to the strict source-build interpreter
            # in the extended namespace too, including __file__/sys.path.
            # CMD rejects that namespace as an UNC working directory. Its
            # short, native private path has identical AppContainer authority.
            sandbox_workspace = _windows_long_path(sandbox_workspace)
        # Preparation failures (workspace copy, snapshot, shell rewrite) must
        # not leak run_root: the main try/finally below only covers execution.
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            _copy_workspace(self.workspace, sandbox_workspace, self.limits.max_workspace_bytes)
            before = _snapshot(sandbox_workspace)
            for relative, digest in (expected_workspace_files or {}).items():
                if relative not in before or before[relative][1] != digest:
                    raise SandboxError("sandbox_bound_input_changed")
            real_cwd = (cwd or self.workspace).expanduser().resolve(strict=False)
            sandbox_cwd = sandbox_workspace / _safe_rel(self.workspace, real_cwd)
            sandbox_cwd.mkdir(parents=True, exist_ok=True)
            rewritten = _rewrite_workspace_paths(command, self._workspace_input, sandbox_workspace)
            if os.name == "nt":
                system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
                if system_root:
                    rewritten = _prepare_windows_cmd_initial_directory(
                        rewritten, cwd=sandbox_cwd, workspace=sandbox_workspace,
                        comspec=Path(system_root) / "System32" / "cmd.exe",
                    )
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
        except BaseException:
            if os.environ.get("TIANGONG_KEEP_SANDBOX", "0").strip().lower() not in {"1", "true", "yes", "on"}:
                shutil.rmtree(_windows_long_path(run_root), ignore_errors=True)
            if cleanup_profile is not None:
                cleanup_profile()
            raise
        started = time.monotonic()
        try:
            if os.name == "nt":
                code, stdout, stderr, containment = _run_windows_appcontainer(
                    rewritten, sandbox_cwd, env, limits, run_root,
                    require_os_containment=require_os_containment,
                    moniker=moniker, cancel_check=cancel_check,
                )
            elif require_os_containment:
                from .linux_sandbox import run_linux_sandbox
                code, stdout, stderr, containment = run_linux_sandbox(
                    rewritten, sandbox_cwd, env, limits, sandbox_workspace,
                    workspace_aliases=(self.workspace, self._workspace_input), cancel_check=cancel_check)
            else:
                code, stdout, stderr, containment = _run_portable(rewritten, sandbox_cwd, env, limits, cancel_check=cancel_check)
            if len(stdout) + len(stderr) > limits.max_output_bytes:
                raise SandboxError("sandbox_process_output_limit")
            if cancel_check is not None and cancel_check():
                raise SandboxError("sandbox_cancelled")
            decoded_stdout = decode_portable_bytes(
                stdout, source="sandbox stdout", allow_legacy_windows=True
            )
            decoded_stderr = decode_portable_bytes(
                stderr, source="sandbox stderr", allow_legacy_windows=True
            )
            receipt = {
                "returncode": int(code),
                "stdout": decoded_stdout.text,
                "stderr": decoded_stderr.text,
                "stdout_encoding": decoded_stdout.encoding,
                "stderr_encoding": decoded_stderr.encoding,
                "legacy_output_encoding": bool(
                    decoded_stdout.legacy_fallback or decoded_stderr.legacy_fallback
                ),
                "ok": int(code) == 0,
                "receipt_role": "execution",
                "execution_state": "completed",
                "commit_state": "committed" if code == 0 else "discarded",
                "committed_workspace": str(self.workspace),
                "outputs_truncated": False,
                "containment": containment,
                "network": "denied" if containment in {"windows-appcontainer", "linux-bubblewrap"} else "not_os_enforced",
                "sandbox_root": str(run_root),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            if code == 0:
                return _merge_changes(
                    sandbox_workspace, self.workspace, before,
                    max_changed_bytes=limits.max_changed_bytes, trash_root=self.trash_root,
                    allow_deletions=allow_deletions, transaction_root=transactions,
                    operation=raw_run_id, input_digest=input_digest, receipt=receipt,
                    cancel_check=cancel_check)
            return {**receipt, "changed_files": [], "deleted_files": [], "changed_bytes": 0}
        finally:
            if os.environ.get("TIANGONG_KEEP_SANDBOX", "0").strip().lower() not in {"1", "true", "yes", "on"}:
                shutil.rmtree(_windows_long_path(run_root), ignore_errors=True)
                if cleanup_profile is not None:
                    cleanup_profile()
