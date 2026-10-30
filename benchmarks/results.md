# Benchmark results

## Run 20261015-074304 UTC

Model: `gemini-3.6-flash`

| Task | Solved | Agent status | Steps | Est. cost (USD) |
|---|---|---|---|---|
| 04_median_bug | yes | finished | 11 | 0.1035 |
| **Total** | **1/1** | | **11** | **0.1035** |

## Run 20261015-081856 UTC

Model: `gemini-3-flash-preview`

| Task | Solved | Agent status | Steps | Est. cost (USD) |
|---|---|---|---|---|
| 01_slugify | yes | finished | 12 | 0.0352 |
| **Total** | **1/1** | | **12** | **0.0352** |

## Run 20261030-102831 UTC (aborted: API quota and credit)

Planned: all 8 tasks x 3 attempts, on Gemini and on Claude, at commit `973c035`, with sandbox image `python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159` and pytest `9.1.1` (Windows 11 host, Python 3.12.10, Docker 29.8.2).

- **Gemini (`gemini-3.6-flash`)**: one attempt ran. `01_slugify` attempt 1 was solved (agent status `finished`, 10 steps, 39,219 input / 6,004 output / 22,552 cached tokens, est. $0.1072 at list price; re-graded with the hardened grader: solved). Every later request failed with `429 RESOURCE_EXHAUSTED` (free-tier daily quota), so the run was stopped.
- **Claude (`claude-sonnet-5-5`)**: no attempt ran. Every request was rejected with "credit balance is too low" before the agent started.
- Baseline: the untouched starting code of all 8 tasks failed the hidden tests.

No success rate is reported for this run: one completed attempt is not a sample.
