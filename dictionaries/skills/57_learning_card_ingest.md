# Learning Card Ingest

Use this skill only when the user explicitly asks the system to learn material, for example:

- 学一下这个
- 学习这个内容
- 把下面内容做成能力
- 把这个流程沉淀成 skill
- 以后遇到这种情况按这个流程处理

Do not use this skill for ordinary chat, summaries, explanations, or questions about learning.

## Required Action

Only call `omni_body` when the host has supplied a verified learning intent token. Otherwise, use the server learning-card API path and do not fabricate `user_text`.

Call `omni_body` with:

```json
{
  "action": "learning.ingest",
  "args": {
    "user_text": "<original user request>",
    "material_text": "<material to learn, if provided inline>",
    "material_path": "<file path, if provided>",
    "desired_scope": "skill",
    "allow_network": false,
    "host_verified_intent_token": "<host token>"
  }
}
```

This action only creates a pending learning card. It must not compile a skill, activate a skill, register a tool, or release a tool.

After the tool returns, report the `card_id` and say that the card is waiting for user confirmation.
