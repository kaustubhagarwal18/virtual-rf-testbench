"""Fact kinds: domain-free statements about a run that a pack's checks rely on.

A check refuses an answer the evidence contradicts. What counts as a contradiction is
domain knowledge, but the shapes of the facts behind it recur in every domain: a recorded
version equals its baseline; the answer names a version the run didn't record; a failed test
carries an error; a log line contains a phrase; a reading in some unit is out of limits. Each
shape is a kind here, and a pack names instances of them with its own field names, phrases
and units (`[facts.no_signal]` with `kind = "log_contains"`).

A fact returns None when it doesn't hold, and otherwise the values a check's reason may
quote, such as the first error or the version mentioned.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from triage.evidence import SavedRun, failed_readings, failed_tests

FactValues = dict[str, str]
"""What a fact that holds can say about itself, for a check's reason to quote."""


class Fact(Protocol):
    """Anything that can say whether it holds for a run and an answer."""

    @property
    def provides(self) -> tuple[str, ...]:
        """The names of the values it returns when it holds."""

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Say whether the fact holds.

        Args:
            run: The run, whole or focused on one failure.
            said: The answer's words (what to act on, and why), for facts about the answer.

        Returns:
            The values it provides when it holds; None when it doesn't.
        """


def _text(value: object, name: str) -> str:
    """Check that a parameter is non-empty text.

    Args:
        value: The parameter.
        name: Its name, for the message.

    Returns:
        The text.

    Raises:
        ValueError: If it isn't text, or is empty.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name!r} must be non-empty text")
    return value


@dataclass(frozen=True)
class VersionsUnchanged:
    """Every version a run recorded equals the baseline: nothing changed since it passed.

    No versions recorded means unknown, which is not unchanged, so the fact doesn't hold.

    Attributes:
        field: The results entry mapping each component to its version.
        baseline: The version the last passing run used.
    """

    field: str
    baseline: str
    provides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Check the parameters."""
        _text(self.field, "field")
        _text(self.baseline, "baseline")

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Hold when versions were recorded and all equal the baseline.

        Args:
            run: The run.
            said: The answer's words, unused.

        Returns:
            No values when it holds; None otherwise.
        """
        del said
        versions = run.results.get(self.field) or {}
        if not versions or any(version != self.baseline for version in versions.values()):
            return None
        return {}


@dataclass(frozen=True)
class NamesOtherVersion:
    """The answer names a version this run didn't record, so it is about another run.

    Attributes:
        field: The results entry mapping each component to its version.
        pattern: A regular expression for a version as the answer would write it.
    """

    field: str
    pattern: str
    provides: tuple[str, ...] = ("mentioned", "ran")

    def __post_init__(self) -> None:
        """Check the parameters.

        Raises:
            ValueError: If the pattern isn't a valid regular expression.
        """
        _text(self.field, "field")
        try:
            re.compile(_text(self.pattern, "pattern"))
        except re.error as error:
            raise ValueError(f"'pattern' isn't a valid regular expression: {error}") from error

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Hold when the answer names a version none of the run's components ran.

        Args:
            run: The run.
            said: The answer's words.

        Returns:
            The first such version and what the run ran; None when the run recorded no
            versions or the answer names none of another run's.
        """
        versions = run.results.get(self.field) or {}
        if not versions:
            return None
        foreign = [name for name in re.findall(self.pattern, said) if name not in versions.values()]
        if not foreign:
            return None
        ran = ", ".join(f"{role} {version}" for role, version in sorted(versions.items()))
        return {"mentioned": foreign[0], "ran": ran}


@dataclass(frozen=True)
class ErrorsReported:
    """A failed test carries errors the system under test reported.

    Attributes:
        field: The entry of a test's record that lists its errors.
    """

    field: str
    provides: tuple[str, ...] = ("error",)

    def __post_init__(self) -> None:
        """Check the parameters."""
        _text(self.field, "field")

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Hold when any failed test lists an error.

        Args:
            run: The run.
            said: The answer's words, unused.

        Returns:
            The first error; None when no failed test lists one.
        """
        del said
        errors = [str(error) for test in failed_tests(run) for error in test.get(self.field, [])]
        return {"error": errors[0]} if errors else None


@dataclass(frozen=True)
class LogContains:
    """A log line contains a phrase.

    Attributes:
        text: The phrase, matched exactly.
    """

    text: str
    provides: tuple[str, ...] = ("count",)

    def __post_init__(self) -> None:
        """Check the parameters."""
        _text(self.text, "text")

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Hold when at least one log line contains the phrase.

        Args:
            run: The run.
            said: The answer's words, unused.

        Returns:
            How many lines contain it; None when none does.
        """
        del said
        count = sum(1 for line in run.logs if self.text in line)
        return {"count": str(count)} if count else None


@dataclass(frozen=True)
class ReadingFailedIn:
    """A reading in one of these units is out of limits.

    Attributes:
        units: The units, such as dB and dBm for levels.
    """

    units: tuple[str, ...] | list[str]
    provides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Check the parameters.

        Raises:
            ValueError: If the units aren't a non-empty list of text.
        """
        if not isinstance(self.units, list | tuple) or not self.units:
            raise ValueError("'units' must be a non-empty list")
        for unit in self.units:
            _text(unit, "units")

    def __call__(self, run: SavedRun, said: str) -> FactValues | None:
        """Hold when any failed reading is in one of the units.

        Args:
            run: The run.
            said: The answer's words, unused.

        Returns:
            No values when it holds; None otherwise.
        """
        del said
        hit = any(str(reading["unit"]) in self.units for reading in failed_readings(run))
        return {} if hit else None


FACT_KINDS: dict[str, Callable[..., Fact]] = {
    "versions_unchanged": VersionsUnchanged,
    "names_other_version": NamesOtherVersion,
    "errors_reported": ErrorsReported,
    "log_contains": LogContains,
    "reading_failed_in": ReadingFailedIn,
}
"""Each kind a pack can name, by its name; a new kind is a new entry."""


def make_fact(kind: str, params: dict[str, Any]) -> Fact:
    """Build a fact of a named kind from a pack's parameters.

    Args:
        kind: The kind's name, a key of FACT_KINDS.
        params: Its parameters, as the pack gives them.

    Returns:
        The fact.

    Raises:
        ValueError: If the kind is unknown, a parameter is missing or unexpected, or a
            value is wrong; the message says which.
    """
    build = FACT_KINDS.get(kind)
    if build is None:
        raise ValueError(f"unknown fact kind {kind!r}; known: {', '.join(sorted(FACT_KINDS))}")
    if "provides" in params:
        raise ValueError(f"wrong parameters for {kind}: 'provides' is fixed by the kind")
    try:
        return build(**params)
    except TypeError as error:
        raise ValueError(f"wrong parameters for {kind}: {error}") from error
