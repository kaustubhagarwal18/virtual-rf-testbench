"""Tests for measurement records: limits, margins, pass or fail."""

import math
from collections.abc import Callable

import pytest

from measurements.record import Limit, Measurement

VERSIONS = {"generator": "1.0.0", "analyzer": "2.0.0"}


def level(value_dbm: float, limit: Limit) -> Measurement:
    """Build a level measurement with the given value and limit."""
    return Measurement("level_1ghz", value_dbm, "dBm", limit, VERSIONS)


@pytest.mark.parametrize(
    ("limit", "value", "margin"),
    [
        (Limit.window(-11.0, 0.5), -11.25, 0.25),
        (Limit.window(-11.0, 0.5), -10.75, 0.25),
        (Limit.window(-11.0, 0.5), -11.75, -0.25),
        (Limit.upper(-110.0), -120.0, 10.0),
        (Limit.upper(-110.0), -105.0, -5.0),
        (Limit.lower(-40.0), -30.0, 10.0),
        (Limit(low=-2.0, high=6.0), 5.0, 1.0),
    ],
)
def test_margin_is_the_distance_to_the_nearest_limit(
    limit: Limit, value: float, margin: float
) -> None:
    """Margin is positive inside the limit, negative outside, measured to the nearest bound."""
    assert limit.margin(value) == pytest.approx(margin)


@pytest.mark.parametrize("value", [-11.5, -10.5])
def test_a_value_exactly_at_the_limit_passes(value: float) -> None:
    """A reading exactly on the limit has zero margin and passes."""
    measurement = level(value, Limit(low=-11.5, high=-10.5))
    assert measurement.margin == 0.0
    assert measurement.passed


def test_a_value_just_outside_fails() -> None:
    """A reading a hair beyond the limit fails."""
    assert not level(-10.4999, Limit(low=-11.5, high=-10.5)).passed


def test_a_non_finite_value_fails() -> None:
    """A reading of NaN can never pass, whatever the limit."""
    measurement = level(math.nan, Limit.window(-11.0, 0.5))
    assert not measurement.passed


@pytest.mark.parametrize(
    "build",
    [
        Limit,
        lambda: Limit(low=1.0, high=0.0),
        lambda: Limit.window(0.0, -0.1),
    ],
)
def test_impossible_limits_are_refused(build: Callable[[], Limit]) -> None:
    """A limit with no bound, crossed bounds or a negative tolerance is a programming error."""
    with pytest.raises(ValueError, match="limit"):
        build()


def test_description_shows_value_limit_margin_and_result() -> None:
    """The description is what a failing test prints: value, limit, margin and the verdict."""
    assert level(-11.25, Limit.window(-11.0, 0.5)).describe() == (
        "level_1ghz: -11.25 dBm, limit -11.5 to -10.5 dBm, margin +0.25 dB, PASS"
    )


@pytest.mark.parametrize(
    ("limit", "text"),
    [(Limit.upper(-110.0), "limit <= -110 dBm"), (Limit.lower(-40.0), "limit >= -40 dBm")],
)
def test_one_sided_limits_describe_themselves(limit: Limit, text: str) -> None:
    """Upper and lower limits read as <= and >=."""
    assert text in level(-120.0, limit).describe()


def test_frequency_margin_keeps_its_unit() -> None:
    """Only dBm differences become dB; other units keep their own name."""
    measurement = Measurement("frequency", 1e9 + 250, "Hz", Limit.window(1e9, 1000), VERSIONS)
    assert "margin +750 Hz, PASS" in measurement.describe()


def test_record_converts_to_a_plain_dict() -> None:
    """The record turns into plain data for the JSON results file and the triage service."""
    assert level(-11.25, Limit.window(-11.0, 0.5)).to_dict() == {
        "name": "level_1ghz",
        "value": -11.25,
        "unit": "dBm",
        "limit_low": -11.5,
        "limit_high": -10.5,
        "margin": 0.25,
        "passed": True,
        "firmware_versions": {"generator": "1.0.0", "analyzer": "2.0.0"},
    }


@pytest.mark.parametrize(
    ("value", "limit"),
    [
        (-math.inf, Limit.upper(-110.0)),
        (math.inf, Limit.lower(-40.0)),
        (math.inf, Limit.upper(0.0)),
    ],
)
def test_an_infinite_value_never_passes(value: float, limit: Limit) -> None:
    """An infinite reading fails even where its margin looks infinitely good."""
    assert not level(value, limit).passed


@pytest.mark.parametrize(
    "build",
    [
        lambda: Limit.upper(math.inf),
        lambda: Limit.lower(math.nan),
        lambda: Limit.window(math.nan, 0.5),
        lambda: Limit.window(0.0, math.inf),
    ],
)
def test_limits_must_be_finite(build: Callable[[], Limit]) -> None:
    """A limit bound of NaN or infinity is a programming error, refused at once."""
    with pytest.raises(ValueError, match="limit"):
        build()
