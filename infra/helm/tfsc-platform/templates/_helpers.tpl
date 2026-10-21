{{- define "tfsc.labels" -}}
app.kubernetes.io/part-of: tfsc-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "tfsc.crmImage" -}}{{ .Values.image.crm.repository }}:{{ .Values.image.crm.tag }}{{- end -}}
{{- define "tfsc.agrImage" -}}{{ .Values.image.agreements.repository }}:{{ .Values.image.agreements.tag }}{{- end -}}
{{- define "tfsc.crmEnv" -}}
- { name: CRM_MODE, value: hosted }
- { name: CRM_ORIGIN, value: {{ .Values.crm.origin | quote }} }
- { name: CRM_TFSC_URL, value: {{ .Values.agreements.origin | quote }} }
- { name: CRM_TFSC_INTERNAL_URL, value: "http://{{ .Release.Name }}-agreements:8000" }
- { name: CRM_OIDC_ISSUER, value: {{ .Values.crm.ssoIssuer | quote }} }
- { name: CRM_OIDC_INTERNAL_URL, value: {{ .Values.crm.ssoInternalUrl | quote }} }
- { name: CRM_OIDC_CLIENT_ID, value: {{ .Values.crm.oidcClientId | quote }} }
- { name: CRM_ALLOW_LOCAL_LOGIN, value: {{ ternary "1" "0" .Values.crm.allowLocalLogin | quote }} }
- { name: CRM_INLINE_DISPATCHER, value: "0" }
- { name: CRM_INLINE_SCHEDULER, value: "0" }
- { name: CRM_DATA_DIR, value: /data }
- { name: CRM_STORAGE, value: {{ .Values.files.storage | quote }} }
- { name: CRM_S3_ENDPOINT, value: {{ .Values.files.endpoint | quote }} }
- { name: CRM_S3_PUBLIC_ENDPOINT, value: {{ .Values.files.publicEndpoint | quote }} }
- { name: CRM_S3_BUCKET, value: {{ .Values.files.bucket | quote }} }
- { name: CRM_ANCHOR_BUCKET, value: {{ .Values.files.anchorBucket | quote }} }
- { name: CRM_S3_URL_TTL, value: {{ .Values.files.urlTtlSeconds | quote }} }
- { name: CRM_CLAMAV, value: {{ ternary (printf "%s-clamav:3310" .Release.Name) .Values.clamav.address .Values.clamav.enabled | quote }} }
- { name: CRM_LOG_FORMAT, value: json }
- { name: CRM_LOG_LEVEL, value: {{ .Values.observability.logLevel | quote }} }
- { name: CRM_METRICS_DIR, value: /run/crm-metrics }
- { name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ .Values.observability.otlpEndpoint | quote }} }
- { name: CRM_SENTRY_DSN, value: {{ .Values.observability.sentry.crmDsn | quote }} }
- { name: CRM_SENTRY_DSN_WEB, value: {{ .Values.observability.sentry.crmWebDsn | quote }} }
{{- end -}}
