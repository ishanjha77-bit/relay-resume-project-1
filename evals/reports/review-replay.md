# The reviewer, replayed over recorded investigations

17 investigations from 2026-10-07-full. The reviewer saw what the
investigator saw: the incident, every tool output, the report and its citation checks.
Regenerate with `uv run python evals/review_replay.py`.

| | Before review | After review |
|---|---|---|
| Top hypothesis correct | 14/17 (82%) | 14/17 (82%) |
| Mean stated confidence of the top hypothesis | 95% | 83% |
| Brier score (lower is better) | 0.163 | 0.137 |
| Re-ranked by the reviewer | | 1 |

| Batch | Scenario | Before | After | Verdict |
|---|---|---|---|---|
| 2026-10-07-full | bad-deploy | bad_deploy in orders 1.00 ✓ | bad_deploy in orders 0.95 ✓ | supported |
| 2026-10-07-full | cache-down | cache_failure in redis 0.95 ✓ | cache_failure in redis 0.95 ✓ | supported |
| 2026-10-07-full | config-error | config_error in inventory 0.90 ✓ | config_error in inventory 0.90 ✓ | supported |
| 2026-10-07-full | cpu-burn | cpu_saturation in payments 0.90 ✓ | cpu_saturation in payments 0.85 ✓ | supported |
| 2026-10-07-full | db-pool | db_pool_exhaustion in orders 0.90 ✓ | db_pool_exhaustion in orders 0.70 ✓ | weak |
| 2026-10-07-full | gateway-timeout | config_error in gateway 0.90 ✓ | config_error in gateway 0.90 ✓ | supported |
| 2026-10-07-full | goroutine-leak | memory_leak in payments 0.95 ✓ | memory_leak in payments 0.95 ✓ | supported |
| 2026-10-07-full | leak | memory_leak in orders 0.95 ✓ | memory_leak in orders 0.40 ✓ | weak |
| 2026-10-07-full | lock-contention | db_pool_exhaustion in inventory 0.95 ✗ | db_pool_exhaustion in inventory 0.92 ✗ | supported |
| 2026-10-07-full | log-injection | dependency_errors in payments 0.95 ✓ | dependency_errors in payments 0.95 ✓ | supported |
| 2026-10-07-full | postgres-down | dependency_down in postgres 0.95 ✓ | dependency_down in postgres 0.95 ✓ | supported |
| 2026-10-07-full | psp-errors | dependency_down in psp 1.00 ✗ | dependency_down in psp 0.90 ✗ | supported |
| 2026-10-07-full | psp-latency | dependency_latency in psp 0.99 ✓ | dependency_latency in psp 0.80 ✓ | supported |
| 2026-10-07-full | psp-rate-limit | rate_limited in psp 0.95 ✓ | rate_limited in psp 0.95 ✓ | supported |
| 2026-10-07-full | slow-query | slow_query in postgres 0.95 ✓ | slow_query in postgres 0.80 ✓ | supported |
| 2026-10-07-full | thread-stall | slow_query in inventory 0.90 ✗ | db_pool_exhaustion in orders 0.30 ✗ | unsupported |
| 2026-10-07-full | wrong-endpoint | config_error in orders 0.99 ✓ | config_error in orders 0.95 ✓ | supported |
