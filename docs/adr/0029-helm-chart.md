# ADR 0029: One Helm chart for the bench and the triage API

- **Status:** Accepted
- **Date:** 2026-11-15

## Context

The roadmap's cluster item runs the same two images (ADR 0004, ADR 0028) on Kubernetes,
deployed by Argo CD from Git. Compose already fixes how they run: one generator, one analyzer
that reads the generator's admin port (ADR 0002), a triage API with its SQLite history on a
volume (ADR 0027), all read-only, as uid 10001, with no capabilities. The chart has to keep
those properties, and it has to fail before anything reaches a cluster when one is broken.

## Decision

**One chart, three workloads.** `charts/virtual-rf-testbench/` renders a Deployment and a
Service for the generator, the analyzer and the triage API. The component label is part of
every selector, so no Deployment or Service can pick up another workload's pods. Every
label value is quoted, so a valid release name such as `true` or `123` stays a string. Every
object is named through one helper that cuts the release prefix so `-<suffix>` still fits in
63 characters, so even Helm's longest release name (53 characters) gives valid names.

**Admin ports get their own Services.** The admin ports carry the fault switches, so the
SCPI Services expose port 5025 only, and `-generator-admin` (5125) and `-analyzer-admin`
(5126) are separate. The analyzer is passed `<generator admin Service>:5125`, host and port
with no scheme, which is what its parser accepts.

**One replica each, no replica setting.** Each simulator is one instrument with its own
state, and the triage history is SQLite on a ReadWriteOnce volume. Every Deployment uses
the `Recreate` strategy: a rolling update would briefly run two generators (or two
analyzers), each with its own in-process state, behind one Service, and two triage pods
would contend for the volume. A rollout therefore has a short outage.

**Probes.** A simulator's startup probe runs the image's healthcheck (`*IDN?` must answer
`VirtualRF`), the same check Docker's HEALTHCHECK runs, while every fault switch is still
off. Readiness and liveness then only open the SCPI port. A fault must not change which
pods a Service routes to: `SLOW_RESPONSE_S` delays every SCPI line, `*IDN?` included, by up
to 30 s, past the healthcheck's 2 s timeout, and a long measurement holds the instrument's
lock. An `*IDN?` readiness check would take a slowed generator out of its admin Service, and
the analyzer would then report no signal, a different fault; a failed liveness check would
restart the instrument and clear the faults. Triage uses `/livez` for
start-up and liveness and `/readyz` for readiness, which answers 503 while the history loads
(ADR 0026): the pod is kept out of the Service, not restarted.

**History volume.** With `triage.persistence.enabled` the history is on a claim; an empty
`storageClassName` leaves the field out so the cluster default applies (minikube
`standard`, k3s `local-path`). The claim is annotated `helm.sh/resource-policy: keep`, so
Helm skips deleting it on `helm uninstall`, and on an upgrade that turns persistence off;
it is then orphaned, no longer managed by Helm, and deleting the history is a deliberate
`kubectl delete pvc`. With persistence off, `/data` is an emptyDir: a kept claim goes
unused, and runs added from then on go with the pod.

**Ingress-only NetworkPolicy, off by default.** When enabled, the chart's policies deny
ingress to every pod of the release, then allow SCPI and the triage API from the namespace
and the generator's admin port from the analyzer only; they allow nothing to the analyzer's
admin port. NetworkPolicies are additive, so another policy selecting these pods can widen
this, and traffic from a pod's own node, such as the kubelet's probes, is always allowed.
Egress isn't restricted: triage calls Ollama outside the cluster at an address that differs
per cluster, and every pod needs cluster DNS; an egress policy would need both written per
environment.

**Image tags are pinned in Git.** `values.yaml` leaves the tags empty and rendering fails
until they are set, with a message saying to pin `<version>-<sha7>` in the environment's
values file. Argo CD deploys from Git, so `--set` would leave the deployed tag nowhere in the
repository, and the planned tag-bump pull request needs a file to edit.

**Checks without a cluster.** `make chart-lint` runs `helm lint --strict` and renders the
chart with NetworkPolicy and persistence both ways; it is part of `make lint`. The unit tests
in `tests/unit/charts/` render the chart with helm and check its properties, parametrized
over every workload and Service. CI installs Helm 4.3.1 from `get.helm.sh`, pinned and
checked against its published SHA-256, in the Lint and Test jobs.

## Alternatives considered

- **A chart per component**, or one per image: the analyzer needs the generator's Service
  name, which would cross chart boundaries; one release is one bench.
- **A replica setting with a guard**: still a value that can only ever be 1.
- **`*IDN?` for simulator readiness** (the first draft): it proves the instrument answers
  SCPI, but `SLOW_RESPONSE_S` and a long measurement make it fail on a working instrument,
  turning an injected slow-response fault into a disconnected one.
- **`RollingUpdate` for the simulators**: no outage on rollout, but two instruments with
  different state behind one Service while it runs.
- **helm-unittest** for the chart tests: another plugin to install and pin; pytest already
  runs in CI and reads YAML, and the assertions read like the repository's other tests.
- **kubeconform** to validate the rendered objects against the Kubernetes schemas: useful,
  but another download; left as a follow-up.

## Consequences

The chart renders and passes its checks, but nothing has been deployed with it. Unverified
until it runs on a cluster: whether the GitHub Container Registry packages can be pulled
without an `imagePullSecret`; whether `host.minikube.internal` reaches the Mac's Ollama from a
pod; whether k3s enforces these NetworkPolicies as written; and how Helm treats the kept
claim when a release of the same name is installed again (its docs warn of problems with
`helm install --replace`). Changing the chart version
changes the `helm.sh/chart` label on the pod templates, so it restarts every pod.
