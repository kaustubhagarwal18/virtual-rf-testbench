"""Finding the past runs most like a new failure, behind one interface.

The baseline scenarios pass no sources; the retrieval scenarios pass the runs this finds.
The interface is deliberately small, so an in-memory index now and a pgvector-backed one
later are interchangeable (Liskov: the service can't tell which it was given).

A source's id is neutral: a hash of its text, never the folder it came from. A history
folder is named after its fault (`cable_loss-1`), so citing it by that name would hand the
model the answer in the id.
"""

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from triage.embed import Embedder
from triage.evidence import FULL, SavedRun, build_evidence
from triage.label import load_label
from triage.prompt import Source

DEFAULT_HISTORY = Path("triage/data/runs/history")
DEFAULT_TOP_K = 3
"""How many past runs to offer the model: enough to show a pattern, few enough to stay short."""

SOURCE_ID_PREFIX = "past-"
SOURCE_ID_HEX_DIGITS = 8
"""Eight hex digits: no clash in a history of thousands, short enough to cite reliably."""


class Retriever(Protocol):
    """Anything that can return the past runs most like a new one."""

    def similar_to(
        self, run: SavedRun, count: int, exclude_fault: str | None = None
    ) -> list[Source]:
        """Return the runs most like this one, most similar first.

        Args:
            run: The run being triaged.
            count: How many to return.
            exclude_fault: A fault whose past runs must not be returned, so an evaluation
                can ask what happens when a failure has no precedent; None excludes nothing.

        Returns:
            The closest past runs, as sources the model may cite.
        """


class HistoryStore(Protocol):
    """Anything that can take a resolved run into the history retrieval searches."""

    def add(self, run: SavedRun, label: dict[str, Any]) -> Source:
        """Add a resolved run.

        Args:
            run: The run's evidence.
            label: How it was resolved: its fault, cause and fix.

        Returns:
            The source it became, with the id the model will cite it by.
        """


def neutral_id(text: str) -> str:
    """Name a source by a hash of its text, so the id carries no hint of its cause.

    Args:
        text: The source's text.

    Returns:
        An id such as 'past-3fa2c1d0', the same for the same text every time.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{SOURCE_ID_PREFIX}{digest[:SOURCE_ID_HEX_DIGITS]}"


def history_source(run: SavedRun, label: dict[str, Any]) -> Source:
    """Turn a resolved run into a source the model can cite.

    The label is part of a history source on purpose: a past run is only useful if it says
    how it was resolved. This is the opposite of a question run, whose label is hidden.

    Args:
        run: A history run.
        label: Its label: the fault, the cause, the fix applied and, if recorded, the action.

    Returns:
        The source: the run's evidence plus its recorded cause and fix, and the key of the
        action taken when the label records one, under a neutral id.
    """
    text = f"{build_evidence(run, FULL)}\nCause: {label['cause']}\nFix applied: {label['fix']}"
    if label.get("action"):
        text += f"\nAction: {label['action']}"
    return Source(
        source_id=neutral_id(text),
        text=text,
        fault=str(label.get("fault", "")),
        action=str(label.get("action") or ""),
    )


@dataclass
class InMemoryRetriever:
    """Past runs embedded into vectors in this process, searched by cosine similarity.

    Attributes:
        sources: The history runs, as citable sources.
        vectors: Their unit-normalised embeddings, one row each.
        embedder: How a new run is embedded for the search.
    """

    sources: list[Source]
    vectors: np.ndarray
    embedder: Embedder

    @classmethod
    def from_history(cls, history: Path, embedder: Embedder) -> "InMemoryRetriever":
        """Build the index from a folder of labelled history runs.

        Args:
            history: The folder of history runs.
            embedder: How to embed them.

        Returns:
            The retriever, ready to search.
        """
        runs = [SavedRun.load(folder) for folder in sorted(history.iterdir()) if folder.is_dir()]
        sources = [history_source(run, load_label(run)) for run in runs]
        vectors = normalise(np.array(embedder.embed([source.text for source in sources])))
        return cls(sources, vectors, embedder)

    def __len__(self) -> int:
        """Count the past runs in the index.

        Returns:
            The count.
        """
        return len(self.sources)

    def add(self, run: SavedRun, label: dict[str, Any]) -> Source:
        """Add a resolved run to the index; adding the same run twice keeps one copy.

        Args:
            run: The run's evidence.
            label: Its fault, cause and fix.

        Returns:
            The source it became.
        """
        source = history_source(run, label)
        if any(known.source_id == source.source_id for known in self.sources):
            return source
        vector = normalise(np.array(self.embedder.embed([source.text])))
        self.vectors = vector if not self.sources else np.vstack([self.vectors, vector])
        self.sources.append(source)
        return source

    def similar_to(
        self, run: SavedRun, count: int = DEFAULT_TOP_K, exclude_fault: str | None = None
    ) -> list[Source]:
        """Return the history runs most like this one.

        Args:
            run: The run being triaged.
            count: How many to return.
            exclude_fault: A fault whose runs are left out; None leaves nothing out.

        Returns:
            The closest sources, most similar first, each carrying its cosine similarity.
        """
        if not self.sources:
            return []
        query = normalise(np.array(self.embedder.embed([build_evidence(run, FULL)])))
        scores = self.vectors @ query[0]
        order = np.argsort(scores)[::-1]
        kept = [
            replace(self.sources[index], similarity=float(scores[index]))
            for index in order
            if exclude_fault is None or self.sources[index].fault != exclude_fault
        ]
        return kept[:count]


def normalise(vectors: np.ndarray) -> np.ndarray:
    """Scale each row to unit length, so a dot product is the cosine similarity.

    Args:
        vectors: One vector per row.

    Returns:
        The rows, each divided by its length; a zero row is left as zeros.
    """
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(lengths == 0, 1.0, lengths)
