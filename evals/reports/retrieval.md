# Runbook retrieval

53 labelled queries against 17 runbooks (86 chunks).
Model BAAI/bge-small-en-v1.5 (384 dimensions, ONNX Runtime on CPU), Postgres 18 with pgvector 0.8.7,
Reciprocal Rank Fusion with k=60. A query is a hit at k when a chunk of a runbook that
answers it is among the first k results. Regenerate with `make eval-retrieval`.

| Retriever | Recall@1 | Recall@3 | MRR@10 | Search p50 |
|---|---|---|---|---|
| Keywords (Postgres full text) | 74% | 94% | 0.841 | 4.0 ms |
| Vectors (bge-small) | 79% | 91% | 0.860 | 3.3 ms |
| Vectors, with BGE's query instruction | 83% | 91% | 0.885 | 3.0 ms |
| Hybrid (RRF of both) | 81% | 94% | 0.885 | 4.2 ms |
| Hybrid, with BGE's query instruction | 85% | 96% | 0.902 | 3.9 ms |

Indexing embedded every chunk in 18.9 s; embedding one query takes 24.3 ms (p50), on one CPU thread.

## Recall@3 by query style

| Style | Queries | Keywords (Postgres full text) | Vectors (bge-small) | Vectors, with BGE's query instruction | Hybrid (RRF of both) | Hybrid, with BGE's query instruction |
|---|---|---|---|---|---|---|
| hypothesis | 6 | 100% | 100% | 100% | 100% | 100% |
| log | 18 | 94% | 89% | 89% | 94% | 94% |
| metric | 11 | 100% | 100% | 100% | 100% | 100% |
| symptom | 18 | 89% | 83% | 83% | 89% | 94% |

## Misses of the best retriever (Hybrid, with BGE's query instruction): not in the top 3

- “which dependency makes checkout slow” (symptom): wanted payment-provider, triage-high-latency; got go-memory-goroutines, postgres-slow-queries, go-memory-goroutines (rank 5)
- “java.net.UnknownHostException: postgres-primary” (log): wanted config-change, pod-crashloop; got postgres-unavailable, postgres-unavailable, postgres-unavailable (rank 10)

## Limits

- The same person wrote the runbooks and the queries, so the wording overlaps more than it
  would with real responders. The log-style queries reuse the services' real log formats.
- 17 runbooks is a small corpus: rankings get harder as postmortems accumulate.
