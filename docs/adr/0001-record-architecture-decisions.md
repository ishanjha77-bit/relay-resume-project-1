# 1. Record architecture decisions

- Status: accepted
- Date: 2026-10-04

## Context

Relay spans five languages and a dozen components. Decisions made early
(what owns state, how agents reach systems, how faults are injected) shape
everything after them, and the reasoning behind them is easy to lose.

## Decision

Significant decisions are recorded as short ADRs in `docs/adr/`, numbered in
order, using the format *Context → Decision → Consequences*. An ADR is never
edited to reverse it; a new ADR supersedes it.

## Consequences

- Reviewers and interviewers can see *why*, not just *what*.
- Writing the "Consequences" section forces the trade-off to be stated.

## Index

| # | Decision |
|---|----------|
| 1 | Record architecture decisions |
| 2 | Java owns state and people; Python owns reasoning |
| 3 | Alert on symptoms, never on causes |
| 4 | Inject faults with runtime flags, not redeploys |
| 5 | Jaeger v2 for traces; service graph from the Collector |
| 6 | Alert intake: HMAC for signed sources, bearer token for Alertmanager |
| 7 | LLM provider interface and an append-only agent loop |
| 8 | MCP tool servers: least privilege, shaped output, untrusted data |
| 9 | The event pipeline: transactional outbox, Redis Streams, idempotent consumers |
| 10 | Free by default: Gemini's free tier behind the provider interface |
