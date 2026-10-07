# 6. Alert intake: HMAC for signed sources, bearer token for Alertmanager

- Status: accepted
- Date: 2026-10-04

## Context

`POST /api/alerts` creates incidents, which start (paid) LLM investigations,
so it must reject forged requests. HMAC body signatures are the standard
(GitHub's `X-Hub-Signature-256`): they prove the sender knows the secret *and*
that the body wasn't altered. Prometheus Alertmanager, however, cannot sign
webhook bodies; its `http_config` only supports basic auth, bearer tokens,
OAuth2 and TLS client certificates.

## Decision

- `POST /api/alerts` (generic sources, the eval runner) and the GitHub
  webhook require an HMAC-SHA256 signature over the raw body, compared in
  constant time, with a timestamp to bound replay.
- `POST /api/alerts/alertmanager` accepts Alertmanager's native payload and
  authenticates with a bearer token mounted from a Kubernetes Secret
  (`credentials_file`), only reachable inside the cluster.
- Both paths are idempotent: Alertmanager's `groupKey` and each alert's
  `fingerprint` deduplicate retries and repeat notifications.

## Consequences

- No signing proxy to operate in front of Alertmanager.
- The bearer path is weaker (no body integrity), mitigated by cluster-internal
  networking; in production it would add mTLS.
