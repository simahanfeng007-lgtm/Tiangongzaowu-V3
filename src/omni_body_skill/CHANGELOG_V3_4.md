# CHANGELOG v3.4

## Added
- Professional application adapter layer: `35` actions.
- App health/probe/matrix actions.
- Native bridge script generation for Playwright, Office, WPS, Adobe, Blender.
- Request-pack generation for Microsoft Graph, Figma, Canva, Feishu, Enterprise WeChat.
- Controlled Git/Docker/SQLite actions.
- Four new Skill Router skills: professional app bridge, browser automation, Office/WPS bridge, Adobe/Blender bridge.

## Kept
- One v3 tool only: `omni_body`.
- `skill.route` returns Skill only; model executes workflow.
- QC/repair/package loop remains model-driven.

## Test
- pytest: 20 passed.
- install_v3.py --dry-run: ok=true.
- simulation: 18 calls / 18 success / 0 failed.
