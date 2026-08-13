from __future__ import annotations

from pathlib import Path
import base64
import os

source = base64.b64decode(
    Path("scripts/p17-m2-02-candidate-patch-original.b64").read_text(encoding="ascii")
).decode("utf-8")

old = '''old_guard = \'\'\'                    guard_count = repeat_observation_counts.get(guard_key, 0) + 1
                    repeat_observation_counts[guard_key] = guard_count
\'\'\'
if text.count(old_guard) != 3:
    raise SystemExit(f"guard repeat anchors: {text.count(old_guard)}")
text = text.replace(old_guard, \'\'\'                    guard_count = turn_loop.bump_repeat(guard_key)
\'\'\')
'''
new = '''guard_pattern = re.compile(
    r"(?P<indent>^[ \\t]+)guard_count = repeat_observation_counts\\.get\\(guard_key, 0\\) \\+ 1\\n"
    r"(?P=indent)repeat_observation_counts\\[guard_key\\] = guard_count\\n",
    re.MULTILINE,
)
text, guard_replacements = guard_pattern.subn(
    lambda match: f"{match.group('indent')}guard_count = turn_loop.bump_repeat(guard_key)\\n",
    text,
)
if guard_replacements != 3:
    raise SystemExit(f"guard repeat anchors: {guard_replacements}")
'''
if source.count(old) != 1:
    raise SystemExit(f"runner patch anchor count={source.count(old)}")
source = source.replace(old, new, 1)
exec(compile(source, "p17_m2_02_patch.py", "exec"), {"__name__": "__main__", "os": os})
