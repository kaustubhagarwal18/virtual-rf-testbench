"""The signal generator driver: frequency, level and RF output."""

from drivers.base import ScpiDriver, scpi_number


class SignalGenerator(ScpiDriver):
    """Drives an SG-1 signal generator, or any generator with the same SCPI commands."""

    def configure(self, frequency_hz: float, level_dbm: float) -> None:
        """Set frequency and level as one command group, then wait for it to finish.

        Args:
            frequency_hz: The carrier frequency.
            level_dbm: The output level.
        """
        self.session.write(f"SOUR:FREQ {scpi_number(frequency_hz)}")
        self.session.write(f"SOUR:POW {scpi_number(level_dbm)}")
        self.wait_until_complete()

    def set_output(self, *, on: bool) -> None:
        """Switch the RF output on or off, then wait for it to finish.

        Args:
            on: True to switch RF on.
        """
        self.session.write("OUTP ON" if on else "OUTP OFF")
        self.wait_until_complete()

    def frequency_hz(self) -> float:
        """Read the carrier frequency.

        Returns:
            The frequency in Hz.
        """
        return self.query_number("SOUR:FREQ?")

    def level_dbm(self) -> float:
        """Read the output level.

        Returns:
            The level in dBm.
        """
        return self.query_number("SOUR:POW?")

    def output_on(self) -> bool:
        """Read whether RF is on.

        Returns:
            True if the RF output is on.
        """
        return self.query_bool("OUTP?")

    def offset_db(self) -> float:
        """Read the level offset, which describes the gain of whatever is attached.

        Returns:
            The offset in dB.
        """
        return self.query_number("SOUR:POW:OFFS?")

    def set_offset_db(self, offset_db: float) -> None:
        """Set the level offset, then wait for it to finish.

        Args:
            offset_db: The offset in dB; negative for a path that loses.
        """
        self.session.write(f"SOUR:POW:OFFS {scpi_number(offset_db)}")
        self.wait_until_complete()
