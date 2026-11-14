"""Fakes shared by the persistent-history tests: an embedder that counts and fails on cue."""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tests.unit.triage.test_retrieval import labelled_run
from triage.embed import FakeEmbedder
from triage.history_store import HistoryConfig, PersistentHistory
from triage.llm import ModelUnavailableError

DIGEST = "sha256:1111"
NEW_DIGEST = "sha256:2222"
NOW = 1_800_000_000.0
DOWN = "can't reach Ollama at http://127.0.0.1:11434; start it with 'ollama serve'"

DATASET_RUNS = (
    ("cable_loss-1", "cable_loss", "worn cable", "replaced the cable"),
    ("link_down-1", "link_down", "unplugged link", "reconnected the link"),
    ("sweep_timeout-1", "sweep_timeout", "slow sweep", "raised the timeout"),
)
"""Three labelled history runs with distinct causes, so each gets its own neutral id."""


@dataclass
class CountingEmbedder:
    """FakeEmbedder vectors, with a count of texts embedded and failures on cue.

    Attributes:
        texts: Every text embedded, in order.
        down: True raises ModelUnavailableError on every call, as an Ollama outage does.
        fail_after: Raise once this many texts have been embedded; None never.
        gate: When set, a call from the thread named `blocked_thread` waits on it first.
        blocked_thread: The thread the gate holds.
        entered: Set when the gated thread reaches the gate.
    """

    texts: list[str] = field(default_factory=list)
    down: bool = False
    fail_after: int | None = None
    gate: threading.Event | None = None
    blocked_thread: str = "loader"
    entered: threading.Event = field(default_factory=threading.Event)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed like FakeEmbedder, unless told to fail or wait."""
        if self.gate is not None and threading.current_thread().name == self.blocked_thread:
            self.entered.set()
            self.gate.wait()
        vectors = []
        for text in texts:
            if self.down or (self.fail_after is not None and len(self.texts) >= self.fail_after):
                raise ModelUnavailableError(DOWN)
            self.texts.append(text)
            vectors.extend(FakeEmbedder().embed([text]))
        return vectors


@dataclass
class FakeDigest:
    """Ollama's answer to 'what is the embedding model's digest', or an outage.

    Attributes:
        digest: The digest it reports.
        down: True raises ModelUnavailableError, as Ollama being unreachable does.
    """

    digest: str = DIGEST
    down: bool = False

    def __call__(self) -> str:
        """Report the digest, or fail as an unreachable Ollama does."""
        if self.down:
            raise ModelUnavailableError(DOWN)
        return self.digest


def write_dataset(folder: Path) -> Path:
    """Write the three labelled history runs and return their folder."""
    for name, fault, cause, fix in DATASET_RUNS:
        labelled_run(folder / name, fault, cause, fix)
    return folder


def open_history(
    tmp_path: Path,
    embedder: CountingEmbedder | None = None,
    digest: FakeDigest | None = None,
) -> PersistentHistory:
    """Open a persistent history over the test data set, in tmp_path/history.db."""
    dataset = tmp_path / "dataset"
    if not dataset.is_dir():
        write_dataset(dataset)
    config = HistoryConfig(
        dataset=dataset,
        inner=embedder if embedder is not None else CountingEmbedder(),
        model="nomic-embed-text",
        digest_source=digest if digest is not None else FakeDigest(),
        clock=lambda: NOW,
    )
    return PersistentHistory.open(tmp_path / "history.db", config)
