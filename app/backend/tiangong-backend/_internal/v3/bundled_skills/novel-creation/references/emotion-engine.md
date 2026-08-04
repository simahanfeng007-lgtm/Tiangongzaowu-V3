# Emotional Energy and Set Pieces

## Deposit or withdrawal

```json
{
  "account_id": "emotion.family.hero-father",
  "kind": "deposit|withdraw",
  "related_event_ids": ["evt.001"],
  "evidence_terms": ["exact term in prose"],
  "factors": {
    "attachment": 0.0,
    "duration": 0.0,
    "sacrifice": 0.0,
    "expectation": 0.0,
    "foreshadow": 0.0,
    "importance": 0.0,
    "leakage": 0.0,
    "repetition": 0.0
  }
}
```

All factors range from 0 to 1. Ground them in the accepted chapter. The tool caps chapter growth, subtracts leakage and repetition, enforces cooldown, and creates a pending scene trigger at the configured threshold.

## Scene candidate

Provide 2-3 candidates containing:

- title and payoff type;
- target chapter inside the trigger window;
- core character choice;
- irreversible cost;
- permanent consequence;
- event closures and callbacks;
- normalized shock and tear scores.

Required scores are `surprise`, `retrospective_inevitability`, `consequence`, `character_relevance`, `causality_support`, `attachment`, `agency`, `irreversibility`, `callbacks`, and `restraint`.

The tool selects only a candidate scoring at least 70. A selected design becomes a hard chapter-card input. Payoff must consume emotional energy and leave a lasting factual consequence.

