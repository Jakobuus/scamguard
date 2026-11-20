# Scam drill

`scenarios.json` holds 22 scam callers and 12 legitimate callers. Run them against the guard:

```
uv run python drill/run.py                              # in-process classifier (fake keywords or SIE)
uv run python drill/run.py --url http://localhost:8000  # through POST /analyze
```

A scam counts as caught when the guard would hold the call and ping the real person. Scams that make
no identity claim (`claims_identity: false`: customs fee, lottery, crypto, charity) are listed apart:
the agent handles them by never paying, not by pinging. The same scenarios are the scam-caller
personas to load into Firedrill.

First run (fake classifier): 16/18 identity scams caught, 1/12 false alarms. Misses: the
"two thousand dollars in gift cards" line (plural "dollars"/"gift cards" don't match the whole-word
keywords) and the bank-code call (no money word).
