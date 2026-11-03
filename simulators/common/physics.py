"""The bench physics model: what the analyzer reads, given what the generator sends.

The generator and the analyzer are separate simulators. The analyzer takes the generator's
SourceState and turns it into readings with this model: the generator level, minus cable
loss, plus a small frequency-dependent flatness error, added to the analyzer's own noise
floor, plus a little seeded random noise so that runs are reproducible.
"""

import math
from dataclasses import dataclass

import numpy as np

THERMAL_NOISE_DBM_PER_HZ = -174.0
"""Thermal noise power density at room temperature (290 K)."""

_ONE_GIGAHERTZ = 1e9
_VISIBLE_TONE_MARGIN_DB = 10.0


@dataclass(frozen=True)
class SourceState:
    """What the signal generator is sending, as the analyzer sees it.

    Attributes:
        frequency_hz: The carrier frequency.
        level_dbm: The output level at the generator's connector.
        output_on: Whether the RF output is switched on.
    """

    frequency_hz: float
    level_dbm: float
    output_on: bool


@dataclass(frozen=True)
class BenchParameters:
    """The bench's physical properties; the defaults describe a short coax cable.

    Attributes:
        cable_loss_at_1ghz_db: Cable loss at 1 GHz; it grows with the square root of frequency.
        flatness_ripple_db: Peak size of the analyzer's frequency-response error.
        flatness_period_hz: How far apart in frequency the flatness ripple repeats.
        noise_figure_db: The analyzer's noise figure, which sets its noise floor.
        repeatability_db: Standard deviation of readings on a strong tone.
        noise_floor_spread_db: Standard deviation of readings on the bare noise floor.
        seed: Seed for the reading noise; the same seed gives the same readings.
    """

    cable_loss_at_1ghz_db: float = 1.5
    flatness_ripple_db: float = 0.15
    flatness_period_hz: float = 1.7e9
    noise_figure_db: float = 24.0
    repeatability_db: float = 0.02
    noise_floor_spread_db: float = 0.5
    seed: int = 20261103


def cable_loss_db(frequency_hz: float, loss_at_1ghz_db: float) -> float:
    """Return the cable loss at a frequency, scaled with its square root (skin effect).

    Args:
        frequency_hz: The signal frequency.
        loss_at_1ghz_db: The cable's loss at 1 GHz.

    Returns:
        The loss in dB, as a positive number.

    Raises:
        ValueError: If the frequency isn't positive.
    """
    if frequency_hz <= 0:
        raise ValueError(f"Cable loss needs a positive frequency, got {frequency_hz} Hz")
    return loss_at_1ghz_db * math.sqrt(frequency_hz / _ONE_GIGAHERTZ)


def flatness_error_db(frequency_hz: float, ripple_db: float, period_hz: float) -> float:
    """Return the analyzer's level error at a frequency: a small, smooth ripple.

    Args:
        frequency_hz: The signal frequency.
        ripple_db: The peak size of the error.
        period_hz: How far apart in frequency the ripple repeats.

    Returns:
        The error in dB, between -ripple_db and +ripple_db.
    """
    return ripple_db * math.sin(2 * math.pi * frequency_hz / period_hz)


def noise_floor_dbm(rbw_hz: float, noise_figure_db: float) -> float:
    """Return the analyzer's noise floor for a resolution bandwidth.

    Args:
        rbw_hz: The resolution bandwidth.
        noise_figure_db: The analyzer's noise figure.

    Returns:
        The noise floor: thermal noise plus noise figure plus 10*log10(RBW).

    Raises:
        ValueError: If the RBW isn't positive.
    """
    if rbw_hz <= 0:
        raise ValueError(f"The noise floor needs a positive RBW, got {rbw_hz} Hz")
    return THERMAL_NOISE_DBM_PER_HZ + noise_figure_db + 10 * math.log10(rbw_hz)


def power_sum_dbm(first_dbm: float, *other_dbm: float) -> float:
    """Add signals as powers, the way an analyzer sees a tone on top of its noise.

    Args:
        first_dbm: The first level.
        *other_dbm: Any further levels.

    Returns:
        The total level in dBm.
    """
    total_milliwatts = sum(10 ** (level / 10) for level in (first_dbm, *other_dbm))
    return 10 * math.log10(total_milliwatts)


class PhysicsModel:
    """Turns the generator's state into analyzer readings, with seeded reading noise."""

    def __init__(self, parameters: BenchParameters) -> None:
        """Create the model with its random generator seeded.

        Args:
            parameters: The bench's physical properties.
        """
        self.parameters = parameters
        self._random = np.random.default_rng(parameters.seed)

    def reset(self) -> None:
        """Reseed the reading noise, so the readings after *RST always repeat."""
        self._random = np.random.default_rng(self.parameters.seed)

    def expected_peak_dbm(self, source: SourceState, rbw_hz: float) -> float:
        """Return the peak level the analyzer would read without any reading noise.

        Args:
            source: What the generator is sending.
            rbw_hz: The analyzer's resolution bandwidth.

        Returns:
            The tone at the analyzer added to the noise floor, or just the noise floor when
            the generator's output is off.
        """
        floor = noise_floor_dbm(rbw_hz, self.parameters.noise_figure_db)
        if not source.output_on:
            return floor
        return power_sum_dbm(self._level_at_analyzer_dbm(source), floor)

    def peak_reading_dbm(self, source: SourceState, rbw_hz: float) -> float:
        """Return one peak reading, with the noise a real sweep would add.

        Args:
            source: What the generator is sending.
            rbw_hz: The analyzer's resolution bandwidth.

        Returns:
            The expected peak plus random noise: a small spread on a clear tone, a wider
            one when only the noise floor is visible.
        """
        spread = (
            self.parameters.repeatability_db
            if self._tone_is_visible(source, rbw_hz)
            else self.parameters.noise_floor_spread_db
        )
        return self.expected_peak_dbm(source, rbw_hz) + float(self._random.normal(0.0, spread))

    def _level_at_analyzer_dbm(self, source: SourceState) -> float:
        """Return the tone level at the analyzer's input, after the cable and flatness error.

        Args:
            source: What the generator is sending.

        Returns:
            The generator level minus cable loss plus the flatness error.
        """
        parameters = self.parameters
        return (
            source.level_dbm
            - cable_loss_db(source.frequency_hz, parameters.cable_loss_at_1ghz_db)
            + flatness_error_db(
                source.frequency_hz, parameters.flatness_ripple_db, parameters.flatness_period_hz
            )
        )

    def _tone_is_visible(self, source: SourceState, rbw_hz: float) -> bool:
        """Check whether the tone stands clearly above the noise floor.

        Args:
            source: What the generator is sending.
            rbw_hz: The analyzer's resolution bandwidth.

        Returns:
            True if the output is on and the tone is at least 10 dB above the floor.
        """
        if not source.output_on:
            return False
        floor = noise_floor_dbm(rbw_hz, self.parameters.noise_figure_db)
        return self._level_at_analyzer_dbm(source) - floor >= _VISIBLE_TONE_MARGIN_DB
