from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src" / "life_service" / "store.py"
SCHEMA = ROOT / "src" / "life_service" / "store_schema.py"
GATE = ROOT / ".github" / "workflows" / "architecture-gate.yml"


def assigned_names(source: str) -> list[str]:
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return names


def method_span(cls: ast.ClassDef, name: str) -> tuple[ast.FunctionDef, int, int]:
    method = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    starts = [method.lineno] + [decorator.lineno for decorator in method.decorator_list]
    return method, min(starts), int(method.end_lineno)


def main() -> None:
    text = STORE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)

    version_nodes: dict[str, ast.Assign] = {}
    first_schema = None
    expected_tables = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        for name in names:
            if name in {"SHADOW_STORE_SCHEMA_VERSION", "SHADOW_STORE_APPLICATION_ID"}:
                version_nodes[name] = node
            if name == "_SCHEMA_SQL" and first_schema is None:
                first_schema = node
            if name == "_EXPECTED_TABLES":
                expected_tables = node

    if set(version_nodes) != {"SHADOW_STORE_SCHEMA_VERSION", "SHADOW_STORE_APPLICATION_ID"}:
        raise SystemExit("M3-02 version/application anchors are not unique")
    if first_schema is None or expected_tables is None:
        raise SystemExit("M3-02 schema block anchors are missing")

    schema_start = first_schema.lineno
    schema_end = int(expected_tables.end_lineno)
    schema_block = "".join(lines[schema_start - 1 : schema_end])
    schema_names = assigned_names(schema_block)
    if "_SCHEMA_SQL" not in schema_names or "_EXPECTED_TABLES" not in schema_names:
        raise SystemExit("M3-02 extracted schema block is incomplete")
    if not any(name.startswith("_P17_") for name in schema_names):
        raise SystemExit("M3-02 extracted schema block does not include migration 17")

    version_source = "".join(
        "".join(lines[node.lineno - 1 : int(node.end_lineno)])
        for _, node in sorted(version_nodes.items(), key=lambda item: item[1].lineno)
    )

    store_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LifeShadowStore"
    )
    initialize, init_start, _init_end = method_span(store_class, "_initialize")
    migrate, _migrate_start, migrate_end = method_span(store_class, "_migrate")
    if init_start >= migrate_end:
        raise SystemExit("M3-02 lifecycle method ordering is invalid")

    initialize_source = textwrap.dedent(
        "".join(lines[initialize.lineno - 1 : int(initialize.end_lineno)])
    )
    initialize_source = initialize_source.replace(
        "def _initialize(connection: sqlite3.Connection, *, now_ms: int) -> None:",
        "def initialize_life_shadow_schema(connection: sqlite3.Connection, *, now_ms: int) -> None:",
        1,
    )

    migrate_source = textwrap.dedent(
        "".join(lines[migrate.lineno - 1 : int(migrate.end_lineno)])
    )
    migrate_source = migrate_source.replace(
        "def _migrate(connection: sqlite3.Connection, *, now_ms: int) -> None:",
        "def migrate_life_shadow_schema(\n"
        "    connection: sqlite3.Connection,\n"
        "    *,\n"
        "    now_ms: int,\n"
        "    error_factory: LifeStoreSchemaErrorFactory,\n"
        ") -> None:",
        1,
    )
    migrate_source = migrate_source.replace("LifeShadowStoreError(", "error_factory(")
    if "LifeShadowStoreError" in migrate_source:
        raise SystemExit("M3-02 migration still owns the store exception type")

    schema_module = (
        '"""Schema and migration authority for the Life shadow SQLite store.\n\n'
        "This module owns schema SQL, migration identities/hashes, schema versions and\n"
        "schema initialization/migration execution. It does not own domain repositories,\n"
        "Life transactions, connection opening, health policy or the Store facade.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "import hashlib\n"
        "import sqlite3\n"
        "from collections.abc import Callable\n\n"
        + version_source
        + "\n"
        + schema_block
        + "\n\nLifeStoreSchemaErrorFactory = Callable[[str], Exception]\n\n\n"
        + initialize_source
        + "\n\n"
        + migrate_source
        + "\n\n__all__ = [\n"
        '    "LifeStoreSchemaErrorFactory",\n'
        '    "SHADOW_STORE_APPLICATION_ID",\n'
        '    "SHADOW_STORE_SCHEMA_VERSION",\n'
        '    "initialize_life_shadow_schema",\n'
        '    "migrate_life_shadow_schema",\n'
        "]\n"
    )
    ast.parse(schema_module)

    compatibility_names = [
        "SHADOW_STORE_APPLICATION_ID",
        "SHADOW_STORE_SCHEMA_VERSION",
        *schema_names,
    ]
    compatibility_names = list(dict.fromkeys(compatibility_names))
    import_block = (
        "from .store_schema import (\n"
        + "".join(f"    {name},\n" for name in compatibility_names)
        + "    initialize_life_shadow_schema,\n"
        + "    migrate_life_shadow_schema,\n"
        + ")\n"
    )

    wrapper_block = (
        "    @staticmethod\n"
        "    def _initialize(connection: sqlite3.Connection, *, now_ms: int) -> None:\n"
        "        initialize_life_shadow_schema(connection, now_ms=now_ms)\n\n"
        "    @staticmethod\n"
        "    def _migrate(connection: sqlite3.Connection, *, now_ms: int) -> None:\n"
        "        migrate_life_shadow_schema(\n"
        "            connection,\n"
        "            now_ms=now_ms,\n"
        "            error_factory=LifeShadowStoreError,\n"
        "        )\n"
    )

    edits: list[tuple[int, int, str]] = []
    edits.append((schema_start - 1, schema_end, ""))
    for node in version_nodes.values():
        edits.append((node.lineno - 1, int(node.end_lineno), ""))
    edits.append((init_start - 1, migrate_end, wrapper_block))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    store_text = "".join(lines)

    connection_import = "from .store_connection import open_life_shadow_sqlite\n"
    if store_text.count(connection_import) != 1:
        raise SystemExit("M3-02 store connection import anchor is not unique")
    store_text = store_text.replace(
        connection_import,
        connection_import
        + "\n# Compatibility re-exports; schema authority lives in store_schema.py.\n"
        + import_block,
        1,
    )
    ast.parse(store_text)

    if "CREATE TABLE schema_migrations" in store_text:
        raise SystemExit("M3-02 store.py still contains schema creation SQL")
    if "def migrate_life_shadow_schema" in store_text:
        raise SystemExit("M3-02 store.py accidentally owns migration implementation")
    if "error_factory=LifeShadowStoreError" not in store_text:
        raise SystemExit("M3-02 Store exception authority was not preserved")

    gate = GATE.read_text(encoding="utf-8")
    anchor = (
        "      - name: Run P17 M3-01 life store connection regression\n"
        "        run: python tests/test_life_store_p17_m3_01.py -v\n"
    )
    addition = (
        anchor
        + "\n      - name: Run P17 M3-02 life store schema regression\n"
        + "        run: python tests/test_life_store_p17_m3_02.py -v\n"
    )
    if gate.count(anchor) != 1:
        raise SystemExit("M3-02 architecture gate test anchor is not unique")
    gate = gate.replace(anchor, addition, 1)
    compile_old = "src/life_service/store.py src/life_service/store_connection.py"
    compile_new = "src/life_service/store.py src/life_service/store_connection.py src/life_service/store_schema.py"
    if gate.count(compile_old) != 1:
        raise SystemExit("M3-02 architecture gate compile anchor is not unique")
    gate = gate.replace(compile_old, compile_new, 1)

    STORE.write_text(store_text, encoding="utf-8")
    SCHEMA.write_text(schema_module, encoding="utf-8")
    GATE.write_text(gate, encoding="utf-8")
    print("P17-M3-02 candidate patched")


if __name__ == "__main__":
    main()
