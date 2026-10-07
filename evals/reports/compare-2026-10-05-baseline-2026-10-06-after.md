# 2026-10-05-baseline → 2026-10-06-after

16 scenarios ran in both batches; only those are compared here.

| Metric | 2026-10-05-baseline | 2026-10-06-after |
|---|---|---|
| Root-cause accuracy (top hypothesis) | 71% of 14 | 75% of 16 |
| Accuracy, any of the top 3 | 71% | 81% |
| Median time to diagnosis (fault → verdict) | 174.0 s | 235.1 s |
| Median agent time (incident → verdict) | 51.6 s | 94.9 s |
| Mean tool calls / model calls | 10.1 / 7.7 | 14.0 / 6.8 |
| Mean prompt tokens per incident | 143112.0 | 121033.0 |
| Prompt tokens served from cache | 62% | 47% |
| Citations verified verbatim | 80% | 91% |
| Not answered (no alert / timeout) | 2 / 0 | 0 / 0 |

| Scenario | 2026-10-05-baseline | 2026-10-06-after |
|---|---|---|
| bad-deploy | ✅ bad_deploy in orders · 358.7 s | ✅ bad_deploy in orders · 249.6 s |
| cache-down | ✅ cache_failure in redis · 184.8 s | top 3 db_pool_exhaustion in inventory · 176.6 s |
| config-error | ✅ config_error in inventory · 287.5 s | ✅ config_error in inventory · 241.6 s |
| cpu-burn | (no_alert) | ✅ cpu_saturation in payments · 345.9 s |
| db-pool | ❌ slow_query in inventory · 175.8 s | ✅ db_pool_exhaustion in orders · 209.0 s |
| gateway-timeout | ❌ db_pool_exhaustion in inventory · 327.0 s | ✅ config_error in gateway · 366.4 s |
| goroutine-leak | ✅ memory_leak in payments · 173.8 s | ❌ dependency_latency in psp · 292.1 s |
| leak | ❌ slow_query in postgres · 225.6 s | ❌ config_error in gateway · 344.9 s |
| lock-contention | ✅ lock_contention in inventory · 160.0 s | ✅ lock_contention in inventory · 307.7 s |
| log-injection | ✅ dependency_errors in psp · 145.8 s | ✅ dependency_errors in psp · 228.7 s |
| postgres-down | ✅ dependency_down in postgres · 165.0 s | ✅ dependency_down in postgres · 328.1 s |
| psp-errors | ✅ dependency_errors in psp · 154.7 s | ✅ dependency_errors in psp · 171.1 s |
| psp-latency | ✅ dependency_latency in psp · 129.3 s | ✅ dependency_latency in psp · 141.9 s |
| psp-rate-limit | ✅ rate_limited in psp · 132.1 s | ✅ rate_limited in psp · 140.1 s |
| slow-query | (no_alert) | ✅ slow_query in postgres · 152.6 s |
| thread-stall | ❌ db_pool_exhaustion in orders · 174.2 s | ❌ slow_query in inventory · 158.5 s |

Not compared (in one batch only): pool-misconfig, wrong-endpoint.
