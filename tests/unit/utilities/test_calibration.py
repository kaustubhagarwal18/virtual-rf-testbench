"""Tests for path-loss calibration: measuring the bench's loss and judging it."""

import pytest

from tests.utilities import bench_checks
from tests.utilities.calibration import (
    CALIBRATION_FREQUENCIES_HZ,
    NOMINAL_LOSS_AT_1GHZ_DB,
    PATH_LOSS_TOLERANCE_DB,
    Calibration,
    datasheet_path_loss_db,
    path_loss_limit,
)

VERSIONS = {"generator": "0.1.0", "analyzer": "0.1.0"}
GENERATOR_RANGE_HZ = (9e3, 6e9)
"""The generator's frequency range (FREQUENCY_RANGE_HZ in its instrument)."""

ANALYZER_HIGHEST_CENTRE_HZ = 7e9
"""The highest centre frequency the analyzer accepts (CENTRE_RANGE_HZ in its instrument)."""


def test_the_datasheet_loss_is_the_rated_loss_at_one_gigahertz() -> None:
    """At 1 GHz the cable loses what its datasheet says."""
    assert datasheet_path_loss_db(1e9) == NOMINAL_LOSS_AT_1GHZ_DB


def test_the_datasheet_loss_grows_with_the_square_root_of_frequency() -> None:
    """Four times the frequency costs twice the loss, as the skin effect gives."""
    assert datasheet_path_loss_db(4e9) == pytest.approx(2 * datasheet_path_loss_db(1e9))


@pytest.mark.parametrize("frequency_hz", CALIBRATION_FREQUENCIES_HZ)
def test_a_path_loss_limit_never_allows_gain(frequency_hz: float) -> None:
    """A passive cable can't amplify, so no limit may accept a loss below zero."""
    limit = path_loss_limit(frequency_hz)
    assert limit.low is not None and limit.low >= 0.0
    assert limit.high == pytest.approx(
        datasheet_path_loss_db(frequency_hz) + PATH_LOSS_TOLERANCE_DB
    )


def test_a_calibrated_level_is_the_generator_level_minus_the_measured_loss() -> None:
    """What the analyzer should read is what was sent, less what the path measured."""
    calibration = Calibration({1e9: 1.6}, VERSIONS)
    assert calibration.expected_level_dbm(-10.0, 1e9) == pytest.approx(-11.6)


def test_an_uncalibrated_frequency_is_refused() -> None:
    """A level may only be judged where the loss is known."""
    calibration = Calibration({1e9: 1.6}, VERSIONS)
    with pytest.raises(KeyError, match="no calibration at 2e\\+09 Hz"):
        calibration.expected_level_dbm(-10.0, 2e9)


def test_each_frequency_is_judged_against_its_datasheet_loss() -> None:
    """Calibration yields one measurement per frequency, in frequency order, with its limit."""
    measured = {frequency: datasheet_path_loss_db(frequency) for frequency in (1e9, 100e6)}
    measurements = Calibration(measured, VERSIONS).measurements()
    assert [measurement.name for measurement in measurements] == [
        "path_loss_100mhz",
        "path_loss_1000mhz",
    ]
    assert all(measurement.passed for measurement in measurements)
    assert all(measurement.unit == "dB" for measurement in measurements)


def test_a_cable_that_lost_too_much_fails_its_limit() -> None:
    """A loss a decibel above the datasheet is outside the window, with the margin to show it."""
    excess_db = 1.0
    nominal_db = datasheet_path_loss_db(1e9)
    (measurement,) = Calibration({1e9: nominal_db + excess_db}, VERSIONS).measurements()
    assert not measurement.passed
    assert measurement.margin == pytest.approx(PATH_LOSS_TOLERANCE_DB - excess_db)


def test_the_calibration_frequencies_suit_the_generator() -> None:
    """Every calibration frequency is inside the generator's range, listed once, in order."""
    lowest_hz, highest_hz = GENERATOR_RANGE_HZ
    assert all(lowest_hz <= frequency <= highest_hz for frequency in CALIBRATION_FREQUENCIES_HZ)
    assert list(CALIBRATION_FREQUENCIES_HZ) == sorted(set(CALIBRATION_FREQUENCIES_HZ))


def test_the_harmonic_of_the_carrier_can_be_measured() -> None:
    """The harmonic measurement tunes to twice the carrier, which the analyzer must reach."""
    assert 2 * bench_checks.CARRIER_FREQUENCY_HZ <= ANALYZER_HIGHEST_CENTRE_HZ
