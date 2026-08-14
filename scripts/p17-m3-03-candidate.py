from __future__ import annotations

import ast
import copy
import textwrap
from pathlib import Path

STORE = Path("src/life_service/store.py")
GATE = Path(".github/workflows/architecture-gate.yml")
SUPPORT = Path("src/life_service/store_contract_support.py")
REPO = Path("src/life_service/store_memory_repository.py")
SUPPORT_NAMES = {"LifeShadowStoreError", "ProtectedPayloadRecord", "MemoryDeletionResult", "_revalidate_contract", "_parse_stored_contract"}
REMOVE_NAMES = SUPPORT_NAMES | {"_normalize_search_term", "_protected_payload_aad"}
STATIC_NAMES = {"_protected_payload_record_from_row", "_term_digests"}

def offsets(lines, start, end):
    return sum(map(len, lines[: start - 1])), sum(map(len, lines[:end]))

def start_line(node):
    return min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])

def segment(source, node):
    value = ast.get_source_segment(source, node)
    if value is None:
        raise RuntimeError(f"source segment missing for {getattr(node, 'name', '?')}")
    return value

def wrapper(node):
    copied = copy.deepcopy(node)
    is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)
    positional = [*node.args.posonlyargs, *node.args.args]
    if not is_static and positional and positional[0].arg == "self":
        positional = positional[1:]
    args = [ast.Name(id=a.arg, ctx=ast.Load()) for a in positional]
    if node.args.vararg:
        args.append(ast.Starred(value=ast.Name(id=node.args.vararg.arg, ctx=ast.Load()), ctx=ast.Load()))
    keywords = [ast.keyword(arg=a.arg, value=ast.Name(id=a.arg, ctx=ast.Load())) for a in node.args.kwonlyargs]
    if node.args.kwarg:
        keywords.append(ast.keyword(arg=None, value=ast.Name(id=node.args.kwarg.arg, ctx=ast.Load())))
    owner = ast.Name(id="LifeMemoryRepository", ctx=ast.Load()) if is_static else ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr="_memory_repository", ctx=ast.Load())
    call = ast.Call(func=ast.Attribute(value=owner, attr=node.name, ctx=ast.Load()), args=args, keywords=keywords)
    body = []
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
        body.append(copy.deepcopy(node.body[0]))
    body.append(ast.Return(value=call))
    copied.body = body
    ast.fix_missing_locations(copied)
    return textwrap.indent(ast.unparse(copied) + "\n", "    ")

def main():
    source = STORE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LifeShadowStore")
    methods = [n for n in store.body if isinstance(n, ast.FunctionDef)]
    names = [n.name for n in methods]
    first, last = names.index("_protected_payload_record_from_row"), names.index("list_memory_relations")
    target_names = names[first:last + 1] + ["delete_memory"]
    targets = {n.name: n for n in methods if n.name in target_names}
    if set(targets) != set(target_names):
        raise RuntimeError("target method cluster changed")
    top = {getattr(n, "name", ""): n for n in tree.body if getattr(n, "name", "") in REMOVE_NAMES}
    if set(top) != REMOVE_NAMES:
        raise RuntimeError("support node cluster changed")

    support_header = """\"\"\"Shared Life store contract validation and result types.\"\"\"
from __future__ import annotations

from dataclasses import dataclass
from contracts import MemoryAssertionV3, PrivacyDeletionTombstone, canonical_json_bytes
"""
    SUPPORT.write_text(
        support_header.rstrip() + "\n\n\n" +
        "\n\n\n".join(segment(source, top[n]) for n in [
            "LifeShadowStoreError", "ProtectedPayloadRecord", "MemoryDeletionResult",
            "_revalidate_contract", "_parse_stored_contract"
        ]) + "\n", encoding="utf-8"
    )

    repo_header = """\"\"\"Memory SSoT persistence on the Life store's single SQLite connection.\"\"\"
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import unicodedata
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from contracts import (
    CausalNodeV3, MemoryAssertionV3, MemoryDerivationV1, MemoryInvalidationRecord,
    MemoryParentRef, MemoryRelationV3, PrivacyDeletionTombstone,
    canonical_json_bytes, canonical_sha256, derive_promotion_key, retention_priority,
)
from contracts.world_understanding.memory_candidate import MemoryWorldCandidate
from .store_contract_support import (
    LifeShadowStoreError, MemoryDeletionResult, ProtectedPayloadRecord,
    _parse_stored_contract, _revalidate_contract,
)
"""
    helpers = [segment(source, top["_normalize_search_term"]), segment(source, top["_protected_payload_aad"])]
    repo_methods = [textwrap.indent(textwrap.dedent(segment(source, targets[name])), "    ") for name in target_names]
    repo_class = """class LifeMemoryRepository:
    \"\"\"Memory repository; connection lifecycle and schema stay outside.\"\"\"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

""" + "\n\n".join(repo_methods) + "\n"
    REPO.write_text(repo_header.rstrip() + "\n\n\n" + "\n\n\n".join(helpers) + "\n\n\n" + repo_class, encoding="utf-8")

    changes = []
    for name in REMOVE_NAMES:
        node = top[name]
        a, b = offsets(lines, start_line(node), node.end_lineno)
        changes.append((a, b, ""))
    for name in target_names:
        node = targets[name]
        a, b = offsets(lines, start_line(node), node.end_lineno)
        changes.append((a, b, wrapper(node)))

    import_anchor = "from .store_connection import open_life_shadow_sqlite\n"
    if source.count(import_anchor) != 1:
        raise RuntimeError("connection import anchor changed")
    import_replacement = import_anchor + """from .store_contract_support import (
    LifeShadowStoreError, MemoryDeletionResult, ProtectedPayloadRecord,
    _parse_stored_contract, _revalidate_contract,
)
from .store_memory_repository import LifeMemoryRepository, _normalize_search_term, _protected_payload_aad
"""
    pos = source.index(import_anchor)
    changes.append((pos, pos + len(import_anchor), import_replacement))
    init_anchor = "        self._connection = connection\n"
    if source.count(init_anchor) != 1:
        raise RuntimeError("store connection assignment anchor changed")
    pos = source.index(init_anchor)
    changes.append((pos, pos + len(init_anchor), init_anchor + "        self._memory_repository = LifeMemoryRepository(connection)\n"))

    patched = source
    for a, b, value in sorted(changes, reverse=True):
        patched = patched[:a] + value + patched[b:]
    STORE.write_text(patched, encoding="utf-8")

    gate = GATE.read_text(encoding="utf-8")
    test_anchor = "          python tests/test_life_store_p17_m3_02.py -v\n"
    if gate.count(test_anchor) != 2:
        raise RuntimeError("M3-02 gate anchor changed")
    gate = gate.replace(test_anchor,test_anchor+ "          python tests/test_life_store_p17_m3_03.py -v\n")
    old = "src/life_service/store.py src/life_service/store_connection.py src/life_service/store_schema.py app/life-service/runtime314/life_service/store.py app/life-service/runtime314/life_service/store_connection.py app/life-service/runtime314/life_service/store_schema.py"
    new = "src/life_service/store.py src/life_service/store_connection.py src/life_service/store_schema.py src/life_service/store_contract_support.py src/life_service/store_memory_repository.py app/life-service/runtime314/life_service/store.py app/life-service/runtime314/life_service/store_connection.py app/life-service/runtime314/life_service/store_schema.py app/life-service/runtime314/life_service/store_contract_support.py app/life-service/runtime314/life_service/store_memory_repository.py"
    if gate.count(old) != 2:
        raise RuntimeError("compile gate anchor changed")
    GATE.write_text(gate.replace(old, new), encoding="utf-8")
    print("P17-M3-03 candidate patched", len(target_names), "memory methods")

if __name__ == "__main__":
    main()
