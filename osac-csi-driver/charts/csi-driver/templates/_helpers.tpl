{{/*
Expand the name of the chart.
*/}}
{{- define "csi-driver.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "csi-driver.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" | lower }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" | lower }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" | lower }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "csi-driver.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "csi-driver.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: osac-csi-driver
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "csi-driver.selectorLabels" -}}
app.kubernetes.io/name: {{ include "csi-driver.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Controller service account name.
*/}}
{{- define "csi-driver.controllerServiceAccountName" -}}
{{- $name := "" -}}
{{- if .Values.serviceAccount.controller.name }}
{{- $name = .Values.serviceAccount.controller.name }}
{{- else }}
{{- $name = printf "%s-controller" (include "csi-driver.fullname" .) }}
{{- end }}
{{- if .Values.debug.enabled }}
{{- default (printf "%s-debug" $name) .Values.serviceAccount.controller.debugName }}
{{- else }}
{{- $name }}
{{- end }}
{{- end }}

{{/*
Node service account name.
*/}}
{{- define "csi-driver.nodeServiceAccountName" -}}
{{- $name := "" -}}
{{- if .Values.serviceAccount.node.name }}
{{- $name = .Values.serviceAccount.node.name }}
{{- else }}
{{- $name = printf "%s-node" (include "csi-driver.fullname" .) }}
{{- end }}
{{- if .Values.debug.enabled }}
{{- default (printf "%s-debug" $name) .Values.serviceAccount.node.debugName }}
{{- else }}
{{- $name }}
{{- end }}
{{- end }}

{{/*
Namespace used by namespaced CSI resources. Direct chart installations use the
Helm release namespace; the umbrella chart overrides this for the shared hub
CSI namespace.
*/}}
{{- define "csi-driver.namespace" -}}
{{- default .Release.Namespace .Values.namespace -}}
{{- end }}
