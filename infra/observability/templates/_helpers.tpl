{{- define "obs.labels" -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/part-of: observability
{{- end -}}

{{- define "obs.selector" -}}
app.kubernetes.io/name: {{ . }}
{{- end -}}
