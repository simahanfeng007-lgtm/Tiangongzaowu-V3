"""Strict dependency detector for tool-result placeholders."""
TOOL_RESULT_DEPENDENCY_PATTERN = r"\$tool(?:\.[a-z0-9_.-]+)?|\{\{\s*(?:\$tool|tool_result|previous_result)(?:[.\s][^{}]*)?\}\}|previous[_ -]?result|待返回|上一步返回"

def has_tool_result_dependency(value: str) -> bool:
    import re
    return bool(re.search(TOOL_RESULT_DEPENDENCY_PATTERN, str(value or "").lower()))
