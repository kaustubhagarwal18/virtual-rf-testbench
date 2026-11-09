"""Set the abstention threshold from the history runs alone, never from the questions.

Each history run is asked as a question would be, from its evidence alone, against every
other history run as retrieval stores it. Its closest run of the same fault is a precedent
that exists; its closest run of another fault is what retrieval offers when the fault is new.
The threshold is the lowest same-fault similarity, rounded down to two decimals: no history
run with a precedent would be refused one. The rule was fixed before the numbers were seen,
and the question runs play no part, so the evaluation that scores the threshold is not also
the data it was tuned on.

Run with `uv run python -m tools.calibrate_abstention`; it needs Ollama for the embeddings.
"""

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from triage.embed import Embedder, OllamaEmbedder
from triage.evidence import FULL, SavedRun, build_evidence
from triage.label import load_label
from triage.llm import ModelUnavailableError
from triage.retrieval import DEFAULT_HISTORY, history_source, normalise


@dataclass(frozen=True)
class Neighbours:
    """One history run's closest neighbours, with and without its own fault.

    Attributes:
        fault: The run's labelled fault.
        same_fault: Cosine similarity to the closest other run of the same fault; None when
            the fault has only this one run.
        other_fault: Cosine similarity to the closest run of any other fault.
    """

    fault: str
    same_fault: float | None
    other_fault: float


def neighbours(history: Path, embedder: Embedder) -> list[Neighbours]:
    """Score each history run, asked as a question, against every other history source.

    The run is embedded as retrieval embeds a question, from its evidence alone; the other
    runs as retrieval stores them, with their cause, fix and action. Comparing two stored
    sources would measure a similarity the gate never sees.

    Args:
        history: The folder of history runs.
        embedder: How to embed them.

    Returns:
        One entry per history run, in folder order.
    """
    runs = [SavedRun.load(folder) for folder in sorted(history.iterdir()) if folder.is_dir()]
    sources = [history_source(run, load_label(run)) for run in runs]
    stored = normalise(np.array(embedder.embed([source.text for source in sources])))
    asked = normalise(np.array(embedder.embed([build_evidence(run, FULL) for run in runs])))
    similarity = asked @ stored.T
    faults = [source.fault for source in sources]
    found = []
    for index, fault in enumerate(faults):
        others = [other for other in range(len(faults)) if other != index]
        same = [similarity[index, other] for other in others if faults[other] == fault]
        different = [similarity[index, other] for other in others if faults[other] != fault]
        found.append(Neighbours(fault, float(max(same)) if same else None, float(max(different))))
    return found


def threshold(found: Sequence[Neighbours]) -> float:
    """Pick the threshold: the lowest same-fault similarity, rounded down to two decimals.

    Args:
        found: Each history run's neighbours.

    Returns:
        The threshold.

    Raises:
        ValueError: If no fault has two history runs, so no precedent was ever measured.
    """
    same = [entry.same_fault for entry in found if entry.same_fault is not None]
    if not same:
        raise ValueError("no fault has two history runs, so there is no precedent to measure")
    return math.floor(min(same) * 100) / 100


def _spread(values: Sequence[float]) -> str:
    """Describe sorted similarities by their range and median.

    Args:
        values: The similarities, sorted ascending.

    Returns:
        Text such as '0.951 to 0.987 (median 0.979)'.
    """
    return f"{values[0]:.3f} to {values[-1]:.3f} (median {float(np.median(values)):.3f})"


def summary(found: Sequence[Neighbours], chosen: float) -> str:
    """Describe the two distributions and what the threshold does to each.

    Args:
        found: Each history run's neighbours.
        chosen: The threshold.

    Returns:
        The lines to print.
    """
    same = sorted(entry.same_fault for entry in found if entry.same_fault is not None)
    other = sorted(entry.other_fault for entry in found)
    refused = sum(1 for value in other if value < chosen)
    lines = [
        f"history runs: {len(found)}",
        f"closest same-fault run:  {_spread(same)}",
        f"closest other-fault run: {_spread(other)}",
        f"threshold: {chosen:.2f}",
        f"with no precedent of their own fault, {refused} of {len(other)} runs fall below it",
    ]
    for entry in sorted(found, key=lambda entry: entry.fault):
        same_text = "none" if entry.same_fault is None else f"{entry.same_fault:.3f}"
        lines.append(f"  {entry.fault:32} same {same_text}  other {entry.other_fault:.3f}")
    return "\n".join(lines) + "\n"


def main(arguments: Sequence[str] | None = None, embedder: Embedder | None = None) -> int:
    """Print the history's neighbour similarities and the threshold they give.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        embedder: How to embed the runs; None uses Ollama.

    Returns:
        0 when it ran, 1 if the embedding model wasn't reachable or the history too small.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.calibrate_abstention",
        description="Set the abstention threshold from the history runs alone.",
    )
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parsed = parser.parse_args(arguments)
    try:
        found = neighbours(parsed.history, embedder or OllamaEmbedder())
        chosen = threshold(found)
    except (ModelUnavailableError, ValueError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    sys.stdout.write(summary(found, chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
