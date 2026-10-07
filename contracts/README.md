# Contracts

JSON Schemas for the messages that cross the Java/Python boundary. Both sides
test against these files, so a change on one side that breaks the other fails
CI instead of failing in production.

| Schema | Stream | Producer → consumer |
|--------|--------|---------------------|
| [incident-opened](schemas/incident-opened.schema.json) | `relay.incidents` | platform-api → agent-service |
| [agent-event](schemas/agent-event.schema.json) | `relay.agent-events` | agent-service → platform-api |
| [approval-decision](schemas/approval-decision.schema.json) | `relay.approvals` | platform-api → agent-service |
| [incident-resolved](schemas/incident-resolved.schema.json) | `relay.resolved` | platform-api → agent-service (postmortem writer) |

[examples/](examples) holds one valid message of each kind; both sides' tests
validate them against the schemas, and validate what they actually produce.

Both streams are Redis Streams with consumer groups: delivery is
at-least-once, so consumers are idempotent (`incident.id` for incidents,
`(run_id, seq)` for agent events, `approval_id` for decisions). An incident message that can never be
processed (malformed, or failing on every attempt) is moved to
`relay.incidents.dead` with the reason.

Evolving a contract: add optional fields freely; anything else gets a new
`type` (e.g. `incident.opened.v2`) and both versions are consumed until the
old producer is gone.
