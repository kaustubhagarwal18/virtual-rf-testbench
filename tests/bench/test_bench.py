"""The bench suite, against the simulators running in Docker."""

import os
from collections.abc import Callable

import pytest

from drivers.session import InstrumentError
from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from tests.support import bench_checks

Bench = tuple[SignalGenerator, SpectrumAnalyzer]
Recorder = Callable[..., None]

NOMINAL_CABLE_LOSS_1GHZ_DB = 1.5
"""The bench cable's loss at 1 GHz, from its datasheet, until calibration measures it."""

EXPECTED_CARRIER_LEVEL_DBM = bench_checks.CARRIER_LEVEL_DBM - NOMINAL_CABLE_LOSS_1GHZ_DB
"""What the analyzer should read: the generator level minus the cable's nominal loss."""


@pytest.mark.smoke
def test_instruments_identify_as_the_release_under_test(bench: Bench) -> None:
    """Both instruments are VirtualRF, the right models, and the release the image was built as."""
    generator, analyzer = bench
    identities = (generator.identity(), analyzer.identity())
    assert [(identity.manufacturer, identity.model) for identity in identities] == [
        ("VirtualRF", "SG-1"),
        ("VirtualRF", "SA-1"),
    ]
    versions = {identity.firmware_version for identity in identities}
    assert len(versions) == 1, f"the instruments run different releases: {sorted(versions)}"
    expected_version = os.environ.get("FIRMWARE_VERSION")
    if expected_version:
        assert versions == {expected_version}


@pytest.mark.smoke
def test_reset_leaves_the_generator_at_safe_defaults(bench: Bench) -> None:
    """After *RST the generator sits at 1 GHz and -30 dBm with RF off."""
    generator, _ = bench
    assert generator.frequency_hz() == bench_checks.CARRIER_FREQUENCY_HZ
    assert generator.level_dbm() == bench_checks.DEFAULT_LEVEL_DBM
    assert not generator.output_on()


@pytest.mark.regression
def test_level_and_frequency_at_1ghz(bench: Bench, record_measurements: Recorder) -> None:
    """The analyzer reads -10 dBm minus the cable's loss within 0.5 dB, at 1 GHz within 1 kHz."""
    record_measurements(
        *bench_checks.carrier_level_and_frequency(*bench, EXPECTED_CARRIER_LEVEL_DBM)
    )


@pytest.mark.regression
def test_channel_power_at_1ghz(bench: Bench, record_measurements: Recorder) -> None:
    """Channel power around the carrier matches the level after the cable, within 0.5 dB."""
    record_measurements(bench_checks.carrier_channel_power(*bench, EXPECTED_CARRIER_LEVEL_DBM))


@pytest.mark.regression
def test_noise_floor_with_rf_off(bench: Bench, record_measurements: Recorder) -> None:
    """With RF off, the analyzer's peak stays below -110 dBm at 1 kHz RBW."""
    record_measurements(bench_checks.noise_floor_with_rf_off(*bench))


@pytest.mark.negative
def test_out_of_range_level_is_rejected_and_changes_nothing(bench: Bench) -> None:
    """+25 dBm is beyond the generator's range: -222, and the level stays at its default."""
    bench_checks.check_out_of_range_level_is_rejected(bench[0])


@pytest.mark.negative
def test_unknown_command_is_reported(bench: Bench) -> None:
    """A command the instrument doesn't know raises the instrument's -113, not silence."""
    generator, _ = bench
    with pytest.raises(InstrumentError, match="-113"):
        generator.session.write("SOURce:BOGus 1")
