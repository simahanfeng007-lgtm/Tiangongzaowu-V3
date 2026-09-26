"""Linux OS containment for ordinary workspace Python and shell execution.

Only system runtime directories and a brokered workspace copy are mounted.
No host home, credentials, network, sockets or parent process namespace enter.
Missing bubblewrap/namespace support is a failure, never a portable fallback.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def bubblewrap_executable() -> str:
    from .sandbox_runtime import SandboxError
    path = Path('/usr/bin/bwrap')
    if not sys.platform.startswith('linux') or not path.is_file():
        raise SandboxError('sandbox_os_containment_unavailable:linux_bubblewrap_required')
    info = path.stat()
    # Inside a containing user namespace, host root is commonly represented
    # by overflow uid 65534. Bind trust to the system tree's owner, not a
    # numeric uid which changes with namespace mappings.
    system_owner = Path('/usr').stat().st_uid
    if (info.st_uid != system_owner or (info.st_uid == os.getuid() and os.getuid() != 0)
            or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise SandboxError('sandbox_os_containment_unavailable:untrusted_bubblewrap')
    return str(path)


def run_linux_sandbox(command, cwd, env, limits, workspace, *, workspace_aliases=(), cancel_check=None):
    from .sandbox_runtime import SandboxError, _rewrite_workspace_paths, _run_captured_process
    executable = bubblewrap_executable()
    if isinstance(command, str):
        raise SandboxError('sandbox_linux_requires_argv')
    argv = list(command)
    # Resolve launcher aliases before entering the empty mount namespace. A
    # user Python symlink may point at a normal /usr interpreter.
    if Path(argv[0]).is_absolute():
        argv[0] = str(Path(argv[0]).resolve(strict=True))
    argv = _rewrite_workspace_paths(argv, workspace, Path('/workspace'))
    relative_cwd = cwd.relative_to(workspace)
    args = [executable, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
            '--unshare-uts', '--disable-userns', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr']
    for name in ('bin', 'sbin', 'lib', 'lib64'):
        path = Path('/') / name
        if path.is_symlink():
            args += ['--symlink', os.readlink(path), str(path)]
        elif path.is_dir():
            args += ['--ro-bind', str(path), str(path)]
    args += ['--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev',
             '--size', str(limits.max_changed_bytes), '--tmpfs', '/tmp',
             '--bind', str(workspace), '/workspace', '--chdir', str(Path('/workspace') / relative_cwd),
             '--clearenv']
    # Scripts may contain absolute paths, not just argv paths. Alias ONLY the
    # brokered copy at the original workspace spelling in the empty namespace;
    # never mount the host workspace or its parent. The same atomic merge still
    # decides whether writes through either spelling reach the real workspace.
    for alias in sorted({str(Path(p).absolute()) for p in workspace_aliases}):
        if alias == '/workspace':
            continue
        if alias == '/' or any(alias == p or alias.startswith(p + '/') for p in
                               ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/dev', '/proc', '/run')):
            raise SandboxError('sandbox_workspace_alias_conflicts_with_runtime')
        args += ['--bind', str(workspace), alias]
    child_env = {k: v for k, v in env.items() if k in {'LANG', 'LC_ALL', 'PYTHONUTF8', 'PYTHONIOENCODING'}}
    child_env.update(PATH='/usr/bin:/bin', HOME='/tmp', TMPDIR='/tmp', TEMP='/tmp', TMP='/tmp',
                     PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    for key, value in sorted(child_env.items()):
        args += ['--setenv', key, value]
    # Apply irreversible limits INSIDE the user namespace, where the process
    # count is separate from unrelated host processes owned by the same user.
    bootstrap = (
        'import os,resource,sys; '
        f'resource.setrlimit(resource.RLIMIT_NPROC, ({limits.max_processes},{limits.max_processes})); '
        'resource.setrlimit(resource.RLIMIT_NOFILE, (256,256)); '
        'os.execvpe(sys.argv[1],sys.argv[1:],os.environ)'
    )
    with tempfile.TemporaryDirectory(prefix='launch-', dir=workspace.parent) as status_dir:
        # Distinguish a failed namespace/bootstrap from a contained command's
        # nonzero exit without trusting its stdout/stderr. This marker is never
        # merged into user outputs and carries no authority or credentials.
        bootstrap = bootstrap.replace('os.execvpe(',
            'open("/run/tiangong/started","w").close(); os.execvpe(')
        args += ['--bind', status_dir, '/run/tiangong',
                 '--remount-ro', '/',
                 '--', '/usr/bin/python3', '-I', '-c', bootstrap, *argv]
        code, stdout, stderr, _ = _run_captured_process(args, cwd, env, limits, cancel_check=cancel_check)
        if not (Path(status_dir) / 'started').exists():
            raise SandboxError('sandbox_linux_start_failed:' + stderr.decode('utf-8', errors='replace')[:600])
    return code, stdout, stderr, 'linux-bubblewrap'
