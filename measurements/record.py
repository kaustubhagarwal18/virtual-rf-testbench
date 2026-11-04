"""Measurement records: every reading kept with its limit, margin and firmware versions."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Limit:
    """Where a reading must lie: below a high bound, above a low bound, or between both.

    Attributes:
        low: The lowest passing value, or None for no lower bound.
        high: The highest passing value, or None for no upper bound.
    """

    low: float | None = None
    high: float | None = None

    def __post_init__(self) -> None:
        """Refuse limits that can't be met or don't limit anything.

        Raises:
            ValueError: If there is no bound, a bound isn't finite, or the low bound is above
                the high bound.
        """
        if self.low is None and self.high is None:
            raise ValueError("A limit needs a low bound, a high bound, or both")
        for bound in (self.low, self.high):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"A limit's bounds must be finite numbers, got {bound}")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"A limit's low bound {self.low} is above its high bound {self.high}")

    @classmethod
    def upper(cls, high: float) -> "Limit":
        """Build a limit the reading must not exceed.

        Args:
            high: The highest passing value.

        Returns:
            The limit.
        """
        return cls(high=high)

    @classmethod
    def lower(cls, low: float) -> "Limit":
        """Build a limit the reading must not fall below.

        Args:
            low: The lowest passing value.

        Returns:
            The limit.
        """
        return cls(low=low)

    @classmethod
    def window(cls, nominal: float, tolerance: float) -> "Limit":
        """Build a limit of nominal plus or minus a tolerance.

        Args:
            nominal: The expected value.
            tolerance: How far either side of it still passes.

        Returns:
            The limit.

        Raises:
            ValueError: If the tolerance is negative or not a finite number.
        """
        if not tolerance >= 0 or not math.isfinite(tolerance):
            raise ValueError(f"A window limit needs a tolerance of zero or more, got {tolerance}")
        return cls(low=nominal - tolerance, high=nominal + tolerance)

    def margin(self, value: float) -> float:
        """Return how far a value lies inside the limit.

        Args:
            value: The reading.

        Returns:
            The distance to the nearest bound: positive inside, zero on it, negative outside.
        """
        distances = []
        if self.low is not None:
            distances.append(value - self.low)
        if self.high is not None:
            distances.append(self.high - value)
        return min(distances)

    def describe(self, unit: str) -> str:
        """Write the limit for people.

        Args:
            unit: The reading's unit.

        Returns:
            For example '-11.5 to -10.5 dBm', '<= -110 dBm' or '>= -40 dBm'.
        """
        if self.low is not None and self.high is not None:
            return f"{self.low:.12g} to {self.high:.12g} {unit}"
        if self.high is not None:
            return f"<= {self.high:.12g} {unit}"
        return f">= {self.low:.12g} {unit}"


def json_number(value: float) -> float | str:
    """Return a number as JSON can hold it: finite numbers as they are, others as strings.

    Args:
        value: The number.

    Returns:
        The number itself if finite; otherwise 'NaN', 'Infinity' or '-Infinity', the names
        JavaScript uses, since JSON has no literal for them.
    """
    if math.isfinite(value):
        return value
    if math.isnan(value):
        return "NaN"
    return "Infinity" if value > 0 else "-Infinity"


def _difference_unit(unit: str) -> str:
    """Return the unit of a difference between two readings.

    Args:
        unit: The readings' unit.

    Returns:
        'dB' for dBm, since the difference of two absolute levels is a ratio; otherwise the
        same unit.
    """
    return "dB" if unit == "dBm" else unit


@dataclass(frozen=True)
class Measurement:
    """One reading, judged against its limit, with the firmware it was taken on.

    Attributes:
        name: What was measured, for example 'level_1ghz_minus10dbm'.
        value: The reading.
        unit: The reading's unit, for example 'dBm' or 'Hz'.
        limit: Where the reading must lie.
        firmware_versions: Each instrument's firmware version, by role, for example
            {'generator': '1.2.0', 'analyzer': '1.1.0'}.
    """

    name: str
    value: float
    unit: str
    limit: Limit
    firmware_versions: dict[str, str]

    @property
    def margin(self) -> float:
        """How far the reading lies inside its limit.

        Returns:
            The margin, in the reading's unit (dB for dBm readings).
        """
        return self.limit.margin(self.value)

    @property
    def passed(self) -> bool:
        """Whether the reading meets its limit; a reading exactly on the limit passes.

        Returns:
            True if the reading is a finite number with a margin of zero or more. NaN and
            infinite readings never pass, whatever their margin works out to.
        """
        return math.isfinite(self.value) and self.margin >= 0

    def describe(self) -> str:
        """Write the measurement for people: what a failing test prints.

        Returns:
            Name, value, limit, margin and PASS or FAIL.
        """
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"{self.name}: {self.value:.12g} {self.unit}, limit {self.limit.describe(self.unit)}, "
            f"margin {self.margin:+.6g} {_difference_unit(self.unit)}, {verdict}"
        )

    def to_dict(self) -> dict[str, object]:
        """Convert to plain data for results files and the triage service.

        Returns:
            The fields, with the limit's bounds, the margin and the verdict. A NaN or
            infinite value or margin is written as 'NaN', 'Infinity' or '-Infinity', so the
            result is always standard JSON; such a measurement never passes.
        """
        return {
            "name": self.name,
            "value": json_number(self.value),
            "unit": self.unit,
            "limit_low": self.limit.low,
            "limit_high": self.limit.high,
            "margin": json_number(self.margin),
            "passed": self.passed,
            "firmware_versions": dict(self.firmware_versions),
        }
