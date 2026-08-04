"""Completion classification constants for the recovered source runtime."""
NOVEL_COMPLETION_PATTERN = r"(?:写|撰写|创作|编写|续写).{0,40}(?:小说|网文|故事章节)|(?:小说|网文|故事章节).{0,40}(?:写|撰写|创作|编写|续写)|(?:write|draft|create).{0,40}(?:novel|fiction chapter)"

def is_novel_request(text: str) -> bool:
    import re
    return bool(re.search(NOVEL_COMPLETION_PATTERN, str(text or ""), re.I))
