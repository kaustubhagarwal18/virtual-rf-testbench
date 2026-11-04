"""The bench physics model: what the analyzer reads, given what the generator sends.

The generator and the analyzer are separate simulators. The analyzer takes the generator's
SourceState and turns it into readings with this model: the generator level, minus cable
loss, plus a small frequency-dependent flatness error, added to the analyzer's own noise
floor, plus a little seeded random noise so that runs are reproducible.
"""

import math
from dataclasses import dataclass, field, replace

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
class ReadingNoise:
    """How much a reading scatters, and the seed that makes the scatter repeatable.

    Attributes:
        repeatability_db: Standard deviation of readings on a strong tone.
        floor_spread_db: Standard deviation of readings on the bare noise floor.
        seed: Seed for the reading noise; the same seed gives the same readings.
    """

    repeatability_db: float = 0.02
    floor_spread_db: float = 0.5
    seed: int = 20261103


@dataclass(frozen=True)
class BenchParameters:
    """The bench's physical properties; the defaults describe a short coax cable.

    Attributes:
        cable_loss_at_1ghz_db: Cable loss at 1 GHz; it grows with the square root of frequency.
        flatness_ripple_db: Peak size of the analyzer's frequency-response error.
        flatness_period_hz: How far apart in frequency the flatness ripple repeats.
        noise_figure_db: The analyzer's noise figure, which sets its noise floor.
        second_harmonic_dbc: The second harmonic's level relative to the carrier, as a
            generator's harmonic suppression specification states it.
        noise: How much each reading scatters, and its seed.
    """

    cable_loss_at_1ghz_db: float = 1.5
    flatness_ripple_db: float = 0.15
    flatness_period_hz: float = 1.7e9
    noise_figure_db: float = 24.0
    second_harmonic_dbc: float = -45.0
    noise: ReadingNoise = field(default_factory=ReadingNoise)


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
        self._random = np.random.default_rng(parameters.noise.seed)

    def signals(self, source: SourceState) -> list[SourceState]:
        """Return every tone the generator puts on the cable, each as a source state of its own.

        A real generator isn't spectrally clean: besides the carrier there is a second
        harmonic at twice the frequency, below the carrier by the generator's harmonic
        suppression. Describing it as a source state means the cable's loss and the
        analyzer's flatness are worked out at the harmonic's own frequency, as they are in
        reality.

        Args:
            source: What the generator is set to.

        Returns:
            The carrier and its second harmonic, strongest first, or nothing with the output
            switched off.
        """
        if not source.output_on:
            return []
        harmonic = replace(
            source,
            frequency_hz=2 * source.frequency_hz,
            level_dbm=source.level_dbm + self.parameters.second_harmonic_dbc,
        )
        return [source, harmonic]

    def reset(self) -> None:
        """Reseed the reading noise, so the readings after *RST always repeat."""
        self._random = np.random.default_rng(self.parameters.noise.seed)

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
            self.parameters.noise.repeatability_db
            if self.tone_is_visible(source, rbw_hz)
            else self.parameters.noise.floor_spread_db
        )
        return self.expected_peak_dbm(source, rbw_hz) + float(self._random.normal(0.0, spread))

    def expected_channel_power_dbm(
        self, source: SourceState, centre_hz: float, channel_bandwidth_hz: float
    ) -> float:
        """Return the channel power the analyzer would read without any reading noise.

        Args:
            source: What the generator is sending.
            centre_hz: The channel's centre frequency.
            channel_bandwidth_hz: The channel's width.

        Returns:
            The noise in the channel, plus the tone when it lies inside the channel.
        """
        expected_dbm, _ = self._channel_power(source, centre_hz, channel_bandwidth_hz)
        return expected_dbm

    def channel_power_reading_dbm(
        self, source: SourceState, centre_hz: float, channel_bandwidth_hz: float
    ) -> float:
        """Return one channel power reading, with the noise a real measurement would add.

        Args:
            source: What the generator is sending.
            centre_hz: The channel's centre frequency.
            channel_bandwidth_hz: The channel's width.

        Returns:
            The expected channel power plus random noise: the small spread when a tone stands
            clearly above the channel noise, the wider one otherwise.
        """
        expected_dbm, tone_stands_out = self._channel_power(source, centre_hz, channel_bandwidth_hz)
        spread = (
            self.parameters.noise.repeatability_db
            if tone_stands_out
            else self.parameters.noise.floor_spread_db
        )
        return expected_dbm + float(self._random.normal(0.0, spread))

    def _channel_power(
        self, source: SourceState, centre_hz: float, channel_bandwidth_hz: float
    ) -> tuple[float, bool]:
        """Work out a channel's power and whether a tone stands out in it, in one pass.

        Args:
            source: What the generator is sending.
            centre_hz: The channel's centre frequency.
            channel_bandwidth_hz: The channel's width.

        Returns:
            The expected channel power, and whether a tone in the channel stands at least
            10 dB above the channel noise.
        """
        noise_dbm = noise_floor_dbm(channel_bandwidth_hz, self.parameters.noise_figure_db)
        if not _tone_in_channel(source, centre_hz, channel_bandwidth_hz):
            return noise_dbm, False
        expected_dbm = power_sum_dbm(self._level_at_analyzer_dbm(source), noise_dbm)
        return expected_dbm, self._stands_out(source, noise_dbm)

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

    def tone_is_visible(self, source: SourceState, rbw_hz: float) -> bool:
        """Check whether the tone stands clearly above the noise floor.

        Args:
            source: What the generator is sending.
            rbw_hz: The analyzer's resolution bandwidth.

        Returns:
            True if the output is on and the tone is at least 10 dB above the floor.
        """
        if not source.output_on:
            return False
        return self._stands_out(source, noise_floor_dbm(rbw_hz, self.parameters.noise_figure_db))

    def _stands_out(self, source: SourceState, floor_dbm: float) -> bool:
        """Check whether the tone stands at least 10 dB above a noise level.

        Args:
            source: What the generator is sending.
            floor_dbm: The noise level to compare with.

        Returns:
            True if the tone at the analyzer is 10 dB or more above the noise.
        """
        return self._level_at_analyzer_dbm(source) - floor_dbm >= _VISIBLE_TONE_MARGIN_DB


def _tone_in_channel(source: SourceState, centre_hz: float, channel_bandwidth_hz: float) -> bool:
    """Check whether the generator's tone is on and inside a channel.

    Args:
        source: What the generator is sending.
        centre_hz: The channel's centre frequency.
        channel_bandwidth_hz: The channel's width.

    Returns:
        True if the output is on and the tone lies within half a bandwidth of the centre.
    """
    return source.output_on and abs(source.frequency_hz - centre_hz) <= channel_bandwidth_hz / 2
