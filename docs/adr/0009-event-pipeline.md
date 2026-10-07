# 9. The event pipeline: transactional outbox, Redis Streams, idempotent consumers

- Status: accepted
- Date: 2026-10-05

## Context

An alert has to become exactly one investigation, and every step of that
investigation has to reach the database and the console, even when a process
crashes halfway or Redis restarts. Two services are involved
([ADR 2](0002-java-owns-state-python-owns-reasoning.md)): platform-api owns
incidents in Postgres, and agent-service runs investigations. The failure
modes to design for:

- platform-api commits an incident and dies before telling anyone;
- a message is delivered twice (a retried publish, a reclaimed delivery);
- agent-service dies mid-investigation;
- a message can never be processed (malformed, or a bug).

## Decision

**Plain JDBC, not JPA.** The platform's writes are a handful of targeted
statements: an insert that returns nothing when it loses a race
(`ON CONFLICT ... DO NOTHING RETURNING`), keyset pagination,
`FOR UPDATE SKIP LOCKED`. `JdbcClient` keeps the SQL visible, and there is no
session cache to reason about.

**Transactional outbox.** The incident row and its `incident.opened` event are
written in the same transaction. A poller ships unpublished outbox rows to
Redis every 250 ms, claiming them with `FOR UPDATE SKIP LOCKED` so replicas
never publish the same row twice. A crash between `XADD` and marking the row
published publishes it again later: at-least-once, never lost.

**Redis Streams with consumer groups**, one stream per direction
(`relay.incidents`, `relay.agent-events`). They give acknowledgements, a
pending list per consumer, and claiming of stranded messages, with no extra
broker to run. Redis persists them with AOF and refuses writes rather than
evicting when it is full. JSON Schemas in `contracts/` define both messages,
and both services test against them ([contracts/README](../../contracts/README.md)).

**Every consumer is idempotent.**
- *platform-api* stores agent steps under `UNIQUE (run_id, seq)`, and a
  duplicate changes nothing. `seq` advances only after Redis accepts an
  event, so a retried publish repeats the same key.
- *agent-service* takes a lease on the incident before investigating
  (`SET relay:agent:incident:<id> running:<run> NX EX`). A copy that arrives
  while a run is in flight, or after it finished (`done:<run>`, kept for 7
  days), is acknowledged without a second run.

**When to acknowledge.** An incident message is acknowledged when its run has
finished, whatever the verdict. A failed investigation ended in `run.failed`,
which the console shows. Rerunning it automatically would only double the
spend; a person can rerun it (`POST /runs`). Only a run that could not report,
because Redis failed, stays pending. So does the message of a worker that
died.

**Reclaiming and dead letters.** platform-api reclaims events idle for 60 s.
agent-service reclaims incident messages only after they have been idle longer
than any run can take (the run timeout plus a margin), so a live
investigation is never stolen. A restarted consumer first re-reads its own
pending list. After a fixed number of deliveries a message is dead-lettered:
logged and acknowledged on the platform side, moved with its reason to
`relay.incidents.dead` on the agent side. Malformed messages are
dead-lettered at once.

**One incident per problem, not per notification.** Alertmanager sends the
whole group again whenever any alert in it changes. So a group has at most one
unresolved incident, and new notifications attach to it. A *failed*
investigation counts as unresolved: it hands the incident to a human. It does
not close it. (The first end-to-end run, with the API out of credits,
showed what happens otherwise: each later notification opened a new incident
and another doomed investigation.) After a person resolves an incident, alerts
that are still firing are often just the fix draining out of a 2-minute rate
window. An alert *episode* (fingerprint + start time) that a recently resolved
incident already covered therefore opens nothing. Only a new alert, or one
that resolved and fired again, opens a new incident.

**Backpressure.** agent-service reads only as many messages as it has free
run slots (2 by default), so waiting incidents stay in Redis where any
replica can take them. They don't sit in one process's memory.

## Consequences

- Redis can be restarted, and either service killed at any point, without
  losing an incident or double-counting a step. Tests cover redelivery,
  concurrent duplicates, crash recovery, reclaiming and dead-lettering
  (Testcontainers on the Java side, an in-memory Redis on the Python side).
- Delivery is at-least-once, not exactly-once. One race is left open: Redis
  can fail after an investigation finished but before it is marked done. The
  rerun then repeats that investigation's spend. That is rare, and costs money,
  not correctness.
- A cancelled run (a deploy that outlasts the shutdown grace period) is picked
  up again only after the reclaim delay of about 11 minutes. Rollouts
  therefore give running investigations 4 minutes to finish.
- Traces don't yet cross the stream boundary. Carrying W3C `traceparent` in
  the envelopes is planned with the observability work in week 8.
