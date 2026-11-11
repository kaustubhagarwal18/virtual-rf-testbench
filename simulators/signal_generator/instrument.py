"""The simulated signal generator's SCPI commands: frequency, level and RF output."""

from dataclasses import replace

from simulators.common.instrument import Identity, ScpiInstrument
from simulators.common.numbers import (
    FREQUENCY_UNITS,
    LEVEL_UNITS,
    check_range,
    format_number,
    parse_number,
)
from simulators.common.parser import expect_no_parameters, expect_one_parameter, parse_boolean
from simulators.common.physics import SourceState
from simulators.signal_generator.releases import apply_release_changes

MANUFACTURER = "VirtualRF"
MODEL = "SG-1"
FREQUENCY_RANGE_HZ = (9e3, 6e9)
LEVEL_RANGE_DBM = (-120.0, 20.0)
OFFSET_RANGE_DB = (-50.0, 50.0)
DEFAULT_STATE = SourceState(frequency_hz=1e9, level_dbm=-30.0, output_on=False)
"""The settings at power-on and after *RST: 1 GHz, -30 dBm, RF off."""

DEFAULT_OFFSET_DB = 0.0
"""The level offset at power-on and after *RST: none."""


class SignalGeneratorScpi(ScpiInstrument):
    """A CW signal generator: one carrier with a settable frequency, level and RF output."""

    def __init__(self, firmware_version: str, serial_number: str = "100001") -> None:
        """Create the generator at its default settings, with its commands registered.

        Args:
            firmware_version: The simulator release, reported by *IDN?; a release listed in
                releases.py also changes some commands.
            serial_number: The unit's serial number, reported by *IDN?.
        """
        super().__init__(Identity(MANUFACTURER, MODEL, serial_number, firmware_version))
        self._state = DEFAULT_STATE
        self._offset_db = DEFAULT_OFFSET_DB
        self._reset_clears_offset = True
        self._frequency_error_ppm = 0.0
        self.register(
            "[SOURce]:FREQuency[:CW]", write=self._set_frequency, query=self._query_frequency
        )
        self.register(
            "[SOURce]:POWer[:LEVel][:IMMediate][:AMPLitude]",
            write=self._set_level,
            query=self._query_level,
        )
        self.register("OUTPut[:STATe]", write=self._set_output, query=self._query_output)
        self.register(
            "[SOURce]:POWer[:LEVel][:IMMediate]:OFFSet",
            write=self._set_offset,
            query=self._query_offset,
        )
        apply_release_changes(self, firmware_version)

    def source_state(self) -> SourceState:
        """Report what leaves the generator's connector, for the analyzer.

        A level offset describes the gain of whatever is attached, so the level asked for is
        the level wanted at the far end and the connector makes up the difference. An offset
        of -1.5 dB, for a cable that loses 1.5 dB, puts out 1.5 dB more than requested.

        Returns:
            The frequency, off by the release's frequency error if it has one, the level at
            the connector and the RF output state.
        """
        return replace(
            self._state,
            frequency_hz=self._state.frequency_hz * (1 + self._frequency_error_ppm * 1e-6),
            level_dbm=self._state.level_dbm - self._offset_db,
        )

    def set_frequency_error_ppm(self, error_ppm: float) -> None:
        """Put the output frequency this many parts per million high, for a faulty release.

        Args:
            error_ppm: The error.
        """
        self._frequency_error_ppm = error_ppm

    def on_reset(self) -> None:
        """Return to the default settings, as *RST does.

        The offset is part of that, unless this release keeps it.
        """
        self._state = DEFAULT_STATE
        if self._reset_clears_offset:
            self._offset_db = DEFAULT_OFFSET_DB

    def keep_offset_across_reset(self) -> None:
        """Stop *RST from clearing the level offset, as a regressed release does.

        Used by a simulated release (see releases.py), never by a healthy instrument.
        """
        self._reset_clears_offset = False

    def _set_offset(self, parameters: tuple[str, ...]) -> None:
        """Handle SOURce:POWer:OFFSet.

        Args:
            parameters: One offset, with or without a unit.
        """
        offset_db = parse_number(expect_one_parameter(parameters), LEVEL_UNITS)
        check_range(offset_db, OFFSET_RANGE_DB, "dB")
        self._offset_db = offset_db

    def _query_offset(self, parameters: tuple[str, ...]) -> str:
        """Answer SOURce:POWer:OFFSet?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The offset in dB.
        """
        expect_no_parameters(parameters)
        return format_number(self._offset_db)

    def _set_frequency(self, parameters: tuple[str, ...]) -> None:
        """Handle SOURce:FREQuency.

        Args:
            parameters: One frequency, with or without a unit.
        """
        frequency_hz = parse_number(expect_one_parameter(parameters), FREQUENCY_UNITS)
        check_range(frequency_hz, FREQUENCY_RANGE_HZ, "Hz")
        self._state = replace(self._state, frequency_hz=frequency_hz)

    def _query_frequency(self, parameters: tuple[str, ...]) -> str:
        """Answer SOURce:FREQuency?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The frequency in Hz.
        """
        expect_no_parameters(parameters)
        return format_number(self._state.frequency_hz)

    def _set_level(self, parameters: tuple[str, ...]) -> None:
        """Handle SOURce:POWer.

        Args:
            parameters: One level, in dBm.
        """
        level_dbm = parse_number(expect_one_parameter(parameters), LEVEL_UNITS)
        check_range(level_dbm, LEVEL_RANGE_DBM, "dBm")
        self._state = replace(self._state, level_dbm=level_dbm)

    def _query_level(self, parameters: tuple[str, ...]) -> str:
        """Answer SOURce:POWer?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The level in dBm.
        """
        expect_no_parameters(parameters)
        return format_number(self._state.level_dbm)

    def _set_output(self, parameters: tuple[str, ...]) -> None:
        """Handle OUTPut[:STATe].

        Args:
            parameters: ON, OFF, 1 or 0.
        """
        output_on = parse_boolean(expect_one_parameter(parameters))
        self._state = replace(self._state, output_on=output_on)

    def _query_output(self, parameters: tuple[str, ...]) -> str:
        """Answer OUTPut?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            '1' if RF is on, '0' if it's off.
        """
        expect_no_parameters(parameters)
        return "1" if self._state.output_on else "0"
