"""Finding the past runs most like a new failure, behind one interface.

The baseline scenarios pass no sources; the retrieval scenario passes the runs this finds.
The interface is deliberately small, so an in-memory index now and a pgvector-backed one
later are interchangeable (Liskov: the service can't tell which it was given).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from triage.embed import Embedder
from triage.evidence import FULL, SavedRun, build_evidence
from triage.label import load_label
from triage.prompt import Source

DEFAULT_HISTORY = Path("triage/data/runs/history")
DEFAULT_TOP_K = 3
"""How many past runs to offer the model: enough to show a pattern, few enough to stay short."""


class Retriever(Protocol):
    """Anything that can return the past runs most like a new one."""

    def similar_to(self, run: SavedRun, count: int) -> list[Source]:
        """Return the runs most like this one, most similar first.

        Args:
            run: The run being triaged.
            count: How many to return.

        Returns:
            The closest past runs, as sources the model may cite.
        """


def history_source(run: SavedRun) -> Source:
    """Turn a labelled history run into a source the model can cite.

    The label is part of a history source on purpose: a past run is only useful if it says
    how it was resolved. This is the opposite of a question run, whose label is hidden.

    Args:
        run: A history run, which has a label.

    Returns:
        The source: the run's evidence, plus its recorded cause and fix.
    """
    label = load_label(run)
    text = f"{build_evidence(run, FULL)}\nCause: {label['cause']}\nFix applied: {label['fix']}"
    return Source(source_id=run.folder.name, text=text)


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
        sources = [history_source(run) for run in runs]
        vectors = _normalise(np.array(embedder.embed([source.text for source in sources])))
        return cls(sources, vectors, embedder)

    def similar_to(self, run: SavedRun, count: int = DEFAULT_TOP_K) -> list[Source]:
        """Return the history runs most like this one.

        Args:
            run: The run being triaged.
            count: How many to return.

        Returns:
            The closest sources, most similar first.
        """
        query = _normalise(np.array(self.embedder.embed([build_evidence(run, FULL)])))
        scores = self.vectors @ query[0]
        order = np.argsort(scores)[::-1][:count]
        return [self.sources[index] for index in order]


def _normalise(vectors: np.ndarray) -> np.ndarray:
    """Scale each row to unit length, so a dot product is the cosine similarity.

    Args:
        vectors: One vector per row.

    Returns:
        The rows, each divided by its length; a zero row is left as zeros.
    """
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(lengths == 0, 1.0, lengths)
