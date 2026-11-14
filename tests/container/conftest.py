"""Docker helpers for the container tests: the triage image, a fake Ollama, a private network.

Every container is started from one image, named by its ID in TRIAGE_IMAGE, so the tests run
exactly the image CI scanned and will publish; nothing here builds. The fake Ollama runs from
the same image with fake_ollama.py mounted read-only. Each test gets its own network and a
fresh named volume, and everything it created is removed afterwards, pass or fail.

The tests skip when TRIAGE_IMAGE is unset. When TRIAGE_IMAGE is set and Docker isn't running,
the tests fail (a silent skip would let triage-smoke satisfy the CI gate without testing
anything). An ID that is set but names no local image is also an error, not a skip.
`make triage-smoke` sets TRIAGE_IMAGE to the image `make triage-image` built.
"""

import http.client
import json
import logging
import os
import shutil

# The tests run the docker CLI, always with fixed argument lists.
import subprocess  # nosec B404
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from triage.embed import DEFAULT_EMBED_MODEL
from triage.health import full_model_name
from triage.llm import DEFAULT_MODEL

IMAGE_VARIABLE = "TRIAGE_IMAGE"
"""The environment variable that names the triage image, by ID."""

FAKE_OLLAMA = Path(__file__).with_name("fake_ollama.py")
"""The fake Ollama server, mounted read-only into its container."""

OLLAMA_ALIAS = "ollama"
"""The fake's name on the test network; the service is pointed at it."""

DOCKER_TIMEOUT_S = 60.0
"""How long one docker command may take."""

READY_TIMEOUT_S = 90.0
"""How long the service may take to load its history and become ready."""

REQUEST_TIMEOUT_S = 10.0
"""How long one HTTP request to the service may take."""

POLL_INTERVAL_S = 0.1
"""How often a wait checks again."""

DIGEST_A = "sha256:" + "a" * 64
"""The embedding model's digest a test starts with."""

DIGEST_B = "sha256:" + "b" * 64
"""A different digest: the same model name, re-pulled as a different model."""

_LOG = logging.getLogger(__name__)


class DockerError(Exception):
    """A docker command failed; the message has the command and its error output."""


def docker(*arguments: str, timeout_s: float = DOCKER_TIMEOUT_S) -> str:
    """Run one docker command and return its output.

    Args:
        *arguments: The arguments after `docker`.
        timeout_s: How long it may take.

    Returns:
        Its standard output, stripped.

    Raises:
        DockerError: If it exits with an error or doesn't finish in time.
    """
    command = ["docker", *arguments]
    try:
        completed = subprocess.run(  # nosec B603
            command, capture_output=True, text=True, check=False, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as expired:
        raise DockerError(f"{' '.join(command)} didn't finish within {timeout_s:g} s") from expired
    if completed.returncode != 0:
        raise DockerError(f"{' '.join(command)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def docker_running() -> bool:
    """Say whether the docker CLI is installed and its daemon answers.

    Returns:
        True when docker commands can run.
    """
    if shutil.which("docker") is None:
        return False
    try:
        docker("info", "--format", "{{.ServerVersion}}", timeout_s=15.0)
    except DockerError:
        return False
    return True


@dataclass(frozen=True)
class Reply:
    """One HTTP reply from the service.

    Attributes:
        status: The status code; 0 when nothing answered.
        body: The JSON body; empty when there was none.
    """

    status: int
    body: dict[str, Any]


def request(url: str, payload: dict[str, Any] | None = None) -> Reply:
    """Send one request, turning every outcome into a Reply rather than an exception.

    Args:
        url: The URL.
        payload: A JSON body to POST; None sends a GET.

    Returns:
        The reply; status 0 when the connection failed, as it does before the server
        listens.
    """
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    sent = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(sent, timeout=REQUEST_TIMEOUT_S) as response:  # nosec B310
            return Reply(response.status, json.loads(response.read() or b"{}"))
    except urllib.error.HTTPError as error:
        return Reply(error.code, json.loads(error.read() or b"{}"))
    except (OSError, http.client.HTTPException):
        return Reply(0, {})


def wait_for(check: Callable[[], bool], timeout_s: float, what: str) -> None:
    """Poll until a check passes.

    Args:
        check: Returns True once the condition holds.
        timeout_s: How long to wait.
        what: What is awaited, for the failure message.
    """
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out after {timeout_s:g} s waiting for {what}")
        time.sleep(POLL_INTERVAL_S)


@dataclass
class TriageContainer:
    """A running triage service and where the host reaches it.

    Attributes:
        name: The container's name.
        base_url: The service's URL on the host, through a port published on localhost.
    """

    name: str
    base_url: str = ""

    def refresh_port(self) -> None:
        """Read the host port Docker published the service on; it changes on a restart."""
        published = docker("port", self.name, "8000/tcp").splitlines()[0]
        self.base_url = f"http://{published}"

    def restart(self) -> None:
        """Restart the container in place, as a redeploy would, and read its new port."""
        docker("restart", self.name)
        self.refresh_port()

    def get(self, path: str) -> Reply:
        """GET a path.

        Args:
            path: The path, such as /readyz.

        Returns:
            The reply.
        """
        return request(f"{self.base_url}{path}")

    def post(self, path: str, payload: dict[str, Any]) -> Reply:
        """POST a JSON body to a path.

        Args:
            path: The path, such as /history.
            payload: The body.

        Returns:
            The reply.
        """
        return request(f"{self.base_url}{path}", payload)

    def inspect(self, template: str) -> str:
        """Read one field of the container's state.

        Args:
            template: A Go template, such as {{.RestartCount}}.

        Returns:
            The field, as docker prints it.
        """
        return docker("inspect", "--format", template, self.name)

    def store(self) -> dict[str, Any]:
        """Read the persistent history's state from /health/dependencies.

        Returns:
            The report's store section.
        """
        report = self.get("/health/dependencies")
        assert report.status == HTTPStatus.OK, report
        store: dict[str, Any] = report.body["store"]
        return store

    def wait_ready(self) -> None:
        """Wait until /readyz says ready."""
        wait_for(
            lambda: self.get("/readyz").status == HTTPStatus.OK,
            READY_TIMEOUT_S,
            f"{self.name} ready",
        )

    def wait_verified(self) -> None:
        """Wait until the service is ready and Ollama has confirmed the history's digest."""
        self.wait_ready()
        wait_for(
            lambda: bool(self.store()["digest_verified"]),
            READY_TIMEOUT_S,
            f"{self.name} to verify its digest",
        )


@dataclass
class ContainerBench:
    """One test's private network, volume and containers, all removed by close().

    Attributes:
        image: The triage image's ID.
        suffix: What makes this test's names unique.
        containers: Every container started, for close() to remove.
    """

    image: str
    suffix: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    containers: list[str] = field(default_factory=list)

    @property
    def network(self) -> str:
        """Name the test's private network.

        Returns:
            The network's name.
        """
        return f"vrt-smoke-{self.suffix}"

    @property
    def volume(self) -> str:
        """Name the test's history volume.

        Returns:
            The volume's name.
        """
        return f"vrt-smoke-history-{self.suffix}"

    @property
    def fake_name(self) -> str:
        """Name the fake Ollama's container; there is at most one at a time.

        Returns:
            The container's name.
        """
        return f"vrt-smoke-ollama-{self.suffix}"

    def open(self) -> None:
        """Create the network and the volume."""
        docker("network", "create", self.network)
        docker("volume", "create", self.volume)

    def start_fake_ollama(
        self,
        digest: str = DIGEST_A,
        embed_delay_s: float = 0.0,
        barrier: bool = False,
    ) -> None:
        """Start the fake Ollama on the network and wait until it answers.

        Args:
            digest: The embedding model's digest it lists.
            embed_delay_s: How long each embedding takes.
            barrier: When True, embeddings block until release_fake_ollama_barrier() is called.
        """
        self.containers.append(self.fake_name)
        models = f"{full_model_name(DEFAULT_MODEL)},{full_model_name(DEFAULT_EMBED_MODEL)}"
        docker(
            "run", "--detach", "--name", self.fake_name, "--network", self.network,
            "--network-alias", OLLAMA_ALIAS, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--no-healthcheck",
            "--volume", f"{FAKE_OLLAMA}:/fake/fake_ollama.py:ro",
            "--env", f"FAKE_OLLAMA_MODELS={models}",
            "--env", f"FAKE_OLLAMA_EMBED_DIGEST={digest}",
            "--env", f"FAKE_OLLAMA_EMBED_DELAY_S={embed_delay_s}",
            "--env", f"FAKE_OLLAMA_EMBED_BARRIER={'1' if barrier else '0'}",
            self.image, "python", "/fake/fake_ollama.py",
        )  # fmt: skip
        probe = (
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version')"
        )
        wait_for(lambda: self._exec_ok(self.fake_name, probe), 30.0, "the fake Ollama")

    def release_fake_ollama_barrier(self) -> None:
        """Release the embedding barrier so blocked embeddings can proceed.

        Only meaningful when start_fake_ollama was called with barrier=True.
        """
        release = (
            "import urllib.request; urllib.request.urlopen"
            "('http://127.0.0.1:11434/barrier/release')"
        )
        self._exec_ok(self.fake_name, release)

    def stop_fake_ollama(self) -> None:
        """Remove the fake Ollama, so its name no longer resolves: an Ollama outage."""
        docker("rm", "--force", self.fake_name)

    def start_triage(self, name: str) -> TriageContainer:
        """Start the service as Compose does, on this test's network and volume.

        It runs with Compose's hardening and memory limit, but with an on-failure restart
        policy rather than none, so a crash shows up as a restart a test can count.

        Args:
            name: A name for the container, unique within the test.

        Returns:
            The running service, with its published port read.
        """
        container = TriageContainer(f"vrt-smoke-{name}-{self.suffix}")
        self.containers.append(container.name)
        docker(
            "run", "--detach", "--name", container.name, "--network", self.network,
            "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--memory", "512m",
            "--restart", "on-failure", "--volume", f"{self.volume}:/data",
            "--env", f"TRIAGE_OLLAMA_HOST=http://{OLLAMA_ALIAS}:11434",
            "--publish", "127.0.0.1::8000", self.image,
        )  # fmt: skip
        container.refresh_port()
        return container

    def remove(self, container: TriageContainer) -> None:
        """Remove a service container at once, as a crashed or replaced pod would go.

        Args:
            container: The container.
        """
        docker("rm", "--force", container.name)

    def close(self) -> None:
        """Remove every container, then the volume and the network; log what can't be."""
        for name in reversed(self.containers):
            self._log_container(name)
            self._quietly("rm", "--force", "--volumes", name)
        self._quietly("volume", "rm", "--force", self.volume)
        self._quietly("network", "rm", self.network)

    @staticmethod
    def _exec_ok(name: str, code: str) -> bool:
        """Run Python inside a container and say whether it succeeded.

        Args:
            name: The container.
            code: The Python code.

        Returns:
            True when it exited 0.
        """
        try:
            docker("exec", name, "python", "-c", code, timeout_s=15.0)
        except DockerError:
            return False
        return True

    @staticmethod
    def _log_container(name: str) -> None:
        """Log a container's output, so a failed test's report shows what it said.

        Args:
            name: The container.
        """
        try:
            output = docker("logs", "--tail", "100", name)
        except DockerError:
            return
        _LOG.info("logs of %s:\n%s", name, output)

    @staticmethod
    def _quietly(*arguments: str) -> None:
        """Run a clean-up command; a failure is logged, never raised over the test's own.

        Args:
            *arguments: The arguments after `docker`.
        """
        try:
            docker(*arguments)
        except DockerError as error:
            _LOG.warning("clean-up failed: %s", error)


@pytest.fixture(scope="session")
def triage_image() -> str:
    """Return the triage image's ID, skipping when there is none to test.

    Returns:
        The image ID from TRIAGE_IMAGE, checked to exist.
    """
    image = os.environ.get(IMAGE_VARIABLE, "").strip()
    if not image:
        pytest.skip(f"{IMAGE_VARIABLE} isn't set; build the image with 'make triage-image'")
    if not docker_running():
        # When TRIAGE_IMAGE is set the caller loaded the image and expects the tests to run.
        # Skipping silently would let triage-smoke pass CI without testing anything.
        pytest.fail(
            "Docker isn't running; container tests cannot be skipped when TRIAGE_IMAGE is set"
        )
    try:
        docker("image", "inspect", "--format", "{{.Id}}", image)
    except DockerError:
        pytest.fail(f"{IMAGE_VARIABLE}={image} names no local image; load or build it first")
    return image


@pytest.fixture
def bench(triage_image: str) -> Iterator[ContainerBench]:  # pylint: disable=redefined-outer-name
    """Give a test its own network and volume, and remove everything afterwards.

    Args:
        triage_image: The image's ID.

    Yields:
        The test's bench.
    """
    created = ContainerBench(triage_image)
    try:
        created.open()
        yield created
    finally:
        created.close()
