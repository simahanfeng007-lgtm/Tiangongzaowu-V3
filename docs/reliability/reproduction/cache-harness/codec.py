"""Lossless experiment-only representation; product dictionary stays unchanged."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent / "source"
MISSING = {"$absent": True}
DERIVED = (("id",), ("summary",), ("runtime", "summary"))

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def sha(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()

def flatten(value, path=()):
    result = {}
    for key, item in value.items():
        if isinstance(item, dict) and item:
            result.update(flatten(item, path + (key,)))
        else:
            result[path + (key,)] = item
    return result

def rebuild(flat):
    result = {}
    for path, value in flat.items():
        if value == MISSING:
            continue
        node = result
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = deepcopy(value)
    return result

def build():
    root = SOURCE / "dictionaries"
    names = ["tools/catalog.json", "tools/schemas.json", "tools/apps.json", "skills/catalog.json",
             "execution-profiles.json", "host-protocol.json"]
    names += sorted(p.relative_to(root).as_posix() for p in (root / "skills/methods").glob("*.json"))
    source = {p: json.loads((root / p).read_text()) for p in names}
    original = source["tools/catalog.json"]
    rows = {name: flatten(row) for name, row in original["tools"].items()}
    assert all(row[("id",)] == name and row[("summary",)] == row[("runtime", "summary")] for name, row in rows.items())
    columns = sorted(set().union(*(set(row) for row in rows.values())) - set(DERIVED))
    defaults = [json.loads(Counter(dump(row.get(key, MISSING)) for row in rows.values()).most_common(1)[0][0]) for key in columns]
    pools = {}
    for i, key in enumerate(columns):
        values = [dump(row.get(key, MISSING)) for row in rows.values()]
        unique = sorted(set(values))
        if sum(map(len, values)) > sum(map(len, unique)) + len(values) * 4:
            pools[str(i)] = [json.loads(x) for x in unique]
    records = []
    for ordinal, (name, row) in enumerate(sorted(rows.items())):
        patch = {}
        for i, key in enumerate(columns):
            value = row.get(key, MISSING)
            if value == defaults[i]:
                continue
            patch[str(i)] = pools[str(i)].index(value) if str(i) in pools else value
        records.append([f"T{ordinal:03X}", name, row[("summary",)], patch])
    encoded = {
        "format": "tiangong.experiment.full-short.v1",
        "dictionary_release": json.loads((root / "registry/release.json").read_text()),
        "catalog_metadata": {k: v for k, v in original.items() if k != "tools"},
        "columns": columns, "defaults": defaults, "pools": pools, "rows": records,
        "other_primary_documents": {k: v for k, v in source.items() if k != "tools/catalog.json"},
    }
    restored = decode_dictionary(encoded)
    assert restored == source
    proof = {"source_baseline_commit": "a372dbc7fb7d5218c07d96c33d21d29da4f48694", "source_candidate_tree_sha256": json.loads((BASE.parent / "candidate-source.json").read_text())["candidate_tree_sha256"], "primary_document_count": len(names),
             "tool_count": len(records), "all_primary_values_roundtrip_equal": restored == source,
             "source_document_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in names},
             "source_semantic_sha256": sha(source), "encoded_sha256": sha(encoded), "encoded_utf8_bytes": len(dump(encoded).encode()),
             "encoded_characters": len(dump(encoded)),
             "excluded_derived_files": ["registry/*", "migration.json"],
             "exclusion_reason": "Generated registry views and migration bookkeeping are not primary load_dictionary inputs; their published hashes remain pinned in dictionary_release."}
    return encoded, proof

def decode_dictionary(encoded):
    tools = {}
    for code, name, summary, patch in encoded["rows"]:
        values = deepcopy(encoded["defaults"])
        for key, value in patch.items():
            values[int(key)] = deepcopy(encoded["pools"][key][value] if key in encoded["pools"] else value)
        flat = {tuple(path): value for path, value in zip(encoded["columns"], values, strict=True)}
        flat[("id",)] = name
        flat[("summary",)] = summary
        flat[("runtime", "summary")] = summary
        tools[name] = rebuild(flat)
    return {"tools/catalog.json": {**encoded["catalog_metadata"], "tools": tools}, **encoded["other_primary_documents"]}

def decode_invocation(envelope, mapping):
    """Decode only action identifiers and schema-discovery targets, never content."""
    value = deepcopy(envelope)
    changed = []
    def action(row):
        if not isinstance(row, dict):
            return
        old = row.get("action")
        if isinstance(old, str) and old in mapping:
            row["action"] = mapping[old]
            changed.append([old, mapping[old]])
        # Unknown identifiers deliberately survive for the native rejection path.
        if row.get("action") == "system.action_schema" and isinstance(row.get("target"), str) and row.get("target") in mapping:
            old = row["target"]
            row["target"] = mapping[old]
            changed.append([old, mapping[old]])
    action(value)
    composition = value.get("composition") if isinstance(value, dict) else None
    if isinstance(composition, dict) and isinstance(composition.get("tools"), list):
        for tool in composition["tools"]:
            if isinstance(tool, dict) and isinstance(tool.get("actions"), list):
                for row in tool["actions"]:
                    action(row)
    return value, changed

def context(encoded):
    return ("[RECONSTRUCTED_FULL_SHORT_DICTIONARY_V1]\n"
        "这是当前发布字典的完整、无损短码表示，不含预置业务 Skill。继续按原规则生成 Tool 与 Skill、经原 Gateway 执行。"
        "本轮组合 tools[].actions[].action 必须使用 rows 第一列短码（例如 T000），不要使用长动作名；宿主只把短码还原成原动作名称，权限和参数完全不变。"
        "rows 每行为 [短码,原动作名,完整说明,字段差异]。columns 是字段路径；defaults 是各列默认值；差异键为列序号。"
        "若该列在 pools 中，差异值是 pools 对应数组的下标，否则为字面值。{\"$absent\":true} 表示该字段不存在。"
        "id 等于原动作名；summary 与 runtime.summary 均等于第三列。其余字段可以逐项无损还原。"
        "other_primary_documents 给出完整参数/结果契约、应用目录、空 Skill 目录、执行预算、host 协议及通用方法源。"
        "implemented=false 的能力仍不能执行；完整目录不增加权限。可以查询 system.action_schema，target 可用短码。"
        "短码仅用于 action 和 schema 查询 target，文件名、正文、参数内数据与 Tool/Skill ID 原样保留。"
        "不必复述目录，直接根据任务选取能力并生成组合。\n" + dump(encoded))

if __name__ == "__main__":
    BASE.mkdir(exist_ok=True)
    encoded, proof = build()
    (BASE / "full-short-dictionary.json").write_text(dump(encoded))
    (BASE / "codec-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    (BASE / "dictionary-context.txt").write_text(context(encoded))
    print(json.dumps(proof, ensure_ascii=False, indent=2))
