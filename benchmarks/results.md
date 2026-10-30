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

## Run 20261030-113534 UTC (1 attempt(s) per task)

Environment:

- provider: `ollama`
- model: `qwen3:4b`
- effort: `medium`
- max_steps: `40`
- max_cost_usd: `2.0`
- sandbox_image: `python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`
- pytest: `9.1.1`
- devpilot_commit: `210151c (uncommitted changes)`
- host_python: `3.12.10`
- host_os: `Windows-11-10.0.26300-SP0`

| Task | Hidden tests | Solved without agent | Solved | Success rate | Avg steps | Avg est. cost (USD) | Agent statuses | Errors (excluded) |
|---|---|---|---|---|---|---|---|---|
| 04_median_bug | 6 | no | 0/1 | 0% | 3.0 | 0.0000 | stopped x1 | 0 |
| **Total** | | **0/1** | **0/1** | **0%** | **3.0** | **0.0000** | | **0** |

Total estimated cost of this run: $0.0000

## Run 20261030-114806 UTC (1 attempt(s) per task)

Environment:

- provider: `ollama`
- model: `qwen3:8b`
- effort: `low`
- max_steps: `40`
- max_cost_usd: `2.0`
- sandbox_image: `python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`
- pytest: `9.1.1`
- devpilot_commit: `210151c (uncommitted changes)`
- host_python: `3.12.10`
- host_os: `Windows-11-10.0.26300-SP0`

| Task | Hidden tests | Solved without agent | Solved | Success rate | Avg steps | Avg est. cost (USD) | Agent statuses | Errors (excluded) |
|---|---|---|---|---|---|---|---|---|
| 04_median_bug | 6 | no | 1/1 | 100% | 14.0 | 0.0000 | finished x1 | 0 |
| **Total** | | **0/1** | **1/1** | **100%** | **14.0** | **0.0000** | | **0** |

Total estimated cost of this run: $0.0000

## Run 20261030-115103 UTC (3 attempt(s) per task)

Environment:

- provider: `ollama`
- model: `qwen3:8b`
- effort: `low`
- max_steps: `40`
- max_cost_usd: `2.0`
- sandbox_image: `python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`
- pytest: `9.1.1`
- devpilot_commit: `210151c (uncommitted changes)`
- host_python: `3.12.10`
- host_os: `Windows-11-10.0.26300-SP0`

| Task | Hidden tests | Solved without agent | Solved | Success rate | Avg steps | Avg est. cost (USD) | Agent statuses | Errors (excluded) |
|---|---|---|---|---|---|---|---|---|
| 01_slugify | 7 | no | 0/3 | 0% | 21.0 | 0.0000 | max_steps x1, stopped x1, unverified x1 | 0 |
| 02_roman_numerals | 4 | no | 0/3 | 0% | 29.7 | 0.0000 | finished x1, max_steps x2 | 0 |
| 03_lru_cache | 7 | no | 2/3 | 67% | 19.3 | 0.0000 | finished x2, max_steps x1 | 0 |
| 04_median_bug | 6 | no | 1/2 | 50% | 12.0 | 0.0000 | error x1, finished x2 | 1 |
| 05_word_frequency | 6 | no | 1/3 | 33% | 8.7 | 0.0000 | finished x3 | 0 |
| 06_merge_intervals | 8 | no | 0/2 | 0% | 9.0 | 0.0000 | error x1, finished x1, unverified x1 | 1 |
| 07_sales_report | 8 | no | 0/3 | 0% | 20.3 | 0.0000 | finished x2, max_steps x1 | 0 |
| 08_token_bucket | 8 | no | 0/3 | 0% | 40.0 | 0.0000 | max_steps x3 | 0 |
| **Total** | | **0/8** | **4/22** | **18%** | **20.9** | **0.0000** | | **2** |

Total estimated cost of this run: $0.0000

Notes on this run (local model on an 8 GB laptop GPU, partly offloaded to CPU; about 3.5 hours):

- The two excluded errors: `04_median_bug` attempt 1 crashed when Windows refused to replace the run-log file while another process (the repository is in a OneDrive folder) held it open; that was a DevPilot bug, and `RunLog` now retries the replace. `06_merge_intervals` attempt 3 was aborted by Ollama with "token repeat limit reached", meaning the model got stuck repeating itself; that is a model failure.
- Success rate by how the errors are counted: 4/22 (18.2%) under the standard rule, which leaves out errored attempts; 4/23 (17.4%) counting the model's loop as a failure; 4/24 (16.7%) also counting the run-log crash.
- Solved: `03_lru_cache` attempts 1 and 2, `04_median_bug` attempt 2, `05_word_frequency` attempt 2.
- Seven attempts called `finish` with their own tests passing but failed the hidden tests, so they are not counted as solved: `02_roman_numerals` attempt 2, `04_median_bug` attempt 3, `05_word_frequency` attempts 1 and 3, `06_merge_intervals` attempt 1, `07_sales_report` attempts 2 and 3. The verification gate is only as strong as the visible tests.
