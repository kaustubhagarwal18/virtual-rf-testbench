{{/*
The chart's short name: Chart.yaml's name field, or nameOverride, cut to the 63-character
label limit. toString keeps an override such as --set nameOverride=true a string.
*/}}
{{- define "vrf.name" -}}
{{- default .Chart.Name .Values.nameOverride | toString | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
The release-scoped prefix for every resource name, at full length. fullnameOverride wins;
otherwise <release>-<name>, or just <release> when the release name already contains the
chart name. It is not shortened here: vrf.componentName shortens it, and hashes what it
cut, so two long releases never share a name.
*/}}
{{- define "vrf.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | toString }}
{{- else }}
{{- $name := include "vrf.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The name of one of the release's objects: "<fullname>-<suffix>". Every object is named
through this.
- When that is longer than 63 characters (the limit for a Service name), the fullname is
  cut and the first 8 characters of its SHA-256 go before the suffix:
  "<cut fullname>-<hash>-<suffix>". Two releases that share a long prefix then still get
  different names; short names are unchanged.
- The name must be an RFC 1035 label (lowercase letters, digits and "-", starting with a
  letter), which Services need on Kubernetes before 1.36 (RelaxedServiceNameValidation is
  on by default only from 1.36). A name that isn't one stops rendering with the fix,
  rather than being renamed: a silently prefixed name wouldn't match what the user typed.
Call with: include "vrf.componentName" (dict "context" . "suffix" "generator-admin")
*/}}
{{- define "vrf.componentName" -}}
{{- $full := include "vrf.fullname" .context -}}
{{- $name := printf "%s-%s" $full .suffix -}}
{{- if gt (len $name) 63 -}}
{{- $hash := sha256sum $full | trunc 8 -}}
{{- $cut := $full | trunc (int (sub 63 (add (len .suffix) 10))) -}}
{{- $prefix := regexReplaceAll "-+$" $cut "" -}}
{{- $name = printf "%s-%s-%s" $prefix $hash .suffix -}}
{{- end -}}
{{- if not (regexMatch "^[a-z]([-a-z0-9]*[a-z0-9])?$" $name) -}}
{{- $problem := printf "gives the object name %q, which must start with a letter and use only lowercase letters, digits and '-'" $name -}}
{{- if .context.Values.fullnameOverride -}}
{{- fail (printf "fullnameOverride %q %s; change fullnameOverride to such a name" (toString .context.Values.fullnameOverride) $problem) -}}
{{- end -}}
{{- if regexMatch "^[a-z]([-a-z0-9]*[a-z0-9])?$" .context.Release.Name -}}
{{- fail (printf "nameOverride %q %s; change nameOverride to such a name" (toString .context.Values.nameOverride) $problem) -}}
{{- end -}}
{{- $suggested := replace "." "-" .context.Release.Name -}}
{{- if not (regexMatch "^[a-z]" $suggested) -}}
{{- $suggested = printf "vrf-%s" $suggested -}}
{{- end -}}
{{- fail (printf "release name %q %s; use a release name such as %s" .context.Release.Name $problem $suggested) -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{/*
Chart name and version for the helm.sh/chart label; "+" (semver build metadata) isn't
allowed in a label value.
*/}}
{{- define "vrf.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels for one component. Every value is quoted: a release named "true", or a
nameOverride of "123", is valid, and unquoted it would render as a YAML boolean or number,
which a label can't be. The component label is part of the selector so the generator,
analyzer and triage Deployments never select each other's pods; a selector
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
