{{- define "sandbox.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: sandbox
{{- with .version }}
app.kubernetes.io/version: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
A sandbox service: Deployment + Service on port 8080, scraped by Prometheus.
Arguments (dict):
  root        the chart root context
  name        service name (also the container name and the `service` label)
  svc         the service's values block (version, resources, nodePort)
  java        true for Spring Boot services (actuator probes, OTel Java agent)
  env         extra environment variables (list)
*/}}
{{- define "sandbox.service" -}}
{{- $svc := .svc -}}
{{- $root := .root -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
  labels: {{- include "sandbox.labels" (dict "name" .name "version" $svc.version) | nindent 4 }}
  annotations:
    kubernetes.io/change-cause: {{ printf "Release %s %s" .name $svc.version | quote }}
spec:
  replicas: 1
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ .name }}
  template:
    metadata:
      labels: {{- include "sandbox.labels" (dict "name" .name "version" $svc.version) | nindent 8 }}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: {{ if .java }}/actuator/prometheus{{ else }}/metrics{{ end }}
    spec:
      # No Docker-link env vars: the `redis` Service would otherwise inject
      # REDIS_PORT=tcp://10.96.x.x:6379 and break Spring's property binding.
      enableServiceLinks: false
      terminationGracePeriodSeconds: 20
      containers:
        - name: {{ .name }}
          image: "relay/sandbox-{{ .name }}:{{ $svc.version }}"
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          env:
            {{- if .java }}
            - name: JAVA_TOOL_OPTIONS
              value: "-javaagent:{{ $root.Values.telemetry.otelJavaAgent }} -XX:MaxRAMPercentage=70 -XX:+ExitOnOutOfMemoryError"
            - name: OTEL_SERVICE_NAME
              value: {{ .name }}
            - name: OTEL_RESOURCE_ATTRIBUTES
              value: "service.namespace=sandbox,service.version={{ $svc.version }}"
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: {{ $root.Values.telemetry.otlpHttp }}
            - name: OTEL_EXPORTER_OTLP_PROTOCOL
              value: http/protobuf
            - name: OTEL_METRICS_EXPORTER
              value: none
            - name: OTEL_LOGS_EXPORTER
              value: none
            {{- else }}
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: {{ $root.Values.telemetry.otlpGrpc }}
            {{- end }}
            {{- with .env }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- if .java }}
          startupProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            periodSeconds: 5
            failureThreshold: 60
          livenessProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /actuator/health/readiness, port: http }
            periodSeconds: 5
            timeoutSeconds: 2
          {{- else }}
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 10
          readinessProbe:
            httpGet: { path: /readyz, port: http }
            periodSeconds: 5
          {{- end }}
          resources: {{- toYaml $svc.resources | nindent 12 }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
  labels: {{- include "sandbox.labels" (dict "name" .name) | nindent 4 }}
spec:
  type: {{ if $svc.nodePort }}NodePort{{ else }}ClusterIP{{ end }}
  selector:
    app.kubernetes.io/name: {{ .name }}
  ports:
    - name: http
      port: 8080
      targetPort: http
      {{- with $svc.nodePort }}
      nodePort: {{ . }}
      {{- end }}
{{- end -}}
