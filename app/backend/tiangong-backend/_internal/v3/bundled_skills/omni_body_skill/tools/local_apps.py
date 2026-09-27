"""Concrete local application operations through the original BodyRuntime.

Obsidian operations affect vault Markdown bytes, not the desktop UI. SQLite
operations use real transactions and reopen exported files before reporting.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time


LOCAL_APP_ACTIONS = {
    "sqlite.schema.read": {"risk": "A0", "implemented": True, "summary": "Read the real SQLite schema without writes."},
    "sqlite.table.export_csv": {"risk": "A2", "implemented": True, "summary": "Export every row of one SQLite table to a fresh CSV; report total rows and input version."},
    "sqlite.table.import_csv": {"risk": "A2", "implemented": True, "summary": "Append a rectangular CSV to an existing table in one transaction; malformed input rolls back."},
    "sqlite.backup.create": {"risk": "A2", "implemented": True, "summary": "Create a consistent SQLite online backup to a fresh file and verify integrity."},
    "obsidian.vault.list": {"risk": "A0", "implemented": True, "summary": "List Markdown notes in an authorized local vault with explicit pagination."},
    "obsidian.note.create": {"risk": "A2", "implemented": True, "summary": "Create a new Markdown note; refuses to overwrite an existing note."},
    "obsidian.note.read": {"risk": "A0", "implemented": True, "summary": "Read exact Markdown note bytes as text with hash and character range."},
    "obsidian.note.update": {"risk": "A2", "implemented": True, "summary": "Replace a note only at the expected SHA256 version, retaining an original snapshot."},
    "obsidian.link.create": {"risk": "A2", "implemented": True, "summary": "Append a wiki-link to a note at an expected version; returns actual new bytes."},
    "obsidian.graph.export": {"risk": "A2", "implemented": True, "summary": "Export Markdown wiki-link references, including unresolved links, to a fresh JSON file."},
    "obsidian.search": {"risk": "A0", "implemented": True, "summary": "Search local Markdown note text literally and paginate results without changing notes."},
}


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fresh(path, writer):
    if path.exists() or path.is_symlink(): raise FileExistsError("output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tiangong-", dir=path.parent)
    os.close(fd)
    temp = Path(name)
    try:
        writer(temp)
        with temp.open("rb") as f: os.fsync(f.fileno())
        # Atomic no-clobber publication. A concurrent creator wins; no replace.
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _identifier(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("SQLite identifier required")
    return '"' + name.replace('"', '""') + '"'


def _sqlite(runtime, action, target, args):
    database = runtime._resolve(target, must_exist=True)
    if not database.is_file(): raise ValueError("SQLite database file required")
    writing = action == "sqlite.table.import_csv"
    deadline = time.monotonic() + max(1, min(120, float(args.get("timeout_seconds", 30))))
    def check_deadline(*_):
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite operation deadline exceeded")
    connection = sqlite3.connect(str(database) if writing else database.as_uri() + "?mode=ro", uri=not writing, timeout=5)
    connection.enable_load_extension(False)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    committed = False
    published = False
    output = None
    try:
        connection.execute("BEGIN IMMEDIATE" if writing else "BEGIN")
        if action == "sqlite.schema.read":
            rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall()
            result = {"objects": [dict(zip(("type","name","table","sql"), row)) for row in rows], "complete": True}
        elif action == "sqlite.backup.create":
            output = runtime._resolve(args["output"])
            def write_backup(temp):
                with sqlite3.connect(temp) as destination:
                    connection.backup(destination, pages=128, progress=check_deadline)
                    destination.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                    if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("backup integrity check failed")
            _fresh(output, write_backup)
            published = True
            result = {"backup": runtime._file_evidence(output), "integrity_check": "ok"}
        elif action == "sqlite.table.export_csv":
            output = runtime._resolve(args["output"])
            cursor = connection.execute("SELECT * FROM " + _identifier(args.get("table")))
            columns = [column[0] for column in cursor.description]
            count = 0
            def write_csv(temp):
                nonlocal count
                with temp.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream); writer.writerow(columns)
                    for row in cursor:
                        check_deadline()
                        if any(isinstance(v, bytes) for v in row):
                            raise ValueError("CSV cannot faithfully encode SQLite BLOB; use a backup or explicit query conversion")
                        writer.writerow(row); count += 1
            _fresh(output, write_csv)
            published = True
            with output.open(encoding="utf-8", newline="") as stream:
                reread = sum(1 for _ in csv.reader(stream)) - 1
            if reread != count: raise ValueError("CSV readback count mismatch")
            result = {"rows": count, "columns": columns, "complete": True, "output": runtime._file_evidence(output),
                      "null_encoding": "empty_field", "value_types": "CSV text; null and empty string are not distinguishable"}
        else:
            source = runtime._resolve(args["input"], must_exist=True)
            if source.stat().st_size > 50 * 1024 * 1024: raise ValueError("CSV input exceeds 50 MiB")
            with source.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                columns = next(reader, None)
                if not columns or len(set(columns)) != len(columns): raise ValueError("unique CSV header required")
                query = "INSERT INTO " + _identifier(args.get("table")) + "(" + ",".join(_identifier(v) for v in columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")"
                count = 0
                for row in reader:
                    check_deadline()
                    if len(row) != len(columns): raise ValueError("nonrectangular CSV at record " + str(count+2))
                    connection.execute(query, row); count += 1
            connection.commit(); committed = True
            result = {"inserted_rows": count, "transaction": "committed", "input": runtime._file_evidence(source)}
        return {"success": True, "result": result, "evidence": runtime._file_evidence(database),
                "execution_state": "completed", "observation_scope": "sqlite_transaction_or_read_snapshot"}
    except Exception as exc:
        connection.rollback()
        unknown = committed or published
        return {"success": False, "error": "local_app.outcome_unknown" if unknown else "local_app.failed",
                "message": str(exc), "execution_state": "unknown" if unknown else "not_executed",
                "ambiguous_effect": unknown, "reconciliation_required": unknown}
    finally:
        connection.close()


def _notes(runtime, root):
    if not root.is_dir(): raise ValueError("vault directory required")
    for file in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in file.relative_to(root).parts): continue
        verified = runtime._resolve(runtime._rel(file), must_exist=True)
        if verified.is_file(): yield verified


def _obsidian(runtime, action, target, args):
    path = runtime._resolve(target, must_exist=action != "obsidian.note.create")
    if action in {"obsidian.vault.list", "obsidian.search", "obsidian.graph.export"}:
        notes = list(_notes(runtime, path))
        rows = []
        query = args.get("query")
        if action == "obsidian.search" and (not isinstance(query, str) or not query): raise ValueError("literal query required")
        for note in notes:
            if note.stat().st_size > 5 * 1024 * 1024: raise ValueError("note exceeds 5 MiB")
            text = note.read_text(encoding="utf-8")
            rel = note.relative_to(path).as_posix()
            if action == "obsidian.search":
                for i, line in enumerate(text.splitlines(), 1):
                    if query in line: rows.append({"note": rel, "line": i, "excerpt": line[:2000], "truncated": len(line)>2000})
            elif action == "obsidian.graph.export":
                rows.append({"note": rel, "sha256": _hash(note), "references": re.findall(r"\[\[([^\]\n]+)\]\]", text)})
            else: rows.append({"note": rel, "sha256": _hash(note), "bytes": note.stat().st_size})
        if action == "obsidian.graph.export":
            output = runtime._resolve(args["output"])
            data = {"notes": rows, "scope": "wiki_link_syntax_only; includes unresolved references"}
            _fresh(output, lambda p: p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8"))
            return {"success": True, "result": data, "evidence": runtime._file_evidence(output)}
        start = max(0,int(args.get("offset",0)));limit=max(1,min(200,int(args.get("limit",50))))
        return {"success": True,"result":{"items": rows[start:start+limit],"total":len(rows),"offset":start,
            "next_offset":start+limit if start+limit<len(rows) else None,"observation_scope":"local_vault_files_not_desktop_ui"}}
    if path.suffix.lower() != ".md": raise ValueError("Markdown note path required")
    if action == "obsidian.note.create":
        content=args.get("content")
        if not isinstance(content,str) or len(content.encode("utf-8"))>5*1024*1024: raise ValueError("content must be text of at most 5 MiB")
        _fresh(path,lambda p:p.write_text(content,encoding="utf-8"))
    elif action in {"obsidian.note.update","obsidian.link.create"}:
        if _hash(path)!=args.get("expected_sha256"): raise ValueError("note version conflict; reread before editing")
        content=args.get("content")
        if action=="obsidian.link.create":
            link=args.get("link")
            if not isinstance(link,str) or not link or any(c in link for c in "[]\r\n"):
                raise ValueError("single wiki-link target required")
            content=path.read_text(encoding="utf-8")+"\n[["+link+"]]\n"
        if not isinstance(content,str) or len(content.encode("utf-8"))>5*1024*1024: raise ValueError("content must be text of at most 5 MiB")
        backup=runtime._resolve(".omni_backups/obsidian/"+_hash(path)+".md")
        if not backup.exists(): _fresh(backup,lambda p:p.write_bytes(path.read_bytes()))
        # The original runtime's workspace lock owns local mutation serialization.
        fd,name=tempfile.mkstemp(prefix=".tiangong-note-",dir=path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                f.write(content);f.flush();os.fsync(f.fileno())
            if _hash(path)!=args.get("expected_sha256"): raise ValueError("note changed while preparing replacement")
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
    if path.stat().st_size>5*1024*1024: raise ValueError("note exceeds 5 MiB")
    text=path.read_text(encoding="utf-8");start=max(0,int(args.get("start",0)));limit=max(1,min(50000,int(args.get("limit",12000))))
    return {"success":True,"result":{"text":text[start:start+limit],"start":start,"total_chars":len(text),
        "truncated":start>0 or start+limit<len(text),"observation_scope":"local_markdown_bytes"},"evidence":runtime._file_evidence(path)}


def handle_local_app(runtime,action,target,args):
    if action.startswith("sqlite."): return _sqlite(runtime,action,target,args)
    with runtime._workspace_lock:
        affected=runtime._resolve(args.get("output") if action=="obsidian.graph.export" else target)
        before=_hash(affected) if affected.is_file() else None
        try:
            return _obsidian(runtime,action,target,args)
        except Exception as exc:
            after=_hash(affected) if affected.is_file() else None
            unknown=action in {"obsidian.note.create","obsidian.note.update","obsidian.link.create","obsidian.graph.export"} and before!=after
            return {"success":False,"error":"local_app.outcome_unknown" if unknown else "local_app.failed",
                    "message":str(exc),"execution_state":"unknown" if unknown else "not_executed",
                    "ambiguous_effect":unknown,"reconciliation_required":unknown}
