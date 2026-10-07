{{/* Alertmanager: one notification group per namespace, so a cascading failure
     (payments slow -> orders erroring -> gateway erroring) arrives as one
     webhook with several alerts instead of an alert storm. */}}
{{- define "obs.alertmanager.config" -}}
route:
  receiver: relay
  group_by: [namespace]
  group_wait: 20s
  group_interval: 1m
  repeat_interval: 4h
receivers:
  - name: relay
    webhook_configs:
      - url: {{ .Values.alertmanager.webhookUrl }}
        send_resolved: true
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/secrets/webhook-token
{{- end -}}

{{- define "obs.loki.config" -}}
auth_enabled: false
server:
  http_listen_port: 3100
  grpc_listen_port: 9095
  log_level: warn
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
schema_config:
  configs:
    - from: "2024-04-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  allow_structured_metadata: true
  discover_log_levels: true
  volume_enabled: true
  retention_period: 72h
  reject_old_samples: true
  reject_old_samples_max_age: 24h
  ingestion_rate_mb: 16
  ingestion_burst_size_mb: 32
compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
pattern_ingester:
  enabled: true
query_range:
  results_cache:
    cache:
      embedded_cache:
        enabled: true
        max_size_mb: 100
analytics:
  reporting_enabled: false
{{- end -}}

{{/* Jaeger v2 is an OpenTelemetry Collector distribution; this trims the
     all-in-one defaults to OTLP-in, in-memory storage with a trace cap. */}}
{{- define "obs.jaeger.config" -}}
service:
  extensions: [jaeger_storage, jaeger_query, healthcheckv2]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [jaeger_storage_exporter]
  telemetry:
    resource:
      service.name: jaeger
    logs:
      level: warn
extensions:
  healthcheckv2:
    use_v2: true
    http:
      endpoint: 0.0.0.0:13133
  jaeger_query:
    storage:
      traces: memstore
    http:
      endpoint: 0.0.0.0:16686
    grpc:
      endpoint: 0.0.0.0:16685
  jaeger_storage:
    backends:
      memstore:
        memory:
          max_traces: {{ .Values.jaeger.maxTraces }}
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
processors:
  batch: {}
exporters:
  jaeger_storage_exporter:
    trace_storage: memstore
{{- end -}}

{{/* One collector per node: tails pod log files into Loki, receives OTLP traces
     from the services and forwards them to Jaeger, and derives service-graph
     metrics (who calls whom, error rates, latency) from the spans. */}}
{{- define "obs.collector.config" -}}
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
  file_log:
    include: {{- toYaml .Values.logs.include | nindent 6 }}
    exclude: {{- toYaml .Values.logs.exclude | nindent 6 }}
    start_at: end
    include_file_path: true
    include_file_name: false
    operators:
      - type: container
        id: container-parser

processors:
  memory_limiter:
    check_interval: 1s
    limit_percentage: 80
    spike_limit_percentage: 25
  k8s_attributes:
    auth_type: serviceAccount
    passthrough: false
    filter:
      node_from_env_var: K8S_NODE_NAME
    extract:
      metadata:
        - k8s.namespace.name
        - k8s.pod.name
        - k8s.pod.uid
        - k8s.deployment.name
        - k8s.container.name
        - k8s.node.name
      labels:
        - tag_name: service.name
          key: app.kubernetes.io/name
          from: pod
        - tag_name: service.version
          key: app.kubernetes.io/version
          from: pod
    pod_association:
      - sources:
          - from: resource_attribute
            name: k8s.pod.uid
      - sources:
          - from: resource_attribute
            name: k8s.pod.ip
      - sources:
          - from: connection
  batch:
    send_batch_size: 1024
    timeout: 2s

connectors:
  service_graph:
    latency_histogram_buckets: [10ms, 50ms, 100ms, 250ms, 500ms, 1s, 2500ms, 5s]
    virtual_node_peer_attributes: [peer.service, db.system.name, db.system, server.address]
    store:
      ttl: 5s
      max_items: 5000

exporters:
  otlp_http/loki:
    endpoint: http://loki.{{ .Release.Namespace }}.svc.cluster.local:3100/otlp
  otlp_grpc/jaeger:
    endpoint: jaeger.{{ .Release.Namespace }}.svc.cluster.local:4317
    tls:
      insecure: true
  prometheus_remote_write:
    endpoint: http://prometheus.{{ .Release.Namespace }}.svc.cluster.local:9090/api/v1/write

service:
  telemetry:
    logs:
      level: warn
  pipelines:
    logs:
      receivers: [file_log]
      processors: [memory_limiter, k8s_attributes, batch]
      exporters: [otlp_http/loki]
    traces:
      receivers: [otlp]
      processors: [memory_limiter, k8s_attributes, batch]
      exporters: [otlp_grpc/jaeger, service_graph]
    metrics/servicegraph:
      receivers: [service_graph]
      processors: [batch]
      exporters: [prometheus_remote_write]
{{- end -}}
