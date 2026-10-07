# 5. Jaeger v2 for traces; service graph from the Collector

- Status: accepted
- Date: 2026-10-04

## Context

Traces serve two purposes in Relay: a human looking at one slow request, and
the agent understanding *who calls whom* (blast radius, upstream vs
downstream). Grafana Tempo was the default choice for a Grafana stack, but
Tempo 3.x moved to a Kafka-based architecture (live-store, block-builder)
that is far heavier than a laptop cluster needs.

## Decision

- **Jaeger v2** (an OpenTelemetry Collector distribution) as the trace store:
  a single binary, OTLP in, in-memory storage capped at 20k traces.
- The **service graph comes from the OpenTelemetry Collector's
  `service_graph` connector**, which derives `traces_service_graph_request_*`
  metrics from spans and remote-writes them to Prometheus. Any trace backend
  works behind it; the agent queries topology with PromQL.
- Jaeger 2.21 only serves its v3 query API, which Grafana's Jaeger
  datasource doesn't support yet. Log lines link to the Jaeger UI instead
  (a Loki derived field on `trace_id`).

## Consequences

- Topology is available as plain metrics, e.g.
  `sum by (client, server) (rate(traces_service_graph_request_total[5m]))`
  shows `payments → psp` even though the PSP is an external, uninstrumented
  dependency (a virtual node from `peer.service`).
- No in-Grafana trace view for now; Jaeger UI at http://localhost:16686.
- Traces are lost when Jaeger restarts — acceptable for a dev cluster.
