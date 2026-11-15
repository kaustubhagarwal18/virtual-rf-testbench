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
every selector, so no Deployment or Service can pick up another workload's pods.

**Admin ports get their own Services.** The admin ports carry the fault switches, so the
SCPI Services expose port 5025 only, and `-generator-admin` (5125) and `-analyzer-admin`
(5126) are separate. The analyzer is passed `<generator admin Service>:5125`, host and port
with no scheme, which is what its parser accepts.

**One replica each, no replica setting.** Each simulator is one instrument with its own
state, and the triage history is SQLite on a ReadWriteOnce volume. Triage uses the
`Recreate` strategy, so a rollout stops the old pod before the new one mounts the volume.

**Probes.** Simulator readiness runs the image's healthcheck (`*IDN?` must answer
`VirtualRF`), the same check Docker's HEALTHCHECK runs; liveness only opens the SCPI port, so
a slow reply never restarts an instrument. `*IDN?` changes no state and the server accepts
concurrent connections, so a probe can't disturb a test's session. Triage uses `/livez` for
start-up and liveness and `/readyz` for readiness, which answers 503 while the history loads
(ADR 0026): the pod is kept out of the Service, not restarted.

**History volume.** With `triage.persistence.enabled` the history is on a claim; an empty
`storageClassName` leaves the field out so the cluster default applies (minikube
`standard`, k3s `local-path`). With persistence off, `/data` is an emptyDir, and added runs
and cached vectors go with the pod.

**Ingress-only NetworkPolicy, off by default.** When enabled, every pod of the release
denies ingress, then SCPI and the triage API are open to the namespace and the generator's
admin port to the analyzer only. The analyzer's admin port stays closed in the cluster.
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
- **A tcpSocket probe for simulator readiness**: an open port says nothing about whether
  the instrument answers SCPI.
- **helm-unittest** for the chart tests: another plugin to install and pin; pytest already
  runs in CI and reads YAML, and the assertions read like the repository's other tests.
- **kubeconform** to validate the rendered objects against the Kubernetes schemas: useful,
  but another download; left as a follow-up.

## Consequences

The chart renders and passes its checks, but nothing has been deployed with it. Unverified
until it runs on a cluster: whether the GitHub Container Registry packages can be pulled
without an `imagePullSecret`; whether `host.minikube.internal` reaches the Mac's Ollama from a
pod; and whether k3s enforces these NetworkPolicies as written. Changing the chart version
changes the `helm.sh/chart` label on the pod templates, so it restarts every pod.
