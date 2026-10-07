# 11. Knowledge base: hybrid search over runbooks and postmortems in pgvector

- Status: accepted
- Date: 2026-10-05

## Context

An on-call engineer doesn't start from zero: the team's runbooks say what to
check for each failure mode, and the postmortems of past incidents say how
similar incidents ended. The investigator should have the same memory, and it
should grow: each resolved incident's postmortem should help the next
investigation.

The queries are unusual for search. Half are exact strings copied from
telemetry: an error message ("orders-db - Connection is not available, request
timed out after 2000ms"), a metric name (`hikaricp_connections_pending`).
Keyword search is good at those, and embeddings blur them. The other half
describe a symptom in other words ("payments keeps getting killed" for a
runbook that says OOMKilled). Embeddings are good at those, and keyword search
misses them.

Constraints: Relay must run free ([ADR 10](0010-free-by-default-gemini.md)),
so no paid embedding API, and every free Gemini request is better spent on
the investigation itself. Postgres with pgvector is already in the stack
([ADR 2](0002-java-owns-state-python-owns-reasoning.md)).

## Decision

**One table, `knowledge_chunks`, in Relay's Postgres** (Flyway V3, owned by
the platform like the rest of the schema). Documents are chunked by `##`
section. A runbook's Symptoms, Diagnose and Mitigate sections are natural
retrieval units: small enough to rank precisely, whole enough to act on. Each
row has a generated, weighted `tsvector` (title and section A, body B) with a
GIN index, and a `vector(384)` with an HNSW index (cosine).

**Hybrid search in one SQL statement, fused with Reciprocal Rank Fusion.** A
lexical retriever (Postgres full text, matching any of the query's terms,
ranked by cover density) and a semantic retriever (pgvector cosine distance)
each rank 40 candidates. RRF scores each chunk as Σ 1/(60 + rank), which needs
no calibration between two incomparable scores. Each result reports which
retriever found it (`matched_by`).

**Embeddings run locally**: BAAI/bge-small-en-v1.5 (33M parameters, 384
dimensions) on ONNX Runtime via fastembed, about 25 ms per query on one CPU
thread. The model is baked into the image and loaded with `HF_HUB_OFFLINE=1`,
so the server needs no network and no key. Until the model loads, search
falls back to keywords only, and says so.

**A separate MCP server, `runbooks`, owns indexing.** On startup it syncs the
bundled runbooks (by checksum: unchanged sections keep their vectors) under an
advisory lock. Every 10 s it embeds whatever has no vector yet, using
`FOR UPDATE SKIP LOCKED`, so replicas can share the work. The platform writes a
published postmortem with `embedding = NULL`, and within one interval it is
searchable. The platform needs no model, and the agent needs no database
access. The tools (`search`, `read_document`, `list_documents`) are read-only.

**Knowledge is a guide, never evidence.** A runbook says how things usually
fail, not what happened this time. The agent wraps knowledge-base results as
`trust="reference"`, and the citation verifier marks a citation of one
`reference_not_evidence`, so a hypothesis supported only by a runbook is never
"verified". Reference outputs aren't scanned for prompt injection: the runbook
about injection quotes attack phrases on purpose, and flagging it would raise
false alarms.

## Measured

`make eval-retrieval` runs 53 labelled queries (log lines in the services'
real formats, metric observations, symptom descriptions, hypotheses) against
the 17 runbooks (86 chunks) in a throwaway pgvector container:

| Retriever | Recall@1 | Recall@3 | MRR@10 |
|---|---|---|---|
| Keywords (Postgres full text) | 74% | 94% | 0.841 |
| Vectors (bge-small) | 79% | 91% | 0.861 |
| Vectors, with BGE's query instruction | 83% | 91% | 0.885 |
| Hybrid (RRF) | 81% | 94% | 0.885 |
| **Hybrid, with BGE's query instruction** | **85%** | **96%** | **0.902** |

Hybrid search with BGE's query instruction (the server's configuration) puts
the right runbook first for 85% of queries, against 74% for keywords alone.
Keywords hold up on verbatim log lines; vectors win on paraphrased symptoms;
the fusion is never worse than the better of the two by style. Full report:
`evals/reports/retrieval.md`.

## Consequences

- One more pod, about 470 MiB of memory once the model has embedded a batch.
  The image is about 700 MB: ONNX Runtime and the model are about 130 MB of
  that.
- The same person wrote the runbooks, the queries and the chaos scenarios, so
  the numbers above are an optimistic upper bound. The runbooks are organised
  by symptom and component, the way a team would write them. None names a
  chaos scenario or says which fault is injected.
- The agent's eval batches before and after this change measure what the
  knowledge base adds to diagnosis, not just to retrieval.
- The server connects as the platform's database role. A role restricted to
  `knowledge_chunks` would be tighter; see the threat model.
