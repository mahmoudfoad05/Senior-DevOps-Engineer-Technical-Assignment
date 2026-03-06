{{/*
_helpers.tpl — Reusable template definitions for the app Helm chart.
These are named templates (Go template "define" blocks) included by other
templates via {{ include "app.fullname" . }}.
*/}}

{{/*
Expand the chart name.
*/}}
{{- define "app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fully-qualified app name.
Truncated to 63 characters because some Kubernetes name fields are limited.
If release name contains the chart name it won't be doubled.
*/}}
{{- define "app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value in the format: <name>-<version>
*/}}
{{- define "app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels — applied to all resources for grouping and selection.
*/}}
{{- define "app.labels" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: {{ include "app.name" . }}
{{- end }}

{{/*
Selector labels — used in matchLabels (must be stable; never change after install).
*/}}
{{- define "app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image tag helper — uses component-specific tag if set, falls back to global tag.
Usage: {{ include "app.imageTag" (dict "component" .Values.backend "global" .Values.global) }}
*/}}
{{- define "app.imageTag" -}}
{{- .component.image.tag | default .global.imageTag }}
{{- end }}

{{/*
Create the full image reference for a component.
Usage: {{ include "app.image" (dict "component" .Values.backend "global" .Values.global "name" "backend") }}
*/}}
{{- define "app.image" -}}
{{- printf "%s/%s:%s" .global.imageRegistry .name (include "app.imageTag" .) }}
{{- end }}
