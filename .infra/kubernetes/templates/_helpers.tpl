{{/*
Helpers Helm classiques — préfixe `pcb-ai-designer.*`.
Noms, labels standards (app.kubernetes.io/*), sélecteurs et image par service.
Tous les templates du chart les incluent pour rester DRY.
*/}}

{{- define "pcb-ai-designer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- /*
Nom complet d'une ressource : <fullname>-<service>.
Usage : include "pcb-ai-designer.fullname" (dict "root" . "service" "parser")
*/}}
{{- define "pcb-ai-designer.fullname" -}}
{{- $root := .root -}}
{{- $svc := .service -}}
{{- printf "%s-%s" (include "pcb-ai-designer.name" $root) $svc | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Labels communs à toutes les ressources du chart.
Appel interne : le composant est passé via .component (facultatif).
*/}}
{{- define "pcb-ai-designer.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | quote }}
{{ include "pcb-ai-designer.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .component }}
app.kubernetes.io/component: {{ . | quote }}
{{- end }}
{{- end }}

{{/*
Sélecteur immuable (labels utilisés par Deployment/Service/HPA).
*/}}
{{- define "pcb-ai-designer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pcb-ai-designer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- with .component }}
app.kubernetes.io/component: {{ . | quote }}
{{- end }}
{{- end }}

{{/*
Image conteneur d'un service : global.image.registry/repository/<service>:tag,
avec surcharge possible par services.<nom>.image.repository/tag.
Usage : include "pcb-ai-designer.serviceImage" (dict "root" . "svc" $svc "name" $name)
*/}}
{{- define "pcb-ai-designer.serviceImage" -}}
{{- $root := .root -}}
{{- $svc := .svc -}}
{{- $name := .name -}}
{{- $gi := $root.Values.global.image -}}
{{- $reg := default $gi.registry (get $svc.image "registry" | default "") -}}
{{- $repo := default (printf "%s/%s" $gi.repository $name) (get $svc.image "repository" | default "") -}}
{{- $tag := default $gi.tag (get $svc.image "tag" | default "") -}}
{{- printf "%s/%s:%s" $reg $repo $tag -}}
{{- end }}

{{/*
Ressources du conteneur : GPU conditionnel (nvidia.com/gpu) + cpu/mem.
Usage : include "pcb-ai-designer.resources" (dict "root" . "svc" $svc)
*/}}
{{- define "pcb-ai-designer.resources" -}}
{{- $svc := .svc -}}
{{- $gpu := $svc.gpu | default dict -}}
requests:
  {{- with $svc.resources.requests }}
  cpu: {{ .cpu | quote }}
  memory: {{ .memory | quote }}
  {{- end }}
  {{- if $gpu.enabled }}
  nvidia.com/gpu: {{ $gpu.count | default 1 }}
  {{- end }}
limits:
  {{- with $svc.resources.limits }}
  cpu: {{ .cpu | quote }}
  memory: {{ .memory | quote }}
  {{- end }}
  {{- if $gpu.enabled }}
  nvidia.com/gpu: {{ $gpu.count | default 1 }}
  {{- end }}
{{- end }}
