{{- define "obs.prometheus.config" -}}
global:
  scrape_interval: 10s
  evaluation_interval: 10s
  external_labels:
    cluster: relay-kind

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  # Any pod annotated prometheus.io/scrape=true. The pod's
  # app.kubernetes.io/name label becomes the `service` label.
  - job_name: pods
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      - source_labels: [__meta_kubernetes_pod_phase]
        action: drop
        regex: Pending|Succeeded|Failed
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        target_label: __metrics_path__
        regex: (.+)
      - source_labels: [__meta_kubernetes_pod_ip, __meta_kubernetes_pod_annotation_prometheus_io_port]
        regex: (.+);(.+)
        target_label: __address__
        replacement: $1:$2
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name]
        target_label: service
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_version]
        target_label: version

  - job_name: kube-state-metrics
    static_configs:
      - targets: ["kube-state-metrics:8080"]

  # Container CPU/memory from the kubelet's cAdvisor, via the API server proxy.
  - job_name: cadvisor
    scheme: https
    tls_config:
      insecure_skip_verify: true
    authorization:
      credentials_file: /var/run/secrets/kubernetes.io/serviceaccount/token
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - target_label: __address__
        replacement: kubernetes.default.svc:443
      - source_labels: [__meta_kubernetes_node_name]
        target_label: __metrics_path__
        replacement: /api/v1/nodes/$1/proxy/metrics/cadvisor
    metric_relabel_configs:
      - source_labels: [__name__]
        action: keep
        regex: container_(cpu_usage_seconds_total|cpu_cfs_throttled_periods_total|cpu_cfs_periods_total|memory_working_set_bytes|spec_memory_limit_bytes|oom_events_total)
      - source_labels: [container]
        action: drop
        regex: ""
      - source_labels: [container]
        target_label: service
{{- end -}}
