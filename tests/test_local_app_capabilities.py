import csv
import hashlib
import json
import sqlite3

import pytest
from omni_body_skill.tools.omni_body_tool import BodyRuntime,BodyRuntimeConfig


@pytest.fixture
def runtime(tmp_path):
    return BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path),run_id="local-apps"))


def test_sqlite_full_export_import_backup_and_schema(runtime,tmp_path):
    db=tmp_path/"data.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("create table records(name text,value integer)")
        c.executemany("insert into records values(?,?)",[("中文"+str(i),i) for i in range(235)])
    read=runtime.run("sqlite.schema.read","data.sqlite",{})
    assert read["success"] and any(r["name"]=="records" for r in read["result"]["objects"])
    exported=runtime.run("sqlite.table.export_csv","data.sqlite",{"table":"records","output":"all.csv"})
    assert exported["success"] and exported["result"]["rows"]==235
    with (tmp_path/"all.csv").open(encoding="utf-8",newline="") as f:
        values=list(csv.reader(f));assert values[-1]==["中文234","234"] and len(values)==236
    (tmp_path/"more.csv").write_text("name,value\n新增,10\n",encoding="utf-8")
    imported=runtime.run("sqlite.table.import_csv","data.sqlite",{"table":"records","input":"more.csv"})
    assert imported["success"] and imported["result"]["inserted_rows"]==1
    backup=runtime.run("sqlite.backup.create","data.sqlite",{"output":"copy.sqlite"})
    assert backup["success"]
    with sqlite3.connect(tmp_path/"copy.sqlite") as c:
        assert c.execute("select count(*),sum(value) from records").fetchone()==(236,sum(range(235))+10)


def test_sqlite_partial_import_rolls_back_and_export_never_overwrites(runtime,tmp_path):
    db=tmp_path/"data.sqlite"
    with sqlite3.connect(db) as c:c.execute("create table items(value integer)")
    (tmp_path/"bad.csv").write_text("value\n7\n8,extra\n")
    result=runtime.run("sqlite.table.import_csv","data.sqlite",{"table":"items","input":"bad.csv"})
    assert not result["success"] and result["execution_state"]=="not_executed"
    with sqlite3.connect(db) as c:assert c.execute("select count(*) from items").fetchone()[0]==0
    (tmp_path/"keep.csv").write_text("keep me")
    result=runtime.run("sqlite.table.export_csv","data.sqlite",{"table":"items","output":"keep.csv"})
    assert not result["success"] and (tmp_path/"keep.csv").read_text()=="keep me"


def test_obsidian_all_actions_versions_and_real_bytes(runtime,tmp_path):
    made=runtime.run("obsidian.note.create","vault/甲.md",{"content":"# 原文\n金额 18"})
    assert made["success"]
    read=runtime.run("obsidian.note.read","vault/甲.md",{})
    assert read["result"]["text"]=="# 原文\n金额 18"
    sha=hashlib.sha256((tmp_path/"vault/甲.md").read_bytes()).hexdigest()
    changed=runtime.run("obsidian.note.update","vault/甲.md",{"content":"# 修订\n金额 19","expected_sha256":sha})
    assert changed["success"]
    conflict=runtime.run("obsidian.note.update","vault/甲.md",{"content":"stale","expected_sha256":sha})
    assert not conflict["success"] and "19" in (tmp_path/"vault/甲.md").read_text()
    sha2=hashlib.sha256((tmp_path/"vault/甲.md").read_bytes()).hexdigest()
    linked=runtime.run("obsidian.link.create","vault/甲.md",{"link":"乙","expected_sha256":sha2})
    assert linked["success"] and "[[乙]]" in (tmp_path/"vault/甲.md").read_text()
    listed=runtime.run("obsidian.vault.list","vault",{})
    assert listed["success"] and listed["result"]["total"]==1
    found=runtime.run("obsidian.search","vault",{"query":"金额"})
    assert found["success"] and found["result"]["items"][0]["line"]==2
    graph=runtime.run("obsidian.graph.export","vault",{"output":"graph.json"})
    assert graph["success"] and json.loads((tmp_path/"graph.json").read_text())["notes"][0]["references"]==["乙"]
    assert (tmp_path/".omni_backups/obsidian"/(sha+".md")).read_text()=="# 原文\n金额 18"
