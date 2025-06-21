{{/*
Expand the name of the chart.
*/}}
{{- define "my-celery-app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "my-celery-app.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart name and version as part of the labels
*/}}
{{- define "my-celery-app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "my-celery-app.labels" -}}
helm.sh/chart: {{ include "my-celery-app.chart" . }}
{{ include "my-celery-app.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- range $key, $value := .Values.global.labels }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "my-celery-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "my-celery-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Create the name of the service account to use
*/}}
{{- define "my-celery-app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
    {{ default (include "my-celery-app.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
    {{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
Generate image name
*/}}
{{- define "my-celery-app.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}


{{/*
Redis Service Name for Broker/Backend (standard Bitnami pattern)
*/}}
{{- define "my-celery-app.redisServiceName" -}}
{{ printf "%s-redis-master" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Redis Secret Name (standard Bitnami pattern)
*/}}
{{- define "my-celery-app.redisSecretName" -}}
{{ printf "%s-redis" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Redis connection string for Celery.
*/}}
{{- define "my-celery-app.redisBrokerUrl" -}}
redis://
{{- if .Values.redis.auth.enabled -}}
:{{ .Values.redis.auth.password | default (printf "%s" (include "my-celery-app.redisSecretName" .) | b64enc | trunc 10) | urlquery }}@
{{- end }}
{{ include "my-celery-app.redisServiceName" . }}:{{ .Values.redis.master.service.port }}/0
{{- end }}