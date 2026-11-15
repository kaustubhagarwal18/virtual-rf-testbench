"""Tests for the Helm chart: render it with helm template and check what would be deployed.

Every workload-level property is parametrized over all three workloads, and every
Service-level property over all five Services, so a new workload or Service that breaks a
rule fails here without a test of its own.
"""

import shutil
import subprocess  # nosec B404 - runs the helm CLI with fixed arguments, no shell
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "charts" / "virtual-rf-testbench"
"""The chart under test."""

TEST_VALUES = CHART / "ci" / "test-values.yaml"
"""The values make chart-lint renders with: both image tags set."""

RELEASE = "vrf"
"""The release name the chart is rendered under."""

PREFIX = f"{RELEASE}-virtual-rf-testbench"
"""The resource-name prefix vrf.fullname gives that release."""

TAG = "0.1.0-a3f8c12"
"""The image tag the test values pin."""

IMAGE_USER_ID = 10001
"""The uid both images run as (the Dockerfile's USER), which the pods must match."""

WORKLOADS = ("generator", "analyzer", "triage")
"""Every component that runs as a Deployment."""

SERVICE_PORTS = {
    f"{PREFIX}-generator": {5025},
    f"{PREFIX}-generator-admin": {5125},
    f"{PREFIX}-analyzer": {5025},
    f"{PREFIX}-analyzer-admin": {5126},
    f"{PREFIX}-triage": {8000},
}
"""Every Service and the only ports it may expose: no admin port in an SCPI Service."""

MAX_NAME_LENGTH = 63
"""The longest name Kubernetes accepts for a Service (a DNS label) and for a label value."""

LONGEST_RELEASE = "vrf-" + "a" * 49
"""A 53-character release name, the longest Helm accepts."""

MISTYPED_STRINGS = ("true", "false", "123", "null", "on", "yes", "1e3")
"""Valid release names and overrides that unquoted YAML would read as a bool, number or null."""

Manifest = dict[str, Any]
"""One rendered Kubernetes object."""

Render = Callable[..., list[Manifest]]
"""Render the chart with extra --set values; returns the objects."""


def _helm() -> str:
    """Find the helm CLI, failing the test with an install hint when it is missing.

    Returns:
        The path to helm.
    """
    found = shutil.which("helm")
    if found is None:
        pytest.fail("helm isn't on PATH; install it with 'brew install helm'", pytrace=False)
    return found


def _run_template(
    *settings: str, release: str = RELEASE, string_settings: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    """Run helm template on the chart with the test values and extra --set values.

    Args:
        *settings: Values as key=value, each passed with --set.
        release: The release name to render under.
        string_settings: Values as key=value, each passed with --set-string.

    Returns:
        The finished process, with its output and error text.
    """
    arguments = [_helm(), "template", release, str(CHART), "--values", str(TEST_VALUES)]
    for setting in settings:
        arguments += ["--set", setting]
    for setting in string_settings:
        arguments += ["--set-string", setting]
    return subprocess.run(  # nosec B603 - fixed arguments, no shell
        arguments, capture_output=True, text=True, check=False, timeout=60
    )


@pytest.fixture(name="render", scope="module")
def _render_fixture() -> Render:
    """Return a function that renders the chart and parses the objects, failing on errors."""

    def render(
        *settings: str, release: str = RELEASE, string_settings: tuple[str, ...] = ()
    ) -> list[Manifest]:
        """Render the chart and parse every object it produces.

        Args:
            *settings: Values as key=value, each passed with --set.
            release: The release name to render under.
            string_settings: Values as key=value, each passed with --set-string.

        Returns:
            The rendered objects, empty documents dropped.
        """
        finished = _run_template(*settings, release=release, string_settings=string_settings)
        assert finished.returncode == 0, finished.stderr
        return [found for found in yaml.safe_load_all(finished.stdout) if found]

    return render


@pytest.fixture(name="manifests", scope="module")
def _manifests_fixture(render: Render) -> list[Manifest]:
    """Return the objects the chart renders with the test values and its defaults."""
    return render()


def _find(manifests: list[Manifest], kind: str, name: str) -> Manifest:
    """Return the one object of a kind with a name.

    Args:
        manifests: The rendered objects.
        kind: The object's kind, such as Deployment.
        name: Its metadata.name.

    Returns:
        The object.
    """
    matches = [
        found for found in manifests if found["kind"] == kind and found["metadata"]["name"] == name
    ]
    assert len(matches) == 1, f"expected one {kind} {name}, found {len(matches)}"
    return matches[0]


def _of_kind(manifests: list[Manifest], kind: str) -> list[Manifest]:
    """Return every object of one kind.

    Args:
        manifests: The rendered objects.
        kind: The kind, such as Service.

    Returns:
        The objects of that kind.
    """
    return [found for found in manifests if found["kind"] == kind]


def _deployment(manifests: list[Manifest], workload: str) -> Manifest:
    """Return one workload's Deployment.

    Args:
        manifests: The rendered objects.
        workload: The component, such as generator.

    Returns:
        Its Deployment.
    """
    return _find(manifests, "Deployment", f"{PREFIX}-{workload}")


def _selects(selector: dict[str, str], labels: dict[str, str]) -> bool:
    """Say whether a label selector matches a set of labels.

    Args:
        selector: The selector's labels.
        labels: The labels of a pod template.

    Returns:
        True when every selector label is present with the same value.
    """
    return all(labels.get(key) == value for key, value in selector.items())


def _pod_labels(deployment: Manifest) -> dict[str, str]:
    """Return the labels a Deployment's pods carry.

    Args:
        deployment: The Deployment.

    Returns:
        Its pod template's labels.
    """
    labels: dict[str, str] = deployment["spec"]["template"]["metadata"]["labels"]
    return labels


def _label_maps(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every label map in an object: labels, matchLabels and Service selectors.

    Args:
        node: A rendered object, or any part of one.

    Yields:
        Each mapping of label keys to values found.
    """
    if isinstance(node, dict):
        if node.get("kind") == "Service":
            yield node["spec"]["selector"]
        for key, value in node.items():
            if key in ("labels", "matchLabels"):
                yield value
            else:
                yield from _label_maps(value)
    elif isinstance(node, list):
        for item in node:
            yield from _label_maps(item)


def _simulator_container(manifests: list[Manifest], workload: str) -> Manifest:
    """Return a workload's only container.

    Args:
        manifests: The rendered objects.
        workload: The component, such as generator.

    Returns:
        The container.
    """
    containers: list[Manifest] = _deployment(manifests, workload)["spec"]["template"]["spec"][
        "containers"
    ]
    (container,) = containers
    return container


def test_the_chart_renders_every_expected_object(manifests: list[Manifest]) -> None:
    """With defaults, the chart renders three Deployments, five Services and one claim."""
    kinds = sorted((found["kind"], found["metadata"]["name"]) for found in manifests)
    assert kinds == sorted(
        [("Deployment", f"{PREFIX}-{workload}") for workload in WORKLOADS]
        + [("Service", name) for name in SERVICE_PORTS]
        + [("PersistentVolumeClaim", f"{PREFIX}-triage-history")]
    )


@pytest.mark.parametrize("workload", WORKLOADS)
def test_each_deployment_selects_only_its_own_pods(
    manifests: list[Manifest], workload: str
) -> None:
    """A Deployment's selector matches its own pod template and no other workload's."""
    selector = _deployment(manifests, workload)["spec"]["selector"]["matchLabels"]
    for other in WORKLOADS:
        matched = _selects(selector, _pod_labels(_deployment(manifests, other)))
        assert matched == (other == workload), f"{workload} selector vs {other} pods"


@pytest.mark.parametrize("service_name", sorted(SERVICE_PORTS))
def test_each_service_selects_exactly_one_workload(
    manifests: list[Manifest], service_name: str
) -> None:
    """Every Service routes to the pods of one workload only."""
    selector = _find(manifests, "Service", service_name)["spec"]["selector"]
    matched = [
        workload
        for workload in WORKLOADS
        if _selects(selector, _pod_labels(_deployment(manifests, workload)))
    ]
    assert len(matched) == 1, f"{service_name} selects {matched}"


@pytest.mark.parametrize("service_name", sorted(SERVICE_PORTS))
def test_services_expose_only_their_own_ports(manifests: list[Manifest], service_name: str) -> None:
    """SCPI Services never carry an admin port, and admin Services only their admin port."""
    service = _find(manifests, "Service", service_name)
    exposed = {port["port"] for port in service["spec"]["ports"]}
    assert exposed == SERVICE_PORTS[service_name]


@pytest.mark.parametrize("workload", WORKLOADS)
def test_every_workload_runs_one_replica(manifests: list[Manifest], workload: str) -> None:
    """Each simulator is one instrument and triage owns one volume, so one replica each."""
    assert _deployment(manifests, workload)["spec"]["replicas"] == 1


@pytest.mark.parametrize("workload", WORKLOADS)
def test_every_workload_is_replaced_not_rolled(manifests: list[Manifest], workload: str) -> None:
    """Every Deployment uses Recreate: two pods would be two instruments, or share one volume."""
    assert _deployment(manifests, workload)["spec"]["strategy"] == {"type": "Recreate"}


@pytest.mark.parametrize("workload", WORKLOADS)
def test_every_workload_is_hardened(manifests: list[Manifest], workload: str) -> None:
    """Each pod runs as uid 10001 with no token, and each container read-only with no caps."""
    pod = _deployment(manifests, workload)["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == IMAGE_USER_ID
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for container in pod["containers"]:
        context = container["securityContext"]
        assert context["readOnlyRootFilesystem"] is True
        assert context["allowPrivilegeEscalation"] is False
        assert context["capabilities"] == {"drop": ["ALL"]}


@pytest.mark.parametrize("workload", WORKLOADS)
def test_every_container_has_probes_and_a_memory_limit(
    manifests: list[Manifest], workload: str
) -> None:
    """Each container has start-up, readiness and liveness probes and a memory limit."""
    for container in _deployment(manifests, workload)["spec"]["template"]["spec"]["containers"]:
        assert "startupProbe" in container
        assert "readinessProbe" in container
        assert "livenessProbe" in container
        assert container["resources"]["limits"]["memory"]


@pytest.mark.parametrize("workload", WORKLOADS)
def test_every_image_is_pinned_to_the_given_tag(manifests: list[Manifest], workload: str) -> None:
    """Images come from the CI registry with the pinned tag, never latest or untagged."""
    for container in _deployment(manifests, workload)["spec"]["template"]["spec"]["containers"]:
        repository, _, tag = container["image"].rpartition(":")
        assert repository.startswith("ghcr.io/kaustubhagarwal18/virtual-rf-testbench-")
        assert tag == TAG


@pytest.mark.parametrize("image", ["simulators", "triage"])
def test_an_empty_tag_stops_rendering_with_the_fix(image: str) -> None:
    """An empty image tag fails rendering with a message saying where to pin it."""
    finished = _run_template(f"images.{image}.tag=")
    assert finished.returncode != 0
    assert f"images.{image}.tag is empty" in finished.stderr
    assert "values file in Git" in finished.stderr


def test_the_analyzer_reaches_the_generator_admin_service_as_host_port(
    manifests: list[Manifest],
) -> None:
    """The analyzer gets <generator admin Service>:5125 with no scheme, as its parser wants."""
    command = _deployment(manifests, "analyzer")["spec"]["template"]["spec"]["containers"][0][
        "command"
    ]
    address = command[command.index("--generator-admin") + 1]
    assert address == f"{PREFIX}-generator-admin:5125"
    _find(manifests, "Service", f"{PREFIX}-generator-admin")


@pytest.mark.parametrize(("storage_class", "expected"), [("", None), ("local-path", "local-path")])
def test_the_storage_class_is_left_out_when_empty(
    render: Render, storage_class: str, expected: str | None
) -> None:
    """An empty storage class leaves the field out, so the cluster default applies."""
    claim = _find(
        render(f"triage.persistence.storageClassName={storage_class}"),
        "PersistentVolumeClaim",
        f"{PREFIX}-triage-history",
    )
    assert claim["spec"].get("storageClassName") == expected


def test_without_persistence_the_history_is_an_empty_dir(render: Render) -> None:
    """With persistence off there is no claim, and /data is an emptyDir."""
    manifests = render("triage.persistence.enabled=false")
    assert not _of_kind(manifests, "PersistentVolumeClaim")
    volumes = _deployment(manifests, "triage")["spec"]["template"]["spec"]["volumes"]
    history = next(volume for volume in volumes if volume["name"] == "history")
    assert history == {"name": "history", "emptyDir": {}}


@pytest.mark.parametrize("enabled", [False, True])
def test_network_policies_render_only_when_enabled(render: Render, enabled: bool) -> None:
    """NetworkPolicies appear only when networkPolicy.enabled is true."""
    policies = _of_kind(render(f"networkPolicy.enabled={str(enabled).lower()}"), "NetworkPolicy")
    assert bool(policies) == enabled


def test_only_the_analyzer_may_reach_the_generator_admin_port(render: Render) -> None:
    """The generator's admin port is open to the analyzer's pods and to nothing else."""
    manifests = render("networkPolicy.enabled=true")
    policy = _find(manifests, "NetworkPolicy", f"{PREFIX}-generator")
    admin_rules = [rule for rule in policy["spec"]["ingress"] if {"port": 5125} in rule["ports"]]
    assert len(admin_rules) == 1
    (source,) = admin_rules[0]["from"]
    analyzer_labels = _pod_labels(_deployment(manifests, "analyzer"))
    generator_labels = _pod_labels(_deployment(manifests, "generator"))
    assert _selects(source["podSelector"]["matchLabels"], analyzer_labels)
    assert not _selects(source["podSelector"]["matchLabels"], generator_labels)


@pytest.mark.parametrize("workload", WORKLOADS)
def test_the_default_deny_policy_covers_every_workload(render: Render, workload: str) -> None:
    """The default-deny ingress policy selects every workload's pods."""
    manifests = render("networkPolicy.enabled=true")
    policy = _find(manifests, "NetworkPolicy", f"{PREFIX}-default-deny-ingress")
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert "ingress" not in policy["spec"]
    assert _selects(
        policy["spec"]["podSelector"]["matchLabels"],
        _pod_labels(_deployment(manifests, workload)),
    )


@pytest.mark.parametrize("workload", ["generator", "analyzer"])
def test_simulators_check_identity_only_at_start_up(
    manifests: list[Manifest], workload: str
) -> None:
    """Only start-up asks *IDN?; readiness and liveness open the port, so faults can't flip them."""
    container = _simulator_container(manifests, workload)
    assert container["startupProbe"]["exec"]["command"] == [
        "python",
        "-m",
        "simulators.healthcheck",
        "--port",
        "5025",
    ]
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["tcpSocket"] == {"port": "scpi"}
        assert "exec" not in container[probe]


@pytest.mark.parametrize("value", MISTYPED_STRINGS)
@pytest.mark.parametrize("set_as", ["release", "nameOverride", "nameOverride-string"])
def test_every_label_value_stays_a_string(render: Render, value: str, set_as: str) -> None:
    """A release name or nameOverride YAML would read as bool, number or null stays a string."""
    if set_as == "release":
        manifests = render("networkPolicy.enabled=true", release=value)
    elif set_as == "nameOverride":
        manifests = render("networkPolicy.enabled=true", f"nameOverride={value}")
    else:
        manifests = render("networkPolicy.enabled=true", string_settings=(f"nameOverride={value}",))
    label_maps = [labels for found in manifests for labels in _label_maps(found)]
    assert label_maps
    for labels in label_maps:
        for key, label_value in labels.items():
            assert isinstance(label_value, str), f"{key}: {label_value!r}"


@pytest.mark.parametrize(
    ("settings", "release"),
    [
        ((), LONGEST_RELEASE),
        (("fullnameOverride=" + "f" * 70,), RELEASE),
        (("nameOverride=" + "n" * 70,), LONGEST_RELEASE),
    ],
    ids=["long-release", "long-fullnameOverride", "long-nameOverride"],
)
def test_every_name_fits_and_stays_distinct(
    render: Render, settings: tuple[str, ...], release: str
) -> None:
    """With the longest names allowed, every object's name fits in 63 and none collide."""
    manifests = render("networkPolicy.enabled=true", *settings, release=release)
    names = [(found["kind"], found["metadata"]["name"]) for found in manifests]
    for kind, name in names:
        assert len(name) <= MAX_NAME_LENGTH, f"{kind} {name} is {len(name)} characters"
        assert not name.endswith("-")
    assert len(set(names)) == len(names)
    (analyzer,) = [
        found
        for found in _of_kind(manifests, "Deployment")
        if found["metadata"]["labels"]["app.kubernetes.io/component"] == "analyzer"
    ]
    command = analyzer["spec"]["template"]["spec"]["containers"][0]["command"]
    host, _, port = command[command.index("--generator-admin") + 1].rpartition(":")
    assert port == "5125"
    assert ("Service", host) in names


def test_the_history_claim_survives_uninstall(manifests: list[Manifest]) -> None:
    """The claim is kept by Helm on uninstall, or when persistence is turned off."""
    claim = _find(manifests, "PersistentVolumeClaim", f"{PREFIX}-triage-history")
    assert claim["metadata"]["annotations"] == {"helm.sh/resource-policy": "keep"}
