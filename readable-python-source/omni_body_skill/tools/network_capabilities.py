"""Governed network capabilities for the Tiangong Omni Body.

Network authority belongs to typed capabilities, never to generic shell/python
execution.  This module deliberately exposes only narrow, auditable primitives:

* bounded HTTP response reads that fail closed on truncation;
* public GitHub HTTPS clone into a new local directory.

It does not read Git credentials, does not enable submodules/LFS smudging, and
does not accept arbitrary git flags or arbitrary network hosts.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
from urllib.parse import urlsplit


class NetworkCapabilityError(RuntimeError):
    pass


_GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ENV_ALLOW = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
    "LANG", "LC_ALL", "PYTHONUTF8", "PYTHONIOENCODING",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}


def read_bounded_http_body(response: Any, max_bytes: int) -> bytes:
    """Read a response completely or fail; never return a silent prefix."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise NetworkCapabilityError("network_download_limit_invalid")
    headers = getattr(response, "headers", None)
    declared = headers.get("Content-Length") if headers is not None else None
    if declared not in (None, ""):
        try:
            declared_size = int(str(declared).strip())
        except (TypeError, ValueError):
            declared_size = -1
        if declared_size > max_bytes:
            raise NetworkCapabilityError(
                f"network_download_too_large:content_length={declared_size}:limit={max_bytes}"
            )
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise NetworkCapabilityError(
            f"network_download_too_large:stream_exceeded_limit={max_bytes}"
        )
    return raw


def canonical_public_github_repo_url(value: str) -> str:
    """Return one canonical credential-free GitHub HTTPS repository URL."""
    raw = str(value or "").strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        raise NetworkCapabilityError("git_clone_url_invalid")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise NetworkCapabilityError("git_clone_requires_https")
    if (parsed.hostname or "").lower() != "github.com":
        raise NetworkCapabilityError("git_clone_host_not_allowed")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkCapabilityError("git_clone_embedded_credentials_forbidden")
    try:
        if parsed.port not in (None, 443):
            raise NetworkCapabilityError("git_clone_nonstandard_port_forbidden")
    except ValueError as exc:
        raise NetworkCapabilityError("git_clone_url_invalid") from exc
    if parsed.query or parsed.fragment or "%" in parsed.path:
        raise NetworkCapabilityError("git_clone_url_extras_forbidden")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise NetworkCapabilityError("git_clone_repository_path_required")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _GITHUB_NAME.fullmatch(owner) or not _GITHUB_NAME.fullmatch(repo):
        raise NetworkCapabilityError("git_clone_repository_name_invalid")
    if owner in {".", ".."} or repo in {".", ".."}:
        raise NetworkCapabilityError("git_clone_repository_name_invalid")
    return f"https://github.com/{owner}/{repo}.git"


def _git_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = dict(base or os.environ)
    env = {str(key): str(value) for key, value in source.items() if key in _ENV_ALLOW}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


def _bounded_text(value: str | bytes | None, limit: int = 64 * 1024) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated by Tiangong network capability]"


def clone_public_github_repo(
    repo_url: str,
    destination: str | os.PathLike[str],
    *,
    timeout_seconds: int = 300,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Clone one public GitHub repository through a fixed, non-shell argv.

    The destination must not exist.  Git credentials/config are disabled, the
    operation is shallow and non-interactive, and failed partial clones are
    removed because this function owns only the newly-created destination.
    """
    canonical_url = canonical_public_github_repo_url(repo_url)
    destination_path = Path(destination).expanduser().resolve(strict=False)
    if destination_path.exists() or destination_path.is_symlink():
        raise NetworkCapabilityError("git_clone_destination_must_be_new")
    if not destination_path.parent.is_dir():
        raise NetworkCapabilityError("git_clone_destination_parent_missing")
    if isinstance(timeout_seconds, bool):
        raise NetworkCapabilityError("git_clone_timeout_invalid")
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NetworkCapabilityError("git_clone_timeout_invalid") from exc
    if timeout < 10 or timeout > 600:
        raise NetworkCapabilityError("git_clone_timeout_out_of_range")

    git = shutil.which("git")
    if not git:
        raise NetworkCapabilityError("git_executable_not_found")

    env = _git_environment(environment)
    command = [
        git,
        "-c", "credential.helper=",
        "-c", "core.askPass=",
        "-c", "credential.interactive=never",
        "-c", "submodule.recurse=false",
        "clone",
        "--depth", "1",
        "--no-tags",
        "--single-branch",
        canonical_url,
        str(destination_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(destination_path.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if destination_path.exists():
            shutil.rmtree(destination_path, ignore_errors=True)
        raise NetworkCapabilityError("git_clone_timeout") from exc
    except OSError as exc:
        if destination_path.exists():
            shutil.rmtree(destination_path, ignore_errors=True)
        raise NetworkCapabilityError(f"git_clone_process_error:{type(exc).__name__}") from exc

    stdout = _bounded_text(completed.stdout)
    stderr = _bounded_text(completed.stderr)
    if completed.returncode != 0:
        if destination_path.exists():
            shutil.rmtree(destination_path, ignore_errors=True)
        raise NetworkCapabilityError(
            f"git_clone_failed:exit={completed.returncode}:stderr={stderr.strip()[:2000]}"
        )
    if not (destination_path / ".git").is_dir():
        if destination_path.exists():
            shutil.rmtree(destination_path, ignore_errors=True)
        raise NetworkCapabilityError("git_clone_verification_missing_git_directory")

    verify = subprocess.run(
        [git, "-C", str(destination_path), "rev-parse", "HEAD"],
        cwd=str(destination_path.parent),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=min(timeout, 60),
        check=False,
    )
    head = _bounded_text(verify.stdout, 4096).strip()
    if verify.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
        shutil.rmtree(destination_path, ignore_errors=True)
        raise NetworkCapabilityError("git_clone_verification_failed")

    return {
        "success": True,
        "repository": canonical_url,
        "destination": str(destination_path),
        "head": head.lower(),
        "shallow": True,
        "submodules_fetched": False,
        "lfs_smudge": False,
        "stdout": stdout,
        "stderr": stderr,
    }
