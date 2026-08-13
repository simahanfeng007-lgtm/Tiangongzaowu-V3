from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZONG = ROOT / "app/backend/tiangong-backend/v3/zongdiaodu.py"
OWNERSHIP = ROOT / "source-ownership.json"
GATE = ROOT / ".github/workflows/architecture-gate.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 anchor, got {count}")
    return text.replace(old, new, 1)


def replace_top_level_function(text: str, name: str, replacement: str) -> str:
    tree = ast.parse(text)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(nodes) != 1:
        raise SystemExit(f"function {name}: expected 1, got {len(nodes)}")
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: node.lineno - 1])
    end = sum(len(line) for line in lines[: node.end_lineno])
    clean = replacement.strip("\n") + "\n\n"
    return text[:start] + clean + text[end:]


text = ZONG.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from .tool_result_contract import (\n    normalize_tool_result,\n",
    "from .tool_result_contract import (\n",
    "remove direct normalize import",
)
turn_import = '''from .runtime_turn_orchestration import (
    PreparedStep,
    TurnLoopState,
    coordinate_parallel_steps,
    evaluate_turn_budget,
)
'''
boundary_import = turn_import + '''from .runtime_tool_result_boundary import (
    attach_tool_result_contract,
    canonical_tool_result,
    contract_observed_write,
    decide_simple_chain_completion,
    project_tool_dispatch,
    tool_write_verified,
)
'''
text = replace_once(text, turn_import, boundary_import, "boundary import")
text = replace_once(
    text,
    "    decide_task_contract_completion,\n",
    "",
    "remove direct completion import",
)

replacements = {
    "_tool_dispatch_with_result": ''
def _tool_dispatch_with_result(meta: dict[str, Any] | None, result: Any) -> dict[str, Any] | None:
    return project_tool_dispatch(meta, result)
''',
    "_tool_result_with_contract": '''
def _tool_result_with_contract(
    tool_name: str,
    result: Any,
    *,
    source_native_id: str = "",
) -> Any:
    return attach_tool_result_contract(
        tool_name,
        result,
        source_native_id=source_native_id,
    )
''',
    "_contract_observed_write": ''
def _contract_observed_write(contract: dict[str, Any] | None) -> bool:
    return contract_observed_write(contract)
''',
    "_tool_write_verified": '''
def _tool_write_verified(tool_name: str, result: Any) -> bool:
    return tool_write_verified(tool_name, result)
''',
    "_simple_chain_life_completion_gate": '''
def _simple_chain_life_completion_gate(
    user_message: str,
    quality_history: list[dict[str, Any]],
    generated_attachments: list[dict[str, str]],
    *,
    task_contract: dict[str, Any] | None,
    required_read_paths: list[str] | None = None,
    final_reply: Any = None,
    task_obligations: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool, str, list[str]]:
    return decide_simple_chain_completion(
        user_message,
        quality_history,
        generated_attachments,
        task_contract=task_contract,
        evidence_check=_simple_chain_evidence_check,
        required_read_paths=required_read_paths,
        final_reply=final_reply,
        task_obligations=task_obligations,
    )
''',
}
for function_name, replacement in replacements.items():
    text = replace_top_level_function(text, function_name, replacement)

text = replace_once(
    text,
    "    contract = normalize_tool_result(tool_name, tool_result)\n",
    "    contract = canonical_tool_result(tool_name, tool_result)\n",
    "quality canonical result",
)

for forbidden in ("normalize_tool_result(", "decide_task_contract_completion("):
    if forbidden in text:
        raise SystemExit(f"forbidden direct call remains: {forbidden}")
if "self._jineng_zhixing(" not in text:
    raise SystemExit("tool executor authority moved or missing")
ast.parse(text)
ZONG.write_text(text, encoding="utf-8")

ownership = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
mapping = next(item for item in ownership["mappings"] if item.get("id") == "v3-backend-main")
roots = mapping["boundary_policy"]["implementation_roots"]
if "runtime_tool_result_boundary.py" not in roots:
    marker = "runtime_turn_orchestration.py"
    if marker not in roots:
        raise SystemExit("ownership anchor missing")
    roots.insert(roots.index(marker) + 1, "runtime_tool_result_boundary.py")
OWNERSHIP.write_text(json.dumps(ownership, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

gate = GATE.read_text(encoding="utf-8")
gate = replace_once(
    gate,
    "      - name: Compile P17 M2 V3 seams\n",
    "      - name: Run P17 M2-03 tool-result continuation regression\n        run: python tests/test_zongdiaodu_p17_m2_03.py -v\n\n      - name: Compile P17 M2 V3seams\n",
    "gate M2-03 test",
)
gate = replace_once(
    gate,
    "app/backend/tiangong-backend/v3/runtime_turn_orchestration.py\n",
    "app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py\n",
    "gate M2-03 compile",
)
GATE.write_text(gate, encoding="utf-8")

print("P17-M2-03 candidate patched")
