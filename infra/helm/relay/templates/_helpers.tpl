{{- define "relay.labels" -}}
app.kubernetes.io/name: {{ . }}
app.kubernetes.io/part-of: relay
{{- end -}}

{{- define "relay.selector" -}}
app.kubernetes.io/name: {{ . }}
{{- end -}}

{{/* An env var read from the relay Secret: (dict "root" $ "env" "NAME" "key" "secret-key" "optional" false) */}}
{{- define "relay.secretEnv" -}}
- name: {{ .env }}
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.secretName }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}
