# 2. Java owns state and people; Python owns reasoning

- Status: accepted
- Date: 2026-10-04

## Context

Relay needs two very different kinds of software:

- **A system of record**: incidents, approvals, audit trail, authentication,
  RBAC, WebSocket fan-out to the console. This wants transactions, mature
  security, and boring reliability.
- **A reasoning engine**: a graph of LLM agents with retries, step budgets,
  human-approval interrupts, and an evaluation harness. The agent ecosystem
  (LangGraph, MCP SDKs, eval tooling) is strongest in Python.

## Decision

- **platform-api (Spring Boot)** owns every domain table (`incidents`,
  `agent_steps`, `hypotheses`, `approvals`, `eval_runs`) and every human
  interaction: alert intake, auth, approval decisions, WebSocket streaming.
- **agent-service (Python, LangGraph)** owns the investigation graph and its
  checkpoints. It never writes domain tables directly; it emits events
  (`step.recorded`, `hypotheses.updated`, `approval.requested`, …) to a Redis
  Stream that platform-api consumes, persists and fans out.
- Agents touch the outside world **only** through MCP servers.
- The contract between the two sides is a set of JSON Schemas in
  `contracts/schemas/`, validated by tests on both sides.

## Consequences

- One clear owner per table; no two services racing to write the same rows.
- The agent service can be restarted, replayed or swapped (another framework,
  another language) without touching the system of record.
- Cost: one extra network hop and two languages to maintain, plus
  at-least-once event delivery that consumers must handle idempotently.
