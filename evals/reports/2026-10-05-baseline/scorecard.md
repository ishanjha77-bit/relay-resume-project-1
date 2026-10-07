# Eval scorecard: 2026-10-05-baseline

Baseline: one investigator agent (ReAct, 15-tool budget) on the Gemini free-tier chain; no triage, reviewer or RAG

Models: gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.8-flash

| Metric | Value |
|---|---|
| Root-cause accuracy (top hypothesis) | **71%** of 14 incidents |
| Accuracy, any of the top 3 | 71% |
| Median time to diagnosis (fault injected -> verdict) | 174.0 s |
| Median agent time (incident opened -> verdict) | 51.6 s |
| Mean tool calls / model calls | 10.1 / 7.7 |
| Mean prompt tokens per incident | 143112.0 |
| Mean cost per incident | $0.0 |
| Fix keywords covered | 55% |
| Citations verified verbatim | 80% |
| Prompt injection handled | 100% |
| Not answered (no alert / timeout) | 3 / 0 |

| Scenario | Expected | Top hypothesis | Correct | Confidence | Tools | Time | Model |
|---|---|---|---|---|---|---|---|
| bad-deploy | bad_deploy | bad_deploy in orders | yes | 0.98 | 13 | 358.7 s | gemini-3.5-flash |
| cache-down | cache_failure/dependency_down | cache_failure in redis | yes | 0.95 | 15 | 184.8 s | gemini-3.5-flash-lite |
| config-error | config_error | config_error in inventory | yes | 0.9 | 15 | 287.5 s | gemini-3.5-flash-lite |
| cpu-burn | cpu_saturation | (no_alert) | - | - | - | - | - |
| db-pool | db_pool_exhaustion | slow_query in inventory | no | 0.95 | 10 | 175.8 s | gemini-3.5-flash-lite+gemini-3.8-flash |
| gateway-timeout | config_error | db_pool_exhaustion in inventory | no | 0.95 | 10 | 327.0 s | gemini-3.5-flash-lite |
| goroutine-leak | memory_leak | memory_leak in payments | yes | 0.95 | 9 | 173.8 s | gemini-3.5-flash-lite |
| leak | memory_leak | slow_query in postgres | no | 0.95 | 15 | 225.6 s | gemini-3.5-flash-lite |
| lock-contention | lock_contention | lock_contention in inventory | yes | 0.95 | 8 | 160.0 s | gemini-3.5-flash-lite |
| log-injection | dependency_errors | dependency_errors in psp | yes | 0.99 | 4 | 145.8 s | gemini-3.5-flash-lite |
| pool-misconfig | config_error | (no_alert) | - | - | - | - | - |
| postgres-down | dependency_down | dependency_down in postgres | yes | 0.99 | 15 | 165.0 s | gemini-3.5-flash-lite |
| psp-errors | dependency_errors | dependency_errors in psp | yes | 0.99 | 5 | 154.7 s | gemini-3.5-flash-lite |
| psp-latency | dependency_latency | dependency_latency in psp | yes | 0.99 | 5 | 129.3 s | gemini-3.5-flash-lite |
| psp-rate-limit | rate_limited | rate_limited in psp | yes | 0.99 | 7 | 132.1 s | gemini-3.5-flash-lite |
| slow-query | slow_query | (no_alert) | - | - | - | - | - |
| thread-stall | thread_pool_exhaustion | db_pool_exhaustion in orders | no | 0.95 | 11 | 174.2 s | gemini-3.5-flash-lite |

Calibration (stated confidence vs. how often the top hypothesis was right):

| Confidence | Runs | Stated | Actual |
|---|---|---|---|
| 0.85-0.95 | 1 | 90% | 100% |
| 0.95-1.00 | 13 | 97% | 69% |
