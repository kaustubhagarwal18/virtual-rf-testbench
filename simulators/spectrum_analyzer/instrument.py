"""The simulated spectrum analyzer: settings, sweeps, the peak marker and channel power."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.instrument import Identity, QueryHandler, ScpiInstrument, WriteHandler
from simulators.common.numbers import FREQUENCY_UNITS, check_range, format_number, parse_number
from simulators.common.parser import (
    expect_no_parameters,
    expect_one_parameter,
    parse_boolean,
    parse_choice,
)
from simulators.common.physics import PhysicsModel
from simulators.spectrum_analyzer.link import GeneratorLink

MANUFACTURER = "VirtualRF"
MODEL = "SA-1"
CENTRE_RANGE_HZ = (9e3, 7e9)
SPAN_RANGE_HZ = (10.0, 7e9)
RBW_RANGE_HZ = (1.0, 10e6)
CHANNEL_BANDWIDTH_RANGE_HZ = (100.0, 100e6)
SWEEP_POINTS = 1001
"""Points per sweep, as on R&S analyzers; the marker frequency snaps to one of them."""


@dataclass(frozen=True)
class AnalyzerSettings:
    """The analyzer's measurement settings.

    Attributes:
        centre_hz: The centre frequency of the display and of the power channel.
        span_hz: The frequency range shown, centred on centre_hz.
        rbw_hz: The resolution bandwidth, which sets the noise floor.
        channel_bandwidth_hz: The width of the channel-power channel.
        continuous_sweep: Whether every measurement takes a fresh sweep.
    """

    centre_hz: float
    span_hz: float
    rbw_hz: float
    channel_bandwidth_hz: float
    continuous_sweep: bool


DEFAULT_SETTINGS = AnalyzerSettings(
    centre_hz=1e9, span_hz=1e6, rbw_hz=1e3, channel_bandwidth_hz=1e5, continuous_sweep=True
)
"""The settings at power-on and after *RST."""


@dataclass(frozen=True)
class _FrequencySetting:
    """One frequency-valued setting: its command, its range, and how to read and change it.

    Attributes:
        pattern: The command in SCPI manual notation.
        valid_range: The values the analyzer accepts, in Hz.
        read: Returns the setting from the analyzer settings.
        update: Returns new analyzer settings with the setting changed.
    """

    pattern: str
    valid_range: tuple[float, float]
    read: Callable[[AnalyzerSettings], float]
    update: Callable[[AnalyzerSettings, float], AnalyzerSettings]


_FREQUENCY_SETTINGS = (
    _FrequencySetting(
        "[SENSe]:FREQuency:CENTer",
        CENTRE_RANGE_HZ,
        lambda settings: settings.centre_hz,
        lambda settings, value_hz: replace(settings, centre_hz=value_hz),
    ),
    _FrequencySetting(
        "[SENSe]:FREQuency:SPAN",
        SPAN_RANGE_HZ,
        lambda settings: settings.span_hz,
        lambda settings, value_hz: replace(settings, span_hz=value_hz),
    ),
    _FrequencySetting(
        "[SENSe]:BANDwidth[:RESolution]",
        RBW_RANGE_HZ,
        lambda settings: settings.rbw_hz,
        lambda settings, value_hz: replace(settings, rbw_hz=value_hz),
    ),
    _FrequencySetting(
        "[SENSe]:POWer:ACHannel:BANDwidth[:CHANnel<n>]",
        CHANNEL_BANDWIDTH_RANGE_HZ,
        lambda settings: settings.channel_bandwidth_hz,
        lambda settings, value_hz: replace(settings, channel_bandwidth_hz=value_hz),
    ),
)
"""The analyzer's frequency-valued settings, all set and read the same way."""


@dataclass(frozen=True)
class SweepResult:
    """What one sweep measured.

    Attributes:
        peak_frequency_hz: Where the highest point of the trace is.
        peak_level_dbm: How high it is.
        channel_power_dbm: The power in the channel around the centre frequency.
    """

    peak_frequency_hz: float
    peak_level_dbm: float
    channel_power_dbm: float


@dataclass(frozen=True)
class Marker:
    """Where the marker sits after a peak search.

    Attributes:
        frequency_hz: The marker frequency.
        level_dbm: The marker level.
    """

    frequency_hz: float
    level_dbm: float


class SpectrumAnalyzerScpi(ScpiInstrument):
    """A swept spectrum analyzer measuring the generator through the bench physics model."""

    def __init__(
        self,
        firmware_version: str,
        generator: GeneratorLink,
        physics: PhysicsModel,
        serial_number: str = "200001",
    ) -> None:
        """Create the analyzer at its default settings, with its commands registered.

        Args:
            firmware_version: The simulator release, reported by *IDN?.
            generator: Tells the analyzer what the generator is sending.
            physics: Turns the generator's output into readings.
            serial_number: The unit's serial number, reported by *IDN?.
        """
        super().__init__(Identity(MANUFACTURER, MODEL, serial_number, firmware_version))
        self._generator = generator
        self._physics = physics
        self._settings = DEFAULT_SETTINGS
        self._sweep: SweepResult | None = None
        self._marker: Marker | None = None
        for setting in _FREQUENCY_SETTINGS:
            self.register(
                setting.pattern,
                write=self._frequency_setter(setting),
                query=self._frequency_query(setting),
            )
        self.register("INITiate<n>[:IMMediate]", write=self._initiate)
        self.register(
            "INITiate<n>:CONTinuous", write=self._set_continuous, query=self._query_continuous
        )
        self.register("CALCulate<n>:MARKer<m>:MAXimum[:PEAK]", write=self._peak_search)
        self.register("CALCulate<n>:MARKer<m>:X", query=self._query_marker_frequency)
        self.register("CALCulate<n>:MARKer<m>:Y", query=self._query_marker_level)
        self.register(
            "CALCulate<n>:MARKer<m>:FUNCtion:POWer:RESult", query=self._query_channel_power
        )

    def on_reset(self) -> None:
        """Restore the default settings, clear the sweep and marker, and reseed the noise."""
        self._settings = DEFAULT_SETTINGS
        self._sweep = None
        self._marker = None
        self._physics.reset()

    def _frequency_setter(self, setting: _FrequencySetting) -> WriteHandler:
        """Build the handler that changes one frequency-valued setting.

        Args:
            setting: The setting to change.

        Returns:
            The write handler.
        """

        def set_frequency(parameters: tuple[str, ...]) -> None:
            """Change the setting from one frequency parameter, checking its range.

            Args:
                parameters: One frequency, with or without a unit.
            """
            frequency_hz = parse_number(expect_one_parameter(parameters), FREQUENCY_UNITS)
            check_range(frequency_hz, setting.valid_range, "Hz")
            self._settings = setting.update(self._settings, frequency_hz)

        return set_frequency

    def _frequency_query(self, setting: _FrequencySetting) -> QueryHandler:
        """Build the handler that reports one frequency-valued setting.

        Args:
            setting: The setting to report.

        Returns:
            The query handler.
        """

        def query_frequency(parameters: tuple[str, ...]) -> str:
            """Report the setting in Hz.

            Args:
                parameters: The query's parameters; there must be none.

            Returns:
                The value in Hz.
            """
            expect_no_parameters(parameters)
            return format_number(setting.read(self._settings))

        return query_frequency

    def _initiate(self, parameters: tuple[str, ...]) -> None:
        """Handle INITiate: take one sweep now.

        Args:
            parameters: The command's parameters; there must be none.
        """
        expect_no_parameters(parameters)
        self._take_sweep()

    def _set_continuous(self, parameters: tuple[str, ...]) -> None:
        """Handle INITiate:CONTinuous.

        Args:
            parameters: ON, OFF, 1 or 0.
        """
        continuous = parse_boolean(expect_one_parameter(parameters))
        self._settings = replace(self._settings, continuous_sweep=continuous)

    def _query_continuous(self, parameters: tuple[str, ...]) -> str:
        """Answer INITiate:CONTinuous?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            '1' for continuous sweep, '0' for single sweep.
        """
        expect_no_parameters(parameters)
        return "1" if self._settings.continuous_sweep else "0"

    def _peak_search(self, parameters: tuple[str, ...]) -> None:
        """Handle CALCulate:MARKer:MAXimum: put the marker on the highest point of the trace.

        Args:
            parameters: The command's parameters; there must be none.
        """
        expect_no_parameters(parameters)
        sweep = self._sweep_for_measurement()
        self._marker = Marker(sweep.peak_frequency_hz, sweep.peak_level_dbm)

    def _query_marker_frequency(self, parameters: tuple[str, ...]) -> str:
        """Answer CALCulate:MARKer:X?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The marker frequency in Hz.
        """
        expect_no_parameters(parameters)
        return format_number(self._placed_marker().frequency_hz)

    def _query_marker_level(self, parameters: tuple[str, ...]) -> str:
        """Answer CALCulate:MARKer:Y?.

        Args:
            parameters: The query's parameters; there must be none.

        Returns:
            The marker level in dBm.
        """
        expect_no_parameters(parameters)
        return format_number(self._placed_marker().level_dbm)

    def _query_channel_power(self, parameters: tuple[str, ...]) -> str:
        """Answer CALCulate:MARKer:FUNCtion:POWer:RESult? CPOWer.

        Args:
            parameters: The CPOWer keyword.

        Returns:
            The channel power in dBm.
        """
        parse_choice(expect_one_parameter(parameters), ("CPOWer",))
        return format_number(self._sweep_for_measurement().channel_power_dbm)

    def _sweep_for_measurement(self) -> SweepResult:
        """Return the sweep a measurement uses: a fresh one in continuous mode.

        Returns:
            The sweep result.

        Raises:
            ScpiCommandError: -230 in single-sweep mode when no sweep has been taken yet.
        """
        if self._settings.continuous_sweep:
            return self._take_sweep()
        if self._sweep is None:
            raise ScpiCommandError(
                ScpiError(ScpiErrorCode.DATA_CORRUPT_OR_STALE, "no sweep yet; send INIT")
            )
        return self._sweep

    def _placed_marker(self) -> Marker:
        """Return the marker, which exists only after a peak search.

        Returns:
            The marker.

        Raises:
            ScpiCommandError: -221 if no peak search has been done since *RST.
        """
        if self._marker is None:
            raise ScpiCommandError(
                ScpiError(ScpiErrorCode.SETTINGS_CONFLICT, "no peak search since *RST")
            )
        return self._marker

    def _take_sweep(self) -> SweepResult:
        """Sweep once: ask the generator what it sends, and measure it through the physics model.

        Both results come from the swept span only, as a trace-based measurement does: a
        tone outside the span is in neither the peak nor the channel power.

        Returns:
            The new sweep result, which is also kept for single-sweep measurements.
        """
        settings = self._settings
        source = self._generator.source_state()
        in_view = source if self._in_span(source.frequency_hz) else replace(source, output_on=False)
        peak_level_dbm = self._physics.peak_reading_dbm(in_view, settings.rbw_hz)
        peak_frequency_hz = (
            self._nearest_sweep_point(source.frequency_hz)
            if self._physics.tone_is_visible(in_view, settings.rbw_hz)
            else settings.centre_hz
        )
        channel_power_dbm = self._physics.channel_power_reading_dbm(
            in_view, settings.centre_hz, settings.channel_bandwidth_hz
        )
        self._sweep = SweepResult(peak_frequency_hz, peak_level_dbm, channel_power_dbm)
        return self._sweep

    def _in_span(self, frequency_hz: float) -> bool:
        """Check whether a frequency lies within the displayed span.

        Args:
            frequency_hz: The frequency to check.

        Returns:
            True if it lies between the start and stop frequencies.
        """
        half_span_hz = self._settings.span_hz / 2
        return abs(frequency_hz - self._settings.centre_hz) <= half_span_hz

    def _nearest_sweep_point(self, frequency_hz: float) -> float:
        """Snap a frequency to the nearest of the sweep's points.

        Args:
            frequency_hz: The frequency to snap.

        Returns:
            The frequency of the nearest sweep point.
        """
        start_hz = self._settings.centre_hz - self._settings.span_hz / 2
        step_hz = self._settings.span_hz / (SWEEP_POINTS - 1)
        return start_hz + round((frequency_hz - start_hz) / step_hz) * step_hz
