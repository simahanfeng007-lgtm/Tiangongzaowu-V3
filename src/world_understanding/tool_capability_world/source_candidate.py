"""Observe immutable source candidates from the existing Git authority.

No candidate Python is imported. The base commit owns the source-ownership
policy; a candidate cannot move its own files into an authoritative root.
These records describe source and review scope, never publication permission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
from typing import Any
import unicodedata

from contracts import canonical_sha256
from source_authority.validator import validate_source_authority


_GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ACTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}\Z")
_WINDOWS_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_CHANGE_BYTES = 128 * 1024 * 1024
_MAX_CHANGES = 1024
_SCHEMA = "tiangong.tool-source-candidate.v1"


class SourceCandidateError(ValueError):
    pass


def _repository_path(value: str) -> str:
    if not isinstance(value, str):
        raise SourceCandidateError("source candidate path must be text")
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 1024
        or value != unicodedata.normalize("NFC", value)
        or str(path) != value
        or path.is_absolute()
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(
            part in {".", ".."}
            or part.casefold() == ".git"
            or part.endswith((" ", "."))
            or any(character in part for character in ':*?"<>|')
            or _WINDOWS_RESERVED.fullmatch(part)
            for part in path.parts
        )
    ):
        raise SourceCandidateError("source candidate contains an unsafe repository path")
    return value


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceCandidateError("source authority contains duplicate JSON keys")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise SourceCandidateError("source authority contains a non-finite value")


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    # Git reads objects only. No checkout, filter, hook, shell or candidate
    # executable participates in observing a candidate.
    environment = dict(os.environ)
    # Explicit -C must not be redirected to a different repository or object
    # namespace by an inherited caller environment. Local grafts also must
    # not manufacture the required base ancestry.
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    environment["GIT_GRAFT_FILE"] = os.devnull
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "--no-optional-locks", "--no-replace-objects", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            input=input_bytes,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceCandidateError("source candidate Git observation failed") from exc
    if result.returncode:
        # Do not reflect arbitrary repository metadata or credential-bearing
        # remotes from stderr into the caller's report.
        raise SourceCandidateError("source candidate Git object is absent or incompatible")
    return result.stdout


def _commit(repository: Path, oid: str) -> str:
    if not isinstance(oid, str) or _GIT_OID.fullmatch(oid) is None:
        raise SourceCandidateError("source candidate requires a full Git commit ID")
    if _git(repository, "cat-file", "-t", oid).strip() != b"commit":
        raise SourceCandidateError("source candidate identity is not a commit")
    if int(_git(repository, "cat-file", "-s", oid).strip()) > 1024 * 1024:
        raise SourceCandidateError("source candidate commit exceeds its size limit")
    raw = _git(repository, "cat-file", "commit", oid)
    _verify_git_content(oid, "commit", raw)
    return oid


def _verify_git_content(oid: str, kind: str, raw: bytes) -> None:
    digest = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    if digest(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest() != oid:
        raise SourceCandidateError("source Git object bytes do not match its identity")


@dataclass(frozen=True, slots=True)
class GitSourceBlobV1:
    mode: str
    git_oid: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SourceCandidateChangeV1:
    path: str
    role: str
    authority_id: str | None
    before: GitSourceBlobV1 | None
    after: GitSourceBlobV1 | None


@dataclass(frozen=True, slots=True)
class ToolSourceCandidateV1:
    schema: str
    base_commit: str
    candidate_commit: str
    base_tree: str
    candidate_tree: str
    ownership_sha256: str
    requested_action_ids: tuple[str, ...]
    changes: tuple[SourceCandidateChangeV1, ...]
    candidate_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("candidate_sha256")
        return value

    def has_valid_sha256(self) -> bool:
        return (
            self.schema == _SCHEMA
            and self.may_authorize is False
            and self.may_execute is False
            and self.candidate_sha256 == canonical_sha256(self.payload())
        )


def _tree(repository: Path, commit: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    case_paths: dict[str, str] = {}
    root_oid = _git(repository, "rev-parse", commit + "^{tree}").decode("ascii").strip()
    tree_oids = {root_oid}
    listing = _git(repository, "ls-tree", "-r", "-t", "-z", "--full-tree", commit)
    if len(listing) > 16 * 1024 * 1024:
        raise SourceCandidateError("source candidate tree listing exceeds its size limit")
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        try:
            header, raw_path = raw.split(b"\t", 1)
            mode, object_type, oid = header.decode("ascii").split(" ")
            path = _repository_path(raw_path.decode("utf-8", errors="strict"))
        except (ValueError, UnicodeError) as exc:
            raise SourceCandidateError("source candidate tree entry is invalid") from exc
        if not _GIT_OID.fullmatch(oid):
            raise SourceCandidateError("source candidate tree object identity is invalid")
        if path.casefold() in case_paths:
            raise SourceCandidateError("source candidate tree has a cross-platform path collision")
        case_paths[path.casefold()] = path
        if mode == "040000" and object_type == "tree":
            tree_oids.add(oid)
            continue
        # Submodules and symlinks are not immutable source bytes of this repo.
        if mode not in {"100644", "100755"} or object_type != "blob":
            raise SourceCandidateError("source candidate tree contains a symlink or non-file object")
        result[path] = (mode, oid)
    _verify_tree_objects(repository, tree_oids)
    return result


def _verify_tree_objects(repository: Path, oids: set[str]) -> None:
    # Git does not rehash every tree while listing it. Verify the native
    # objects too, so even a corrupted local object store cannot hide changes
    # behind an unchanged tree OID. One batch keeps large source trees cheap.
    ordered = sorted(oids)
    data = _git(repository, "cat-file", "--batch", input_bytes=("\n".join(ordered) + "\n").encode("ascii"))
    offset = 0
    for expected_oid in ordered:
        boundary = data.find(b"\n", offset)
        try:
            oid, kind, size_text = data[offset:boundary].decode("ascii").split(" ")
            size = int(size_text)
        except (ValueError, UnicodeError) as exc:
            raise SourceCandidateError("source candidate tree batch is invalid") from exc
        if boundary < offset or oid != expected_oid or kind != "tree" or not 0 <= size <= _MAX_FILE_BYTES:
            raise SourceCandidateError("source candidate tree batch identity is invalid")
        start, end = boundary + 1, boundary + 1 + size
        if data[end:end + 1] != b"\n":
            raise SourceCandidateError("source candidate tree batch is truncated")
        _verify_git_content(oid, kind, data[start:end])
        offset = end + 1
    if offset != len(data):
        raise SourceCandidateError("source candidate tree batch has trailing data")


def _read_blob(repository: Path, oid: str) -> bytes:
    size = int(_git(repository, "cat-file", "-s", oid).strip())
    if not 0 <= size <= _MAX_FILE_BYTES:
        raise SourceCandidateError("source candidate blob exceeds its size limit")
    raw = _git(repository, "cat-file", "blob", oid)
    if len(raw) != size:
        raise SourceCandidateError("source candidate blob size changed")
    _verify_git_content(oid, "blob", raw)
    return raw


def _source_policy(repository: Path, tree: dict[str, tuple[str, str]]) -> tuple[dict[str, Any], str]:
    entry = tree.get("source-ownership.json")
    if entry is None:
        raise SourceCandidateError("base has no source authority policy")
    raw = _read_blob(repository, entry[1])
    try:
        policy = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError) as exc:
        raise SourceCandidateError("base source authority policy is invalid") from exc
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != "tiangong.source-ownership.v2"
        or not isinstance(policy.get("mappings"), list)
        or not isinstance(policy.get("authority_policy"), dict)
    ):
        raise SourceCandidateError("base source authority schema is unsupported")
    seen: set[str] = set()
    for mapping in policy["mappings"]:
        if (
            not isinstance(mapping, dict)
            or not isinstance(mapping.get("id"), str)
            or not mapping["id"]
            or mapping["id"] in seen
            or mapping.get("source_role") not in {
                "authoritative", "frozen_authoritative", "authoritative_alias"
            }
            or not isinstance(mapping.get("targets"), list)
        ):
            raise SourceCandidateError("base source authority mapping is invalid")
        _repository_path(mapping.get("source", ""))
        for target in mapping["targets"]:
            _repository_path(target)
        seen.add(mapping["id"])
    if validate_source_authority(policy, repo_root=repository, require_sources=False):
        raise SourceCandidateError("base policy violates the existing source authority topology")
    return policy, hashlib.sha256(raw).hexdigest()


def _classify(path: str, policy: dict[str, Any]) -> tuple[str, str | None]:
    if path == "source-ownership.json":
        raise SourceCandidateError("source candidate cannot modify its ownership policy")
    mappings = policy["mappings"]
    for mapping in mappings:
        if any(_under(path, target) for target in mapping["targets"]):
            return "GENERATED", mapping["id"]
    candidates = sorted(
        (mapping for mapping in mappings if _under(path, mapping["source"])),
        key=lambda mapping: len(mapping["source"]),
        reverse=True,
    )
    for mapping in candidates:
        role = mapping["source_role"]
        if role == "frozen_authoritative":
            raise SourceCandidateError("source candidate changes a frozen authority")
        if role == "authoritative_alias":
            continue
        boundary = mapping.get("boundary_policy")
        if boundary is not None:
            if not isinstance(boundary, dict) or boundary.get("mode") != "closed_world":
                raise SourceCandidateError("source authority boundary is unsupported")
            relative = path[len(mapping["source"]) + 1:]
            child = relative.split("/", 1)[0]
            if child in boundary.get("non_runtime_artifacts", ()):
                return "DOCUMENTATION", mapping["id"]
            if child not in boundary.get("implementation_roots", ()):
                raise SourceCandidateError("source candidate escapes the closed-world authority")
        return "SOURCE", mapping["id"]
    if any(_under(path, root) for root in policy["authority_policy"].get("frozen_roots", ())):
        raise SourceCandidateError("source candidate changes a frozen authority")
    if _under(path, "docs"):
        return "DOCUMENTATION", None
    if any(_under(path, root) for root in ("tests", "scripts", ".github/workflows")):
        return "VALIDATION", None
    if path in {"pyproject.toml", "requirements-source.lock", "pytest.ini"}:
        return "BUILD", None
    raise SourceCandidateError(f"source candidate has an unowned path: {path}")


def inspect_tool_source_candidate(
    repository: Path,
    *,
    base_commit: str,
    candidate_commit: str,
    requested_action_ids: tuple[str, ...],
) -> ToolSourceCandidateV1:
    """Rebuild the full review envelope from exact, immutable Git objects.

    Requested action IDs express intent only. The manifest compilation step
    must derive the actual changed Action set and compare it to this request.
    Generated changes are classified, never accepted as author-written source;
    publication additionally requires official generated-source validation.
    """
    if not repository.is_absolute() or not repository.is_dir() or repository.is_symlink():
        raise SourceCandidateError("source candidate repository is missing or unsafe")
    if (
        not isinstance(requested_action_ids, tuple)
        or not 1 <= len(requested_action_ids) <= 2048
        or any(not isinstance(item, str) or not _ACTION_ID.fullmatch(item) for item in requested_action_ids)
        or requested_action_ids != tuple(sorted(set(requested_action_ids)))
    ):
        raise SourceCandidateError("source candidate Action IDs must be sorted, unique and valid")
    base_commit = _commit(repository, base_commit)
    candidate_commit = _commit(repository, candidate_commit)
    ancestor = _git(repository, "merge-base", base_commit, candidate_commit).decode("ascii").strip()
    if ancestor != base_commit or base_commit == candidate_commit:
        raise SourceCandidateError("source candidate must descend from its distinct base")
    before_tree = _tree(repository, base_commit)
    after_tree = _tree(repository, candidate_commit)
    policy, ownership_sha256 = _source_policy(repository, before_tree)
    paths = sorted(
        path for path in before_tree.keys() | after_tree.keys()
        if before_tree.get(path) != after_tree.get(path)
    )
    if not 1 <= len(paths) <= _MAX_CHANGES:
        raise SourceCandidateError("source candidate change count is empty or exceeds its limit")
    changes: list[SourceCandidateChangeV1] = []
    total_bytes = 0
    for path in paths:
        role, authority_id = _classify(path, policy)
        blobs: list[GitSourceBlobV1 | None] = []
        for tree in (before_tree, after_tree):
            entry = tree.get(path)
            if entry is None:
                blobs.append(None)
                continue
            raw = _read_blob(repository, entry[1])
            total_bytes += len(raw)
            if total_bytes > _MAX_CHANGE_BYTES:
                raise SourceCandidateError("source candidate total bytes exceed its limit")
            blobs.append(GitSourceBlobV1(entry[0], entry[1], len(raw), hashlib.sha256(raw).hexdigest()))
        changes.append(SourceCandidateChangeV1(path, role, authority_id, blobs[0], blobs[1]))
    if not any(change.role == "SOURCE" for change in changes):
        raise SourceCandidateError("source candidate has no authoritative source change")
    draft = ToolSourceCandidateV1(
        schema=_SCHEMA,
        base_commit=base_commit,
        candidate_commit=candidate_commit,
        base_tree=_git(repository, "rev-parse", base_commit + "^{tree}").decode("ascii").strip(),
        candidate_tree=_git(repository, "rev-parse", candidate_commit + "^{tree}").decode("ascii").strip(),
        ownership_sha256=ownership_sha256,
        requested_action_ids=requested_action_ids,
        changes=tuple(changes),
        candidate_sha256="0" * 64,
    )
    return replace(draft, candidate_sha256=canonical_sha256(draft.payload()))


def verify_tool_source_candidate(repository: Path, candidate: ToolSourceCandidateV1) -> None:
    """A rehashed caller object is not source authority: rebuild from Git."""
    if not isinstance(candidate, ToolSourceCandidateV1) or not candidate.has_valid_sha256():
        raise SourceCandidateError("source candidate digest is invalid")
    expected = inspect_tool_source_candidate(
        repository,
        base_commit=candidate.base_commit,
        candidate_commit=candidate.candidate_commit,
        requested_action_ids=candidate.requested_action_ids,
    )
    if expected != candidate:
        raise SourceCandidateError("source candidate differs from authoritative Git objects")


def read_tool_source_manifests(
    repository: Path, candidate: ToolSourceCandidateV1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read committed manifest artifacts at the exact reviewed revisions.

    Artifact provenance is NOT evidence that candidate Python was built or
    tested. This deliberately reads Git blobs without importing candidate
    code, running filters or trusting mutable checkout files.
    """
    verify_tool_source_candidate(repository, candidate)
    path = "src/omni_body_skill/registry/capability_manifest.generated.json"
    documents: list[dict[str, Any]] = []
    for commit in (candidate.base_commit, candidate.candidate_commit):
        oid = _git(repository, "rev-parse", commit + ":" + path).decode("ascii").strip()
        raw = _read_blob(repository, oid)
        try:
            document = json.loads(raw.decode("utf-8", errors="strict"),
                                  object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise SourceCandidateError("committed source manifest is invalid JSON") from exc
        if not isinstance(document, dict):
            raise SourceCandidateError("committed source manifest must be an object")
        documents.append(document)
    return documents[0], documents[1]


@contextmanager
def materialize_tool_source_candidate(repository: Path, candidate: ToolSourceCandidateV1):
    """Yield a private byte-verified copy for an isolated build, never import it.

    Git archive attributes are not trusted to choose or rewrite build inputs:
    every regular entry must exactly match the verified Git tree and native
    blob hash. Missing, substituted, linked, extra or oversized data fails.
    Only this newly created temporary directory is ever written or removed.
    """
    verify_tool_source_candidate(repository, candidate)
    entries = _tree(repository, candidate.candidate_commit)
    oids = sorted({entry[1] for entry in entries.values()})
    sizes = _git(repository, "cat-file", "--batch-check", input_bytes=("\n".join(oids) + "\n").encode("ascii"))
    size_rows = sizes.decode("ascii").splitlines()
    if len(size_rows) != len(oids):
        raise SourceCandidateError("source build object size inventory is incomplete")
    by_oid: dict[str, int] = {}
    for expected, row in zip(oids, size_rows):
        fields = row.split(" ")
        if len(fields) != 3 or fields[0] != expected or fields[1] != "blob":
            raise SourceCandidateError("source build object size identity is invalid")
        size = int(fields[2])
        if not 0 <= size <= _MAX_FILE_BYTES:
            raise SourceCandidateError("source build blob exceeds its size limit")
        by_oid[expected] = size
    total_bytes = sum(by_oid[entry[1]] for entry in entries.values())
    if total_bytes > 256 * 1024 * 1024:
        raise SourceCandidateError("source build snapshot exceeds its size limit")
    archive = _git(repository, "archive", "--format=tar", candidate.candidate_commit)
    if len(archive) > total_bytes + 16 * 1024 * 1024:
        raise SourceCandidateError("source build archive exceeds its size limit")
    with tempfile.TemporaryDirectory(prefix="tg-source-build-") as temporary:
        root = Path(temporary).resolve()
        observed: set[str] = set()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            for member in stream:
                path = _repository_path(member.name.rstrip("/") if member.isdir() else member.name)
                if member.isdir():
                    if not any(_under(name, path) for name in entries):
                        raise SourceCandidateError("source build archive contains an extra directory")
                    continue
                if not member.isfile() or path not in entries or path in observed:
                    raise SourceCandidateError("source build archive contains an unsafe or extra entry")
                mode, oid = entries[path]
                if member.size != by_oid[oid]:
                    raise SourceCandidateError("source build archive blob size differs")
                handle = stream.extractfile(member)
                if handle is None:
                    raise SourceCandidateError("source build archive blob is absent")
                with handle:
                    raw = handle.read(_MAX_FILE_BYTES + 1)
                _verify_git_content(oid, "blob", raw)
                target = root.joinpath(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as destination:
                    destination.write(raw)
                target.chmod(0o755 if mode == "100755" else 0o644)
                observed.add(path)
        if observed != set(entries):
            raise SourceCandidateError("source build archive omitted committed inputs")
        yield root
