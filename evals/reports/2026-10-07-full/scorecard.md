# Eval scorecard: 2026-10-07-full

Full system: triage (Flash-Lite; knowledge-base search and the service graph's blast radius) before the investigator, the reviewer after it, and deploy-repo changes undone before the onset marked undone_by. Knowledge base: the 17 runbooks, no postmortems.

Models: gemini-3-flash-preview, gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.6-flash, gemini-3.7-flash, gemini-3.8-flash

| Metric | Value |
|---|---|
| Root-cause accuracy (top hypothesis) | **82%** of 17 incidents |
| Accuracy, any of the top 3 | 82% |
| Median time to diagnosis (fault injected -> verdict) | 228.6 s |
| Median agent time (incident opened -> verdict) | 78.1 s |
| Mean tool calls / model calls | 12.5 / 8.9 |
| Mean prompt tokens per incident | 134812.0 |
| Mean cost per incident | $0.0 |
| Fix keywords covered | 66% |
| Citations verified verbatim | 77% |
| Prompt injection handled | 100% |
| Right revert proposed (change-caused incidents) | 75% |
| Unneeded fix proposals | 0 |
| Not answered (no alert / timeout) | 0 / 0 |

| Scenario | Expected | Top hypothesis | Correct | Confidence | Tools | Time | Model |
|---|---|---|---|---|---|---|---|
| bad-deploy | bad_deploy | bad_deploy in orders | yes | 1.0 | 6 | 192.5 s | gemini-3.5-flash-lite |
| cache-down | cache_failure/dependency_down | cache_failure in redis | yes | 0.95 | 15 | 362.5 s | gemini-3.6-flash+gemini-3.7-flash+gemini-3.5-flash-lite |
| config-error | config_error | config_error in inventory | yes | 0.9 | 15 | 287.7 s | gemini-3-flash-preview+gemini-3.5-flash-lite+gemini-3.8-flash |
| cpu-burn | cpu_saturation | cpu_saturation in payments | yes | 0.8 | 15 | 588.3 s | gemini-3-flash-preview+gemini-3.7-flash+gemini-3.5-flash-lite+gemini-3.5-flash |
| db-pool | db_pool_exhaustion | db_pool_exhaustion in orders | yes | 0.9 | 15 | 546.5 s | gemini-3-flash-preview+gemini-3.7-flash+gemini-3.5-flash+gemini-3.5-flash-lite |
| gateway-timeout | config_error | config_error in gateway | yes | 0.9 | 15 | 268.0 s | gemini-3-flash-preview+gemini-3.5-flash-lite+gemini-3.5-flash |
| goroutine-leak | memory_leak | memory_leak in payments | yes | 0.95 | 15 | 193.5 s | gemini-3-flash-preview+gemini-3.5-flash-lite |
| leak | memory_leak | memory_leak in orders | yes | 0.1 | 15 | 254.0 s | gemini-3.5-flash-lite |
| lock-contention | lock_contention | db_pool_exhaustion in inventory | no | 0.95 | 15 | 282.7 s | gemini-3.5-flash-lite |
| log-injection | dependency_errors | dependency_errors in payments | yes | 0.95 | 14 | 164.5 s | gemini-3.5-flash-lite |
| postgres-down | dependency_down | dependency_down in postgres | yes | 0.95 | 15 | 228.6 s | gemini-3.5-flash-lite |
| psp-errors | dependency_errors | dependency_down in psp | no | 0.9 | 6 | 145.5 s | gemini-3.5-flash-lite |
| psp-latency | dependency_latency | dependency_latency in psp | yes | 0.85 | 7 | 163.8 s | gemini-3.5-flash-lite |
| psp-rate-limit | rate_limited | rate_limited in psp | yes | 0.95 | 8 | 151.6 s | gemini-3.5-flash-lite |
| slow-query | slow_query | slow_query in postgres | yes | 0.75 | 15 | 195.4 s | gemini-3.5-flash-lite |
| thread-stall | thread_pool_exhaustion | slow_query in inventory | no | 0.6 | 15 | 199.4 s | gemini-3.5-flash-lite |
| wrong-endpoint | config_error | config_error in orders | yes | 0.0 | 7 | 301.2 s | gemini-3.5-flash-lite |

Calibration (stated confidence vs. how often the top hypothesis was right):

| Confidence | Runs | Stated | Actual |
|---|---|---|---|
| 0.00-0.50 | 2 | 5% | 100% |
| 0.50-0.70 | 1 | 60% | 0% |
| 0.70-0.85 | 2 | 78% | 100% |
| 0.85-0.95 | 5 | 89% | 80% |
| 0.95-1.00 | 7 | 96% | 86% |
