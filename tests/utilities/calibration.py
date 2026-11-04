"""Path-loss calibration: measure what the bench's cable costs, before measuring anything else.

Every level measurement is judged against the loss this calibration measured, not against a
nominal figure, which is how a real bench works: the cable, connectors and adapters are
measured once at the start, and the result is applied to each reading afterwards.

The calibration result is itself a measurement with a limit, taken from the cable's
datasheet. That is what catches a degraded cable: the levels then still agree with the
calibration, and only the calibration disagrees with the datasheet.

The datasheet figure is written here rather than imported from the simulator's physics
model. The test must not check the model against itself, and on a real bench the datasheet is
knowledge the test has independently of the instruments.
"""

import math
from dataclasses import dataclass

from drivers.signal_generator import SignalGenerator
from drivers.spectrum_analyzer import SpectrumAnalyzer
from measurements.record import Limit, Measurement
from tests.utilities.bench import firmware_versions, measure_peak_at

CALIBRATION_FREQUENCIES_HZ = (100e6, 1e9, 2.4e9, 5e9)
"""Where the path loss is measured: the frequencies the level measurements use."""

CALIBRATION_LEVEL_DBM = -10.0
"""The generator level used for calibration: well above the noise, well below compression."""

NOMINAL_LOSS_AT_1GHZ_DB = 1.5
"""The cable's rated loss at 1 GHz, from its datasheet."""

PATH_LOSS_TOLERANCE_DB = 0.5
"""How far the measured loss may sit from the datasheet before the cable is suspect."""

_ONE_GIGAHERTZ = 1e9


def datasheet_path_loss_db(frequency_hz: float) -> float:
    """Return the loss the cable's datasheet promises at a frequency.

    Coaxial loss grows with the square root of frequency, because of the skin effect.

    Args:
        frequency_hz: The frequency.

    Returns:
        The rated loss in dB, as a positive number.
    """
    return NOMINAL_LOSS_AT_1GHZ_DB * math.sqrt(frequency_hz / _ONE_GIGAHERTZ)


def path_loss_limit(frequency_hz: float) -> Limit:
    """Return where the measured path loss must lie at a frequency.

    The window is the datasheet loss either side, but never below zero: a passive cable
    can't have gain, so a negative lower bound would accept a reading that is impossible.

    Args:
        frequency_hz: The frequency.

    Returns:
        The limit for the loss in dB.
    """
    nominal_db = datasheet_path_loss_db(frequency_hz)
    return Limit(
        low=max(0.0, nominal_db - PATH_LOSS_TOLERANCE_DB),
        high=nominal_db + PATH_LOSS_TOLERANCE_DB,
    )


@dataclass(frozen=True)
class Calibration:
    """What the bench's path from generator to analyzer costs, as measured.

    Attributes:
        path_loss_db: The measured loss at each calibration frequency.
        firmware_versions: The firmware the calibration was taken on, by instrument role.
    """

    path_loss_db: dict[float, float]
    firmware_versions: dict[str, str]

    def expected_level_dbm(self, level_dbm: float, frequency_hz: float) -> float:
        """Return what the analyzer should read for a generator level, after the measured loss.

        Args:
            level_dbm: The level the generator is set to.
            frequency_hz: The carrier frequency, which must be a calibrated one.

        Returns:
            The level expected at the analyzer.

        Raises:
            KeyError: If the frequency wasn't calibrated; a level measurement may only be
                judged where the loss is known.
        """
        if frequency_hz not in self.path_loss_db:
            raise KeyError(
                f"no calibration at {frequency_hz:g} Hz; calibrated: {sorted(self.path_loss_db)}"
            )
        return level_dbm - self.path_loss_db[frequency_hz]

    def measurements(self) -> list[Measurement]:
        """Judge the measured loss at each frequency against the cable's datasheet.

        Returns:
            One measurement per calibration frequency, in frequency order.
        """
        return [
            Measurement(
                f"path_loss_{frequency_hz / 1e6:.0f}mhz",
                self.path_loss_db[frequency_hz],
                "dB",
                path_loss_limit(frequency_hz),
                self.firmware_versions,
            )
            for frequency_hz in sorted(self.path_loss_db)
        ]


def measure_path_loss(
    generator: SignalGenerator,
    analyzer: SpectrumAnalyzer,
    frequencies_hz: tuple[float, ...] = CALIBRATION_FREQUENCIES_HZ,
) -> Calibration:
    """Measure the loss between the generator's output and the analyzer's input.

    At each frequency the generator sends a known level and the analyzer reads it; the
    difference is the loss of everything in between.

    Args:
        generator: The generator driver.
        analyzer: The analyzer driver.
        frequencies_hz: Where to measure.

    Returns:
        The calibration, with the loss at each frequency.
    """
    path_loss_db = {}
    for frequency_hz in frequencies_hz:
        peak = measure_peak_at(generator, analyzer, frequency_hz, CALIBRATION_LEVEL_DBM)
        path_loss_db[frequency_hz] = CALIBRATION_LEVEL_DBM - peak.level_dbm
    generator.set_output(on=False)
    return Calibration(path_loss_db, firmware_versions(generator, analyzer))
