"""Small interfaces the bench tests use, so simulated and real instruments are interchangeable."""

from typing import Protocol

from drivers.types import PeakReading


class LevelSource(Protocol):
    """Something that produces a carrier at a set frequency and level."""

    def configure(self, frequency_hz: float, level_dbm: float) -> None:
        """Set frequency and level.

        Args:
            frequency_hz: The carrier frequency.
            level_dbm: The output level.
        """

    def set_output(self, *, on: bool) -> None:
        """Switch the output on or off.

        Args:
            on: True to switch the output on.
        """


class PowerMeter(Protocol):
    """Something that measures the level of a carrier."""

    def measure_peak(self) -> PeakReading:
        """Measure the strongest signal.

        Returns:
            Its frequency and level.
        """

    def measure_channel_power_dbm(self, channel_bandwidth_hz: float) -> float:
        """Measure the power in a channel.

        Args:
            channel_bandwidth_hz: The channel width.

        Returns:
            The channel power in dBm.
        """
