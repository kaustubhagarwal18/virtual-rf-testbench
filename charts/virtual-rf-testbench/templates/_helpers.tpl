{{/*
The chart's short name: Chart.yaml's name field, or nameOverride, cut to the 63-character
label limit. toString keeps an override such as --set nameOverride=true a string.
*/}}
{{- define "vrf.name" -}}
{{- default .Chart.Name .Values.nameOverride | toString | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
The release-scoped prefix for every resource name. fullnameOverride wins; otherwise
<release>-<name>, or just <release> when the release name already contains the chart name.
*/}}
{{- define "vrf.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | toString | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "vrf.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The name of one of the release's objects: the fullname, cut so that "-<suffix>" still fits
in 63 characters (the limit for Service names and labels), then "-<suffix>". Every object
is named through this, so a long release name never makes a name Kubernetes rejects.
Call with: include "vrf.componentName" (dict "context" . "suffix" "generator-admin")
*/}}
{{- define "vrf.componentName" -}}
{{- $prefix := include "vrf.fullname" .context | trunc (int (sub 62 (len .suffix))) | trimSuffix "-" -}}
{{- printf "%s-%s" $prefix .suffix -}}
{{- end }}

{{/*
Chart name and version for the helm.sh/chart label; "+" (semver build metadata) isn't
allowed in a label value.
*/}}
{{- define "vrf.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels for one component. Every value is quoted: a release named "true" or "123"
is valid, and unquoted it would render as a YAML boolean or number, which a label can't be. The component label is part of the selector so the
generator, analyzer and triage Deployments never select each other's pods; a selector
can't change after the Deployment is created, so it is set from the first version.
Call with: include "vrf.selectorLabels" (dict "context" . "component" "generator")
*/}}
{{- define "vrf.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vrf.name" .context | quote }}
app.kubernetes.io/instance: {{ .context.Release.Name | quote }}
app.kubernetes.io/component: {{ .component | quote }}
{{- end }}

{{/*
The full label set for one component's resources: the selector labels plus chart,
version and manager.
Call with: include "vrf.labels" (dict "context" . "component" "generator")
*/}}
{{- define "vrf.labels" -}}
helm.sh/chart: {{ include "vrf.chart" .context | quote }}
{{ include "vrf.selectorLabels" . }}
app.kubernetes.io/version: {{ .context.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .context.Release.Service | quote }}
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
Probes for a simulator container. The startup probe runs the image's own healthcheck, the
same check Docker's HEALTHCHECK runs: *IDN? must get a VirtualRF reply. It runs only while
the container starts, when every fault switch is still off. Readiness and liveness then
only open the SCPI port: SLOW_RESPONSE_S delays every SCPI line, *IDN? included, by up to
30 s, and a long measurement holds the instrument's lock, so an *IDN? check after start-up
would take a slowed but working instrument out of its Services (the analyzer would then
read "no signal", a different fault) or restart it and clear the faults.
*/}}
{{- define "vrf.simulatorProbes" -}}
startupProbe:
  exec:
    command: ["python", "-m", "simulators.healthcheck", "--port", "5025"]
  periodSeconds: 2
  timeoutSeconds: 3
  failureThreshold: 15
readinessProbe:
  tcpSocket:
    port: scpi
  periodSeconds: 5
livenessProbe:
  tcpSocket:
    port: scpi
  periodSeconds: 10
  failureThreshold: 3
{{- end }}

{{/*
The generator's admin Service name. It is a bare name: the analyzer's --generator-admin
takes host:port with no scheme, so pass "<this name>:5125", never "http://...".
*/}}
{{- define "vrf.generatorAdminService" -}}
{{- include "vrf.componentName" (dict "context" . "suffix" "generator-admin") -}}
{{- end }}
