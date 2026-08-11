"""Incremental, rebuildable structural perception for repository source.

This module is an RPS cache/builder. It may read bounded source bytes under the
active worktree, but it owns no WorldState, Runtime, Gateway, scheduler, memory,
learning, or execution authority. The full snapshot is rebuildable and remains
inside this adapter; only a bounded RepositoryStructureDelta is published.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from contracts.canonical import canonical_sha256
from contracts.world_understanding.repository import RepositoryObservation
from contracts.world_understanding.repository_structure import (
    RepositoryImportDeclaration,
    RepositorySourceSpan,
    RepositoryStructureDelta,
    RepositoryStructureFile,
    RepositoryStructureNode,
    RepositoryStructureRetirement,
    RepositoryStructureSnapshot,
)

_BUILDER_VERSION = "repository-structure.v0.1"
_MAX_BASELINE_FILES = 256
_MAX_CHANGED_FILES = 128
_MAX_FILE_BYTES = 512 * 1024
_MAX_BASELINE_BYTES = 8 * 1024 * 1024
_MAX_NODES_PER_FILE = 128
_MAX_IMPORTS_PER_FILE = 128
_MAX_TOTAL_NODES = 4096
_MAX_TOTAL_IMPORTS = 4096

_SUPPORTED_SUFFIXES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}
_PRUNED_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules", "dist",
    "build", "coverage", ".next", ".nuxt", ".turbo", "vendor",
})
_SECRET_BASENAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "secrets.json", "secrets.yaml", "secrets.yml",
})
_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks"})
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


class RepositoryStructureError(RuntimeError):
    """Bounded structural perception failure."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _repo_path(value: str) -> str:
    return _nfc(value.replace("\\", "/"))


def _is_secret_path(path: str) -> bool:
    p = PurePosixPath(path)
    lowered = p.name.lower()
    stem = p.stem.lower()
    sensitive_token = any(
        token in stem for token in ("secret", "credential", "private_key")
    )
    return (
        lowered in _SECRET_BASENAMES
        or p.suffix.lower() in _SECRET_SUFFIXES
        or sensitive_token
    )


def _language_for(path: str) -> str | None:
    return _SUPPORTED_SUFFIXES.get(PurePosixPath(path).suffix.lower())


def _module_name(path: str, language: str) -> str:
    p = PurePosixPath(path)
    if language == "python":
        parts = list(p.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) or p.parent.name or "__root__"
    text = p.with_suffix("").as_posix()
    if text.endswith("/index"):
        text = text[:-6]
    return text.replace("/", ".") or "__root__"


def _file_key(repository_id: str, worktree_id: str, path: str) -> str:
    return "sfile." + canonical_sha256({
        "repository_id": repository_id,
        "worktree_id": worktree_id,
        "path": path,
    })[:48]


def _module_anchor(file_key: str) -> str:
    return "smod." + canonical_sha256({"file_key": file_key, "kind": "Module"})[:48]


def _structure_anchor(file_key: str, kind: str, lexical_anchor: str) -> str:
    return "snode." + canonical_sha256({
        "file_key": file_key,
        "kind": kind,
        "lexical_anchor": lexical_anchor,
    })[:48]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _line_offsets(data: bytes) -> tuple[int, ...]:
    offsets = [0]
    for index, byte in enumerate(data):
        if byte == 10:
            offsets.append(index + 1)
    return tuple(offsets)


def _point_for_byte(offsets: tuple[int, ...], byte_offset: int) -> tuple[int, int]:
    low, high = 0, len(offsets)
    while low + 1 < high:
        mid = (low + high) // 2
        if offsets[mid] <= byte_offset:
            low = mid
        else:
            high = mid
    return low + 1, max(0, byte_offset - offsets[low])


def _span_from_bytes(
    path: str,
    data: bytes,
    start_byte: int,
    end_byte: int,
    *,
    offsets: tuple[int, ...] | None = None,
) -> RepositorySourceSpan:
    points = offsets or _line_offsets(data)
    start_line, start_column = _point_for_byte(points, start_byte)
    end_line, end_column = _point_for_byte(points, end_byte)
    return RepositorySourceSpan(
        path=path,
        start_byte=start_byte,
        end_byte=end_byte,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def _bounded_target(root: Path, path: str) -> Path | None:
    try:
        root_resolved = root.resolve(strict=False)
        target = root.joinpath(*PurePosixPath(path).parts)
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _candidate_paths(root: Path) -> tuple[tuple[str, ...], int, bool]:
    root = root.resolve(strict=False)
    rows: list[str] = []
    candidate_count = 0
    truncated = False
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in _PRUNED_DIRS and not Path(current, name).is_symlink()
        )
        for name in sorted(files):
            full = Path(current, name)
            try:
                rel = _repo_path(full.relative_to(root).as_posix())
            except (OSError, ValueError):
                continue
            if _language_for(rel) is None:
                continue
            candidate_count += 1
            if len(rows) >= _MAX_BASELINE_FILES:
                truncated = True
                return tuple(rows), candidate_count - 1, truncated
            rows.append(rel)
    return tuple(rows), candidate_count, truncated


@dataclass(frozen=True, slots=True)
class _RawNode:
    kind: str
    name: str
    parent_index: int | None
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class _RawImport:
    module: str
    imported_names: tuple[str, ...]
    start_byte: int
    end_byte: int


class _TreeSitterRegistry:
    """Optional parser registry; import failure never blocks the Runtime."""

    __slots__ = ("_loaded", "_parsers", "_versions", "_error")

    def __init__(self) -> None:
        self._loaded = False
        self._parsers: dict[str, object] = {}
        self._versions: dict[str, str] = {}
        self._error: Exception | None = None

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            core = importlib.import_module("tree_sitter")
            language_cls = getattr(core, "Language")
            parser_cls = getattr(core, "Parser")
            py = importlib.import_module("tree_sitter_python")
            js = importlib.import_module("tree_sitter_javascript")
            ts = importlib.import_module("tree_sitter_typescript")
            languages = {
                "python": language_cls(py.language()),
                "javascript": language_cls(js.language()),
                "typescript": language_cls(ts.language_typescript()),
                "tsx": language_cls(ts.language_tsx()),
            }
            self._parsers = {
                name: parser_cls(language) for name, language in languages.items()
            }
            core_version = importlib.metadata.version("tree-sitter")
            self._versions = {
                "python": f"tree-sitter/{core_version}+python/"
                + importlib.metadata.version("tree-sitter-python"),
                "javascript": f"tree-sitter/{core_version}+javascript/"
                + importlib.metadata.version("tree-sitter-javascript"),
                "typescript": f"tree-sitter/{core_version}+typescript/"
                + importlib.metadata.version("tree-sitter-typescript"),
                "tsx": f"tree-sitter/{core_version}+typescript/"
                + importlib.metadata.version("tree-sitter-typescript"),
            }
        except Exception as exc:
            self._error = exc
            self._parsers = {}
            self._versions = {}

    def parser(self, language: str) -> tuple[object | None, str]:
        self._load()
        return self._parsers.get(language), self._versions.get(language, "unavailable")


_TREE_SITTER = _TreeSitterRegistry()


def _node_name(node: object, data: bytes) -> str | None:
    child = node.child_by_field_name("name")
    if child is None:
        return None
    try:
        return _nfc(data[child.start_byte:child.end_byte].decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return None


def _parse_import_text(
    language: str, text: str, start_byte: int, end_byte: int
) -> list[_RawImport]:
    text = text.strip()
    rows: list[_RawImport] = []
    if language == "python":
        match = re.match(r"^from\s+([.\w]+)\s+import\s+(.+)$", text, flags=re.S)
        if match:
            module = match.group(1)
            names = tuple(sorted(set(
                part.strip().split(" as ", 1)[0]
                for part in match.group(2).replace("(", "").replace(")", "").split(",")
                if part.strip()
            )))
            rows.append(_RawImport(module, names, start_byte, end_byte))
            return rows
        match = re.match(r"^import\s+(.+)$", text, flags=re.S)
        if match:
            for part in match.group(1).split(","):
                module = part.strip().split(" as ", 1)[0]
                if module:
                    rows.append(_RawImport(module, (), start_byte, end_byte))
            return rows
    match = re.search(r"\bfrom\s*([\"'])(.+?)\1", text, flags=re.S)
    if match:
        rows.append(_RawImport(match.group(2), (), start_byte, end_byte))
        return rows
    match = re.match(r"^import\s*([\"'])(.+?)\1", text, flags=re.S)
    if match:
        rows.append(_RawImport(match.group(2), (), start_byte, end_byte))
    return rows


def _tree_sitter_raw(
    language: str, data: bytes
) -> tuple[list[_RawNode], list[_RawImport], bool, str]:
    parser, version = _TREE_SITTER.parser(language)
    if parser is None:
        return [], [], False, version
    tree = parser.parse(data)
    raw_nodes: list[_RawNode] = []
    raw_imports: list[_RawImport] = []
    structure_stack: list[int] = []
    if language == "python":
        class_types = {"class_definition"}
        function_types = {"function_definition"}
        method_types = {"function_definition"}
        import_types = {"import_statement", "import_from_statement"}
    else:
        class_types = {"class_declaration", "class"}
        function_types = {"function_declaration", "generator_function_declaration"}
        method_types = {"method_definition"}
        import_types = {"import_statement"}

    def visit(node: object) -> None:
        node_type = str(node.type)
        pushed = False
        kind: str | None = None
        if node_type in class_types:
            kind = "Class"
        elif node_type in function_types:
            kind = (
                "Method"
                if language == "python"
                and structure_stack
                and raw_nodes[structure_stack[-1]].kind == "Class"
                else "Function"
            )
        elif node_type in method_types:
            kind = "Method"
        if kind is not None:
            name = _node_name(node, data)
            if name:
                parent_index = structure_stack[-1] if structure_stack else None
                raw_nodes.append(_RawNode(
                    kind=kind,
                    name=name,
                    parent_index=parent_index,
                    start_byte=int(node.start_byte),
                    end_byte=int(node.end_byte),
                ))
                structure_stack.append(len(raw_nodes) - 1)
                pushed = True
        if node_type in import_types:
            text = data[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            raw_imports.extend(_parse_import_text(
                language, text, int(node.start_byte), int(node.end_byte)
            ))
        for child in node.named_children:
            visit(child)
        if pushed:
            structure_stack.pop()

    visit(tree.root_node)
    return raw_nodes, raw_imports, bool(tree.root_node.has_error), version


def _python_ast_fallback(
    data: bytes,
) -> tuple[list[_RawNode], list[_RawImport], bool, str]:
    import sys
    version = "python-ast/" + ".".join(map(str, sys.version_info[:3]))
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return [], [], True, version
    offsets = _line_offsets(data)
    try:
        root = ast.parse(text)
    except SyntaxError:
        return [], [], True, version
    raw_nodes: list[_RawNode] = []
    raw_imports: list[_RawImport] = []
    stack: list[int] = []

    def byte_pos(lineno: int, col: int) -> int:
        line_index = max(0, min(lineno - 1, len(offsets) - 1))
        return min(len(data), offsets[line_index] + max(0, col))

    def visit(node: ast.AST) -> None:
        pushed = False
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef):
                kind = "Class"
            else:
                kind = (
                    "Method"
                    if stack and raw_nodes[stack[-1]].kind == "Class"
                    else "Function"
                )
            start = byte_pos(getattr(node, "lineno", 1), getattr(node, "col_offset", 0))
            end = byte_pos(
                getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                getattr(node, "end_col_offset", getattr(node, "col_offset", 0)),
            )
            raw_nodes.append(_RawNode(kind, node.name, stack[-1] if stack else None, start, end))
            stack.append(len(raw_nodes) - 1)
            pushed = True
        if isinstance(node, ast.Import):
            start = byte_pos(node.lineno, node.col_offset)
            end = byte_pos(node.end_lineno or node.lineno, node.end_col_offset or node.col_offset)
            for alias in node.names:
                raw_imports.append(_RawImport(alias.name, (), start, end))
        elif isinstance(node, ast.ImportFrom):
            start = byte_pos(node.lineno, node.col_offset)
            end = byte_pos(node.end_lineno or node.lineno, node.end_col_offset or node.col_offset)
            module = "." * int(node.level or 0) + str(node.module or "")
            names = tuple(sorted(set(alias.name for alias in node.names)))
            raw_imports.append(_RawImport(module, names, start, end))
        for child in ast.iter_child_nodes(node):
            visit(child)
        if pushed:
            stack.pop()

    visit(root)
    return raw_nodes, raw_imports, False, version


def _materialize_file(
    *, repository_id: str, worktree_id: str, path: str, target: Path,
    prior_file_key: str | None,
) -> RepositoryStructureFile:
    language = _language_for(path)
    if language is None:
        raise RepositoryStructureError("unsupported structure language")
    key = prior_file_key or _file_key(repository_id, worktree_id, path)
    module_anchor = _module_anchor(key)
    module_name = _module_name(path, language)

    def skipped(
        status: str,
        version: str,
        *,
        content_hash: str,
        size: int,
    ) -> RepositoryStructureFile:
        return RepositoryStructureFile(
            path=path,
            file_key=key,
            module_anchor=module_anchor,
            module_name=module_name,
            content_sha256=content_hash,
            source_fingerprint=canonical_sha256({
                "content_sha256": content_hash,
                "builder": _BUILDER_VERSION,
                "status": status,
            }),
            size=size,
            language=language,
            parser_kind="none" if status != "PARSER_UNAVAILABLE" else "tree-sitter",
            parser_version=version,
            parse_status=status,
        )

    if _is_secret_path(path):
        size = max(0, target.stat().st_size)
        return skipped(
            "SKIPPED_SECRET",
            "secret-redaction",
            content_hash=canonical_sha256({"path": path, "redacted": True}),
            size=size,
        )
    size = max(0, target.stat().st_size)
    if size > _MAX_FILE_BYTES:
        return skipped(
            "SKIPPED_LARGE",
            "bounded-size",
            content_hash=canonical_sha256({"path": path, "size": size, "bounded": True}),
            size=size,
        )
    with target.open("rb") as handle:
        data = handle.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        return skipped(
            "SKIPPED_LARGE",
            "bounded-size",
            content_hash=canonical_sha256({"path": path, "size": len(data), "bounded": True}),
            size=max(size, len(data)),
        )
    content_hash = _sha256_bytes(data)
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return skipped(
            "SKIPPED_BINARY",
            "utf8-required",
            content_hash=content_hash,
            size=len(data),
        )

    raw_nodes, raw_imports, syntax_error, parser_version = _tree_sitter_raw(language, data)
    parser_kind = "tree-sitter"
    if parser_version == "unavailable" and language == "python":
        raw_nodes, raw_imports, syntax_error, parser_version = _python_ast_fallback(data)
        parser_kind = "python-ast-fallback"
    elif parser_version == "unavailable":
        return skipped(
            "PARSER_UNAVAILABLE",
            "unavailable",
            content_hash=content_hash,
            size=len(data),
        )

    offsets = _line_offsets(data)
    local_paths: list[str] = []
    counts: dict[tuple[str, str, str], int] = {}
    nodes: list[RepositoryStructureNode] = []
    for raw in raw_nodes[:_MAX_NODES_PER_FILE]:
        parent_local = "" if raw.parent_index is None else local_paths[raw.parent_index]
        counter_key = (parent_local, raw.kind, raw.name)
        occurrence = counts.get(counter_key, 0)
        counts[counter_key] = occurrence + 1
        local = (
            f"{parent_local}/{raw.kind}:{raw.name}#{occurrence}"
            if parent_local else f"{raw.kind}:{raw.name}#{occurrence}"
        )
        local_paths.append(local)
        stable = _structure_anchor(key, raw.kind, local)
        parent_anchor = None
        if raw.parent_index is not None and raw.parent_index < len(nodes):
            parent_anchor = nodes[raw.parent_index].stable_anchor
        lexical_names = [
            piece.split(":", 1)[1].rsplit("#", 1)[0]
            for piece in local.split("/")
        ]
        qualified = module_name + "." + ".".join(lexical_names)
        syntax = data[raw.start_byte:raw.end_byte]
        nodes.append(RepositoryStructureNode(
            node_id=stable,
            stable_anchor=stable,
            file_key=key,
            kind=raw.kind,
            name=raw.name,
            qualified_name=qualified,
            parent_anchor=parent_anchor,
            span=_span_from_bytes(path, data, raw.start_byte, raw.end_byte, offsets=offsets),
            syntax_sha256=_sha256_bytes(syntax),
        ))

    imports: list[RepositoryImportDeclaration] = []
    for ordinal, raw in enumerate(raw_imports[:_MAX_IMPORTS_PER_FILE]):
        syntax = data[raw.start_byte:raw.end_byte]
        import_id = "simp." + canonical_sha256({
            "file_key": key,
            "module": raw.module,
            "ordinal": ordinal,
            "syntax_sha256": _sha256_bytes(syntax),
        })[:48]
        imports.append(RepositoryImportDeclaration(
            import_id=import_id,
            file_key=key,
            source_anchor=module_anchor,
            module=_nfc(raw.module),
            imported_names=tuple(sorted(set(_nfc(name) for name in raw.imported_names))),
            span=_span_from_bytes(path, data, raw.start_byte, raw.end_byte, offsets=offsets),
            syntax_sha256=_sha256_bytes(syntax),
        ))

    parse_status = "SYNTAX_ERROR" if syntax_error else "PARSED"
    fingerprint = canonical_sha256({
        "content_sha256": content_hash,
        "builder": _BUILDER_VERSION,
        "parser_kind": parser_kind,
        "parser_version": parser_version,
        "parse_status": parse_status,
        "node_hashes": [node.syntax_sha256 for node in nodes],
        "import_hashes": [item.syntax_sha256 for item in imports],
    })
    return RepositoryStructureFile(
        path=path,
        file_key=key,
        module_anchor=module_anchor,
        module_name=module_name,
        content_sha256=content_hash,
        source_fingerprint=fingerprint,
        size=len(data),
        language=language,
        parser_kind=parser_kind,
        parser_version=parser_version,
        parse_status=parse_status,
        nodes=tuple(sorted(nodes, key=lambda item: item.sort_key())),
        imports=tuple(sorted(imports, key=lambda item: item.sort_key())),
    )


def _python_resolve_module(current: RepositoryStructureFile, module: str) -> str:
    if not module.startswith("."):
        return module
    dots = len(module) - len(module.lstrip("."))
    suffix = module[dots:]
    current_parts = current.module_name.split(".")
    package = current_parts[:-1]
    keep = max(0, len(package) - max(0, dots - 1))
    base = package[:keep]
    if suffix:
        base.extend(suffix.split("."))
    return ".".join(base)


def _resolve_import(
    current: RepositoryStructureFile,
    item: RepositoryImportDeclaration,
    *, module_map: dict[str, str], path_map: dict[str, RepositoryStructureFile],
) -> tuple[str | None, str | None]:
    if current.language == "python":
        target_module = _python_resolve_module(current, item.module)
        path = module_map.get(target_module)
        return path, target_module if path is not None else None
    module = item.module
    if not module.startswith("."):
        return None, None
    base = PurePosixPath(current.path).parent
    candidate_base = (base / module).as_posix()
    normalized_parts: list[str] = []
    for part in PurePosixPath(candidate_base).parts:
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
        elif part not in {".", ""}:
            normalized_parts.append(part)
    normalized = "/".join(normalized_parts)
    candidates: list[str] = []
    if PurePosixPath(normalized).suffix.lower() in _JS_EXTENSIONS:
        candidates.append(normalized)
    else:
        candidates.extend(normalized + ext for ext in _JS_EXTENSIONS)
        candidates.extend(normalized + "/index" + ext for ext in _JS_EXTENSIONS)
    for path in candidates:
        target = path_map.get(path)
        if target is not None:
            return path, target.module_name
    return None, None


def _resolve_all_imports(
    files: dict[str, RepositoryStructureFile],
) -> tuple[dict[str, RepositoryStructureFile], set[str]]:
    module_map = {file.module_name: path for path, file in files.items()}
    changed: set[str] = set()
    output: dict[str, RepositoryStructureFile] = {}
    for path, file in sorted(files.items()):
        imports: list[RepositoryImportDeclaration] = []
        different = False
        for item in file.imports:
            resolved_path, resolved_module = _resolve_import(
                file, item, module_map=module_map, path_map=files
            )
            if resolved_path != item.resolved_path or resolved_module != item.resolved_module_name:
                different = True
                item = item.model_copy(update={
                    "resolved_path": resolved_path,
                    "resolved_module_name": resolved_module,
                })
            imports.append(item)
        if different:
            changed.add(path)
            file = file.model_copy(update={"imports": tuple(imports)})
        output[path] = file
    return output, changed


def _retirements_for_removed(
    old: RepositoryStructureFile, new: RepositoryStructureFile | None,
) -> tuple[RepositoryStructureRetirement, ...]:
    new_anchors = set()
    if new is not None:
        new_anchors.add(new.module_anchor)
        new_anchors.update(node.stable_anchor for node in new.nodes)
    rows: list[RepositoryStructureRetirement] = []
    if old.module_anchor not in new_anchors:
        rows.append(RepositoryStructureRetirement(
            entity_type="Module",
            stable_anchor=old.module_anchor,
            canonical_name=old.module_name,
        ))
    for node in old.nodes:
        if node.stable_anchor not in new_anchors:
            rows.append(RepositoryStructureRetirement(
                entity_type=node.kind,
                stable_anchor=node.stable_anchor,
                canonical_name=node.qualified_name,
            ))
    return tuple(sorted(rows, key=lambda item: item.sort_key()))


def _changed_structure_paths(observation: RepositoryObservation) -> tuple[str, ...]:
    paths: set[str] = set()
    for change in observation.changes:
        for path in (change.old_path, change.new_path):
            if path is not None and _language_for(path) is not None:
                paths.add(path)
    return tuple(sorted(paths))


def _assert_no_parser_regression(
    previous: RepositoryStructureSnapshot,
    candidate: RepositoryStructureSnapshot,
) -> None:
    previous_by_path = {file.path: file for file in previous.files}
    for file in candidate.files:
        old = previous_by_path.get(file.path)
        if old is not None and (
            old.parse_status in {"PARSED", "SYNTAX_ERROR"}
            and file.parse_status == "PARSER_UNAVAILABLE"
        ):
            raise RepositoryStructureError(
                "parser capability regressed; preserve prior coherent view"
            )


class RepositoryStructureIndex:
    """In-process rebuildable cache keyed by repository/worktree identity."""

    __slots__ = ("_lock", "_snapshots", "_last_deltas")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[tuple[str, str], RepositoryStructureSnapshot] = {}
        self._last_deltas: dict[
            tuple[str, str], tuple[str, RepositoryStructureDelta]
        ] = {}

    def current(
        self, repository_id: str, worktree_id: str
    ) -> RepositoryStructureSnapshot | None:
        with self._lock:
            return self._snapshots.get((repository_id, worktree_id))

    def discard(self, repository_id: str, worktree_id: str) -> None:
        with self._lock:
            key = (repository_id, worktree_id)
            self._snapshots.pop(key, None)
            self._last_deltas.pop(key, None)

    def update(self, observation: RepositoryObservation) -> RepositoryStructureDelta:
        started = time.perf_counter_ns()
        built_at_ms = time.time_ns() // 1_000_000
        identity = observation.identity
        revision = observation.revision
        state_hash = observation.working_tree_state.state_sha256
        key = (identity.repository_id, identity.worktree_id)
        root = Path(identity.worktree_root_ref).resolve(strict=False)
        with self._lock:
            previous = self._snapshots.get(key)
            last = self._last_deltas.get(key)
            if last is not None and last[0] == observation.observation_sha256:
                return last[1]
            changed_paths = _changed_structure_paths(observation)
            needs_full = previous is None
            if previous is not None and previous.head_commit != revision.head_commit and not changed_paths:
                needs_full = True
            if len(changed_paths) > _MAX_CHANGED_FILES:
                needs_full = True
            try:
                if needs_full:
                    snapshot, upserts, parsed_count, candidate_count, truncated = self._full_build(
                        root=root,
                        observation=observation,
                        built_at_ms=built_at_ms,
                        started_ns=started,
                    )
                    if previous is not None:
                        _assert_no_parser_regression(previous, snapshot)
                    base_hash = None if previous is None else previous.view_sha256
                    self._snapshots[key] = snapshot
                    delta = RepositoryStructureDelta.build(
                        repository_id=identity.repository_id,
                        worktree_id=identity.worktree_id,
                        head_commit=revision.head_commit,
                        working_tree_state_sha256=state_hash,
                        builder_version=_BUILDER_VERSION,
                        status="APPLIED",
                        base_view_sha256=base_hash,
                        new_view_sha256=snapshot.view_sha256,
                        full_rescan=True,
                        truncated=truncated,
                        candidate_path_count=candidate_count,
                        changed_paths=tuple(file.path for file in upserts),
                        parsed_file_count=parsed_count,
                        reused_file_count=0,
                        upsert_files=upserts,
                        retirements=(),
                        retired_file_keys=(),
                        built_at_ms=built_at_ms,
                        build_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
                    )
                    self._last_deltas[key] = (observation.observation_sha256, delta)
                    return delta

                assert previous is not None
                if not changed_paths:
                    snapshot = RepositoryStructureSnapshot.build(
                        repository_id=identity.repository_id,
                        worktree_id=identity.worktree_id,
                        head_commit=revision.head_commit,
                        working_tree_state_sha256=state_hash,
                        builder_version=_BUILDER_VERSION,
                        truncated=previous.truncated,
                        candidate_path_count=previous.candidate_path_count,
                        files=previous.files,
                        built_at_ms=built_at_ms,
                        build_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
                    )
                    self._snapshots[key] = snapshot
                    delta = RepositoryStructureDelta.build(
                        repository_id=identity.repository_id,
                        worktree_id=identity.worktree_id,
                        head_commit=revision.head_commit,
                        working_tree_state_sha256=state_hash,
                        builder_version=_BUILDER_VERSION,
                        status="NOOP",
                        base_view_sha256=previous.view_sha256,
                        new_view_sha256=snapshot.view_sha256,
                        full_rescan=False,
                        truncated=snapshot.truncated,
                        candidate_path_count=snapshot.candidate_path_count,
                        changed_paths=(),
                        parsed_file_count=0,
                        reused_file_count=len(snapshot.files),
                        upsert_files=(),
                        retirements=(),
                        retired_file_keys=(),
                        built_at_ms=built_at_ms,
                        build_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
                    )
                    self._last_deltas[key] = (observation.observation_sha256, delta)
                    return delta

                delta = self._incremental(
                    root=root,
                    observation=observation,
                    previous=previous,
                    changed_paths=changed_paths,
                    built_at_ms=built_at_ms,
                    started_ns=started,
                )
                self._last_deltas[key] = (observation.observation_sha256, delta)
                return delta
            except Exception:
                if previous is None:
                    raise
                delta = RepositoryStructureDelta.build(
                    repository_id=identity.repository_id,
                    worktree_id=identity.worktree_id,
                    head_commit=revision.head_commit,
                    working_tree_state_sha256=state_hash,
                    builder_version=_BUILDER_VERSION,
                    status="FAILED_OPEN",
                    base_view_sha256=previous.view_sha256,
                    new_view_sha256=previous.view_sha256,
                    full_rescan=needs_full,
                    truncated=previous.truncated,
                    candidate_path_count=previous.candidate_path_count,
                    changed_paths=changed_paths,
                    parsed_file_count=0,
                    reused_file_count=len(previous.files),
                    upsert_files=(),
                    retirements=(),
                    retired_file_keys=(),
                    built_at_ms=built_at_ms,
                    build_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
                )
                self._last_deltas[key] = (observation.observation_sha256, delta)
                return delta

    def _full_build(
        self, *, root: Path, observation: RepositoryObservation,
        built_at_ms: int, started_ns: int,
    ) -> tuple[
        RepositoryStructureSnapshot, tuple[RepositoryStructureFile, ...], int, int, bool
    ]:
        paths, candidate_count, truncated = _candidate_paths(root)
        files: dict[str, RepositoryStructureFile] = {}
        total_bytes = 0
        total_nodes = 0
        total_imports = 0
        parsed_count = 0
        for path in paths:
            target = _bounded_target(root, path)
            if target is None:
                continue
            try:
                size = target.stat().st_size
            except OSError:
                continue
            if total_bytes + min(size, _MAX_FILE_BYTES) > _MAX_BASELINE_BYTES:
                truncated = True
                break
            file = _materialize_file(
                repository_id=observation.identity.repository_id,
                worktree_id=observation.identity.worktree_id,
                path=path,
                target=target,
                prior_file_key=None,
            )
            if total_nodes + len(file.nodes) > _MAX_TOTAL_NODES:
                truncated = True
                file = file.model_copy(update={
                    "nodes": file.nodes[: max(0, _MAX_TOTAL_NODES - total_nodes)]
                })
            if total_imports + len(file.imports) > _MAX_TOTAL_IMPORTS:
                truncated = True
                file = file.model_copy(update={
                    "imports": file.imports[: max(0, _MAX_TOTAL_IMPORTS - total_imports)]
                })
            files[path] = file
            total_bytes += min(size, _MAX_FILE_BYTES)
            total_nodes += len(file.nodes)
            total_imports += len(file.imports)
            parsed_count += int(file.parse_status in {"PARSED", "SYNTAX_ERROR"})
            if total_nodes >= _MAX_TOTAL_NODES or total_imports >= _MAX_TOTAL_IMPORTS:
                truncated = True
                break
        files, _ = _resolve_all_imports(files)
        ordered = tuple(files[path] for path in sorted(files))
        snapshot = RepositoryStructureSnapshot.build(
            repository_id=observation.identity.repository_id,
            worktree_id=observation.identity.worktree_id,
            head_commit=observation.revision.head_commit,
            working_tree_state_sha256=observation.working_tree_state.state_sha256,
            builder_version=_BUILDER_VERSION,
            truncated=truncated,
            candidate_path_count=candidate_count,
            files=ordered,
            built_at_ms=built_at_ms,
            build_ms=max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        )
        return snapshot, ordered, parsed_count, candidate_count, truncated

    def _incremental(
        self, *, root: Path, observation: RepositoryObservation,
        previous: RepositoryStructureSnapshot, changed_paths: tuple[str, ...],
        built_at_ms: int, started_ns: int,
    ) -> RepositoryStructureDelta:
        old_files = {file.path: file for file in previous.files}
        files = dict(old_files)
        upsert_paths: set[str] = set()
        retirements: list[RepositoryStructureRetirement] = []
        retired_file_keys: set[str] = set()
        parsed_count = 0
        renamed_handled_paths: set[str] = set()
        renamed_new_paths: set[str] = set()

        for change in observation.changes:
            if change.change_kind not in {"RENAME", "MOVE"}:
                continue
            if change.old_path is None or change.new_path is None:
                continue
            if _language_for(change.old_path) is None and _language_for(change.new_path) is None:
                continue
            old = files.pop(change.old_path, None)
            if old is None:
                continue
            renamed_handled_paths.update((change.old_path, change.new_path))
            renamed_new_paths.add(change.new_path)
            target = _bounded_target(root, change.new_path)
            if target is None:
                raise RepositoryStructureError("renamed structure target is unreadable")
            new = _materialize_file(
                repository_id=observation.identity.repository_id,
                worktree_id=observation.identity.worktree_id,
                path=change.new_path,
                target=target,
                prior_file_key=old.file_key,
            )
            if (
                old.parse_status in {"PARSED", "SYNTAX_ERROR"}
                and new.parse_status == "PARSER_UNAVAILABLE"
            ):
                raise RepositoryStructureError("parser capability regressed during rename")
            files[change.new_path] = new
            upsert_paths.add(change.new_path)
            parsed_count += int(new.parse_status in {"PARSED", "SYNTAX_ERROR"})
            retirements.extend(_retirements_for_removed(old, new))

        for path in changed_paths:
            if path in renamed_handled_paths:
                continue
            relevant_change = next(
                (change for change in observation.changes
                 if path in {change.old_path, change.new_path}),
                None,
            )
            if (
                relevant_change is not None
                and relevant_change.change_kind == "DELETE"
                and relevant_change.old_path == path
            ):
                old = files.pop(path, None)
                if old is not None:
                    retired_file_keys.add(old.file_key)
                    retirements.extend(_retirements_for_removed(old, None))
                continue
            target = _bounded_target(root, path)
            if target is None:
                old = files.pop(path, None)
                if old is not None:
                    retired_file_keys.add(old.file_key)
                    retirements.extend(_retirements_for_removed(old, None))
                continue
            old = files.get(path)
            new = _materialize_file(
                repository_id=observation.identity.repository_id,
                worktree_id=observation.identity.worktree_id,
                path=path,
                target=target,
                prior_file_key=None if old is None else old.file_key,
            )
            if (
                old is not None
                and old.parse_status in {"PARSED", "SYNTAX_ERROR"}
                and new.parse_status == "PARSER_UNAVAILABLE"
            ):
                raise RepositoryStructureError(
                    "parser capability regressed during incremental update"
                )
            files[path] = new
            upsert_paths.add(path)
            parsed_count += int(new.parse_status in {"PARSED", "SYNTAX_ERROR"})
            if old is not None:
                retirements.extend(_retirements_for_removed(old, new))

        files, resolution_changed = _resolve_all_imports(files)
        upsert_paths.update(resolution_changed)
        ordered = tuple(files[path] for path in sorted(files))
        snapshot = RepositoryStructureSnapshot.build(
            repository_id=observation.identity.repository_id,
            worktree_id=observation.identity.worktree_id,
            head_commit=observation.revision.head_commit,
            working_tree_state_sha256=observation.working_tree_state.state_sha256,
            builder_version=_BUILDER_VERSION,
            truncated=previous.truncated,
            candidate_path_count=max(
                0,
                previous.candidate_path_count
                + sum(
                    1
                    for path in upsert_paths
                    if path not in old_files and path not in renamed_new_paths
                )
                - len(retired_file_keys),
            ),
            files=ordered,
            built_at_ms=built_at_ms,
            build_ms=max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        )
        upserts = tuple(files[path] for path in sorted(upsert_paths) if path in files)
        self._snapshots[(observation.identity.repository_id, observation.identity.worktree_id)] = snapshot
        retirement_map = {item.sort_key(): item for item in retirements}
        return RepositoryStructureDelta.build(
            repository_id=observation.identity.repository_id,
            worktree_id=observation.identity.worktree_id,
            head_commit=observation.revision.head_commit,
            working_tree_state_sha256=observation.working_tree_state.state_sha256,
            builder_version=_BUILDER_VERSION,
            status="APPLIED",
            base_view_sha256=previous.view_sha256,
            new_view_sha256=snapshot.view_sha256,
            full_rescan=False,
            truncated=snapshot.truncated,
            candidate_path_count=snapshot.candidate_path_count,
            changed_paths=changed_paths,
            parsed_file_count=parsed_count,
            reused_file_count=max(0, len(files) - len(upserts)),
            upsert_files=upserts,
            retirements=tuple(retirement_map[key] for key in sorted(retirement_map)),
            retired_file_keys=tuple(sorted(retired_file_keys)),
            built_at_ms=built_at_ms,
            build_ms=max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        )


_INDEX = RepositoryStructureIndex()


def repository_structure_index() -> RepositoryStructureIndex:
    return _INDEX


__all__ = [
    "RepositoryStructureError",
    "RepositoryStructureIndex",
    "repository_structure_index",
]
