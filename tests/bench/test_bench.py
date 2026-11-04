"""The bench suite, against the simulators running in Docker.

The instruments are black boxes here: the tests reach them only over SCPI through the
drivers, and judge every level against the path loss the calibration measured, exactly as a
real bench does. Markers separate the quick checks (smoke), the measurements (regression)
and the error handling (negative).
"""

import os
import socket
from collections.abc import Callable

import pytest

from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from measurements.record import Limit, Measurement
from tests.utilities import bench_checks
from tests.utilities.bench import firmware_versions, measure_peak_at
from tests.utilities.calibration import CALIBRATION_FREQUENCIES_HZ, Calibration

Bench = tuple[SignalGenerator, SpectrumAnalyzer]
Recorder = Callable[..., None]

CARRIER_FREQUENCY_HZ = 1e9
"""The frequency the measurements that don't sweep the band use."""

LEVELS_DBM = (-30.0, -20.0, 0.0)
"""Levels checked at the carrier frequency, spanning the generator's useful range."""

NOISE_FLOOR_CASES = ((1e3, -110.0), (1e5, -90.0))
"""Resolution bandwidths and the highest peak allowed at each: wider lets more noise in."""

LEVEL_TOLERANCE_DB = 0.3
"""Allowed level error once calibration has removed the path loss: reading noise only."""

OFFSET_UNDER_TEST_DB = 2.0
"""How much offset the offset test applies, in dB."""


@pytest.mark.smoke
def test_instruments_identify_as_the_release_under_test(bench: Bench) -> None:
    """Both instruments are VirtualRF, the right models, and the release the image was built as."""
    generator, analyzer = bench
    identities = (generator.identity(), analyzer.identity())
    read = [(identity.manufacturer, identity.model) for identity in identities]
    expected = [("VirtualRF", "SG-1"), ("VirtualRF", "SA-1")]
    assert read == expected, f"instruments identify as {read}, expected {expected}"
    versions = {identity.firmware_version for identity in identities}
    assert len(versions) == 1, f"the instruments run different releases: {sorted(versions)}"
    expected_version = os.environ.get("FIRMWARE_VERSION")
    if expected_version:
        assert versions == {expected_version}, (
            f"instruments report {sorted(versions)}, but the image was built as {expected_version}"
        )


@pytest.mark.smoke
def test_reset_leaves_the_generator_at_safe_defaults(
    bench: Bench, record_measurements: Recorder
) -> None:
    """After *RST the generator sits at 1 GHz and -30 dBm with RF off.

    The frequency and level are readings, so they are recorded with their limits rather
    than asserted bare: a failure then says what was read and by how much it was out.
    """
    generator, _ = bench
    record_measurements(*bench_checks.reset_defaults(generator))
    assert not generator.output_on(), "RF output is on after *RST, which is not a safe default"


@pytest.mark.smoke
def test_path_loss_calibration_matches_the_cable_datasheet(
    calibration: Calibration, record_measurements: Recorder
) -> None:
    """The measured path loss agrees with the cable's rated loss at every frequency."""
    record_measurements(*calibration.measurements())


@pytest.mark.regression
def test_a_level_offset_shifts_the_level_at_the_connector(
    bench: Bench, record_measurements: Recorder
) -> None:
    """A level offset makes the generator compensate for a lossy path, and *RST then clears it.

    The offset this test sets must not reach the tests that follow: their setup sequence
    checks that, so a firmware release that stops clearing it on *RST is caught.
    """
    generator, analyzer = bench
    level_dbm = bench_checks.CARRIER_LEVEL_DBM
    without_offset = measure_peak_at(generator, analyzer, CARRIER_FREQUENCY_HZ, level_dbm)
    generator.set_offset_db(-OFFSET_UNDER_TEST_DB)
    with_offset = measure_peak_at(generator, analyzer, CARRIER_FREQUENCY_HZ, level_dbm)
    record_measurements(
        Measurement(
            "level_offset_shift",
            with_offset.level_dbm - without_offset.level_dbm,
            "dB",
            Limit.window(OFFSET_UNDER_TEST_DB, LEVEL_TOLERANCE_DB),
            firmware_versions(generator, analyzer),
        )
    )


@pytest.mark.regression
@pytest.mark.parametrize("frequency_hz", CALIBRATION_FREQUENCIES_HZ)
def test_level_and_frequency_across_the_band(
    bench: Bench, calibration: Calibration, frequency_hz: float, record_measurements: Recorder
) -> None:
    """The analyzer reads the calibrated level, and the carrier sits on its frequency."""
    level_dbm = bench_checks.CARRIER_LEVEL_DBM
    record_measurements(
        *bench_checks.carrier_level_and_frequency(
            *bench,
            calibration.expected_level_dbm(level_dbm, frequency_hz),
            frequency_hz=frequency_hz,
            level_dbm=level_dbm,
            tolerance_db=LEVEL_TOLERANCE_DB,
        )
    )


@pytest.mark.regression
@pytest.mark.parametrize("level_dbm", LEVELS_DBM)
def test_level_accuracy_across_levels(
    bench: Bench, calibration: Calibration, level_dbm: float, record_measurements: Recorder
) -> None:
    """Every level reads back correctly: the path loss doesn't depend on the level."""
    record_measurements(
        *bench_checks.carrier_level_and_frequency(
            *bench,
            calibration.expected_level_dbm(level_dbm, CARRIER_FREQUENCY_HZ),
            frequency_hz=CARRIER_FREQUENCY_HZ,
            level_dbm=level_dbm,
            tolerance_db=LEVEL_TOLERANCE_DB,
        )
    )


@pytest.mark.regression
def test_channel_power_at_the_carrier(
    bench: Bench, calibration: Calibration, record_measurements: Recorder
) -> None:
    """Channel power around the carrier matches the carrier's calibrated level."""
    expected_dbm = calibration.expected_level_dbm(
        bench_checks.CARRIER_LEVEL_DBM, CARRIER_FREQUENCY_HZ
    )
    record_measurements(bench_checks.carrier_channel_power(*bench, expected_dbm))


@pytest.mark.regression
@pytest.mark.parametrize(("rbw_hz", "limit_dbm"), NOISE_FLOOR_CASES)
def test_noise_floor_with_rf_off(
    bench: Bench, rbw_hz: float, limit_dbm: float, record_measurements: Recorder
) -> None:
    """With RF off the analyzer sees only its own noise, which grows with the bandwidth."""
    record_measurements(bench_checks.noise_floor_with_rf_off(*bench, rbw_hz, limit_dbm))


@pytest.mark.regression
def test_second_harmonic_is_below_its_limit(bench: Bench, record_measurements: Recorder) -> None:
    """The generator's second harmonic stays at least 40 dB below the carrier."""
    record_measurements(bench_checks.second_harmonic_suppression(*bench, CARRIER_FREQUENCY_HZ))


@pytest.mark.regression
def test_switching_the_output_off_removes_the_carrier(
    bench: Bench, record_measurements: Recorder
) -> None:
    """Switching RF on lifts the reading far above the floor, so the output really switches."""
    record_measurements(bench_checks.carrier_to_floor(*bench, CARRIER_FREQUENCY_HZ))


@pytest.mark.negative
def test_out_of_range_level_is_rejected_and_changes_nothing(bench: Bench) -> None:
    """+25 dBm is beyond the generator's range: -222, and the level stays at its default."""
    bench_checks.check_out_of_range_level_is_rejected(bench[0])


@pytest.mark.negative
def test_unknown_command_is_reported(bench: Bench) -> None:
    """A command the instrument doesn't know raises the instrument's -113, not silence."""
    generator, _ = bench
    bench_checks.check_command_is_rejected(generator, "SOURce:BOGus 1", "-113")


@pytest.mark.negative
def test_a_parameter_of_the_wrong_type_is_reported(bench: Bench) -> None:
    """A word where the instrument expects a state raises -104, naming what it received."""
    generator, _ = bench
    bench_checks.check_command_is_rejected(generator, "OUTP MAYBE", "-104")


@pytest.mark.negative
def test_the_bench_recovers_after_a_dropped_connection(
    bench: Bench,
    calibration: Calibration,
    generator_address: tuple[str, int],
    record_measurements: Recorder,
) -> None:
    """A client that vanishes mid-command doesn't take the instrument with it.

    The fragment is a valid command on purpose. An instrument parses whatever arrived when
    the stream ends, and its error queue is shared by every client, so an invalid fragment
    would queue an error that reaches the next session at a moment nothing controls.
    """
    with socket.create_connection(generator_address, timeout=5.0) as abandoned:
        abandoned.sendall(b"*CLS")
    generator, analyzer = bench
    level_dbm = bench_checks.CARRIER_LEVEL_DBM
    record_measurements(
        *bench_checks.carrier_level_and_frequency(
            generator,
            analyzer,
            calibration.expected_level_dbm(level_dbm, CARRIER_FREQUENCY_HZ),
            frequency_hz=CARRIER_FREQUENCY_HZ,
            level_dbm=level_dbm,
            tolerance_db=LEVEL_TOLERANCE_DB,
        )
    )
