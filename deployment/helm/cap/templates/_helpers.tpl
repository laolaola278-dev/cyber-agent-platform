{{- define "cap.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cap.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "cap.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "cap.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "cap.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cap.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "cap.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- /*
Image reference for an {repository, tag, digest} block.

  include "cap.imageRef" (dict "image" .Values.backend.image "defaultTag" .Chart.AppVersion)

An empty tag means "the version this chart is for", taken from Chart.AppVersion,
so a release line carries one version literal instead of five that can drift. A
digest, when set, wins: it is the only form that cannot move under a running
cluster, and release-images-<version>.json records one per published image.
`latest` is deliberately unsupported anywhere in this chart -- the release
publishes versioned tags, and mutable defaults are how F-7 shipped three images
nobody could pull.
*/ -}}
{{- define "cap.imageRef" -}}
{{- $image := .image -}}
{{- $repository := required "image.repository is required" $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repository $image.digest -}}
{{- else -}}
{{- $tag := $image.tag | default .defaultTag -}}
{{- $tag := required (printf "%s: set image.tag or image.digest (latest is not a release coordinate)" $repository) $tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
{{- end -}}
