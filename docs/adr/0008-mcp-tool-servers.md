# 8. MCP tool servers: least privilege, shaped output, untrusted data

- Status: accepted
- Date: 2026-10-04

## Context

Agents reach the outside world only through tools. Every tool is a privilege
(what can the agent read or change?), a cost (every byte returned is re-read
as input tokens on every later turn) and an attack surface (logs contain text
written by users and upstream providers).

## Decision

**One MCP server per system**, each a separate deployable on Streamable HTTP
(stateless, JSON responses — horizontally scalable, no session affinity):
logs (Loki), metrics (Prometheus), GitHub, Kubernetes (read-only), runbooks.

**Authentication.** Every server is an OAuth 2.1 protected resource as the MCP
spec requires for HTTP: unauthenticated calls get `401` with RFC 9728
resource metadata. The agent presents a per-server bearer token, checked in
constant time by a `TokenVerifier` that grants per-server scopes
(`logs:read`, `metrics:read`). Swapping in JWT validation against a real
issuer is a new `TokenVerifier`, not a redesign. DNS-rebinding protection
stays on with an explicit host allowlist.

**Least privilege, enforced twice.** Tools declare `readOnlyHint` and the
servers hold no credentials that can change anything. The agent service does
not trust annotations alone: a role policy (`ROLE_SCOPES`) decides which tools
each agent sees, write tools are never exposed to investigators, and a call to
a tool outside the role's list is refused without being executed.

**Shaped output.** Tools return compact, redacted JSON with a size cap:
`error_summary` clusters error lines into message templates with counts,
first/last seen and one example instead of returning raw lines;
`service_health` folds ~25 PromQL queries into one per-service snapshot with a
baseline and a list of notable deviations; `query_range` returns statistics,
the detected change point and ~20 downsampled points. Secrets (bearer tokens,
passwords, keys, JWTs, credentials in URLs) are masked on the server before
anything leaves it. Label values are validated and quoted, so a model can't
inject LogQL through a service name; raw LogQL must stay inside the sandbox
namespace.

**Tool output is data, never instructions.** The agent wraps every result as
`<tool_output evidence_id="E7" tool="..." trust="untrusted">`, neutralizes any
attempt to close that tag early, and flags outputs that match prompt-injection
phrasing (`injection_suspected="true"`). The system prompt tells the model to
report such text and never act on it; write actions additionally require a
human approval bound to the exact action (a later phase).

**Evidence-backed answers.** Each tool result gets an evidence ID; the final
report cites evidence IDs with verbatim quotes, and every quote is checked
mechanically against the cited output (`verified` / `quote_not_found` /
`unknown_evidence`).

## Consequences

- One canned tool call often replaces five free-form queries: fewer turns,
  fewer malformed queries, lower cost per incident.
- Pre-digested outputs (notable deviations, error patterns) also shape what
  the agent notices; raw-query tools remain available for anything they miss.
- Injection scanning is heuristic and only flags; the real defence is that
  untrusted text can't trigger a write without a human.
