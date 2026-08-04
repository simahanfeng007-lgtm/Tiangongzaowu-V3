# v3.5 Model Adapter Test Report

## Automated tests

```text
.........................                                                [100%]
25 passed in 0.28s
```

Exit code: `0`

## install_v3 dry run

```json
{
  "ok": true,
  "dry_run": true,
  "actions": [
    {
      "step": "copy_package",
      "from": "/mnt/data/tiangong_omni_body_v3_5_model_adapters",
      "to": "<USER_HOME>/.tiangong/v3/omni_body_skill"
    },
    {
      "step": "copy_tool",
      "from": "/mnt/data/tiangong_omni_body_v3_5_model_adapters/api/v1/v3/tools/omni_body.py",
      "to": "/api/v1/v3/tools/omni_body.py"
    },
    {
      "step": "merge_nengli",
      "file": "<USER_HOME>/.tiangong/v3/nengli_zhuche.json",
      "incoming_count": 47
    }
  ]
}
```

## Simulation

- Providers tested: DeepSeek, MiniMax, GLM, MiMo, GPT, Kimi, Doubao
- Roundtrip profiles tested: 9
- Roundtrip success: true
- Total runtime actions after v3.5: 751

See `examples/v3_5_model_adapters/v35_model_adapter_simulation_log.json`.

## Boundary

The adapter layer only translates model-native tool-call formats to CanonicalOmniCall and renders tool results back. It does not plan tasks, execute Skills, or generate final deliverables. Complex work still starts with `skill.route`.
