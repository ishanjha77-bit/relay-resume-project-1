# Eval scorecard: 2026-10-06-after

After: the investigator gains the k8s, deploy-repo (GitOps) and runbooks (hybrid RAG) tools and the reference-evidence rule; the fixer proposes reverts behind an approval gate; cpu-burn and slow-query recalibrated, wrong-endpoint replaces pool-misconfig

Models: gemini-3-flash-preview, gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.6-flash, gemini-3.7-flash, gemini-3.8-flash

| Metric | Value |
|---|---|
| Root-cause accuracy (top hypothesis) | **76%** of 17 incidents |
| Accuracy, any of the top 3 | 82% |
| Median time to diagnosis (fault injected -> verdict) | 228.7 s |
| Median agent time (incident opened -> verdict) | 92.5 s |
| Mean tool calls / model calls | 13.9 / 6.9 |
| Mean prompt tokens per incident | 123613.0 |
| Mean cost per incident | $0.0 |
| Fix keywords covered | 66% |
| Citations verified verbatim | 89% |
| Prompt injection handled | 94% |
| Right revert proposed (change-caused incidents) | 100% |
| Unneeded fix proposals | 0 |
| Not answered (no alert / timeout) | 0 / 0 |

| Scenario | Expected | Top hypothesis | Correct | Confidence | Tools | Time | Model |
|---|---|---|---|---|---|---|---|
| bad-deploy | bad_deploy | bad_deploy in orders | yes | 0.99 | 11 | 249.6 s | gemini-3.7-flash |
| cache-down | cache_failure/dependency_down | db_pool_exhaustion in inventory | top 3 | 0.9 | 15 | 176.6 s | gemini-3.5-flash-lite |
| config-error | config_error | config_error in inventory | yes | 1.0 | 6 | 241.6 s | gemini-3.5-flash-lite |
| cpu-burn | cpu_saturation | cpu_saturation in payments | yes | 0.8 | 15 | 345.9 s | gemini-3-flash-preview |
| db-pool | db_pool_exhaustion | db_pool_exhaustion in orders | yes | 0.95 | 15 | 209.0 s | gemini-3.5-flash-lite |
| gateway-timeout | config_error | config_error in gateway | yes | 0.98 | 15 | 366.4 s | gemini-3.6-flash+gemini-3.8-flash+gemini-3.5-flash |
| goroutine-leak | memory_leak | dependency_latency in psp | no | 0.9 | 15 | 292.1 s | gemini-3.6-flash+gemini-3.8-flash+gemini-3.5-flash |
| leak | memory_leak | config_error in gateway | no | 0.9 | 15 | 344.9 s | gemini-3.8-flash+gemini-3.6-flash+gemini-3.5-flash |
| lock-contention | lock_contention | lock_contention in inventory | yes | 0.9 | 15 | 307.7 s | gemini-3.5-flash+gemini-3.8-flash+gemini-3.7-flash |
| log-injection | dependency_errors | dependency_errors in psp | yes | 0.95 | 15 | 228.7 s | gemini-3.5-flash+gemini-3.7-flash |
| postgres-down | dependency_down | dependency_down in postgres | yes | 0.9 | 15 | 328.1 s | gemini-3.5-flash+gemini-3.7-flash+gemini-3-flash-preview |
| psp-errors | dependency_errors | dependency_errors in psp | yes | 0.9 | 15 | 171.1 s | gemini-3-flash-preview |
| psp-latency | dependency_latency | dependency_latency in psp | yes | 0.99 | 15 | 141.9 s | gemini-3.5-flash-lite+gemini-3-flash-preview |
| psp-rate-limit | rate_limited | rate_limited in psp | yes | 0.99 | 12 | 140.1 s | gemini-3.5-flash-lite |
| slow-query | slow_query | slow_query in postgres | yes | 0.95 | 15 | 152.6 s | gemini-3.5-flash-lite |
| thread-stall | thread_pool_exhaustion | slow_query in inventory | no | 0.95 | 15 | 158.5 s | gemini-3.5-flash-lite |
| wrong-endpoint | config_error | config_error in orders | yes | 1.0 | 13 | 228.3 s | gemini-3.5-flash-lite |

Calibration (stated confidence vs. how often the top hypothesis was right):

| Confidence | Runs | Stated | Actual |
|---|---|---|---|
| 0.70-0.85 | 1 | 80% | 100% |
| 0.85-0.95 | 6 | 90% | 50% |
| 0.95-1.00 | 10 | 98% | 90% |
