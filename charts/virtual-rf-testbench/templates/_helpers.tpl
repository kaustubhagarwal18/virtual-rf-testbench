{{/*
The chart's short name: Chart.yaml's name field, or nameOverride, cut to the 63-character
label limit.
*/}}
{{- define "vrf.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
The release-scoped prefix for every resource name. fullnameOverride wins; otherwise
<release>-<name>, or just <release> when the release name already contains the chart name.
*/}}
{{- define "vrf.fullname" -}}
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
Chart name and version for the helm.sh/chart label; "+" (semver build metadata) isn't
allowed in a label value.
*/}}
{{- define "vrf.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels for one component. The component label is part of the selector so the
generator, analyzer and triage Deployments never select each other's pods; a selector
can't change after the Deployment is created, so it is set from the first version.
Call with: include "vrf.selectorLabels" (dict "context" . "component" "generator")
*/}}
{{- define "vrf.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vrf.name" .context }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
The full label set for one component's resources: the selector labels plus chart,
version and manager.
Call with: include "vrf.labels" (dict "context" . "component" "generator")
*/}}
{{- define "vrf.labels" -}}
helm.sh/chart: {{ include "vrf.chart" .context }}
{{ include "vrf.selectorLabels" . }}
app.kubernetes.io/version: {{ .context.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .context.Release.Service }}
{{- end }}

{{/*
The image reference for one entry under .Values.images. Fails when the tag is empty, so
nothing is ever deployed from an unpinned image.
Call with: include "vrf.image" (dict "context" . "image" "simulators")
*/}}
{{- define "vrf.image" -}}
{{- $image := index .context.Values.images .image -}}
{{- if not $image.tag -}}
{{- fail (printf "images.%s.tag is empty; set it to <version>-<sha7> (for example 0.1.0-a3f8c12) in the environment's values file in Git" .image) -}}
{{- end -}}
{{- printf "%s:%s" $image.repository $image.tag -}}
{{- end }}

{{/*
Pod security context shared by every workload, matching compose: the images run as uid
10001, and fsGroup lets that user write to the triage volume.
*/}}
{{- define "vrf.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/*
Container security context shared by every container, matching compose's read_only,
cap_drop: [ALL] and no-new-privileges.
*/}}
{{- define "vrf.containerSecurityContext" -}}
readOnlyRootFilesystem: true
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
{{- end }}

{{/*
The generator's admin Service name. It is a bare name: the analyzer's --generator-admin
takes host:port with no scheme, so pass "<this name>:5125", never "http://...".
*/}}
{{- define "vrf.generatorAdminService" -}}
{{- printf "%s-generator-admin" (include "vrf.fullname" .) -}}
{{- end }}
