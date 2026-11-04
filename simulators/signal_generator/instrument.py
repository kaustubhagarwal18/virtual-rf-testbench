"""The simulated signal generator's SCPI commands: frequency, level and RF output."""

from dataclasses import replace

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.instrument import Identity, ScpiInstrument
from simulators.common.numbers import FREQUENCY_UNITS, LEVEL_UNITS, format_number, parse_number
from simulators.common.parser import expect_no_parameters, expect_one_parameter, parse_boolean
from simulators.common.physics import SourceState

MANUFACTURER = "VirtualRF"
MODEL = "SG-1"
FREQUENCY_RANGE_HZ = (9e3, 6e9)
LEVEL_RANGE_DBM = (-120.0, 20.0)
DEFAULT_STATE = SourceState(frequency_hz=1e9, level_dbm=-30.0, output_on=False)
"""The settings at power-on and after *RST: 1 GHz, -30 dBm, RF off."""


def _check_range(value: float, valid_range: tuple[float, float], unit: str) -> float:
    """Return the value if it lies within the instrument's range.

    Args:
        value: The requested value.
        valid_range: The lowest and highest value the instrument accepts.
        unit: The value's unit, for the error detail.

    Returns:
        The value, unchanged.

    Raises:
        ScpiCommandError: -222 if the value is outside the range.
    """
    low, high = valid_range
    if not low <= value <= high:
        raise ScpiCommandError(
            ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, f"{format_number(value)} {unit}")
        )
    return value


class SignalGeneratorScpi(ScpiInstrument):
    """A CW signal generator: one carrier with a settable frequency, level and RF output."""

    def __init__(self, firmware_version: str, serial_number: str = "100001") -> None:
        """Create the generator at its default settings, with its commands registered.

        Args:
            firmware_version: The simulator release, reported by *IDN?.
            serial_number: The unit's serial number, reported by *IDN?.
        """
        super().__init__(Identity(MANUFACTURER, MODEL, serial_number, firmware_version))
        self._state = DEFAULT_STATE
        self.register(
            "[SOURce]:FREQuency[:CW]", write=self._set_frequency, query=self._query_frequency
        )
        self.register(
            "[SOURce]:POWer[:LEVel][:IMMediate][:AMPLitude]",
            write=self._set_level,
            query=self._query_level,
        )
        self.register("OUTPut[:STATe]", write=self._set_output, query=self._query_output)

    def source_state(self) -> SourceState:
        """Report what the generator is sending, for the analyzer.

        Returns:
            The current frequency, level and RF output state.
        """
        return self._state

    def on_reset(self) -> None:
        """Return to the default settings, as *RST does."""
        self._state = DEFAULT_STATE

    def _set_frequency(self, parameters: tuple[str, ...]) -> None:
        """Handle SOURce:FREQuency.

        Args:
            parameters: One frequency, with or without a unit.
        """
        frequency_hz = parse_number(expect_one_parameter(parameters), FREQUENCY_UNITS)
        _check_range(frequency_hz, FREQUENCY_RANGE_HZ, "Hz")
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
        _check_range(level_dbm, LEVEL_RANGE_DBM, "dBm")
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
