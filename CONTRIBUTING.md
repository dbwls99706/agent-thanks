# Contributing

Thanks for helping make AI-assisted open-source acknowledgment more accurate
and less noisy.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Detection changes

A detector should be conservative. Pull requests that add a new high-confidence
signal should include:

- A positive test showing substantive use.
- A negative test showing a nearby but non-meaningful reference.
- A clear evidence message suitable for human review.
- No hidden network calls or account mutations during `demo`, `scan`, `review`,
  or `export`.

New account mutations must preserve the per-repository interactive-confirmation
invariant and provide a reversal path where the platform supports one. Star
mutations must never gain an unattended, bulk, or piped-confirmation path.

## Pull requests

Keep changes focused and include tests. Explain false-positive and
false-negative tradeoffs for detection logic.
