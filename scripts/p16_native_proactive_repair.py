from pathlib import Path

path = Path("src/total_gateway/embedded_backend.py")
text = path.read_text(encoding="utf-8")
old = (
    "        # P16 keeps only a derived in-process continuity projection. It\n"
    "        is not a second conversation store and is rebuilt by each real\n"
    "        user turn. Proactive compose reads it without persisting a fake\n"
    "        user message.\n"
)
new = (
    "        # P16 keeps only a derived in-process continuity projection. It\n"
    "        # is not a second conversation store and is rebuilt by each real\n"
    "        # user turn. Proactive compose reads it without persisting a fake\n"
    "        # user message.\n"
)
count = text.count(old)
if count != 1:
    raise RuntimeError(f"P16 backend comment repair expected 1 match, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("P16 backend comment repaired")
