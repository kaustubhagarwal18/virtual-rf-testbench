"""The spectrum analyzer driver: settings, single sweeps, the peak marker and channel power."""

from drivers.base import ScpiDriver, scpi_number
from drivers.types import PeakReading


class SpectrumAnalyzer(ScpiDriver):
    """Drives an SA-1 spectrum analyzer, or any analyzer with the same SCPI commands.

    Every measurement takes its own single sweep and waits for it, so a reading always
    reflects the bench as it was when the measurement was asked for.
    """

    def configure(self, centre_hz: float, span_hz: float, rbw_hz: float) -> None:
        """Set centre, span and RBW, and select single sweep, as one command group.

        Args:
            centre_hz: The centre frequency.
            span_hz: The frequency span.
            rbw_hz: The resolution bandwidth.
        """
        self.session.write(f"FREQ:CENT {scpi_number(centre_hz)}")
        self.session.write(f"FREQ:SPAN {scpi_number(span_hz)}")
        self.session.write(f"BAND {scpi_number(rbw_hz)}")
        self.session.write("INIT:CONT OFF")
        self.wait_until_complete()

    def sweep(self) -> None:
        """Take one sweep and wait until it has finished."""
        self.session.write("INIT")
        self.wait_until_complete()

    def measure_peak(self) -> PeakReading:
        """Take a sweep, put the marker on its peak, and read the marker.

        Returns:
            The peak's frequency and level.
        """
        self.sweep()
        self.session.write("CALC1:MARK1:MAX")
        return PeakReading(
            frequency_hz=self.query_number("CALC1:MARK1:X?"),
            level_dbm=self.query_number("CALC1:MARK1:Y?"),
        )

    def measure_channel_power_dbm(self, channel_bandwidth_hz: float) -> float:
        """Set the channel width, take a sweep, and read the power in the channel.

        Args:
            channel_bandwidth_hz: The channel width, centred on the centre frequency.

        Returns:
            The channel power in dBm.
        """
        self.session.write(f"POW:ACH:BAND {scpi_number(channel_bandwidth_hz)}")
        self.sweep()
        return self.query_number("CALC1:MARK1:FUNC:POW:RES? CPOW")

    def set_channel_bandwidth_hz(self, channel_bandwidth_hz: float) -> None:
        """Set the bandwidth channel power is measured over, then wait for it to finish.

        Args:
            channel_bandwidth_hz: The channel bandwidth.
        """
        self.session.write(f"POW:ACH:BAND {scpi_number(channel_bandwidth_hz)}")
        self.wait_until_complete()
